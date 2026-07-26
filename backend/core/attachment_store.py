import os
import json
import uuid
import time
import threading
from typing import Optional

ATTACHMENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "attachments")
ATTACHMENTS_META_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "attachments.json")


class AttachmentStore:
    def __init__(self):
        self._lock = threading.RLock()
        os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
        self._ensure_meta_file()

    def _ensure_meta_file(self):
        if not os.path.exists(ATTACHMENTS_META_FILE):
            with open(ATTACHMENTS_META_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def _load_meta(self) -> dict:
        with open(ATTACHMENTS_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_meta(self, meta: dict):
        temp_path = f"{ATTACHMENTS_META_FILE}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, ATTACHMENTS_META_FILE)

    def save_attachment(self, session_id: str, filename: str, content: bytes, content_type: str) -> dict:
        """保存附件文件，返回附件元数据"""
        attachment_id = str(uuid.uuid4())
        ext = os.path.splitext(filename)[1]
        stored_filename = f"{attachment_id}{ext}"
        file_path = os.path.join(ATTACHMENTS_DIR, stored_filename)

        with open(file_path, "wb") as f:
            f.write(content)

        with self._lock:
            meta = self._load_meta()
            attachment_meta = {
                "id": attachment_id,
                "session_id": session_id,
                "filename": filename,
                "stored_filename": stored_filename,
                "size": len(content),
                "content_type": content_type,
                "uploaded_at": time.time(),
                "url": f"/api/attachments/file/{attachment_id}",
            }
            meta[attachment_id] = attachment_meta
            self._save_meta(meta)
        return attachment_meta

    def get_attachments_by_session(self, session_id: str) -> list:
        """获取会话的所有附件"""
        meta = self._load_meta()
        return [v for v in meta.values() if v.get("session_id") == session_id]

    def get_attachment(self, attachment_id: str) -> Optional[dict]:
        """获取单个附件元数据"""
        meta = self._load_meta()
        return meta.get(attachment_id)

    def delete_attachment(self, attachment_id: str) -> bool:
        """删除附件"""
        with self._lock:
            meta = self._load_meta()
            if attachment_id not in meta:
                return False
            attachment = meta[attachment_id]
            file_path = os.path.join(ATTACHMENTS_DIR, attachment["stored_filename"])
            if os.path.exists(file_path):
                os.remove(file_path)
            del meta[attachment_id]
            self._save_meta(meta)
            return True

    def get_file_path(self, attachment_id: str) -> Optional[str]:
        """获取附件文件路径"""
        attachment = self.get_attachment(attachment_id)
        if not attachment:
            return None
        return os.path.join(ATTACHMENTS_DIR, attachment["stored_filename"])


attachment_store = AttachmentStore()
