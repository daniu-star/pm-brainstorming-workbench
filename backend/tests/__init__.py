"""测试包。

生产默认 ``LLM_KEY_MODE=byok``（每个账户自带模型 Key，登录后强制配置）；
测试与内部回放脚本共用 ``backend/.env`` 里的 Key，所以统一切到 ``system`` 模式，
避免每个用例都要先造一份假的账户配置。BYOK 自身的行为由
``tests/test_llm_config.py`` 显式切回 ``byok`` 后验证。

注意：崩溃注入 / 跨进程用例会 ``subprocess`` 起子进程，子进程只继承环境变量
（不会 import 本包），所以这里同时写 ``os.environ``。

另外两件 CI 上必做的事（本机因为 ``backend/.tmp_tests`` 与 ``backend/data`` 早就存在
而一直没暴露）：
1. 建好 ``backend/.tmp_tests``——多个用例直接 ``tempfile.mkdtemp(dir=...)`` 到这里，
   目录不存在时 Windows 会自己退回到系统 TEMP，Linux（CI）则直接
   ``FileNotFoundError``，导致整批用例在 setUp 里 ERROR。
2. 给默认数据库一个**文件型测试库**并建表——不设置 ``DATABASE_URL`` 时默认库是
   ``backend/data/app.db``，CI 上是空目录（没有 DDL），任何读写都会
   ``sqlite3.OperationalError: no such table``，且 ``/api/v2/sessions`` 会退化成
   ``FEATURE_NOT_READY`` 503。
"""

import os
from pathlib import Path

from core.config import settings

_TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"
_TEST_ROOT.mkdir(parents=True, exist_ok=True)

# 用例显式 configure() 的库会覆盖它；这里只兜底「直接用默认库」的那批用例。
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite+pysqlite:///{(_TEST_ROOT / 'default-suite.db').as_posix()}",
)
os.environ.setdefault("LLM_KEY_MODE", "system")
settings.llm_key_mode = "system"

from repositories.database import initialize_database  # noqa: E402  (需在环境变量之后)

initialize_database()
