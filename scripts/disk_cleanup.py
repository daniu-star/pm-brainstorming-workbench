"""磁盘清理：报告并清掉本项目产生的可重建产物（默认只报告，不动手）。

这些产物都能重新生成，删掉不影响代码、数据与验收证据：

  chrome-e2e   浏览器 e2e 的临时 user-data-dir（`%TEMP%\\browser-e2e-*`）与日志
  temp-tests   `backend/.tmp_tests/`（测试用的临时库与恢复副本）
  tmp-files    仓库根/backend 下的 `.tmp_*` 脚本、`*.tmp_*.txt`、`chrome-e2e-*.log`
  backups      `backend/data/backups/` 里超出保留份数的旧备份（保留最近 N 份）
  npm-cache    `npm config get cache` 指向的目录（实测单机可占十几 GB；需显式 `--npm-cache`）
  next-cache   `frontend/.next/`（Next 构建缓存；删了要重启 `npm run dev`；需 `--include-next`）

用法：
    python scripts/disk_cleanup.py                    # 只报告还有多少可清
    python scripts/disk_cleanup.py --apply            # 清理 chrome-e2e / temp-tests / tmp-files / backups
    python scripts/disk_cleanup.py --apply --npm-cache
    python scripts/disk_cleanup.py --keep-backups 2 --only chrome-e2e,temp-tests
    python scripts/disk_cleanup.py --json

注意：只处理本仓库与 e2e 自己产生的路径，不碰其它项目目录。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"

CATEGORIES = ("chrome-e2e", "temp-tests", "tmp-files", "backups", "next-cache", "npm-cache")
DEFAULT_KEEP_BACKUPS = 2
DEFAULT_APPLY = ("chrome-e2e", "temp-tests", "tmp-files", "backups")


@dataclass
class Target:
    category: str
    path: Path
    files: int
    bytes: int
    note: str = ""

    @property
    def exists(self) -> bool:
        return self.path.exists()


def _path_stats(path: Path) -> tuple[int, int]:
    """返回 (文件数, 字节数)；不存在的路径返回 (0, 0)。"""
    if not path.exists():
        return 0, 0
    files = 0
    total = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                stat = os.stat(os.path.join(root, name))
            except OSError:
                continue
            files += 1
            total += stat.st_size
    return files, total


def _drives() -> list[tuple[str, int, int]]:
    """返回 [(盘符, 可用字节, 总字节)]；非 Windows 只有根盘。"""
    letters: list[str] = []
    if os.name == "nt":
        letters = [f"{chr(code)}:\\" for code in range(ord("A"), ord("Z") + 1)]
    else:
        letters = ["/"]
    rows: list[tuple[str, int, int]] = []
    for letter in letters:
        if not os.path.exists(letter):
            continue
        try:
            usage = shutil.disk_usage(letter)
        except OSError:
            continue
        rows.append((letter.rstrip("\\") or letter, usage.free, usage.total))
    return rows


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _chrome_temp_dirs() -> list[Path]:
    tmp = Path(tempfile.gettempdir())
    return sorted(p for p in tmp.glob("browser-e2e-*") if p.is_dir())


def _chrome_logs() -> list[Path]:
    tmp = Path(tempfile.gettempdir())
    logs = list(tmp.glob("chrome-e2e-*.log"))
    logs += list(BACKEND.glob("chrome-e2e-*.log"))
    return sorted(logs)


def _tmp_files() -> list[Path]:
    patterns = (".tmp_*", "*.tmp_*.txt", "*.tmp_*.py")
    found: list[Path] = []
    for base in (REPO, BACKEND):
        for pattern in patterns:
            found.extend(p for p in base.glob(pattern) if p.is_file())
    return sorted(set(found))


def _backup_dirs() -> list[Path]:
    root = BACKEND / "data" / "backups"
    if not root.exists():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()),
        key=lambda p: p.stat().st_mtime,
    )


def plan_backup_rotation(backup_root: Path, keep: int) -> list[Path]:
    """返回需要删除的旧备份目录（按 mtime 保留最新 keep 份）。"""
    if keep < 0:
        raise ValueError("keep 不能为负数")
    if not backup_root.exists():
        return []
    backups = sorted(
        (p for p in backup_root.iterdir() if p.is_dir() and (p / "manifest.json").exists()),
        key=lambda p: p.stat().st_mtime,
    )
    return backups[: max(0, len(backups) - keep)]


def _npm_cache_dir() -> Path | None:
    env = os.environ.get("npm_config_cache")
    if env:
        return Path(env)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return None
    try:
        out = subprocess.run(
            [npm, "config", "get", "cache"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
            shell=os.name == "nt",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return Path(value) if value else None


def collect(*, include_next: bool, include_npm: bool, keep_backups: int) -> list[Target]:
    targets: list[Target] = []

    chrome_dirs = _chrome_temp_dirs()
    if chrome_dirs:
        files = total = 0
        for path in chrome_dirs:
            count, size = _path_stats(path)
            files += count
            total += size
        targets.append(Target("chrome-e2e", Path(f"{len(chrome_dirs)} 个临时目录"), files, total, "浏览器 e2e 的临时 user-data-dir"))
    for log in _chrome_logs():
        count, size = _path_stats(log)
        targets.append(Target("chrome-e2e", log, count, size, "e2e 日志"))

    tmp_tests = BACKEND / ".tmp_tests"
    if tmp_tests.exists():
        count, size = _path_stats(tmp_tests)
        targets.append(Target("temp-tests", tmp_tests, count, size, "测试临时库/恢复副本"))

    for path in _tmp_files():
        count, size = _path_stats(path)
        targets.append(Target("tmp-files", path, count, size, "临时脚本/输出"))

    stale_backups = plan_backup_rotation(BACKEND / "data" / "backups", keep_backups)
    for path in stale_backups:
        count, size = _path_stats(path)
        targets.append(Target("backups", path, count, size, f"旧备份（保留最近 {keep_backups} 份）"))

    if include_next:
        next_dir = FRONTEND / ".next"
        if next_dir.exists():
            count, size = _path_stats(next_dir)
            targets.append(Target("next-cache", next_dir, count, size, "Next 构建缓存（删后需重启 dev）"))

    if include_npm:
        cache = _npm_cache_dir()
        if cache is not None and cache.exists():
            count, size = _path_stats(cache)
            targets.append(Target("npm-cache", cache, count, size, "npm 缓存（可再下载）"))

    return targets


def _remove(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink()
        return True
    except OSError:
        return False


def select_categories(only: str | None, skip: str) -> tuple[list[str], str | None]:
    picked = list(DEFAULT_APPLY)
    if only:
        wanted = [item.strip() for item in only.split(",") if item.strip()]
        unknown = [item for item in wanted if item not in CATEGORIES]
        if unknown:
            return [], f"未知类别：{', '.join(unknown)}（可用：{', '.join(CATEGORIES)}）"
        picked = [item for item in CATEGORIES if item in wanted]
    skipped = {item.strip() for item in skip.split(",") if item.strip()}
    unknown_skip = [item for item in skipped if item not in CATEGORIES]
    if unknown_skip:
        return [], f"未知类别：{', '.join(unknown_skip)}（可用：{', '.join(CATEGORIES)}）"
    return [item for item in picked if item not in skipped], None


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理本项目产生的可重建产物（默认只报告）")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只报告）")
    parser.add_argument("--only", help="只处理这些类别（逗号分隔）")
    parser.add_argument("--skip", default="", help="跳过这些类别（逗号分隔）")
    parser.add_argument("--keep-backups", type=int, default=DEFAULT_KEEP_BACKUPS, help="保留几份备份")
    parser.add_argument("--npm-cache", action="store_true", help="同时清理 npm 缓存（可能很慢）")
    parser.add_argument("--include-next", action="store_true", help="同时清理 frontend/.next")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--list-categories", action="store_true", help="列出可清理类别")
    args = parser.parse_args(argv)

    if args.list_categories:
        for name in CATEGORIES:
            default = "（--apply 默认清理）" if name in DEFAULT_APPLY else "（需显式指定）"
            print(f"{name}{default}")
        return 0

    picked, error = select_categories(args.only, args.skip)
    if error:
        print(error, file=sys.stderr)
        return 2

    free_before = _free_bytes(BACKEND)
    targets = [
        target
        for target in collect(
            include_next="next-cache" in picked or args.include_next,
            include_npm="npm-cache" in picked or args.npm_cache,
            keep_backups=args.keep_backups,
        )
        if target.category in picked or (args.include_next and target.category == "next-cache")
    ]

    candidates = [t for t in targets if t.category in picked]
    total_bytes = sum(t.bytes for t in candidates)
    total_files = sum(t.files for t in candidates)

    removed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    if args.apply:
        for target in candidates:
            ok = _remove(target.path)
            entry = {
                "category": target.category,
                "path": str(target.path),
                "files": target.files,
                "bytes": target.bytes,
                "ok": ok,
            }
            (removed if ok else failed).append(entry)
        free_after = _free_bytes(BACKEND)
    else:
        free_after = free_before

    if args.json:
        print(
            json.dumps(
                {
                    "applied": args.apply,
                    "free_bytes_before": free_before,
                    "free_bytes_after": free_after,
                    "candidate_bytes": total_bytes,
                    "candidate_files": total_files,
                    "targets": [
                        {
                            "category": t.category,
                            "path": str(t.path),
                            "files": t.files,
                            "bytes": t.bytes,
                            "note": t.note,
                        }
                        for t in candidates
                    ],
                    "removed": removed,
                    "failed": failed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for letter, free, total in _drives():
            print(f"磁盘 {letter} 可用 {_human(free)} / 总 {_human(total)}")
        if not candidates:
            print("没有需要清理的产物（已经干净）")
        else:
            print("")
            print(f"{'类别':<12} {'大小':>10} {'文件':>7}  路径")
            for target in candidates:
                print(f"{target.category:<12} {_human(target.bytes):>10} {target.files:>7}  {target.path}")
            print("")
            print(f"可清理合计：{_human(total_bytes)} / {total_files} 个文件")
            if args.apply:
                print(f"已删除 {len(removed)} 项，失败 {len(failed)} 项")
                for item in failed:
                    print(f"  失败（可能被进程占用）：{item['path']}")
                print(f"仓库所在盘可用 {_human(free_after)}（释放 {_human(max(0, free_after - free_before))}）")
            else:
                print("只报告未删除：确认后加 --apply")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())