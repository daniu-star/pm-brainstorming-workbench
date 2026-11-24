"""发版前统一体检：一条命令跑完所有本地可验证项。

覆盖：
  docs            文档引用体检（scripts/verify_docs_refs.py --strict）
  backend-import  后端可导入（python -c "import main"）
  backend-tests   后端全量用例（unittest discover）
  frontend-types  前端类型检查（npm run typecheck）
  frontend-tests  前端用例（npm test）
  browser-e2e     真实浏览器 e2e（AC-01 / AC-19 / AC-31，需 8001/3001 已启动）
  backup-drill    备份 → 校验 → 恢复演练（真实库一致性快照）

用法：
    python scripts/pre_release_check.py                     # 全部（含浏览器 e2e）
    python scripts/pre_release_check.py --skip-browser      # 不需要已启动的服务
    python scripts/pre_release_check.py --only docs,backend-tests
    python scripts/pre_release_check.py --list

退出码：全部通过 0；有失败 1；参数错误 2。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"

API_URL = os.environ.get("PM_API_URL", "http://127.0.0.1:8001")
WEB_URL = os.environ.get("PM_WEB_URL", "http://127.0.0.1:3001")

ALL_STEPS = (
    "docs",
    "backend-import",
    "backend-tests",
    "frontend-types",
    "frontend-tests",
    "browser-e2e",
    "backup-drill",
)

# 单步超时（秒）：给足 CI/慢盘余量
TIMEOUTS = {
    "docs": 120,
    "backend-import": 120,
    "backend-tests": 900,
    "frontend-types": 300,
    "frontend-tests": 600,
    "browser-e2e": 600,
    "backup-drill": 300,
}

TAIL_LINES = 3


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    detail: str = ""
    output: list[str] = field(default_factory=list)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _npm() -> str:
    for name in ("npm.cmd", "npm"):
        found = _which(name)
        if found:
            return found
    return "npm"


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def _tail(text: str, *, lines: int = TAIL_LINES) -> list[str]:
    rows = [line.rstrip() for line in text.splitlines() if line.strip()]
    return rows[-lines:]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    shell: bool = False,
) -> tuple[int, str]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=_env(),
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=shell,
    )
    return completed.returncode, completed.stdout or ""


def _http_ok(url: str, *, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- steps


def step_docs() -> Result:
    started = time.time()
    code, out = _run(
        [sys.executable, "scripts/verify_docs_refs.py", "--strict"],
        cwd=REPO,
        timeout=TIMEOUTS["docs"],
    )
    ok = code == 0
    match = re.search(r"扫描 (\d+) 个文件，疑似断链 (\d+) 条", out)
    detail = f"扫描 {match.group(1)} 个文件，断链 {match.group(2)} 条" if match else (out.strip().splitlines() or [""])[-1]
    return Result("docs", ok, time.time() - started, detail, _tail(out))


def step_backend_import() -> Result:
    started = time.time()
    code, out = _run(
        [sys.executable, "-c", "import main; print('IMPORT_OK')"],
        cwd=BACKEND,
        timeout=TIMEOUTS["backend-import"],
    )
    ok = code == 0 and "IMPORT_OK" in out
    return Result("backend-import", ok, time.time() - started, "import main 成功" if ok else "import main 失败", _tail(out))


def step_backend_tests() -> Result:
    started = time.time()
    code, out = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=BACKEND,
        timeout=TIMEOUTS["backend-tests"],
    )
    ran = re.search(r"Ran (\d+) tests?", out)
    ok = code == 0 and re.search(r"^OK", out, re.MULTILINE) is not None
    detail = f"Ran {ran.group(1)} tests / {'OK' if ok else 'FAILED'}" if ran else "未跑出用例数"
    return Result("backend-tests", ok, time.time() - started, detail, _tail(out))


def step_frontend_types() -> Result:
    started = time.time()
    code, out = _run(
        [_npm(), "run", "typecheck"],
        cwd=FRONTEND,
        timeout=TIMEOUTS["frontend-types"],
        shell=True,
    )
    ok = code == 0
    return Result("frontend-types", ok, time.time() - started, "tsc --noEmit 0 错" if ok else "tsc 报错", _tail(out))


def step_frontend_tests() -> Result:
    started = time.time()
    code, out = _run(
        [_npm(), "test"],
        cwd=FRONTEND,
        timeout=TIMEOUTS["frontend-tests"],
        shell=True,
    )
    passed = re.search(r"# pass (\d+)", out)
    failed = re.search(r"# fail (\d+)", out)
    ok = code == 0 and (failed is None or int(failed.group(1)) == 0)
    if passed:
        detail = f"{passed.group(1)} pass / {failed.group(1) if failed else '0'} fail"
    else:
        detail = "npm test 失败" if not ok else "npm test 通过"
    return Result("frontend-tests", ok, time.time() - started, detail, _tail(out))


def step_browser_e2e() -> Result:
    started = time.time()
    if not _http_ok(f"{API_URL}/health"):
        return Result(
            "browser-e2e",
            False,
            time.time() - started,
            f"API 未就绪（{API_URL}/health）：先运行 start.bat，或用 --skip-browser",
        )
    if not _http_ok(f"{WEB_URL}/"):
        return Result(
            "browser-e2e",
            False,
            time.time() - started,
            f"Web 未就绪（{WEB_URL}）：先运行 start.bat，或用 --skip-browser",
        )
    code, out = _run(
        [sys.executable, "../scripts/browser_e2e/run_browser_e2e.py", "AC-01", "AC-19", "AC-31"],
        cwd=BACKEND,
        timeout=TIMEOUTS["browser-e2e"],
    )
    passed = out.count("[PASS]")
    ok = code == 0 and passed >= 3
    detail = f"{passed}/3 场景 PASS"
    return Result("browser-e2e", ok, time.time() - started, detail, _tail(out, lines=6))


def step_backup_drill() -> Result:
    started = time.time()
    code, out = _run(
        [sys.executable, "scripts/backup_restore.py", "backup", "--keep", "2"],
        cwd=REPO,
        timeout=TIMEOUTS["backup-drill"],
    )
    if code != 0:
        return Result("backup-drill", False, time.time() - started, "备份失败", _tail(out))

    backups = sorted(
        (p for p in (BACKEND / "data" / "backups").glob("*/") if (p / "manifest.json").exists()),
        key=lambda p: p.stat().st_mtime,
    )
    if not backups:
        return Result("backup-drill", False, time.time() - started, "找不到刚生成的备份目录", _tail(out))

    latest = backups[-1]
    code, verify_out = _run(
        [sys.executable, "scripts/backup_restore.py", "verify", str(latest)],
        cwd=REPO,
        timeout=TIMEOUTS["backup-drill"],
    )
    if code != 0:
        return Result("backup-drill", False, time.time() - started, "备份校验失败", _tail(verify_out))

    code, drill_out = _run(
        [sys.executable, "scripts/backup_restore.py", "drill", str(latest)],
        cwd=REPO,
        timeout=TIMEOUTS["backup-drill"],
    )
    ok = code == 0 and "RESTORE_DRILL_OK" in drill_out
    sessions = re.search(r"RESTORE_DRILL_OK sessions=(\d+) messages=(\d+) readable_sessions=(\d+)", drill_out)
    detail = (
        f"备份 {latest.name}：sessions={sessions.group(1)} messages={sessions.group(2)} "
        f"readable={sessions.group(3)}"
        if sessions
        else ("恢复演练通过" if ok else "恢复演练失败")
    )
    return Result("backup-drill", ok, time.time() - started, detail, _tail(drill_out))


STEPS = {
    "docs": step_docs,
    "backend-import": step_backend_import,
    "backend-tests": step_backend_tests,
    "frontend-types": step_frontend_types,
    "frontend-tests": step_frontend_tests,
    "browser-e2e": step_browser_e2e,
    "backup-drill": step_backup_drill,
}


# ---------------------------------------------------------------- cli


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def select_steps(*, only: str | None, skip: str, skip_browser: bool) -> tuple[list[str], str | None]:
    """按 CLI 参数算出要跑的步骤；未知步骤返回错误信息。"""
    wanted = list(ALL_STEPS)
    if only:
        picked = _parse_csv(only)
        unknown = [name for name in picked if name not in STEPS]
        if unknown:
            return [], f"未知步骤：{', '.join(unknown)}（可用：{', '.join(ALL_STEPS)}）"
        wanted = [name for name in ALL_STEPS if name in picked]
    skipped = set(_parse_csv(skip))
    if skip_browser:
        skipped.add("browser-e2e")
    return [name for name in wanted if name not in skipped], None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发版前统一体检")
    parser.add_argument("--only", help="只跑这些步骤（逗号分隔）")
    parser.add_argument("--skip", default="", help="跳过这些步骤（逗号分隔）")
    parser.add_argument("--skip-browser", action="store_true", help="跳过浏览器 e2e（不需要已启动的服务）")
    parser.add_argument("--keep-going", action="store_true", help="有失败仍继续跑后续步骤")
    parser.add_argument("--list", action="store_true", help="列出所有步骤后退出")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    args = parser.parse_args(argv)

    if args.list:
        for name in ALL_STEPS:
            print(name)
        return 0

    wanted, error = select_steps(only=args.only, skip=args.skip, skip_browser=args.skip_browser)
    if error:
        print(error, file=sys.stderr)
        return 2

    if not wanted:
        print("没有要执行的步骤", file=sys.stderr)
        return 2

    results: list[Result] = []
    started_all = time.time()
    for name in wanted:
        if not args.json:
            print(f"[..] {name}", flush=True)
        try:
            result = STEPS[name]()
        except subprocess.TimeoutExpired:
            result = Result(name, False, time.time() - started_all, f"超时（>{TIMEOUTS[name]}s）")
        except Exception as exc:  # noqa: BLE001 - 体检脚本需要把任何异常变成一条失败记录
            result = Result(name, False, 0.0, f"执行异常：{type(exc).__name__}: {exc}")
        results.append(result)
        if not args.json:
            mark = "OK  " if result.ok else "FAIL"
            print(f"[{mark}] {name}  {result.seconds:.1f}s  {result.detail}")
            if not result.ok:
                for line in result.output:
                    print(f"       | {line}")
        if not result.ok and not args.keep_going:
            break

    ok = all(result.ok for result in results) and len(results) == len(wanted)
    elapsed = time.time() - started_all
    payload = {
        "ok": ok,
        "elapsed_seconds": round(elapsed, 1),
        "steps": [
            {"name": r.name, "ok": r.ok, "seconds": round(r.seconds, 1), "detail": r.detail, "output": r.output}
            for r in results
        ],
        "skipped": sorted({step for step in ALL_STEPS if step not in wanted}),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("")
        print(f"{'PRE_RELEASE_OK' if ok else 'PRE_RELEASE_FAILED'}  {len(results)} 步 / {elapsed:.1f}s")
        if not ok:
            failed = [r.name for r in results if not r.ok]
            print(f"失败步骤：{', '.join(failed)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())