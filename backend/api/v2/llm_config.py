"""账户自带模型（BYOK）配置接口。

* ``GET    /api/v2/llm-config``      读取配置视图（密钥只回末 4 位）
* ``PUT    /api/v2/llm-config``      保存 / 覆盖自己的模型配置
* ``DELETE /api/v2/llm-config``      清除配置（回到「未配置」强制状态）
* ``POST   /api/v2/llm-config/test`` 用一次极小请求验证连通性
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from api.v2 import common
from domain import contracts as C

router = APIRouter(tags=["v2-llm-config"])


@router.get("/llm-config")
def get_llm_config(request: Request) -> dict[str, Any]:
    owner_email = common.require_user(request)
    service = common.service("llm_config_service")
    with common.open_db() as db:
        return service.view(db, owner_email)


@router.put("/llm-config")
def put_llm_config(request: Request, payload: C.LlmConfigPayload) -> dict[str, Any]:
    owner_email = common.require_user(request)
    service = common.service("llm_config_service")
    with common.open_db() as db:
        return service.save(db, owner_email, payload)


@router.delete("/llm-config")
def delete_llm_config(request: Request) -> dict[str, Any]:
    owner_email = common.require_user(request)
    service = common.service("llm_config_service")
    with common.open_db() as db:
        return service.remove(db, owner_email)


@router.post("/llm-config/test")
async def test_llm_config(request: Request) -> dict[str, Any]:
    owner_email = common.require_user(request)
    service = common.service("llm_config_service")
    with common.open_db() as db:
        return await service.test_connection(db, owner_email)
