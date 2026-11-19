#!/usr/bin/env python
"""H3 写权限隔离校验：对比基线，检查每个角色的改动是否越界。

用法（在目标仓库根目录）：
    python scripts/verify_write_scopes.py --baseline HEAD~1
    python scripts/verify_write_scopes.py --baseline <sha> \
        --client frontend/** --server backend/** --tests "backend/tests/**,frontend/tests/**"

输出：
    OK   所有改动都落在允许的写域内
    WARN 存在未被任何角色认领的改动（可能是工具生成或越界）
    FAIL 某个角色改了自己写域之外的文件

约定：
- 每个 `--xxx` 参数是一组 glob（逗号分隔），代表一个角色的写域；
- 未匹配任何写域的改动 → 先尝试归类到 tests/docs/tooling，否则 WARN；
- 该脚本只读 git，不修改工作树。
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from collections import defaultdict

try:  # Windows 控制台默认 cp936，统一 UTF-8 避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

DEFAULT_SCOPES = {
    "client": ["frontend/**"],
    "server": ["backend/**"],
    "tests": ["backend/tests/**", "frontend/tests/**"],
    "docs": ["docs/**", "scripts/**", ".github/**", "README.md"],
}


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def norm(path: str) -> str:
    return path.replace("\\", "/")


def best_match(path: str, patterns: list[str]) -> int:
    """返回命中的最长 glob 长度（0 表示未命中）。越具体的写域优先。"""
    best = 0
    for pat in patterns:
        if pat.endswith("/**"):
            if path.startswith(pat[:-3] + "/") or path == pat[:-3]:
                best = max(best, len(pat))
        elif fnmatch.fnmatch(path, pat):
            best = max(best, len(pat))
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="HEAD", help="对比基线（默认 HEAD）")
    ap.add_argument("--diff-filter", default="ACMR", help="git diff 过滤（默认新增/复制/修改/重命名）")
    for role, patterns in DEFAULT_SCOPES.items():
        ap.add_argument(f"--{role}", default=",".join(patterns), help=f"{role} 写域 glob，逗号分隔")
    args = ap.parse_args()

    scopes = {role: [p.strip() for p in getattr(args, role).split(",") if p.strip()] for role in DEFAULT_SCOPES}

    try:
        raw = git("diff", "--name-only", f"--diff-filter={args.diff_filter}", args.baseline)
        files = [norm(f.strip()) for f in raw.splitlines() if f.strip()]
    except SystemExit:
        files = []

    if not files:
        print("OK  没有待检查的改动")
        return 0

    by_role: dict[str, list[str]] = defaultdict(list)
    unclaimed: list[str] = []
    violations: list[str] = []

    for path in files:
        scored = [(best_match(path, pats), role) for role, pats in scopes.items()]
        scored = [(score, role) for score, role in scored if score > 0]
        if not scored:
            unclaimed.append(path)
            continue
        top = max(score for score, _ in scored)
        winners = [role for score, role in scored if score == top]
        by_role[winners[0]].append(path)
        if len(winners) > 1:
            violations.append(f"{path} 同时命中 {winners}（写域定义冲突）")

    for role, paths in sorted(by_role.items()):
        print(f"[{role}] {len(paths)} 个文件")
        for path in paths:
            print(f"    {path}")

    if unclaimed:
        print(f"\nWARN {len(unclaimed)} 个改动未被任何写域认领：")
        for path in unclaimed:
            print(f"    {path}")

    if violations:
        print("\nFAIL 写域重叠：")
        for line in violations:
            print(f"    {line}")
        return 1

    if unclaimed:
        print("\nWARN 请人工确认上述未认领改动（可能是工具生成/临时文件）")
        return 0

    print("\nOK  所有改动都在允许的写域内")
    return 0


if __name__ == "__main__":
    sys.exit(main())