"""真机验收：模型配置页在网络故障时的中文提示与自愈能力。

用 CDP 的 `Network.setBlockedURLs` 只阻断 `/api/v2/llm-config`（不动页面文档），
确认页面给出中文网络错误与「重新读取配置」按钮；解除阻断后点击按钮能加载表单。
默认打本地 `http://127.0.0.1:3001`，也可用 `--web` 指定其他地址（本地栈需在线）。

用法（仓库根目录）：

    .venv\\Scripts\\python.exe scripts\\check_retry_cdp.py
    .venv\\Scripts\\python.exe scripts\\check_retry_cdp.py --shot docs/browser-qa/e2e/llm-config-offline-retry.png
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts" / "browser_e2e"))

import cdp  # noqa: E402
from core.auth import create_session_token  # noqa: E402
from core.config import settings  # noqa: E402

DEFAULT_OWNER = "retry-owner@example.com"


def run(web: str, *, owner: str, shot: pathlib.Path | None) -> int:
    checks: list[str] = []
    browser = cdp.Browser()
    try:
        page = browser.new_page()
        page._send("Network.enable")
        page._send("Network.setBlockedURLs", {"urls": ["*api/v2/llm-config*"]})
        page.set_cookie(settings.auth_cookie_name, create_session_token(owner), url=web)
        page.goto(f"{web}/settings/llm", timeout=60, expect="document.querySelectorAll('button').length > 0")

        # apiV2 会重试 3 次，表单再静默重试 2 次（2s/4s），因此这里等足 90s。
        page.wait_js("document.body.innerText.includes('重新读取配置')", timeout=90)
        body = page.text()
        if "网络连接失败" in body:
            checks.append("offline-chinese-error")
        if "重新读取配置" in body:
            checks.append("offline-retry-button")
        if "Failed to fetch" not in body:
            checks.append("no-english-error")

        page._send("Network.setBlockedURLs", {"urls": []})
        clicked = page.eval(
            "(() => { const b = [...document.querySelectorAll('button')]"
            ".find(x => x.textContent.trim() === '重新读取配置'); if (!b) return false; b.click(); return true; })()"
        )
        if clicked:
            checks.append("retry-clicked")
        page.wait_js("document.body.innerText.includes('保存并测试')", timeout=60)
        checks.append("recovered-after-retry")

        errors = page.page_errors()
        if errors:
            print("PAGE_ERRORS:", errors)
        else:
            checks.append("no-page-errors")

        if shot is not None:
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(shot)
            print("screenshot:", shot)
    finally:
        browser.close()

    print("RETRY_CDP_CHECKS", checks)
    required = {
        "offline-chinese-error",
        "offline-retry-button",
        "no-english-error",
        "retry-clicked",
        "recovered-after-retry",
    }
    return 0 if required.issubset(set(checks)) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模型配置页断网重试验收")
    parser.add_argument("--web", default="http://127.0.0.1:3001")
    parser.add_argument("--owner", default=DEFAULT_OWNER, help="铸造登录 Cookie 用的邮箱")
    parser.add_argument("--shot", default="", help="截图保存路径（留空则不截图）")
    args = parser.parse_args(argv)
    shot = pathlib.Path(args.shot) if args.shot else None
    return run(args.web.rstrip("/"), owner=args.owner, shot=shot)


if __name__ == "__main__":
    raise SystemExit(main())
