"""工程化验证：C080-C089（checklist 第六节）—— tsc / pytest / 结构。"""
from __future__ import annotations

import os
import subprocess
import sys

from common import BACKEND, FRONTEND, ROOT, check, read


def _run(cmd: list[str], cwd) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          shell=False, timeout=600)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run() -> list[tuple[str, bool, str]]:
    print("\n== 工程化验证 ==")
    nc = read(FRONTEND / "next.config.js")
    check("C080", "ignoreBuildErrors: false" in nc.replace(" ", "")
          or "ignoreBuildErrors:false" in nc.replace(" ", ""), "构建不忽略 TS 错误")

    npx = "npx.cmd" if os.name == "nt" else "npx"
    code, out = _run([npx, "tsc", "--noEmit"], FRONTEND)
    check("C081", code == 0, f"tsc --noEmit（exit={code}）{out[-300:] if code else ''}")

    code, out = _run([sys.executable, "-m", "pytest", "tests/", "-q"], BACKEND)
    passed = code == 0 and "passed" in out and "failed" not in out.replace(" 0 failed", "")
    summary = out.strip().splitlines()[-1] if out.strip() else f"exit={code}"
    check("C082", passed, f"pytest（exit={code}）{summary}")

    missing = [f for f in ("test_session_store.py", "test_canvas_parser.py",
                           "test_interviewer.py", "test_retriever.py")
               if not (BACKEND / "tests" / f).exists()]
    check("C083", not missing, f"核心测试文件缺失：{missing or '无'}")

    mp = read(BACKEND / "main.py")
    check("C084", "basicConfig" in mp, "logging.basicConfig 配置")
    mods = ["core/agent_loop.py", "core/interviewer.py", "db/session_store.py",
            "api/auth_routes.py", "rag/retriever.py"]
    ok = all("getLogger" in read(BACKEND / m) for m in mods)
    check("C085", ok, "核心模块 logger 覆盖")

    check("C086", (BACKEND / "core" / "sse.py").exists()
          and "sse_event" in read(BACKEND / "core" / "agent_loop.py"),
          "sse_event 统一构造")
    check("C087", "_extract_json" in read(BACKEND / "core" / "canvas_parser.py"),
          "_extract_json 复用")

    srcs = ["core/canvas_parser.py", "core/agent_loop.py", "db/session_store.py",
            "rag/retriever.py"]
    ok = all("from typing import" not in read(BACKEND / s) for s in srcs)
    check("C088", ok, "typing.List/Optional 清零")

    check("C089", "BaseSettings" in read(BACKEND / "core" / "config.py"),
          "pydantic-settings 配置")

    from common import _results
    return _results
