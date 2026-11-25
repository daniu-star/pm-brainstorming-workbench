"""测试包。

生产默认 ``LLM_KEY_MODE=byok``（每个账户自带模型 Key，登录后强制配置）；
测试与内部回放脚本共用 ``backend/.env`` 里的 Key，所以统一切到 ``system`` 模式，
避免每个用例都要先造一份假的账户配置。BYOK 自身的行为由
``tests/test_llm_config.py`` 显式切回 ``byok`` 后验证。

注意：崩溃注入 / 跨进程用例会 ``subprocess`` 起子进程，子进程只继承环境变量
（不会 import 本包），所以这里同时写 ``os.environ``。
"""

import os

from core.config import settings

os.environ.setdefault("LLM_KEY_MODE", "system")
settings.llm_key_mode = "system"
