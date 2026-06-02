import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.auth import create_jwt_token, send_sms_code, verify_sms_code
from db.user_store import user_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _validate_oauth_code(code: str, provider: str) -> bool:
    if not code:
        return False
    if len(code) < 8 or len(code) > 128:
        return False
    if not re.match(r'^[a-zA-Z0-9_-]+$', code):
        return False
    return True


class SmsSendRequest(BaseModel):
    phone: str


class SmsVerifyRequest(BaseModel):
    phone: str
    code: str


class WechatAuthRequest(BaseModel):
    code: str


class QQAuthRequest(BaseModel):
    code: str


@router.post("/sms/send")
async def sms_send(req: SmsSendRequest, request: Request):
    if not req.phone:
        raise HTTPException(status_code=400, detail="手机号不能为空")
    code, hint = send_sms_code(req.phone)
    is_dev = request.client.host in ("127.0.0.1", "::1") if request.client else False
    response = {"success": True}
    if hint:
        if hint == "sms_unavailable":
            response["hint"] = "短信服务暂不可用"
        elif is_dev:
            response["hint"] = hint
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


@router.post("/wechat")
async def wechat_auth():
    raise HTTPException(status_code=501, detail="微信登录暂未上线，请使用手机号登录")


@router.post("/qq")
async def qq_auth():
    raise HTTPException(status_code=501, detail="QQ登录暂未上线，请使用手机号登录")


@router.get("/me")
async def get_me(authorization: str = ""):
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
