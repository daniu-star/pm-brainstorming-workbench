"""旧画布路由（/api/canvas）→ v2 确定性投影（FR-05、PRD §7.3）。

* ``POST /generate``、``POST /incremental``：不再调用模型，直接返回
  ``services.canvas_projector.project_canvas(product_state)`` 的确定性结果；
* ``PUT /{session_id}`` 与 ``PUT /tree``：画布已改为派生视图 → 409 ``CANVAS_IS_DERIVED``；
* ``canvas_last_msg_index`` 游标退役，响应里恒为 0。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.v2 import common
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


# 旧请求体模型：保留以便前端旧版本仍能通过校验（内容已不再使用）。
class CanvasLeaf(BaseModel):
    name: str
    source_role: str
    type: str
    source_text: str | None = None


class CanvasBranch(BaseModel):
    name: str
    children: list[CanvasLeaf] = Field(default_factory=list)


class CanvasTree(BaseModel):
    root: str
    branches: list[CanvasBranch] = Field(default_factory=list)


class GenerateCanvasRequest(BaseModel):
    session_id: str


class UpdateCanvasRequest(BaseModel):
    session_id: str | None = None
    tree: CanvasTree | None = None


def _canvas_tree(db, session_id: str) -> dict:
    state = common.get_product_state(db, session_id)
    projection = common.project_canvas(state)
    return common.legacy_canvas_tree(projection)


def _derived_error() -> AppError:
    return AppError(
        ErrorCode.CANVAS_IS_DERIVED,
        "画布由产品状态自动推导，不再支持直接写入",
        details={"hint": "请通过命令更新产品状态，画布会随之派生"},
    )


@router.post("/generate")
def generate_canvas(req: GenerateCanvasRequest, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, req.session_id, owner_email)
        return _canvas_tree(db, session.id)


@router.post("/incremental")
def incremental_canvas(req: GenerateCanvasRequest, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, req.session_id, owner_email)
        return _canvas_tree(db, session.id)


@router.get("/{session_id}")
def get_canvas(session_id: str, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        return _canvas_tree(db, session.id)


@router.put("/tree")
def update_canvas_tree(request: Request):
    common.require_user(request)
    raise _derived_error()


@router.put("/{session_id}")
def update_canvas(session_id: str, request: Request, req: UpdateCanvasRequest | None = None):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        # 仍先做归属/存在性校验：已删除会话 → 404（AC-27）。
        common.require_owned_session(db, session_id, owner_email)
    raise _derived_error()