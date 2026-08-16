"""审计脚本公共工具：路径、check 结果收集。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

_results: list[tuple[str, bool, str]] = []


def check(cid: str, ok: bool, detail: str = "") -> bool:
    _results.append((cid, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {cid} {detail}")
    return ok


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def setup_backend_path() -> None:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
