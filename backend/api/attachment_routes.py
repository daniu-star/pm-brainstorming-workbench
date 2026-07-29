import os
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from typing import Optional
from core.attachment_store import attachment_store
from db.session_store import session_store
from api.deps import get_current_user
from core.team_access import require_session_access

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_CONTENT_TYPES = [
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf", "text/plain", "text/markdown",
    "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/json", "application/zip",
]
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".json", ".zip",
}


def _owned_session(session_id: str, user: dict) -> dict:
    return require_session_access(session_id, user)


def _owned_attachment(attachment_id: str, user: dict) -> dict:
    attachment = attachment_store.get_attachment(attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="附件不存在")
    _owned_session(attachment.get("session_id", ""), user)
    return attachment


@router.post("/upload")
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
):
    user = get_current_user(request)
    _owned_session(session_id, user)
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"不支持的文件扩展名: {extension or '无'}")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {content_type}")

    result = attachment_store.save_attachment(
        session_id=session_id,
        filename=file.filename,
        content=content,
        content_type=content_type,
    )
    return result


@router.get("/{session_id}")
async def list_attachments(
    session_id: str,
    request: Request,
):
    user = get_current_user(request)
    _owned_session(session_id, user)
    return attachment_store.get_attachments_by_session(session_id)


@router.delete("/{attachment_id}")
async def delete_attachment(
    attachment_id: str,
    request: Request,
):
    user = get_current_user(request)
    _owned_attachment(attachment_id, user)
    success = attachment_store.delete_attachment(attachment_id)
    if not success:
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"success": True}


@router.get("/file/{attachment_id}")
async def get_file(
    attachment_id: str,
    request: Request,
):
    user = get_current_user(request)
    attachment = _owned_attachment(attachment_id, user)
    file_path = attachment_store.get_file_path(attachment_id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(file_path, filename=attachment["filename"], media_type=attachment.get("content_type"))
