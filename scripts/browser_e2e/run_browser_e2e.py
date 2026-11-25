"""真实浏览器验收（CDP + 本机 Chrome，不依赖 Playwright）。

覆盖：
- AC-01 新会话刷新三次（StrictMode 双挂载）：消息不重复、不会多出教练 Run；
- AC-19 八分支画布：分支不被折叠成「+N 更多」，第 8 个分支的节点原文/引用计数可见；
- AC-31 同浏览器换账号：A 登出、B 登录后没有 A 的会话/消息/本地残留；
- AC-33 摄像头被拒 / STT 未配置 / TTS 播放切模式：仍可完成文字审计（TTS 用本地
  静音 WAV 替换外部语音服务，只替换被依赖的外部能力，不伪造被测界面）。

前置：``scripts/launch.ps1`` 已拉起 API 8001 与 Web 3001（或 ``start.bat``）。

用法::

    cd backend
    ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py          # 全部
    ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py AC-19     # 单个

结果打印为 JSON，截图写入 ``docs/browser-qa/e2e/``；有失败时退出码非 0。
"""

from __future__ import annotations

import base64
import io
import json
import pathlib
import sys
import time
import traceback
import wave
from typing import Any, Callable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import cdp  # noqa: E402
import seed  # noqa: E402

seed.configure_live_database()

from core.auth import create_session_token  # noqa: E402
from core.config import settings  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402

WEB = "http://127.0.0.1:3001"
API = "http://127.0.0.1:8001"
SHOTS = REPO / "docs" / "browser-qa" / "e2e"

OWNER_A = "browser-e2e-a@example.com"
OWNER_B = "browser-e2e-b@example.com"
COOKIE = settings.auth_cookie_name


def cookie_for(email: str) -> str:
    return create_session_token(email)


def db_counts(session_id: str) -> dict[str, int]:
    with database.session_scope() as db:
        runs = len(run_repo.list_session_runs(db, session_id, limit=50))
        messages = session_repo.count_messages(db, session_id)
        session = session_repo.get_session(db, session_id)
        return {
            "runs": runs,
            "messages": messages,
            "session_version": int(session.session_version) if session else -1,
            "product_version": int(session.current_product_version) if session else -1,
        }


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --------------------------------------------------------------------------- #
# 场景
# --------------------------------------------------------------------------- #


def scenario_ac01(browser: cdp.Browser, results: dict[str, Any]) -> None:
    """新会话刷新三次：消息不重复、不产生额外教练 Run（StrictMode 双挂载）。"""
    seeded = seed.seed_chat_session(
        OWNER_A,
        problem_statement="AC01 刷新三次：为中小企业做财务对账助手",
        assistant_reply="先确认一下：使用者是财务专员还是老板？他们现在用什么工具对账？",
    )
    sid = seeded["session_id"]
    results["seeded_ac01"] = seeded

    page = browser.new_page("about:blank")
    try:
        page.set_cookie(COOKIE, cookie_for(OWNER_A), url=WEB)
        page.goto(f"{WEB}/session/{sid}", expect="document.body.innerText.includes('AC01')")
        page.wait_js(
            "document.body.innerText.includes('财务专员') && document.body.innerText.includes('AC01')",
            timeout=60,
        )
        before = db_counts(sid)
        for _ in range(3):
            page.reload()
            page.wait_js("document.body.innerText.includes('财务专员')", timeout=60)
        after = db_counts(sid)
        body = page.text()
        page.screenshot(SHOTS / "ac-01-refresh-x3.png")

        check(
            body.count("AC01 刷新三次") == 1,
            f"刷新三次后原始用户消息应恰好一条，实际 innerText 出现 {body.count('AC01 刷新三次')} 次",
        )
        check(after["messages"] == before["messages"] == 2, f"消息数不应变化：{before} -> {after}")
        check(after["runs"] == before["runs"] == 1, f"不应产生额外教练 Run：{before} -> {after}")
        check(
            after["session_version"] == before["session_version"],
            f"只读刷新不应推进会话版本：{before} -> {after}",
        )
        check(not page.page_errors(), f"页面出现未捕获异常：{page.page_errors()}")
        results["ac01"] = {"before": before, "after": after, "ok": True}
    finally:
        browser.close_page(page)


def scenario_ac19(browser: cdp.Browser, results: dict[str, Any]) -> None:
    """八分支画布：全部分支可见、无「+N 更多」折叠、节点带引用计数与版本。"""
    seeded = seed.seed_branchy_canvas_session(
        OWNER_A, problem_statement="AC19 八分支画布：对账助手需要几个方向？"
    )
    sid = seeded["session_id"]
    results["seeded_ac19"] = {
        "session_id": sid,
        "branches": seeded["branches"],
        "product_version": seeded["product_version"],
    }

    page = browser.new_page("about:blank")
    try:
        page.set_cookie(COOKIE, cookie_for(OWNER_A), url=WEB)
        page.goto(f"{WEB}/session/{sid}", expect="document.body.innerText.includes('AC19')")
        page.wait_js("document.body.innerText.includes('实时画布')", timeout=60)
        page.wait_js(
            "document.body.innerText.includes('%s')" % seeded["last_branch_title"], timeout=60
        )
        body = page.text()
        page.screenshot(SHOTS / "ac-19-eight-branches.png")

        check(len(seeded["branches"]) >= 8, f"种子状态应产出至少 8 个分支，实际 {len(seeded['branches'])}")
        for _key, title, count in seeded["branches"]:
            check(count > 0, f"分支 {title} 没有节点")
            check(title in body, f"画布未渲染分支「{title}」（8 个分支必须全部可见）")
        check("条更多" not in body, "v2 画布不应出现「+N 条更多」折叠提示")
        first_line = seeded["last_node_detail"].splitlines()[0]
        check(first_line in body, f"第 8 个分支的节点原文未展示：{first_line!r}")
        check(f"v{seeded['product_version']}" in body, "画布版本标签缺失")
        check("证据" in body or "消息" in body, "节点未展示引用计数")
        check(not page.page_errors(), f"页面出现未捕获异常：{page.page_errors()}")
        results["ac19"] = {"branches": len(seeded["branches"]), "ok": True}
    finally:
        browser.close_page(page)


def scenario_ac31(browser: cdp.Browser, results: dict[str, Any]) -> None:
    """同浏览器换账号：A 登出后 B 登录，看不到 A 的历史/消息/本地残留。"""
    a = seed.seed_chat_session(
        OWNER_A,
        problem_statement="AC31 A账户专属会话：只应出现在 A 的历史里",
        assistant_reply="A 的教练回复：请补充目标用户。",
    )
    b = seed.seed_chat_session(
        OWNER_B,
        problem_statement="AC31 B账户专属会话：B 自己的历史",
        assistant_reply="B 的教练回复：请描述使用场景。",
    )
    results["seeded_ac31"] = {"a": a["session_id"], "b": b["session_id"]}

    page = browser.new_page("about:blank")
    try:
        page.set_cookie(COOKIE, cookie_for(OWNER_A), url=WEB)
        page.goto(f"{WEB}/", expect="document.body.innerText.length > 0")
        page.click('[aria-label="历史会话"]')
        page.wait_js("document.body.innerText.includes('A账户专属会话')", timeout=45)
        check("B账户专属会话" not in page.text(), "A 登录时不应看到 B 的会话")

        page.goto(
            f"{WEB}/session/{a['session_id']}",
            expect="document.body.innerText.includes('A账户专属会话')",
        )
        page.wait_js("document.body.innerText.includes('A 的教练回复')", timeout=60)
        page.screenshot(SHOTS / "ac-31-account-a.png")

        # A 登出（真实 UI 按钮）
        page.click('[aria-label="退出登录"]')
        page.wait_js("location.pathname.startsWith('/login')", timeout=45)

        # B 在同一浏览器登录
        page.set_cookie(COOKIE, cookie_for(OWNER_B), url=WEB)
        page.goto(f"{WEB}/", expect="document.body.innerText.length > 0")
        page.click('[aria-label="历史会话"]')
        page.wait_js("document.body.innerText.includes('B账户专属会话')", timeout=45)
        body = page.text()
        check("A账户专属会话" not in body, "换账号后仍能看到 A 的会话（历史残留）")
        check("A 的教练回复" not in body, "换账号后仍能看到 A 的消息")

        storage = page.storage_keys()
        leftovers = json.dumps(storage, ensure_ascii=False)
        check(a["session_id"] not in leftovers, f"本地存储残留 A 会话 ID：{leftovers}")

        # B 直接打开 A 的会话链接 → 必须被拒（不泄露内容）
        page.goto(
            f"{WEB}/session/{a['session_id']}", expect="document.body.innerText.length > 0"
        )
        page.wait_js(
            "document.body.innerText.includes('加载失败') || location.pathname === '/'", timeout=45
        )
        check("A 的教练回复" not in page.text(), "越权打开 A 的会话仍渲染了内容")
        page.screenshot(SHOTS / "ac-31-account-b.png")
        check(not page.page_errors(), f"页面出现未捕获异常：{page.page_errors()}")
        results["ac31"] = {"ok": True, "storage_keys": storage}
    finally:
        browser.close_page(page)




def _silent_wav_base64(seconds: float = 1.5) -> str:
    """1.5s 静音 WAV：只替换外部 TTS 服务，验证「播放中禁用麦克风 / 切模式即停」。"""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(bytes(2 * int(8000 * seconds)))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


TTS_STUB_TEMPLATE = """
(() => {
  const originalFetch = window.fetch;
  const payload = atob("__WAV_BASE64__");
  window.__e2eTtsCalls = 0;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/api/voice/tts') >= 0) {
      window.__e2eTtsCalls += 1;
      const bytes = new Uint8Array(payload.length);
      for (let i = 0; i < payload.length; i += 1) bytes[i] = payload.charCodeAt(i);
      return Promise.resolve(
        new Response(bytes, {status: 200, headers: {'Content-Type': 'audio/wav'}})
      );
    }
    return originalFetch.apply(this, arguments);
  };
})();
"""

ANSWER_TEXTAREA = 'textarea[aria-label="回答审计官问题"]'
SEND_BUTTON = 'button[aria-label="发送回答"]'
MIC_BUTTON = 'button[aria-label="开始语音识别"]'
ALERT_BOX = '[role="alert"]'


def _click_text(page: cdp.Page, needle: str) -> None:
    clicked = page.eval(
        "((needle) => {"
        "  const nodes = Array.from(document.querySelectorAll('button, a'));"
        "  const hit = nodes.find((el) => (el.innerText || '').includes(needle));"
        "  if (!hit) return false;"
        "  hit.scrollIntoView({block: 'center'});"
        "  hit.click();"
        "  return true;"
        f"}})({json.dumps(needle, ensure_ascii=False)})"
    )
    if not clicked:
        raise AssertionError(f"页面上找不到可点击的按钮/链接：{needle}")


def _wait_selector(page: cdp.Page, selector: str, *, timeout: float = 20.0) -> None:
    page.wait_js(f"Boolean(document.querySelector({json.dumps(selector, ensure_ascii=False)}))", timeout=timeout)


def _query_text(page: cdp.Page, selector: str) -> str:
    return str(
        page.eval(
            "((sel) => { const el = document.querySelector(sel);"
            " return el ? el.innerText : ''; })"
            f"({json.dumps(selector, ensure_ascii=False)})"
        )
        or ""
    )


def _db_audit_state(session_id: str) -> dict[str, Any]:
    with database.session_scope() as db:
        row = db.execute(
            database.text(
                "select id, status, active_question_id, question_count from audits"
                " where session_id = :sid order by created_at desc limit 1"
            ),
            {"sid": session_id},
        ).first()
    if row is None:
        return {}
    return {"id": row[0], "status": row[1], "active": row[2], "count": row[3]}


def _type_answer(page: cdp.Page, text: str) -> None:
    _wait_selector(page, ANSWER_TEXTAREA, timeout=20)
    page.eval(
        "((sel, value) => {"
        "  const el = document.querySelector(sel);"
        "  const setter = Object.getOwnPropertyDescriptor("
        "    window.HTMLTextAreaElement.prototype, 'value').set;"
        "  setter.call(el, value);"
        "  el.dispatchEvent(new Event('input', {bubbles: true}));"
        "  return el.value;"
        f"}})({json.dumps(ANSWER_TEXTAREA, ensure_ascii=False)}, {json.dumps(text, ensure_ascii=False)})"
    )


def scenario_ac33(browser: cdp.Browser, results: dict[str, Any]) -> None:
    """AC-33：摄像头被拒 + STT 未配置 + TTS 切模式，仍能完成一次文字审计。"""

    info = seed.seed_branchy_canvas_session(
        OWNER_A, problem_statement="AC33 摄像头拒绝后仍可完成文字审计"
    )
    sid = info["session_id"]
    page = browser.new_page("about:blank")
    try:
        page.add_init_script(TTS_STUB_TEMPLATE.replace("__WAV_BASE64__", _silent_wav_base64()))
        page.set_cookie(COOKIE, cookie_for(OWNER_A), url=WEB)
        page.goto(f"{WEB}/session/{sid}/interview", timeout=60)

        # 1) 摄像头被拒（headless 默认拒绝 getUserMedia）：真实原因 + 可降级为文字面试
        page.wait_js("document.body.innerText.includes('面试监考告知与同意')", timeout=60)
        _click_text(page, "同意并开启摄像头面试")
        _wait_selector(page, ALERT_BOX, timeout=90)
        camera_error = _query_text(page, ALERT_BOX)
        check("摄像头" in camera_error, f"摄像头错误文案不真实：{camera_error!r}")
        page.screenshot(SHOTS / "ac-33-camera-denied.png")
        _click_text(page, "继续仅文字面试")
        _wait_selector(page, ANSWER_TEXTAREA, timeout=30)
        check(
            page.eval(
                f"document.querySelector({json.dumps(ANSWER_TEXTAREA, ensure_ascii=False)}).disabled"
            )
            is False,
            "摄像头被拒后文字输入被禁用",
        )

        # 2) 等 Worker 真实生成第 1 个审计问题（interview 页调用 start_audit）
        deadline = time.time() + 210
        state: dict[str, Any] = {}
        while time.time() < deadline:
            state = _db_audit_state(sid)
            if state.get("active"):
                break
            time.sleep(1.5)
        check(bool(state.get("active")), f"开场问题未生成：{state}")

        # 3) TTS 播放中：不虚报服务端引擎、麦克风禁用；切到文字立即停止播放
        _click_text(page, "语音")
        page.wait_js("document.body.innerText.includes('正在发言…')", timeout=60)
        voice_text = page.text()
        check("AI 高精度转写" not in voice_text, "服务端 STT 未配置却标注「AI 高精度转写」")
        check(
            page.eval(
                "(() => { const b = document.querySelector("
                + json.dumps(MIC_BUTTON, ensure_ascii=False)
                + "); return Boolean(b) && b.disabled; })()"
            )
            is True,
            "TTS 播放期间麦克风未被禁用",
        )
        page.screenshot(SHOTS / "ac-33-voice-playing.png")
        _click_text(page, "文字")
        page.wait_js("!document.body.innerText.includes('正在发言…')", timeout=30)
        check("正在播报" not in page.text(), "切到文字模式后仍在播报（播放未停止）")

        # 4) 麦克风被拒时给出真实错误（STT 不可用不静默）
        _click_text(page, "语音")
        _wait_selector(page, MIC_BUTTON, timeout=20)
        page.wait_js("!document.body.innerText.includes('正在发言…')", timeout=30)
        page.click(MIC_BUTTON)
        _wait_selector(page, ALERT_BOX, timeout=60)
        mic_error = _query_text(page, ALERT_BOX)
        check("麦克风" in mic_error, f"麦克风失败文案不真实：{mic_error!r}")

        # 5) 回到文字模式，真实提交一次回答并等审计推进
        _click_text(page, "文字")
        _type_answer(
            page,
            "我们访谈了 12 家中小企业财务团队：对账平均每周耗时 6 小时，"
            "财务负责人愿意为自动对账按月付费。",
        )
        page.click(SEND_BUTTON)
        deadline = time.time() + 240
        after: dict[str, Any] = {}
        while time.time() < deadline:
            after = _db_audit_state(sid)
            if after.get("active") and after.get("active") != state.get("active"):
                break
            if int(after.get("count") or 0) > int(state.get("count") or 0):
                break
            time.sleep(2)
        check(
            bool(after.get("active")) and after.get("active") != state.get("active"),
            f"文字回答后审计未推进：{after}",
        )
        page.screenshot(SHOTS / "ac-33-text-answer.png")
        check(not page.page_errors(), f"页面出现未捕获异常：{page.page_errors()}")
        results["ac33"] = {
            "ok": True,
            "session_id": sid,
            "camera_error": camera_error,
            "mic_error": mic_error,
            "audit_after": after,
        }
    finally:
        browser.close_page(page)


SCENARIOS: dict[str, Callable[[cdp.Browser, dict[str, Any]], None]] = {
    "AC-01": scenario_ac01,
    "AC-19": scenario_ac19,
    "AC-31": scenario_ac31,
    "AC-33": scenario_ac33,
}


def main() -> int:
    wanted = sys.argv[1:] or ["AC-01", "AC-19", "AC-31", "AC-33"]
    unknown = [name for name in wanted if name not in SCENARIOS]
    if unknown:
        print(f"未知场景：{unknown}；可用：{list(SCENARIOS)}", flush=True)
        return 2

    # BYOK 闸门：先给两个 e2e 账户写假配置，否则登录后页面会被强制配置面板挡住。
    for owner in (OWNER_A, OWNER_B):
        seed.seed_llm_config(owner)

    results: dict[str, Any] = {}
    failures: list[str] = []
    started = time.time()
    with cdp.Browser(window_size="1440,1100") as browser:
        for name in wanted:
            try:
                SCENARIOS[name](browser, results)
                print(f"[PASS] {name}", flush=True)
            except Exception as exc:  # noqa: BLE001 - 逐场景收口，继续跑其余场景
                failures.append(name)
                results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                print(f"[FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
                traceback.print_exc()

    seeded_ids: list[str] = []
    for value in results.values():
        if isinstance(value, dict):
            for key in ("session_id", "a", "b"):
                found = value.get(key)
                if isinstance(found, str) and len(found) == 32:
                    seeded_ids.append(found)
    try:
        results["cleanup"] = seed.cleanup_sessions(sorted(set(seeded_ids)))
    except Exception as exc:  # noqa: BLE001 - 清理失败不掩盖验收结论
        results["cleanup"] = f"{type(exc).__name__}: {exc}"

    try:
        results["cleanup_llm_configs"] = seed.cleanup_llm_configs()
    except Exception as exc:  # noqa: BLE001 - 清理失败不掩盖验收结论
        results["cleanup_llm_configs"] = f"{type(exc).__name__}: {exc}"

    results["failures"] = failures
    results["elapsed_seconds"] = round(time.time() - started, 2)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())