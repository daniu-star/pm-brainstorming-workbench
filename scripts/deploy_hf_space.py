"""把 V4 后端「可部署子集」推到 Hugging Face Space（第 40 批）。

为什么不能直接推整个仓库：HF Space 的 pre-receive 钩子拒收二进制文件
（``Your push was rejected because it contains binary files.``），而仓库里有
前端 wasm 模型、浏览器验收截图。Space 只需要能构建镜像的那部分文件。

实现方式（纯 plumbing，不碰工作区、不建临时 worktree）：
1. ``git ls-tree -r HEAD -- <子集>`` 取出对象；
2. 独立 index 文件里 ``git read-tree HEAD``，再 ``update-index --force-remove`` 删掉子集以外的路径，
   最后 ``git write-tree`` 得到子集树；
3. ``git commit-tree`` 以既有 ``space-main`` 为父提交（首次则无父）；
4. ``git update-ref refs/heads/space-main`` + ``git push --force space space-main:main``。

这样每次部署都是普通提交（历史留在 ``space-main``），主分支不受影响；
也不依赖临时目录写权限（桌面根 / 用户 TEMP 都可能被安全策略拒绝）。

用法：
    python scripts/deploy_hf_space.py            # 推送
    python scripts/deploy_hf_space.py --dry-run  # 只看将推送的文件与树
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

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


def _run(
    args: list[str], *, cwd: pathlib.Path, check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    """跑外部命令。推送要走凭据管理器，用 capture=False 继承控制台，
    否则 git 可能因为没有交互终端而卡在凭据提示上。"""
    env = dict(os.environ)
    proc = subprocess.run(
        args, cwd=str(cwd), check=False, text=True, capture_output=capture, env=env
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() if capture else ""
        raise SystemExit(f"命令失败（exit {proc.returncode}）：{' '.join(args)}\n{detail}")
    return proc


def _git(args: list[str], *, cwd: pathlib.Path, check: bool = True, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), check=False, text=True, capture_output=True, env=env
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"命令失败（exit {proc.returncode}）：git {' '.join(args)}\n{proc.stderr.strip()}")
    return proc.stdout


def _git_ok(args: list[str], *, cwd: pathlib.Path) -> bool:
    if not "".join(args):
        return False
    proc = subprocess.run(["git", *args], cwd=str(cwd), check=False, text=True, capture_output=True)
    return proc.returncode == 0


def _subset_tree(repo: pathlib.Path, index_file: pathlib.Path) -> tuple[str, list[str]]:
    """用独立 index 从 HEAD 生成「只含子集」的树对象，返回 (tree_sha, 文件列表)。

    做法：``read-tree HEAD`` 填入完整索引 → 用 ``update-index --force-remove``
    删掉子集以外的路径 → ``write-tree``。不直接构造 index-info 条目，是因为
    ``update-index --index-info`` 对「索引里还没有的路径」会套用 .gitignore 规则
    打印 ``Ignoring path`` 并静默跳过（``backend/.env.example`` 等正是这种）；
    路径统一用 ``-z`` 的原始字节传输，避免中文文件名被 core.quotePath 转义成八进制。
    """
    index_file.parent.mkdir(parents=True, exist_ok=True)
    if index_file.exists():
        index_file.unlink()

    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = str(index_file)

    def _bytes(args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(args, cwd=str(repo), capture_output=True, env=env, input=stdin)

    if _bytes(["git", "read-tree", "HEAD"]).returncode != 0:
        raise SystemExit("read-tree HEAD 失败")

    subset = _bytes(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", *SUBSET]).stdout
    keep = {path for path in subset.split(b"\x00") if path}
    everything = _bytes(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"]).stdout.split(b"\x00")
    drop = [path for path in everything if path and path not in keep]
    if drop:
        payload = b"".join(path + b"\x00" for path in drop)
        proc = _bytes(["git", "update-index", "-z", "--force-remove", "--stdin"], stdin=payload)
        if proc.returncode != 0:
            raise SystemExit(f"force-remove 失败：{proc.stderr.decode('utf-8', 'replace')[:200]}")

    tree = _bytes(["git", "write-tree"]).stdout.decode("utf-8").strip()
    files = sorted(path.decode("utf-8", "replace") for path in keep)
    return tree, files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help=f"git 远端名（默认 {DEFAULT_REMOTE}）")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help=f"本地部署分支（默认 {DEFAULT_BRANCH}）")
    parser.add_argument("--message", default="", help="提交信息（默认自动生成）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将推送的文件，不提交不推送")
    args = parser.parse_args(argv)

    repo = pathlib.Path(__file__).resolve().parents[1]
    head = _git(["rev-parse", "--short", "HEAD"], cwd=repo).strip()
    index_file = repo / "backend" / ".tmp_tests" / "space-deploy.index"

    tree, files = _subset_tree(repo, index_file)
    try:
        if args.dry_run:
            print(f"HEAD {head}: 子集树 {tree}，{len(files)} 个文件 -> {args.remote}/main（{args.branch}）")
            for name in files[:20]:
                print(f"  {name}")
            if len(files) > 20:
                print(f"  ... 其余 {len(files) - 20} 个")
            return 0

        parent = ""
        if _git_ok(["rev-parse", "--verify", f"refs/heads/{args.branch}"], cwd=repo):
            parent = _git(["rev-parse", f"refs/heads/{args.branch}"], cwd=repo).strip()
            parent_tree = _git(["rev-parse", f"{parent}^{{tree}}"], cwd=repo).strip()
            if parent_tree == tree:
                print(f"Space 仓库已是最新（树 {tree}），无需推送。")
                return 0

        name = _git(["config", "user.name"], cwd=repo).strip() or "pm-brainstorm"
        email = _git(["config", "user.email"], cwd=repo).strip() or "pm-brainstorm@example.com"
        message = args.message or f"deploy: Space backend subset at {head} ({len(files)} files)"
        commit_args = ["commit-tree", tree]
        if parent:
            commit_args += ["-p", parent]
        commit_args += ["-m", message]
        env = dict(os.environ)
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
                "GIT_INDEX_FILE": str(index_file),
            }
        )
        commit = _git(commit_args, cwd=repo, env=env).strip()
        _git(["update-ref", f"refs/heads/{args.branch}", commit], cwd=repo)
        print(f"提交 {commit[:9]}（父 {parent[:9] or '无'}），{len(files)} 个文件", flush=True)

        ref = f"{args.branch}:main"
        print(f"推送 {ref} -> {args.remote} …", flush=True)
        _run(["git", "push", "--force", args.remote, ref], cwd=repo, check=False, capture=False)
        print("已推送。Space 会按根 Dockerfile 重建（BUILDING → RUNNING）。")
        print("验证：https://www.brainstorming.top/backend/health 与 /backend/openapi.json")
        return 0
    finally:
        if index_file.exists():
            index_file.unlink()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
