import json
import os
import threading
import uuid
from datetime import datetime
from typing import Optional

USER_DATA_DIR = os.getenv("USER_DATA_DIR", "./data/users")
INITIAL_QUOTA = int(os.getenv("INITIAL_QUOTA", "100000"))
PHONE_INDEX_FILE = os.path.join(USER_DATA_DIR, "_phone_index.json")
WECHAT_INDEX_FILE = os.path.join(USER_DATA_DIR, "_wechat_index.json")
QQ_INDEX_FILE = os.path.join(USER_DATA_DIR, "_qq_index.json")


def _validate_id(value: str) -> str:
    if not value or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid ID: {value}")
    return value


class UserStore:
    def __init__(self):
        self.data_dir = USER_DATA_DIR
        self._lock = threading.RLock()
        os.makedirs(self.data_dir, exist_ok=True)
        self._phone_index = self._load_index(PHONE_INDEX_FILE)
        self._wechat_index = self._load_index(WECHAT_INDEX_FILE)
        self._qq_index = self._load_index(QQ_INDEX_FILE)

    def _load_index(self, path: str) -> dict:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_index(self, path: str, index: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def get_user_by_phone(self, phone: str) -> Optional[dict]:
        user_token = self._phone_index.get(phone)
        if user_token is None:
            return None
        return self._load(user_token)

    def get_user_by_wechat(self, openid: str) -> Optional[dict]:
        user_token = self._wechat_index.get(openid)
        if user_token is None:
            return None
        return self._load(user_token)

    def get_user_by_qq(self, openid: str) -> Optional[dict]:
        user_token = self._qq_index.get(openid)
        if user_token is None:
            return None
        return self._load(user_token)

    def create_user_with_phone(self, phone: str, nickname: str) -> dict:
        user_token = f"phone_{phone}"
        user = {
            "user_token": user_token,
            "phone": phone,
            "wechat_openid": "",
            "qq_openid": "",
            "nickname": nickname,
            "avatar": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "token_quota": INITIAL_QUOTA,
            "tokens_used": 0,
            "created_at": datetime.now().isoformat(),
        }
        self._save(user)
        self._phone_index[phone] = user_token
        self._save_index(PHONE_INDEX_FILE, self._phone_index)
        return user

    def create_user_with_wechat(self, openid: str, nickname: str) -> dict:
        user_token = f"wechat_{openid}"
        user = {
            "user_token": user_token,
            "phone": "",
            "wechat_openid": openid,
            "qq_openid": "",
            "nickname": nickname,
            "avatar": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "token_quota": INITIAL_QUOTA,
            "tokens_used": 0,
            "created_at": datetime.now().isoformat(),
        }
        self._save(user)
        self._wechat_index[openid] = user_token
        self._save_index(WECHAT_INDEX_FILE, self._wechat_index)
        return user

    def create_user_with_qq(self, openid: str, nickname: str) -> dict:
        user_token = f"qq_{openid}"
        user = {
            "user_token": user_token,
            "phone": "",
            "wechat_openid": "",
            "qq_openid": openid,
            "nickname": nickname,
            "avatar": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "token_quota": INITIAL_QUOTA,
            "tokens_used": 0,
            "created_at": datetime.now().isoformat(),
        }
        self._save(user)
        self._qq_index[openid] = user_token
        self._save_index(QQ_INDEX_FILE, self._qq_index)
        return user

    def get_or_create_user(
        self,
        user_token: str,
        nickname: str = "",
        token_quota: int | None = None,
    ) -> dict:
        user = self._load(user_token)
        if user is not None:
            return user
        user = {
            "user_token": user_token,
            "nickname": nickname,
            "api_key": "",
            "base_url": "",
            "model": "",
            "token_quota": token_quota if token_quota is not None else INITIAL_QUOTA,
            "tokens_used": 0,
            "created_at": datetime.now().isoformat(),
        }
        self._save(user)
        return user

    def get_quota(self, user_token: str) -> dict:
        user = self._load(user_token)
        if user is None:
            return {"quota": INITIAL_QUOTA, "used": 0, "remaining": INITIAL_QUOTA}
        remaining = user["token_quota"] - user["tokens_used"]
        return {"quota": user["token_quota"], "used": user["tokens_used"], "remaining": remaining}

    def has_quota(self, user_token: str) -> bool:
        user = self._load(user_token)
        if user is None:
            return True
        return (user["token_quota"] - user["tokens_used"]) > 0

    def deduct_tokens(self, user_token: str, amount: int) -> bool:
        user = self._load(user_token)
        if user is None:
            return False
        remaining = user["token_quota"] - user["tokens_used"]
        if remaining <= 0:
            return False
        user["tokens_used"] += amount
        self._save(user)
        return True

    def add_quota(self, user_token: str, amount: int):
        user = self._load(user_token)
        if user is None:
            return
        user["token_quota"] += amount
        self._save(user)

    def set_active_team(self, user_token: str, team_id: str) -> dict:
        with self._lock:
            user = self._load(user_token)
            if user is None:
                user = self.get_or_create_user(user_token)
            user["active_team_id"] = team_id
            self._save(user)
            return user

    def save_api_key(self, user_token: str, api_key: str, base_url: str, model: str):
        user = self._load(user_token)
        if user is None:
            user = self.get_or_create_user(user_token)
        user["api_key"] = api_key
        user["base_url"] = base_url
        user["model"] = model
        self._save(user)

    def get_api_key_config(self, user_token: str) -> Optional[dict]:
        user = self._load(user_token)
        if user is None:
            return None
        if not user.get("api_key"):
            return None
        return {
            "api_key": user["api_key"],
            "base_url": user.get("base_url", ""),
            "model": user.get("model", ""),
        }

    def migrate_remove_plaintext_api_keys(self) -> int:
        """Remove legacy BYOK secrets that older versions persisted as plaintext."""
        removed = 0
        for filename in os.listdir(self.data_dir):
            if not filename.endswith(".json") or filename.startswith("_"):
                continue
            path = os.path.join(self.data_dir, filename)
            with open(path, "r", encoding="utf-8") as file:
                user = json.load(file)
            if user.get("api_key"):
                user["api_key"] = ""
                user["base_url"] = ""
                user["model"] = ""
                self._save(user)
                removed += 1
        return removed

    def create_recharge_request(self, user_token: str, tier_name: str, amount: int, price: float) -> dict:
        request_id = str(uuid.uuid4())[:8]
        verify_code = str(uuid.uuid4())[:6].upper()
        request = {
            "id": request_id,
            "user_token": user_token,
            "tier_name": tier_name,
            "tokens": amount,
            "price": price,
            "verify_code": verify_code,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "approved_at": None,
        }
        recharge_dir = os.path.join(self.data_dir, "recharges")
        os.makedirs(recharge_dir, exist_ok=True)
        path = os.path.join(recharge_dir, f"{request_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(request, f, ensure_ascii=False, indent=2)
        return request

    def get_recharge(self, request_id: str) -> Optional[dict]:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        path = os.path.join(recharge_dir, f"{request_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def confirm_recharge(self, request_id: str) -> Optional[dict]:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        path = os.path.join(recharge_dir, f"{request_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data["status"] != "pending":
            return None
        data["status"] = "pending_review"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    def get_pending_recharges(self) -> list:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        if not os.path.exists(recharge_dir):
            return []
        results = []
        for fname in os.listdir(recharge_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(recharge_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("status") in ("pending", "pending_review"):
                results.append(data)
        return sorted(results, key=lambda x: x.get("created_at", ""))

    def get_user_recharges(self, user_token: str) -> list:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        if not os.path.exists(recharge_dir):
            return []
        results = []
        for fname in os.listdir(recharge_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(recharge_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_token") == user_token:
                results.append(data)
        return sorted(results, key=lambda x: x.get("created_at", ""), reverse=True)

    def approve_recharge(self, request_id: str) -> Optional[dict]:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        path = os.path.join(recharge_dir, f"{request_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data["status"] not in ("pending", "pending_review"):
            return None
        data["status"] = "approved"
        data["approved_at"] = datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.add_quota(data["user_token"], data["tokens"])
        return data

    def reject_recharge(self, request_id: str) -> Optional[dict]:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        path = os.path.join(recharge_dir, f"{request_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data["status"] not in ("pending", "pending_review"):
            return None
        data["status"] = "rejected"
        data["approved_at"] = datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    def cancel_recharge(self, request_id: str, user_token: str) -> Optional[dict]:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        path = os.path.join(recharge_dir, f"{request_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data["status"] not in ("pending", "pending_review"):
            return None
        if data.get("user_token") != user_token:
            return None
        data["status"] = "cancelled"
        data["approved_at"] = datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data

    def count_recent_recharges(self, user_token: str, minutes: int = 10) -> int:
        recharge_dir = os.path.join(self.data_dir, "recharges")
        if not os.path.exists(recharge_dir):
            return 0
        cutoff = datetime.now().timestamp() - minutes * 60
        count = 0
        for fname in os.listdir(recharge_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(recharge_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user_token") != user_token:
                continue
            try:
                created = datetime.fromisoformat(data["created_at"]).timestamp()
                if created >= cutoff:
                    count += 1
            except (ValueError, KeyError):
                count += 1
        return count

    def _load(self, user_token: str) -> Optional[dict]:
        _validate_id(user_token)
        path = os.path.join(self.data_dir, f"{user_token}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, user: dict):
        _validate_id(user['user_token'])
        path = os.path.join(self.data_dir, f"{user['user_token']}.json")
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        with self._lock:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(user, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)


user_store = UserStore()
