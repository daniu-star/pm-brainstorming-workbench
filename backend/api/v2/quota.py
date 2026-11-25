"""v2 配额端点：查询当前账户在滚动窗口内的模型消耗与上限。

前端用它显示「今天还剩多少额度」，也用于发版前的联调自查。
配额未开启（默认）时 ``enabled=false``，其余字段仍如实返回已用量。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from api.v2 import common

router = APIRouter(tags=["v2-quota"])


@router.get("/quota")
def get_quota(request: Request):
    owner_email = common.require_user(request)
    quota_service = common.service("quota_service")
    with common.open_db() as db:
        return quota_service.snapshot(
            db, owner_email=owner_email, now_epoch=int(time.time())
        )
