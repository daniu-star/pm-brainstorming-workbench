"""部署环境自检：一条命令看清线上变量是否配齐（第 44 批）。

为什么需要：线上故障里「登录发不出去」「重启丢数据」都属于配置问题，但它们在
应用层面表现成一串看似无关的报错（502 / 数据消失 / 验证码读不到）。这脚本把
关键变量逐项判定为 ok / warn / fail，并给出可直接照做的修复提示。

用法::

    python scripts/check_deploy_environment.py                  # 读当前环境变量
    python scripts/check_deploy_environment.py --env-file backend/.env
    python scripts/check_deploy_environment.py --json           # 机器可读

退出码：0 全通过（允许 warn）；1 存在 fail。脚本只用标准库，不依赖项目代码，
因此在容器、CI 或本机都能跑；不会发送邮件，也不消耗模型额度。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

OK = "ok"
WARN = "warn"
FAIL = "fail"

#: 需要至少 32 字符的会话签名密钥（与 core/auth.py 的判定保持一致）。
MIN_AUTH_SECRET_LENGTH = 32

#: 判定顺序即输出顺序：先数据库（P0 数据安全），再认证，再投递，最后模型。
CHECK_ORDER = ("database", "auth", "cookie", "cors", "email", "llm", "storage")

TcpProbe = Callable[[str, int, float], bool]


@dataclass
class Check:
    """单项判定结果。"""

    name: str
    level: str
    detail: str
    hint: str = ""


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 ``KEY=VALUE`` 形式的 env 文件（忽略注释与空行，去掉成对引号）。"""

    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def merged_env(env_file: Path | None, environ: dict[str, str] | None = None) -> dict[str, str]:
    """文件在前、真实环境变量在后（环境变量优先，和 pydantic-settings 一致）。"""

    merged = parse_env_file(env_file) if env_file else {}
    merged.update({k: v for k, v in (environ if environ is not None else os.environ).items()})
    return merged


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    """TCP 连通性探测（只握手，不发送业务数据）。"""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_database(env: dict[str, str], *, tcp: TcpProbe = _tcp_probe) -> Check:
    url = (env.get("DATABASE_URL") or "").strip()
    if not url:
        # 平台环境判定（第 45 批）：HF Space 每次重启/休眠回收都会重建容器层，
        # 容器内 SQLite 必然丢数据 —— 这属于「上线即数据丢失」，默认判 fail，
        # 而不是像本机那样只给建议。显式接受风险时才降级为 warn。
        on_container_platform = bool((env.get("SPACE_ID") or env.get("SPACE_HOST") or "").strip())
        accepted = (env.get("ACCEPT_EPHEMERAL_STORAGE") or "").strip().lower() in {"1", "true", "yes"}
        # 第 46 批：SQLite 周期快照到私有 dataset，也算一种可恢复的持久化方案。
        # 它不是强一致（最坏丢一个周期），所以判 warn 而不是 ok —— 让人看见代价。
        snapshot_repo = (env.get("DB_SNAPSHOT_REPO") or "").strip()
        snapshot_token = (
            env.get("DB_SNAPSHOT_TOKEN") or env.get("DB_SYNC_TOKEN") or env.get("HF_API_TOKEN") or ""
        ).strip()
        if snapshot_repo and snapshot_token:
            interval = (env.get("DB_SNAPSHOT_INTERVAL_SECONDS") or "").strip() or "120"
            return Check(
                "database",
                WARN,
                f"平台环境未设置 DATABASE_URL，但启用了 SQLite 周期快照（{snapshot_repo}，每 {interval} 秒）",
                "快照不是强一致：重启最多丢最近一个周期的写入；要零丢失请挂持久卷或改用外部数据库",
            )
        if on_container_platform and not accepted:
            return Check(
                "database",
                FAIL,
                "平台环境未设置 DATABASE_URL：SQLite 位于容器内，重启或休眠回收即丢全部数据",
                "给 Space 挂持久卷，或改用外部数据库（Supabase/Neon 的 Postgres URL）；"
                "若确认只是临时演示并接受数据丢失，请显式设置 ACCEPT_EPHEMERAL_STORAGE=1",
            )
        if on_container_platform and accepted:
            return Check(
                "database",
                WARN,
                "平台环境未设置 DATABASE_URL，且已显式接受数据丢失（ACCEPT_EPHEMERAL_STORAGE=1）",
                "仅适用于临时演示：每次重启、休眠回收都会清空全部会话",
            )
        return Check(
            "database",
            WARN,
            "未设置 DATABASE_URL：SQLite 落在容器内，实例重启或休眠回收后数据会丢失",
            "给 Space 挂持久卷，或改用外部数据库（例如 Supabase/Neon 的 Postgres URL）；"
            "否则每次重启、休眠回收都会清空全部会话",
        )
    if url.startswith("sqlite"):
        return Check("database", OK, "使用 SQLite（请确认挂载目录可持久化）")
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (5432 if "postgres" in url else 3306)
    if not host:
        return Check("database", FAIL, f"DATABASE_URL 无法解析主机名：{url[:24]}…", "检查连接串格式")
    if not tcp(host, port, 5.0):
        return Check(
            "database",
            FAIL,
            f"连不上数据库 {host}:{port}",
            "确认数据库允许外部访问（HF 出口 IP 需放行）并核对端口",
        )
    return Check("database", OK, f"数据库端口可达：{host}:{port}（未校验凭据）")


def check_auth(env: dict[str, str]) -> Check:
    secret = (env.get("AUTH_SECRET_KEY") or env.get("JWT_SECRET") or "").strip()
    if not secret:
        return Check(
            "auth",
            FAIL,
            "AUTH_SECRET_KEY 未配置：登录会直接 500",
            "生成 32 位以上随机串（如 `python -c \"import secrets;print(secrets.token_urlsafe(32))\"`）填进变量",
        )
    if len(secret) < MIN_AUTH_SECRET_LENGTH:
        return Check(
            "auth",
            FAIL,
            f"AUTH_SECRET_KEY 只有 {len(secret)} 字符，要求 ≥{MIN_AUTH_SECRET_LENGTH}",
            "换更长的随机串后重启实例",
        )
    return Check("auth", OK, f"AUTH_SECRET_KEY 长度 {len(secret)}（来源：{'AUTH_SECRET_KEY' if env.get('AUTH_SECRET_KEY') else '旧名 JWT_SECRET'}）")


def check_cookie(env: dict[str, str]) -> Check:
    secure = (env.get("AUTH_COOKIE_SECURE") or "").strip().lower()
    if secure in ("1", "true", "yes", "on"):
        return Check("cookie", OK, "AUTH_COOKIE_SECURE 已开启（HTTPS 站点正确）")
    if (env.get("SPACE_ID") or env.get("SPACE_HOST") or "").strip():
        return Check(
            "cookie",
            WARN,
            "线上是 HTTPS，但 AUTH_COOKIE_SECURE 未开启",
            "设 AUTH_COOKIE_SECURE=true，避免 Cookie 在明文链路传输",
        )
    return Check("cookie", OK, "本地环境未强制 secure Cookie（开发态可接受）")


def check_cors(env: dict[str, str]) -> Check:
    raw = (env.get("CORS_ORIGINS") or "").strip()
    if not raw:
        return Check("cors", OK, "未显式配置 CORS_ORIGINS：使用内置白名单")
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if any(item == "*" for item in items):
        return Check(
            "cors",
            FAIL,
            "CORS_ORIGINS 含通配符 *（带凭据时等于放开任意站点）",
            f"改写成具体域名：{','.join(i for i in items if i != '*') or 'https://你的域名'}",
        )
    return Check("cors", OK, f"白名单 {len(items)} 个来源")


def check_email(env: dict[str, str], *, tcp: TcpProbe = _tcp_probe) -> Check:
    proxy = (env.get("EMAIL_PROXY_URL") or "").strip()
    if proxy:
        return Check("email", OK, f"已配置邮件 HTTP 代理：{urlsplit(proxy).hostname or proxy}")
    host = (env.get("SMTP_HOST") or "").strip()
    from_addr = (env.get("SMTP_FROM") or env.get("SMTP_FROM_EMAIL") or "").strip()
    if not host or not from_addr:
        return Check(
            "email",
            WARN,
            "未配置邮件通道：验证码只能写进服务日志（用户需看平台 Logs）",
            "配 SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM，或设 EMAIL_PROXY_URL 走 HTTP 代理；"
            "端口被封时不要把 AUTH_CODE_LOG_FALLBACK 当长期方案",
        )
    port = int((env.get("SMTP_PORT") or "465").strip() or 465)
    if not tcp(host, port, 5.0):
        return Check(
            "email",
            FAIL,
            f"SMTP {host}:{port} 不可达（多数容器平台封 25/465/587）",
            "改用 EMAIL_PROXY_URL 指向你自己的发信服务（代码会先走代理，失败再回落直连）",
        )
    return Check("email", OK, f"SMTP 端口可达：{host}:{port}")


def check_llm(env: dict[str, str]) -> Check:
    mode = (env.get("LLM_KEY_MODE") or "byok").strip().lower()
    if mode == "byok":
        return Check("llm", OK, "BYOK 模式：由登录用户自己填模型 Key")
    key = (env.get("LLM_API_KEY") or "").strip()
    if not key:
        return Check(
            "llm",
            FAIL,
            "LLM_KEY_MODE=system 但 LLM_API_KEY 为空：所有命令会 428",
            "填入账户 Key，或改回 LLM_KEY_MODE=byok 让用户自带",
        )
    model = (env.get("LLM_MODEL") or "").strip() or "(默认 gpt-4o)"
    return Check("llm", OK, f"系统 Key 已配置，模型 {model}")


def check_storage(env: dict[str, str]) -> Check:
    target = (env.get("SESSION_DATA_DIR") or "").strip()
    if not target:
        return Check("storage", WARN, "未设置 SESSION_DATA_DIR：使用默认容器目录（重启即丢）", "指向可持久化的挂载目录")
    path = Path(target)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:  # pragma: no cover - 依赖文件系统状态
        return Check("storage", FAIL, f"目录不可写：{target}（{type(exc).__name__}）", "换可写目录或修权限")
    return Check("storage", OK, f"会话数据目录可写：{target}")


def inspect_environment(
    env: dict[str, str], *, tcp: TcpProbe = _tcp_probe
) -> list[Check]:
    """按固定顺序跑完全部检查（纯函数，方便在测试里断言）。"""

    checks = {
        "database": check_database(env, tcp=tcp),
        "auth": check_auth(env),
        "cookie": check_cookie(env),
        "cors": check_cors(env),
        "email": check_email(env, tcp=tcp),
        "llm": check_llm(env),
        "storage": check_storage(env),
    }
    return [checks[name] for name in CHECK_ORDER]


def overall_exit_code(checks: Iterable[Check]) -> int:
    return 1 if any(check.level == FAIL for check in checks) else 0


def render_text(checks: Iterable[Check]) -> str:
    # ASCII 图标：Windows 默认控制台是 GBK/cp437，编码不了 ✓/✗，
    # 直接用会抛 UnicodeEncodeError（第 45 批修复）。
    symbols = {OK: "[OK]", WARN: "[!]", FAIL: "[X]"}
    lines = ["部署环境自检（check_deploy_environment）", ""]
    for check in checks:
        lines.append(f"{symbols.get(check.level, '?')} {check.name:<9} {check.detail}")
        if check.hint:
            lines.append(f"    → {check.hint}")
    fails = sum(1 for c in checks if c.level == FAIL)
    warns = sum(1 for c in checks if c.level == WARN)
    lines += ["", f"结果：{fails} 项必须修复，{warns} 项建议处理"]
    return "\n".join(lines)


def _tolerate_console_encoding() -> None:
    """让非 UTF-8 控制台把无法编码的字符替换掉，而不是抛异常崩掉整条命令。

    Windows 默认代码页是 GBK（中文）/cp437（英文），`✓`/`✗` 这类字符编码不了，
    直接 print 会抛 UnicodeEncodeError（第 45 批真缺陷，R3 评分卡实测）。
    """

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _tolerate_console_encoding()
    parser = argparse.ArgumentParser(description="部署环境自检（不发送邮件、不调用模型）")
    parser.add_argument("--env-file", default="", help="可选：读取 KEY=VALUE 的 env 文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    parser.add_argument("--skip-network", action="store_true", help="跳过 TCP 探测（离线环境）")
    args = parser.parse_args(argv)

    env_file = Path(args.env_file) if args.env_file else None
    env = merged_env(env_file)
    tcp: TcpProbe = (lambda *_: True) if args.skip_network else _tcp_probe
    checks = inspect_environment(env, tcp=tcp)

    if args.json:
        payload = {
            "checks": [asdict(check) for check in checks],
            "ok": overall_exit_code(checks) == 0,
        }
        # 单行输出：调用方（pre_release_check.py / CI）可以直接取最后一行 json.loads，
        # 不会被缩进换行打断。
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(render_text(checks))
        if args.skip_network:
            print("（已跳过网络探测，端口可达性未验证）")
    return overall_exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
