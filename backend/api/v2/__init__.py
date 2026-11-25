"""v2 路由聚合：全部挂在 ``/api/v2`` 前缀下（docs/v4-m1-interfaces.md §5.2）。"""
from __future__ import annotations

from fastapi import APIRouter

from api.v2 import (
    audits,
    canvas,
    commands,
    controls,
    evidence,
    loops,
    messages,
    quota,
    runs,
    sessions,
)

router = APIRouter(prefix="/api/v2")
router.include_router(sessions.router)
router.include_router(messages.router)
router.include_router(evidence.router)
router.include_router(commands.router)
router.include_router(runs.router)
router.include_router(canvas.router)
router.include_router(audits.router)
router.include_router(controls.router)
router.include_router(loops.router)
router.include_router(quota.router)

__all__ = ["router"]