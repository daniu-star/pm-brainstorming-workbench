import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import get_current_user
from core.config import settings
from core.email_service import EmailDeliveryError, send_team_invitation
from core.team_access import ensure_active_team, require_team_manager, require_team_member
from core.team_events import team_event_broker
from db.session_store import session_store
from db.team_store import team_store
from db.user_store import user_store


router = APIRouter(prefix="/api/team", tags=["team"])


class TeamRenameRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class TeamActivateRequest(BaseModel):
    team_id: str = Field(min_length=1, max_length=80)


class TeamInvitationRequest(BaseModel):
    email: str = Field(
        min_length=5,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    role: str = Field(default="member", pattern=r"^(admin|member)$")


class TeamInvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class TeamChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


def _public_member(member: dict, quota: dict | None = None) -> dict:
    return {
        "id": member.get("id", ""),
        "email": member.get("email", ""),
        "nickname": member.get("nickname", "") or "团队成员",
        "role": member.get("role", "member"),
        "status": member.get("status", "active"),
        "joined_at": member.get("joined_at", ""),
        "quota": quota,
    }


def _public_invitation(invitation: dict) -> dict:
    return {
        key: invitation.get(key)
        for key in (
            "id",
            "email",
            "role",
            "status",
            "delivery_status",
            "created_at",
            "expires_at",
        )
    }


def _public_chat_message(message: dict) -> dict:
    return {
        "id": message.get("id", ""),
        "author_name": message.get("author_name", "团队成员"),
        "content": message.get("content", ""),
        "created_at": message.get("created_at", ""),
    }


def _team_summary(team: dict, current_user: dict) -> dict:
    member_quotas = []
    public_members = []
    for member in team.get("members", []):
        quota = user_store.get_quota(member["user_token"])
        member_quotas.append(quota)
        public_members.append(_public_member(member, quota))

    sessions = session_store.list_by_team(team["id"])
    prd_versions = [
        version
        for session in sessions
        for version in (session.get("decision_hub") or {}).get("prd_versions", [])
    ]
    remaining = sum(item["remaining"] for item in member_quotas)
    total = sum(item["quota"] for item in member_quotas)
    used = sum(item["used"] for item in member_quotas)

    return {
        "id": team["id"],
        "name": team["name"],
        "current_user_role": team_store.member_role(team["id"], current_user["user_token"]),
        "members": public_members,
        "invitations": [
            _public_invitation(item)
            for item in team.get("invitations", [])
            if item.get("status") in {"pending", "accepted"}
        ][:30],
        "quota": {
            "total": total,
            "used": used,
            "remaining": remaining,
        },
        "prd_document_count": len(prd_versions),
        "prd_project_count": sum(
            1
            for session in sessions
            if (session.get("decision_hub") or {}).get("prd_versions")
        ),
        "session_count": len(sessions),
        "recent_sessions": [
            {
                "id": session["id"],
                "problem_statement": session.get("problem_statement", ""),
                "phase": session.get("phase", ""),
                "prd_count": len((session.get("decision_hub") or {}).get("prd_versions", [])),
                "updated_at": (session.get("decision_hub") or {}).get("updated_at")
                or session.get("created_at", ""),
            }
            for session in sorted(
                sessions,
                key=lambda item: (item.get("decision_hub") or {}).get("updated_at")
                or item.get("created_at", ""),
                reverse=True,
            )[:8]
        ],
        "smtp_configured": bool(settings.smtp_host and settings.smtp_from_email),
        "updated_at": team.get("updated_at", ""),
    }


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


@router.get("/current")
async def get_current_team(request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    return _team_summary(team, user)


@router.post("/activate")
async def activate_team(req: TeamActivateRequest, request: Request):
    user = get_current_user(request)
    team = require_team_member(req.team_id, user)
    user_store.set_active_team(user["user_token"], team["id"])
    return _team_summary(team, user)


@router.patch("/current")
async def rename_current_team(req: TeamRenameRequest, request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    require_team_manager(team["id"], user)
    name = req.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="团队名称至少需要 2 个字符")
    updated = team_store.rename(team["id"], name)
    team_event_broker.publish(team["id"], "team_updated", {"name": updated["name"]})
    return _team_summary(updated, user)


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
async def create_team_invitation(req: TeamInvitationRequest, request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    require_team_manager(team["id"], user)
    try:
        invitation, token = team_store.create_invitation(
            team["id"],
            req.email,
            req.role,
            user["user_token"],
            settings.team_invite_expiry_hours,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    invitation_url = (
        f"{settings.frontend_base_url}/team/invite?token={quote(token, safe='')}"
    )
    email_sent = True
    warning = ""
    try:
        await asyncio.to_thread(
            send_team_invitation,
            invitation["email"],
            team["name"],
            user.get("nickname") or "团队负责人",
            invitation_url,
        )
        team_store.update_invitation_delivery(team["id"], invitation["id"], "sent")
    except EmailDeliveryError as error:
        email_sent = False
        warning = str(error)
        team_store.update_invitation_delivery(team["id"], invitation["id"], "failed")

    updated_team = team_store.get(team["id"]) or team
    stored_invitation = next(
        (
            item
            for item in updated_team.get("invitations", [])
            if item.get("id") == invitation["id"]
        ),
        invitation,
    )
    team_event_broker.publish(
        team["id"],
        "invitation_created",
        {"invitation": _public_invitation(stored_invitation), "email_sent": email_sent},
    )
    return {
        "invitation": _public_invitation(stored_invitation),
        "email_sent": email_sent,
        "warning": warning,
        "invitation_url": invitation_url,
    }


@router.post("/share-session/{session_id}")
async def share_session_with_team(session_id: str, request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if session.get("user_token") != user["user_token"]:
        raise HTTPException(status_code=403, detail="只有项目创建者可以把项目共享到团队")
    current_team_id = session.get("team_id", "")
    if current_team_id and current_team_id != team["id"]:
        raise HTTPException(status_code=409, detail="该项目已经属于另一个团队")
    session_store.update(session_id, {"team_id": team["id"]})
    team_event_broker.publish(
        team["id"],
        "session_shared",
        {"session_id": session_id},
    )
    return {"status": "shared", "team_id": team["id"]}


@router.get("/invitations/preview")
async def preview_team_invitation(token: str = Query(min_length=20, max_length=200)):
    match = team_store.preview_invitation(token)
    if match is None:
        raise HTTPException(status_code=404, detail="邀请链接无效或已过期")
    team, invitation = match
    return {
        "team_name": team["name"],
        "email": _mask_email(invitation["email"]),
        "role": invitation["role"],
        "expires_at": invitation["expires_at"],
    }


@router.post("/invitations/accept")
async def accept_team_invitation(req: TeamInvitationAcceptRequest, request: Request):
    user = get_current_user(request)
    try:
        team = team_store.accept_invitation(req.token, user)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    user_store.set_active_team(user["user_token"], team["id"])
    team_event_broker.publish(
        team["id"],
        "member_joined",
        {"member_count": len(team.get("members", []))},
    )
    return _team_summary(team, user)


@router.delete("/members/{member_id}")
async def remove_team_member(member_id: str, request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    require_team_manager(team["id"], user)
    try:
        updated, removed_user_token = team_store.remove_member(team["id"], member_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    removed_user = user_store.get_or_create_user(removed_user_token)
    if removed_user.get("active_team_id") == team["id"]:
        user_store.set_active_team(removed_user_token, "")
    team_event_broker.publish(
        team["id"],
        "member_removed",
        {"member_count": len(updated.get("members", []))},
    )
    return {"status": "removed"}


@router.get("/chat/messages")
async def list_team_chat_messages(
    request: Request,
    after: str = Query(default="", max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
):
    user = get_current_user(request)
    team = ensure_active_team(user)
    require_team_member(team["id"], user)
    return [
        _public_chat_message(item)
        for item in team_store.list_chat_messages(team["id"], limit=limit, after=after)
    ]


@router.post("/chat/messages", status_code=status.HTTP_201_CREATED)
async def create_team_chat_message(req: TeamChatMessageRequest, request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    require_team_member(team["id"], user)
    try:
        message = team_store.add_chat_message(team["id"], user, req.content)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    public_message = _public_chat_message(message)
    team_event_broker.publish(team["id"], "chat_message", public_message)
    return public_message


@router.post("/chat/stream")
async def stream_team_chat(request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    require_team_member(team["id"], user)
    queue = team_event_broker.subscribe(team["id"])

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {event}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            team_event_broker.unsubscribe(team["id"], queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
