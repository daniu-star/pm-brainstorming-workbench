#!/usr/bin/env python
"""把 skills/loop-engineering-product 导出为一个可直接推送到 GitHub 的独立仓库。

用法：
    python scripts/export_loop_skill.py --target "C:/Users/13067/Desktop/loop-engineering-product"
    python scripts/export_loop_skill.py --target ../loop-engineering-product --dry-run

行为：
- 复制 skill 目录（SKILL.md / README.md / LICENSE / references / scripts / assets / examples）
- 生成 .gitignore 与 PUSH.md（含 gh / git 两种推送方式）
- 若目标是空目录或已初始化的 git 仓库：git init（默认分支 main）→ git add -A → commit
- 不联网、不 push（GitHub 网络不可达时也能产出可交付仓库）
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "skills" / "loop-engineering-product"

GITIGNORE = """# Python
__pycache__/
*.pyc
.venv/
# Node
node_modules/
# OS / editor
.DS_Store
Thumbs.db
.vscode/
.idea/
# local scratch
.tmp/
*.log
"""

PUSH_MD = """# 推送到 GitHub

本地仓库已初始化并提交（分支 `main`）。任选一种方式推送：

## 方式 A：gh CLI（推荐，一次创建仓库并推送）

```bash
cd <本目录>
gh auth login                # 首次需要
gh repo create loop-engineering-product --public --source . --push \\
  --description "Loop Engineering Product — 五段闭环产品研发 Loop（规划→研发→测试→迭代→评测）"
```

## 方式 B：先用网页创建空仓库，再 push

1. 打开 https://github.com/new ，仓库名填 `loop-engineering-product`，**不要**勾选 README/.gitignore/LICENSE（避免冲突）。
2. 执行：

```bash
cd <本目录>
git remote add origin https://github.com/<你的账号>/loop-engineering-product.git
git branch -M main
git push -u origin main
```

若使用 SSH：`git remote add origin git@github.com:<你的账号>/loop-engineering-product.git`。

## 方式 C：GitHub CLI 已在别处登录，直接指定 token

```bash
GH_TOKEN=<token> gh repo create loop-engineering-product --public --source . --push
```
"""

WANTED = ["SKILL.md", "README.md", "LICENSE", "references", "scripts", "assets", "examples"]


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\nstdout={proc.stdout}\nstderr={proc.stderr}")
    return proc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="导出目录（不存在则创建）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not SOURCE.is_dir():
        raise SystemExit(f"找不到 skill 源目录：{SOURCE}")

    plan: list[str] = []
    for name in WANTED:
        src = SOURCE / name
        if src.exists():
            plan.append(f"copy {src.relative_to(REPO_ROOT)} -> {target / name}")
    plan.append(f"write {target / '.gitignore'}")
    plan.append(f"write {target / 'PUSH.md'}")
    plan.append("git init (if needed) + git add -A + git commit (branch main)")

    print(f"[export] source = {SOURCE}")
    print(f"[export] target = {target}")
    for line in plan:
        print("  -", line)

    if args.dry_run:
        print("[export] dry-run，未写任何文件")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    for name in WANTED:
        src = SOURCE / name
        if not src.exists():
            continue
        dst = target / name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)

    (target / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (target / "PUSH.md").write_text(PUSH_MD, encoding="utf-8")

    is_repo = (target / ".git").is_dir()
    if not is_repo:
        run(["git", "init", "-b", "main"], target, check=False)
    run(["git", "add", "-A"], target, check=False)
    staged = run(["git", "status", "--porcelain"], target, check=False).stdout.strip()
    if staged:
        run(["git", "-c", "user.name=Loop Engineering Product", "-c", "user.email=loop@local", "commit", "-m",
             "feat: Loop Engineering Product —— 五段闭环产品研发 Loop（skill + workflow 脚本 + 模板）"], target, check=False)
    head = run(["git", "log", "--oneline", "-1"], target, check=False).stdout.strip()
    print(f"[export] done. HEAD={head or '(empty repo)'}")
    print(f"[export] 下一步：cd \"{target}\" && 按 PUSH.md 推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())