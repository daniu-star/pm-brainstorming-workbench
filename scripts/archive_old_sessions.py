"""一次性归档旧格式（12 位 hex ID）会话文件到 data/sessions/_archived/。

B002 修复后 session_id 升级为 32 位 hex，旧文件无法通过校验、也不会出现在列表中。
为避免数据混淆与误删，将旧文件移入 _archived/ 保留；是否迁移/清理由产品决策。
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
SESSIONS_DIR = BACKEND / "data" / "sessions"
OLD_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def main() -> int:
    if not SESSIONS_DIR.is_dir():
        print("会话目录不存在，无需归档")
        return 0
    archived = SESSIONS_DIR / "_archived"
    moved = 0
    for f in SESSIONS_DIR.glob("*.json"):
        if OLD_ID_RE.fullmatch(f.stem):
            archived.mkdir(exist_ok=True)
            shutil.move(str(f), archived / f.name)
            moved += 1
            print(f"已归档：{f.name}")
    print(f"完成：共归档 {moved} 个旧格式会话文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
