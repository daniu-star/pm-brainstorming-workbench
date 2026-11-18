"""一次性数据迁移脚本包（PRD §8）。

当前仅包含旧 JSON 会话导入器 :mod:`migrations.import_json_sessions`。
迁移脚本只读取旧 ``data/sessions`` 目录、绝不删除/移动/覆盖源文件，
并通过 ``migration_records`` 保证重复执行幂等。
"""

__all__ = ["import_json_sessions"]