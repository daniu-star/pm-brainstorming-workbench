"""后端安全验证：C005-C007, C010-C017（checklist 第二节 + SMTP 限流部分）。"""
from __future__ import annotations

import threading

from common import BACKEND, ROOT, check, read, setup_backend_path

setup_backend_path()


def check_smtp_error_classes() -> None:
    src = read(BACKEND / "api" / "auth_routes.py")
    check("C005", all(k in src for k in (
        "SMTPAuthenticationError", "SMTPRecipientsRefused", "SSLError", "502")),
        "SMTP 异常分类（认证/收件人/网络/SSL/协议）")


def check_rate_limit() -> None:
    from core import auth
    ok_ip = hasattr(auth, "check_send_rate_limit")
    if ok_ip:
        # 模拟同一 IP 连续 11 次发送，第 11 次应被拒
        allowed = True
        try:
            for _ in range(11):
                auth.check_send_rate_limit("10.0.0.1")
        except Exception:
            allowed = False
        ok_ip = allowed is False
    check("C007", ok_ip, "IP 维度限流（10 次/小时）")

    ok_email = False
    src = read(BACKEND / "core" / "auth.py")
    if "next_send_at" in src:
        from core.config import settings
        auth._verification_codes.clear()
        code_holder = {}
        import time as _t
        with_test = {
            "email": "rl@test.com",
        }
        sent = []
        orig_send = auth._send_verification_email
        auth._send_verification_email = lambda e, c: sent.append((e, c))
        old_resend = settings.auth_code_resend_seconds
        old_secret = settings.auth_secret_key
        settings.auth_secret_key = "x" * 40
        settings.auth_code_resend_seconds = 60
        try:
            auth.issue_verification_code(with_test["email"])
            try:
                auth.issue_verification_code(with_test["email"])
                ok_email = False
            except auth.AuthRateLimitError:
                ok_email = True
        finally:
            auth._send_verification_email = orig_send
            settings.auth_code_resend_seconds = old_resend
            settings.auth_secret_key = old_secret
            auth._verification_codes.clear()
    check("C006", ok_email, "邮箱维度限流（重发冷却）")


def check_code_gc() -> None:
    from core import auth
    ok = hasattr(auth, "_gc_verification_codes")
    if ok:
        import time
        auth._verification_codes.clear()
        auth._verification_codes["gc@test.com"] = auth.VerificationCode(
            code_hash="h", expires_at=time.time() - 9999, next_send_at=0)
        auth._gc_verification_codes()
        ok = "gc@test.com" not in auth._verification_codes
        auth._verification_codes.clear()
    check("C008", ok, "验证码记录过期清理")


def check_toctou() -> None:
    """并发 8 线程同时请求同一邮箱验证码，实际发送次数应 <= 1（预占锁）。"""
    from core.config import settings
    from core import auth
    old = settings.auth_secret_key, settings.auth_code_resend_seconds
    settings.auth_secret_key = "x" * 40
    settings.auth_code_resend_seconds = 300
    auth._verification_codes.clear()
    calls = []
    lock = threading.Lock()
    import time as _t

    def slow_send(email: str, code: str):
        with lock:
            calls.append(code)
        _t.sleep(0.15)
    orig = auth._send_verification_email
    auth._send_verification_email = slow_send
    errors: list[Exception] = []
    try:
        def worker():
            try:
                auth.issue_verification_code("toctou@test.com")
            except auth.AuthRateLimitError as e:
                errors.append(e)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    finally:
        auth._send_verification_email = orig
        settings.auth_secret_key, settings.auth_code_resend_seconds = old
        auth._verification_codes.clear()
    check("C009", len(calls) == 1, f"TOCTOU 并发发送次数={len(calls)}（应为1）")


def check_session_id_guard() -> None:
    from db.session_store import session_store
    ok = True
    for bad in ("../../etc/passwd", "..\\..\\app\\.env", "abc", "0" * 12, "Z" * 32):
        try:
            session_store.get(bad)
            ok = False
        except ValueError:
            pass
    check("C010", ok, "session_id 路径穿越拒绝（32位hex 白名单）")


def check_api_security() -> None:
    from fastapi.testclient import TestClient
    from core.auth import create_session_token
    from db.session_store import session_store
    import main  # noqa: F401

    client = TestClient(main.app)
    alice = create_session_token("alice-audit@test.com")
    bob = create_session_token("bob-audit@test.com")
    h_a = {"Cookie": f"pm_brainstorm_session={alice}"}
    h_b = {"Cookie": f"pm_brainstorm_session={bob}"}

    # C011 跨用户访问 403
    s = session_store.create("审计用会话-安全", owner_email="alice-audit@test.com")
    r = client.get(f"/api/session/{s['id']}", headers=h_b)
    check("C011", r.status_code == 403, f"跨用户读取返回 {r.status_code}（应403）")
    r = client.get(f"/api/session/{s['id']}", headers=h_a)
    check("C011b", r.status_code == 200, f"本人读取返回 {r.status_code}（应200）")

    # C012 列表仅本人
    r = client.get("/api/session", headers=h_b)
    ok = r.status_code == 200 and all(
        item["id"] != s["id"] for item in r.json())
    check("C012", ok, "会话列表不包含他人会话")

    # C013 OPTIONS 预检不被 401 拦截
    r = client.options("/api/session/list",
                       headers={"Origin": "http://localhost:3000",
                                "Access-Control-Request-Method": "GET",
                                "Access-Control-Request-Headers": "content-type"})
    check("C013", r.status_code != 401 and (
        "access-control-allow-origin" in {k.lower() for k in r.headers}),
        f"OPTIONS 预检 status={r.status_code}")

    # C014 CORS 方法白名单
    src = read(BACKEND / "main.py")
    check("C014", 'allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]' in src,
          "CORS 方法白名单")

    # C015 /health 不泄露详情
    r = client.get("/health")
    ok = r.status_code == 200 and "services" not in r.json()
    r2 = client.get("/health/detail")
    check("C015", ok and r2.status_code == 401, "公开 health 无详情，详情需登录")

    # C016 Canvas 结构校验
    src = read(BACKEND / "api" / "canvas_routes.py")
    check("C016", "CanvasTree" in src and "UpdateCanvasRequest" in src,
          "Canvas Pydantic 结构校验")

    # C017 跨阶段调用面试接口 409
    r = client.post("/api/interview/respond",
                    json={"session_id": s["id"], "answer": "测试回答"}, headers=h_a)
    check("C017", r.status_code == 409, f"脑暴阶段 respond 返回 {r.status_code}（应409）")

    session_store.delete(s["id"])


def run() -> list[tuple[str, bool, str]]:
    print("\n== 后端安全验证 ==")
    check_smtp_error_classes()
    check_rate_limit()
    check_code_gc()
    check_toctou()
    check_session_id_guard()
    try:
        check_api_security()
    except Exception as exc:  # noqa: BLE001
        check("C011-C017", False, f"API 安全验证异常: {exc!r}")
    from common import _results
    return _results
