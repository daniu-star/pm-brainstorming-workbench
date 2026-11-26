"""Loop Studio 真实执行沙箱：把「开发 / 测试」阶段的产出真正跑起来。

设计约束（与 PRD 的「可检验」要求一致，但必须先能安全落地）：

* **fail-closed**：默认关闭；只有 ``LOOP_SANDBOX_ENABLED=1`` 且
  ``LOOP_SANDBOX_ALLOWED_COMMANDS`` 配了白名单时才允许执行；
* **argv 直传，不经 shell**：``subprocess`` 只用列表参数（``shell=False``），
  因此 ``&&`` / ``;`` / ``|`` 之类只是普通字符串，不构成拼接注入；
* **进程级隔离**：每次运行在独立临时目录里起进程，环境变量只保留 ``PATH`` 与
  UTF-8 相关项（剥离 ``LLM_API_KEY`` / ``STMP_PASSWORD`` 等密钥），超时到点强杀；
* **输出截断**：stdout / stderr 各保留末尾 ``max_output_bytes``，并标记 ``truncated``，
  避免一条 ``yes`` 就把库撑爆；
* **不是安全边界**：白名单里的解释器（python / node）本身就能执行任意代码，
  所以这功能只在本地 / 受信环境开启，不要暴露给不可信用户。

该模块只做「执行 + 规整结果」，持久化（写 ``loop_artifacts``）由 API 层负责。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import settings
from domain import contracts as C
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 单次运行的 argv 上限（防御性：请求体里塞几千个参数没有意义）。
MAX_ARGS = 12
#: 单个参数的长度上限。
MAX_ARG_LEN = 200
#: 环境变量里必须剥离的键（前缀匹配，统一大写比较）。
_SECRET_ENV_PREFIXES = (
    "LLM_",
    "STT_",
    "TTS_",
    "SMTP_",
    "AUTH_SECRET",
    "ANTHROPIC",
    "OPENAI",
    "DEEPSEEK",
    "API_KEY",
    "SECRET",
    "TOKEN",
)
#: 明确拒绝的参数片段：即便 shell=False 下它们是惰性的，也避免用户误以为是 shell。
_SHELL_METACHARS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "\x00")


@dataclass(frozen=True)
class SandboxOutcome:
    """一次沙箱执行的结果（API 层据此组装 :class:`domain.contracts.SandboxRunReceipt`）。"""

    status: C.SandboxStatus
    command_display: str
    exit_code: int | None
    duration_ms: int
    timeout_seconds: int
    stdout_tail: str
    stderr_tail: str
    truncated: bool


def _allowed_commands() -> list[str]:
    raw = settings.loop_sandbox_allowed_commands or ""
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _timeout_seconds() -> int:
    try:
        return max(1, min(60, int(settings.loop_sandbox_timeout_seconds)))
    except (TypeError, ValueError):  # pragma: no cover - 配置非法时回落到默认
        return 20


def _max_output_bytes() -> int:
    try:
        return max(512, min(65536, int(settings.loop_sandbox_max_output_bytes)))
    except (TypeError, ValueError):  # pragma: no cover
        return 8192


def sandbox_status() -> C.SandboxStatusView:
    """能力开关视图：前端据此决定按钮是否可用、提示怎么写。"""

    allowed = _allowed_commands()
    enabled = bool(settings.loop_sandbox_enabled) and bool(allowed)
    if not settings.loop_sandbox_enabled:
        note = "沙箱未开启：设置 LOOP_SANDBOX_ENABLED=1 并配置 LOOP_SANDBOX_ALLOWED_COMMANDS 后可用。"
    elif not allowed:
        note = "沙箱已开启但白名单为空：请在 LOOP_SANDBOX_ALLOWED_COMMANDS 里列出允许的可执行文件名。"
    else:
        note = (
            "沙箱在独立临时目录里以 argv 直传方式执行（无 shell、超时强杀、输出截断），"
            "只保留 PATH 等最小环境变量；它是进程隔离而非安全边界，仅建议在本地/受信环境使用。"
        )
    return C.SandboxStatusView(
        enabled=enabled,
        allowed_commands=allowed,
        timeout_seconds=_timeout_seconds(),
        max_output_bytes=_max_output_bytes(),
        note=note,
    )


def _display_command(argv: list[str]) -> str:
    return " ".join(argv)[:300]


def _tail(raw: bytes, limit: int) -> tuple[str, bool]:
    """取末尾 ``limit`` 字节；返回 (文本, 是否被截断)。"""

    truncated = len(raw) > limit
    chunk = raw[-limit:] if truncated else raw
    try:
        text = chunk.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, ValueError):  # pragma: no cover - errors="replace" 已兜底
        text = ""
    return text, truncated


def _child_env(workdir: str) -> dict[str, str]:
    """最小环境变量：保留 PATH，其余密钥类键一律剥离。"""

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(upper.startswith(prefix) for prefix in _SECRET_ENV_PREFIXES):
            continue
        env[key] = value
    env["PATH"] = os.environ.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["HOME"] = workdir
    env["TEMP"] = workdir
    env["TMP"] = workdir
    return env


def _workdir_parent() -> Path | None:
    raw = (settings.loop_sandbox_workdir or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def validate_command(argv: list[str]) -> list[str]:
    """校验 argv 并返回规范化后的列表；不合法时抛 ``SANDBOX_COMMAND_NOT_ALLOWED``。"""

    if not isinstance(argv, list) or not argv:
        raise AppError(ErrorCode.SANDBOX_COMMAND_NOT_ALLOWED, "沙箱命令不能为空。")
    if len(argv) > MAX_ARGS:
        raise AppError(
            ErrorCode.SANDBOX_COMMAND_NOT_ALLOWED, f"沙箱命令最多 {MAX_ARGS} 个参数。"
        )
    normalized: list[str] = []
    for item in argv:
        if not isinstance(item, str) or not item.strip():
            raise AppError(
                ErrorCode.SANDBOX_COMMAND_NOT_ALLOWED, "沙箱命令的每个参数都必须是非空字符串。"
            )
        if len(item) > MAX_ARG_LEN:
            raise AppError(
                ErrorCode.SANDBOX_COMMAND_NOT_ALLOWED,
                f"沙箱命令的单个参数不能超过 {MAX_ARG_LEN} 个字符。",
            )
        for token in _SHELL_METACHARS:
            if token in item:
                raise AppError(
                    ErrorCode.SANDBOX_COMMAND_NOT_ALLOWED,
                    "沙箱命令不接受 shell 元字符（含 &&、|、>、$( 等）：请传 argv 数组。",
                )
        normalized.append(item)

    program = os.path.basename(normalized[0]).lower()
    # 白名单按「文件名」与「去掉扩展名的名字」双重匹配：Windows 上是 ``python.exe``，
    # 用户在配置里更可能写 ``python``，两者都应视为同一程序。
    names = {program, os.path.splitext(program)[0]}
    allowed = _allowed_commands()
    if not allowed:
        raise AppError(
            ErrorCode.SANDBOX_DISABLED, "沙箱未配置可执行命令白名单，拒绝执行。"
        )
    if not (names & set(allowed)):
        raise AppError(
            ErrorCode.SANDBOX_COMMAND_NOT_ALLOWED,
            f"可执行文件「{program}」不在白名单里：当前允许 {','.join(allowed)}。",
        )
    return normalized


def run_sandbox(
    argv: list[str],
    *,
    timeout_seconds: int | None = None,
) -> SandboxOutcome:
    """执行一次沙箱运行并返回规整后的结果。

    失败语义：**进程非 0 退出不是异常**（正是要被记录的证据，返回 ``failed``）；
    只有「能力未开 / 命令不合法」与「超时」会以错误码形式暴露给调用方，
    其中超时同时携带 ``SandboxOutcome(status=timeout)`` 供 API 落证据。
    """

    allowed = _allowed_commands()
    # 先判「能力是否开启」再校验命令：关闭时统一回 SANDBOX_DISABLED（fail-closed），
    # 而不是用空白名单把命令判成「不在白名单」（那会让前端提示误导）。
    if not settings.loop_sandbox_enabled or not allowed:
        raise AppError(
            ErrorCode.SANDBOX_DISABLED,
            "沙箱未开启：设置 LOOP_SANDBOX_ENABLED=1 并配置 LOOP_SANDBOX_ALLOWED_COMMANDS 后可用。",
        )
    normalized = validate_command(argv)
    limit = _timeout_seconds()
    timeout = limit if timeout_seconds is None else max(1, min(60, int(timeout_seconds)))

    parent = _workdir_parent()
    if parent is not None and not parent.exists():
        raise AppError(
            ErrorCode.SANDBOX_DISABLED, f"沙箱工作目录不存在：{parent}"
        )
    workdir = tempfile.mkdtemp(prefix="loop-sandbox-", dir=str(parent) if parent else None)
    display = _display_command(normalized)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            normalized,
            cwd=workdir,
            env=_child_env(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            # 独立进程组：否则 _kill_tree 的 killpg 会把调用方（CI 的 shell）一起杀掉。
            start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - 罕见：二次超时
                out, err = b"", b""
        duration_ms = int((time.monotonic() - started) * 1000)
        max_bytes = _max_output_bytes()
        stdout_tail, out_cut = _tail(out or b"", max_bytes)
        stderr_tail, err_cut = _tail(err or b"", max_bytes)
        if timed_out:
            return SandboxOutcome(
                status=C.SandboxStatus.timeout,
                command_display=display,
                exit_code=None,
                duration_ms=duration_ms,
                timeout_seconds=timeout,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                truncated=out_cut or err_cut,
            )
        code = proc.returncode
        status = C.SandboxStatus.passed if code == 0 else C.SandboxStatus.failed
        return SandboxOutcome(
            status=status,
            command_display=display,
            exit_code=code,
            duration_ms=duration_ms,
            timeout_seconds=timeout,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            truncated=out_cut or err_cut,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _kill_tree(proc: subprocess.Popen) -> None:
    """强杀整个进程树：Windows 上裸 ``kill()`` 常留下子进程继续吃 CPU。"""

    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                shell=False,
                timeout=10,
            )
        else:
            # 子进程是以 start_new_session=True 起的，pgid == pid，只杀它自己的组。
            os.killpg(proc.pid, 9)
    except Exception:  # pragma: no cover - 清理失败不应掩盖主流程
        logger.warning("沙箱进程树清理失败（pid=%s）", proc.pid)
    finally:
        try:
            proc.kill()
        except Exception:  # pragma: no cover
            pass


def outcome_to_payload(outcome: SandboxOutcome) -> dict:
    """转成 ``loop_artifacts.payload_json`` 里的负载（不依赖 Pydantic 的持久化格式）。"""

    return {
        "status": outcome.status.value,
        "command_display": outcome.command_display,
        "exit_code": outcome.exit_code,
        "duration_ms": outcome.duration_ms,
        "timeout_seconds": outcome.timeout_seconds,
        "stdout_tail": outcome.stdout_tail,
        "stderr_tail": outcome.stderr_tail,
        "truncated": outcome.truncated,
    }
