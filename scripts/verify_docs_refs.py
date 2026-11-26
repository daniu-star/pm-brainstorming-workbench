"""校验文档里引用的仓库路径是否仍然存在（防止文档腐烂）。

背景：这个仓库的文档会同时记录「当前实现」和「历史热修」，很容易出现
`backend/db/session_store.py` 这类**已被删除**的模块被当成现存文件引用，
或者 screenshot 路径写错。人工 review 难以每条都点一遍，所以用脚本扫：
1) Markdown 链接 `[x](path)` 里的相对目标；
2) 反引号里的仓库相对路径（`backend/...`、`frontend/...`、`scripts/...`、`docs/...` 等）。

默认只打印「疑似断链」并以退出码 0 结束（历史叙述里提到已删除模块是合理的）；
`--strict` 时任何断链都以退出码 1 失败，供 CI 使用——此时应用 `--ignore` 或
在文档里把路径标记为 `~~已删除~~`（脚本跳过带删除线的引用）。

用法：
    python scripts/verify_docs_refs.py                # 报告模式
    python scripts/verify_docs_refs.py --strict       # CI 模式
    python scripts/verify_docs_refs.py --verbose      # 打印每条被检查的引用
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SCAN_DIRS = ("docs",)
SCAN_FILES = ("README.md", "AGENTS.md")

#: 只检查这些前缀开头的反引号路径（避免把 `pip install xxx` 里的名字当路径）
PATH_PREFIXES = (
    "backend/",
    "frontend/",
    "scripts/",
    "docs/",
    "skills/",
    "tests/",
    ".github/",
    "workers/",
    "migrations/",
    "api/",
    "services/",
    "repositories/",
    "workflows/",
    "domain/",
    "core/",
    "rag/",
)
#: 这些路径是「本地生成物 / 密钥 / 依赖目录」，已被 .gitignore 排除：
#: 干净检出（CI、新同事）里本来就不存在，因此不参与断链判定。
GENERATED_PREFIXES = (
    ".tmp/",
    "backend/.tmp/",
    "backend/.tmp_tests/",
    "backend/data/",
    "frontend/.next",
    "frontend/node_modules",
    "node_modules/",
    ".run/",
    "dist/",
    "vendor/",
)
GENERATED_FILES = ("backend/.env", "frontend/.env", ".env", "package-lock.json")

PATH_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".cjs",
    ".mjs",
    ".md",
    ".json",
    ".ps1",
    ".bat",
    ".yml",
    ".yaml",
    ".txt",
    ".db",
)

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
TRAILING_REF_RE = re.compile(r"(?:::[^\s]+|:[0-9]+(?:[,\-][0-9]+)*(?:-[0-9]+)?)$")
BRACE_RE = re.compile(r"\{([^{}]+)\}")
#: 历史记录里明确「已删除」的模块：这些路径不再存在，但文档保留叙述是合理的
REMOVED_TARGETS = frozenset(
    {
        "backend/db/session_store.py",
        "backend/db/__init__.py",
        "backend/tests/test_session_store.py",
        "backend/tests/test_interviewer.py",
        "backend/tests/test_canvas_parser.py",
        "backend/core/agent_loop.py",
        "backend/core/interviewer.py",
        "backend/core/canvas_parser.py",
        "core/agent_loop.py",
        "core/interviewer.py",
        "core/canvas_parser.py",
        "db/session_store.py",
        "db/__init__.py",
    }
)
GLOB_CHARS = ("*", "...", "<", ">", "|", "$", "?", "「", "」")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    target: str
    kind: str

    def render(self) -> str:
        rel = self.path.relative_to(REPO).as_posix()
        return f"{rel}:{self.line}  [{self.kind}] {self.target}"


def iter_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_FILES:
        candidate = REPO / name
        if candidate.is_file():
            files.append(candidate)
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.is_dir():
            continue
        files.extend(sorted(p for p in root.rglob("*.md") if p.is_file()))
    return files


def looks_like_repo_path(text: str) -> bool:
    value = text.strip()
    if not value or " " in value:
        return False
    if value.startswith(("http://", "https://", "mailto:", "#", "/c/", "C:\\", "\\\\")):
        return False
    if any(ch in value for ch in GLOB_CHARS):
        return False
    if re.search(r"-[A-Z]\.", value) or "/x." in value or "/y." in value:
        return False  # round-N.md / src/x.ts 这类模板占位符
    if "/" not in value and not value.endswith((".py", ".ts", ".ps1", ".bat")):
        return False
    return value.startswith(PATH_PREFIXES) or value.endswith(PATH_SUFFIXES)


def normalized_target(raw: str) -> str | None:
    value = raw.strip().strip("`").strip()
    value = value.split("#", 1)[0]
    value = TRAILING_REF_RE.sub("", value)
    value = value.rstrip(",;:)").strip()
    if not looks_like_repo_path(value):
        return None
    return value


def iter_source_files() -> dict[str, set[str]]:
    """仓库内的文件名 → 相对路径集合，用于解析「只写文件名」的引用。"""
    index: dict[str, set[str]] = {}
    for dirname in ("backend", "frontend", "scripts", "docs", "skills"):
        root = REPO / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and ".venv" not in path.parts and "node_modules" not in path.parts:
                index.setdefault(path.name, set()).add(path.relative_to(REPO).as_posix())
    return index


def _expand_braces(target: str) -> list[str]:
    match = BRACE_RE.search(target)
    if match is None:
        return [target]
    prefix, suffix = target[: match.start()], target[match.end() :]
    return [prefix + part + suffix for part in match.group(1).split(",")]


def check_target(source: Path, target: str, basenames: dict[str, set[str]]) -> bool:
    expanded = _expand_braces(target)
    if len(expanded) > 1:
        return all(_check_one(source, item, basenames) for item in expanded)
    return _check_one(source, target, basenames)


def _check_one(source: Path, target: str, basenames: dict[str, set[str]]) -> bool:
    # 0) 生成物 / 本地密钥 / 依赖目录：被 .gitignore 排除，干净检出里不存在，不算断链
    if target in GENERATED_FILES:
        return True
    for prefix in GENERATED_PREFIXES:
        if target.startswith(prefix) or target == prefix.rstrip("/"):
            return True
    # 1) 相对当前文档目录  2) 相对仓库根  3) 相对后端/前端（文档常写成 domain/contracts.py）
    for candidate in (
        source.parent / target,
        REPO / target,
        REPO / "backend" / target,
        REPO / "frontend" / target,
    ):
        if candidate.exists():
            return True
    # 4) 只写文件名（如 `cdp.py`）：仓库里存在同名文件即视为可解析
    if "/" not in target and target in basenames:
        return True
    # 5) 只用路径末段（如 references/harness.md、scripts/verify_write_scopes.py）：
    #    仓库里该文件名唯一时视为可解析（文档常省略中间目录）
    tail = target.rsplit("/", 1)[-1]
    if "/" in target and len(basenames.get(tail, ())) == 1:
        return True
    return False


def scan_file(path: Path, verbose: bool, basenames: dict[str, set[str]]) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        # 删除线（历史记录里「已删除」的路径）跳过
        segments = re.split(r"~~.*?~~", line)
        for segment in segments:
            for match in LINK_RE.finditer(segment):
                target = match.group(1).strip()
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                cleaned = target.split("#", 1)[0].strip()
                if not cleaned:
                    continue
                if any(ch in cleaned for ch in GLOB_CHARS):
                    continue
                if verbose:
                    print(f"  link {cleaned}")
                if not (path.parent / cleaned).exists() and not (REPO / cleaned).exists():
                    findings.append(Finding(path, lineno, cleaned, "link"))
            for match in CODE_RE.finditer(segment):
                target = normalized_target(match.group(1))
                if target is None:
                    continue
                if verbose:
                    print(f"  code {target}")
                if target in REMOVED_TARGETS:
                    continue
                if not check_target(path, target, basenames):
                    findings.append(Finding(path, lineno, target, "code"))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="校验文档中引用的仓库路径")
    parser.add_argument("--strict", action="store_true", help="发现断链时以退出码 1 结束")
    parser.add_argument("--verbose", action="store_true", help="打印每条被检查的引用")
    args = parser.parse_args(argv)

    files = iter_files()
    if not files:
        print("没有找到要扫描的文档")
        return 0
    basenames = iter_source_files()
    findings: list[Finding] = []
    for path in files:
        if args.verbose:
            print(f"scan {path.relative_to(REPO).as_posix()}")
        findings.extend(scan_file(path, args.verbose, basenames))

    print(f"扫描 {len(files)} 个文件，疑似断链 {len(findings)} 条")
    for finding in findings:
        print("  " + finding.render())

    if findings and args.strict:
        print("\n--strict：存在断链，失败")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
