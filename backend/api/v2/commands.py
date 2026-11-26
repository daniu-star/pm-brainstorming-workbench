"""v2 命令入口：POST /sessions/{sid}/commands → command_service.submit_command。

返回码（docs/v4-m1-interfaces.md §5.1）：
* RunReceipt / ControlReceipt → 202（同步命令的回执里 status 已是 succeeded）；
* StartAuditRecovery（恢复已有审计）→ 200。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.v2 import common
from domain.contracts import Action, CommandRequest, StartAuditRecovery
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-commands"])


class CommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    action: str
    expected_session_version: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def dispatch_command(session_id: str, body: CommandBody, request: Request) -> JSONResponse:
    owner_email = common.require_user(request)
    request_id = common.resolve_request_id(request, body.request_id)
    try:
        action = Action(body.action)
    except ValueError as exc:
        raise AppError(
            ErrorCode.UNKNOWN_ACTION,
            f"未知命令：{body.action}",
            details={"action": body.action},
        ) from exc
    if body.expected_session_version is not None and body.expected_session_version < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "expected_session_version 必须大于等于 1")

    # 共享协作：editor 可以提交命令，但以会话所有者身份落账（Run 归属、幂等键与
    # 模型 Key 都沿用所有者，等价于「借用所有者的账户操作这个会话」）。
    with common.open_db() as db:
        session, _role = common.require_session_write(db, session_id, owner_email)
        acting_email = session.owner_email

    # BYOK：平台不带模型额度，未配置自己的 Key 前只允许改稿/登记证据等同步命令。
    with common.open_db() as db:
        common.service("llm_config_service").gate(db, acting_email, action=action.value)

    command = CommandRequest(
        request_id=request_id,
        action=action,
        expected_session_version=body.expected_session_version,
        payload=body.payload,
    )
    with common.open_db() as db:
        receipt = common.service("command_service").submit_command(
            db,
            owner_email=acting_email,
            session_id=session_id,
            command=command,
        )

    status_code = 200 if isinstance(receipt, StartAuditRecovery) else 202
    return JSONResponse(status_code=status_code, content=receipt.model_dump(mode="json"))


@router.post("/sessions/{session_id}/commands", status_code=202)
def submit_command(session_id: str, body: CommandBody, request: Request):
    return dispatch_command(session_id, body, request)


__all__ = ["CommandBody", "dispatch_command", "router"]