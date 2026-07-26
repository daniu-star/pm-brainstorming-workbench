import re
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth import create_jwt_token, derive_guest_user_id, send_sms_code, verify_sms_code
from db.user_store import user_store
from core.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SmsSendRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")


class SmsVerifyRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    code: str = Field(pattern=r"^\d{6}$")


class GuestLoginRequest(BaseModel):
    installation_id: UUID


@router.post("/guest")
async def guest_login(req: GuestLoginRequest):
    user_id = derive_guest_user_id(str(req.installation_id))
    user = user_store.get_or_create_user(
        user_id,
        nickname="体验用户",
        token_quota=settings.guest_initial_quota,
    )
    token = create_jwt_token(user["user_token"])
    return {
        "token": token,
        "user": {
            "nickname": user.get("nickname") or "体验用户",
            "mode": "guest",
        },
    }


@router.post("/sms/send")
async def sms_send(req: SmsSendRequest, request: Request):
    if not req.phone:
        raise HTTPException(status_code=400, detail="手机号不能为空")
    code, hint = send_sms_code(req.phone)
    response = {"success": True}
    if hint:
        if hint == "sms_unavailable":
            response["hint"] = "短信服务暂未配置"
            if settings.allow_sms_code_echo:
                response["code"] = code
    return response


@router.post("/sms/verify")
async def sms_verify(req: SmsVerifyRequest):
    if not verify_sms_code(req.phone, req.code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    user = user_store.get_user_by_phone(req.phone)
    if user is None:
        nickname = f"用户{req.phone[-4:]}"
        user = user_store.create_user_with_phone(req.phone, nickname)
    token = create_jwt_token(user["user_token"])
    return {
        "token": token,
        "user": {
            "user_token": user["user_token"],
            "nickname": user.get("nickname", ""),
            "phone": user.get("phone", ""),
        },
    }


@router.get("/me")
async def get_me(authorization: str = Header(default="")):
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization Header")
    parts = authorization.split(" ")
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1]
    else:
        token = authorization
    from core.auth import verify_jwt_token
    payload = verify_jwt_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Token 缺少用户信息")
    user = user_store.get_or_create_user(user_id)
    return {
        "user_token": user["user_token"],
        "nickname": user.get("nickname", ""),
        "phone": user.get("phone", ""),
        "avatar": user.get("avatar", ""),
    }
