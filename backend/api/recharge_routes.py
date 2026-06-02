import os

from fastapi import APIRouter, Request, HTTPException, Depends, Header
from pydantic import BaseModel
from db.user_store import user_store
from api.deps import get_current_user

router = APIRouter(prefix="/api/recharge", tags=["recharge"])

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
if not ADMIN_TOKEN:
    import warnings
    warnings.warn("ADMIN_TOKEN 环境变量未设置，充值审批接口已禁用。请设置 ADMIN_TOKEN 环境变量。")


def verify_admin(request: Request):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="管理员接口未配置，请设置 ADMIN_TOKEN 环境变量")
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="需要管理员权限")

TIER_MAP = {
    "standard": {"name": "标准版", "tokens": 200000, "price": 10},
    "professional": {"name": "专业版", "tokens": 500000, "price": 30},
    "flagship": {"name": "旗舰版", "tokens": 1000000, "price": 50},
}


class RechargeSubmitRequest(BaseModel):
    tier: str


@router.post("/submit")
async def submit_recharge(req: RechargeSubmitRequest, request: Request):
    user = get_current_user(request)
    tier_info = TIER_MAP.get(req.tier)
    if not tier_info:
        raise HTTPException(status_code=400, detail=f"无效套餐: {req.tier}")
    recent_count = user_store.count_recent_recharges(user["user_token"], minutes=10)
    if recent_count >= 3:
        raise HTTPException(status_code=429, detail="操作过于频繁，请 10 分钟后再试")
    result = user_store.create_recharge_request(
        user["user_token"],
        tier_info["name"],
        tier_info["tokens"],
        tier_info["price"],
    )
    return {
        "id": result["id"],
        "verify_code": result["verify_code"],
        "tier_name": result["tier_name"],
        "tokens": result["tokens"],
        "price": result["price"],
        "status": result["status"],
    }


@router.get("/status")
async def get_recharge_status(request: Request):
    user = get_current_user(request)
    recharges = user_store.get_user_recharges(user["user_token"])
    return {"recharges": recharges}


@router.get("/latest")
async def get_latest_recharge(request: Request):
    user = get_current_user(request)
    recharges = user_store.get_user_recharges(user["user_token"])
    if not recharges:
        return {"recharge": None}
    return {"recharge": recharges[0]}


@router.get("/pending")
async def get_pending_recharges(request: Request, admin = Depends(verify_admin)):
    return {"pending": user_store.get_pending_recharges()}


@router.post("/approve/{request_id}")
async def approve_recharge(request_id: str, admin = Depends(verify_admin)):
    result = user_store.approve_recharge(request_id)
    if result is None:
        raise HTTPException(status_code=404, detail="充值记录未找到或已处理")
    return {"status": "approved", "tokens_added": result["tokens"]}


@router.post("/reject/{request_id}")
async def reject_recharge(request_id: str, admin = Depends(verify_admin)):
    result = user_store.reject_recharge(request_id)
    if result is None:
        raise HTTPException(status_code=404, detail="充值记录未找到或已处理")
    return {"status": "rejected"}


@router.post("/confirm/{request_id}")
async def confirm_recharge(request_id: str, request: Request):
    user = get_current_user(request)
    recharges = user_store.get_user_recharges(user["user_token"])
    match = next((r for r in recharges if r["id"] == request_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="充值记录未找到")
    if match["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"充值记录状态为 {match['status']}，无法确认")
    result = user_store.confirm_recharge(request_id)
    if result is None:
        raise HTTPException(status_code=500, detail="确认失败")
    return {"status": "pending_review", "message": "已确认付款，等待管理员审核"}


@router.post("/cancel/{request_id}")
async def cancel_recharge(request_id: str, request: Request):
    user = get_current_user(request)
    result = user_store.cancel_recharge(request_id, user["user_token"])
    if result is None:
        raise HTTPException(status_code=404, detail="充值记录未找到或无法取消")
    return {"status": "cancelled"}


@router.post("/admin-approve/{recharge_id}")
async def admin_approve_recharge(recharge_id: str, admin_token: str = Header(..., alias="X-Admin-Token")):
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or admin_token != expected:
        raise HTTPException(status_code=403, detail="管理员权限不足")
    record = user_store.get_recharge(recharge_id)
    if not record:
        raise HTTPException(status_code=404, detail="充值记录不存在")
    if record.get("status") not in ("pending", "pending_review"):
        raise HTTPException(status_code=400, detail="仅待审核状态可审批")
    user_store.approve_recharge(recharge_id)
    return {"status": "approved", "message": "充值已审批通过，额度已到账"}
