from fastapi import Request, HTTPException
from db.user_store import user_store
from core.auth import verify_jwt_token


def get_current_user(request: Request) -> dict:
    authorization = request.headers.get("Authorization", "")
    if authorization:
        parts = authorization.split(" ")
        if len(parts) == 2 and parts[0].lower() == "bearer":
            payload = verify_jwt_token(parts[1])
            if payload is not None:
                user_id = payload.get("sub")
                if user_id:
                    user = user_store.get_or_create_user(user_id)
                    return user

    user_token = request.headers.get("X-User-Token", "")
    if not user_token:
        raise HTTPException(status_code=400, detail="缺少 Authorization 或 X-User-Token Header")
    return user_store.get_or_create_user(user_token)


def get_user_llm_config(request: Request) -> dict:
    api_key = request.headers.get("X-API-Key", "") or request.headers.get("X-API_Key", "")
    base_url = request.headers.get("X-Base-URL", "") or request.headers.get("X-Base_URL", "")
    model = request.headers.get("X-Model", "")
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
    }


def check_quota(user: dict, llm_config: dict) -> None:
    if llm_config["api_key"]:
        return
    if not user_store.has_quota(user["user_token"]):
        raise HTTPException(
            status_code=403,
            detail="额度不足，请充值或使用自己的 API Key",
        )
