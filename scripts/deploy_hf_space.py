"""把 V4 后端「可部署子集」推到 Hugging Face Space（第 40 批）。

为什么不能直接推整个仓库：HF Space 的 pre-receive 钩子拒收二进制文件
（``Your push was rejected because it contains binary files.``），而仓库里有
前端 wasm 模型、浏览器验收截图。Space 只需要能构建镜像的那部分文件。

做法：在临时 worktree 里基于当前 HEAD 的 ``space-main`` 分支重建一个只含子集文件
的提交，然后 ``git push --force space space-main:main``。每次部署都是一次
普通提交（保留在本地 ``space-main`` 分支），不影响主分支历史。

用法：
    python scripts/deploy_hf_space.py            # 推送
    python scripts/deploy_hf_space.py --dry-run  # 只打印将要推送的文件
    python scripts/deploy_hf_space.py --remote space --branch space-main
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

# 只有这些路径会进 Space 仓库：Dockerfile 构建 + 部署契约测试需要它们。
SUBSET: tuple[str, ...] = (
    "Dockerfile",
    ".dockerignore",
    "README.md",
    "backend",
    "scripts/container_start.py",
)
DEFAULT_REMOTE = "space"
DEFAULT_BRANCH = "space-main"


def _run(args: list[str], *, cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), check=check, text=True, capture_output=True)


def _git(args: list[str], *, cwd: pathlib.Path, check: bool = True) -> str:
    return _run(["git", *args], cwd=cwd, check=check).stdout


def _prepare_archive(repo: pathlib.Path, archive: pathlib.Path) -> None:
    """用 git archive 导出子集（只含已跟踪文件，天然避开 .env / data/ / 临时目录）。"""
    _git(["archive", "--format=tar", "-o", str(archive), "HEAD", *SUBSET], cwd=repo)


def _extract(archive: pathlib.Path, target: pathlib.Path) -> list[str]:
    with tarfile.open(archive) as tar:
        names = [member.name for member in tar.getmembers() if member.isfile()]
        try:
            # Python 3.14 起 extractall 默认 filter="data"；显式传入让行为一致。
            tar.extractall(target, filter="data")  # noqa: S202 - 归档来自本仓库 HEAD
        except TypeError:  # pragma: no cover - Python 3.11 没有 filter 参数
            tar.extractall(target)  # noqa: S202 - 归档来自本仓库 HEAD，不是外部输入
    return sorted(names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help=f"git 远端名（默认 {DEFAULT_REMOTE}）")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help=f"本地部署分支（默认 {DEFAULT_BRANCH}）")
    parser.add_argument("--message", default="", help="提交信息（默认自动生成）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将推送的文件，不提交不推送")
    args = parser.parse_args(argv)

    repo = pathlib.Path(__file__).resolve().parents[1]
    head = _git(["rev-parse", "--short", "HEAD"], cwd=repo).strip()

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="pm-space-deploy-"))
    archive = workdir / "subset.tar"
    worktree = workdir / "tree"
    try:
        _prepare_archive(repo, archive)
        worktree.mkdir()
        files = _extract(archive, worktree)

        if args.dry_run:
            print(f"HEAD {head}: 将推送 {len(files)} 个文件到 {args.remote}/main（分支 {args.branch}）")
            for name in files[:20]:
                print(f"  {name}")
            if len(files) > 20:
                print(f"  ... 其余 {len(files) - 20} 个")
            return 0

        # 用一个临时 worktree 复现部署分支，避免动到主工作区。
        _git(["worktree", "add", "-f", str(worktree), "--detach", "HEAD"], cwd=repo)
        try:
            existing = _run(["git", "rev-parse", "--verify", args.branch], cwd=worktree, check=False)
            if existing.returncode == 0:
                _git(["checkout", "-f", args.branch], cwd=worktree)
            else:
                _git(["checkout", "--orphan", args.branch], cwd=worktree)
            # 清空索引，只保留子集；工作区文件由上面的 archive 覆盖。
            _git(["rm", "-r", "--cached", ".", "-q"], cwd=worktree, check=False)
            _extract(archive, worktree)
            _git(["add", "--all", "--", *SUBSET], cwd=worktree)
            staged = [line for line in _git(["diff", "--cached", "--name-only"], cwd=worktree).splitlines() if line]
            if not staged:
                print("Space 仓库已是最新，无需推送。")
                return 0
            message = args.message or f"deploy: backend subset at {head} ({len(staged)} files changed)"
            env_name = _git(["config", "user.name"], cwd=repo, check=False).strip()
            env_email = _git(["config", "user.email"], cwd=repo, check=False).strip()
            _git(
                [
                    "-c",
                    f"user.name={env_name or 'pm-brainstorm'}",
                    "-c",
                    f"user.email={env_email or 'pm-brainstorm@example.com'}",
                    "commit",
                    "-q",
                    "-m",
                    message,
                ],
                cwd=worktree,
            )
            ref = f"{args.branch}:main"
            print(f"推送 {ref} -> {args.remote}（{len(staged)} 个文件变更）")
            _run(["git", "push", "--force", args.remote, ref], cwd=worktree)
            print("已推送。Space 会自动重建镜像；随后检查：")
            print("  https://huggingface.co/api/spaces/<owner>/<space> 的 runtime.stage 变为 RUNNING")
            print("  https://www.brainstorming.top/backend/health 返回 200")
            return 0
        finally:
            _run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, check=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
