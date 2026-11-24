"""极简 Chrome DevTools Protocol 驱动（无第三方浏览器依赖）。

为什么不用 Playwright：本机 npm registry 不可达，装不了 playwright；但本机有 Chrome，
所以直接用 ``--headless=new --remote-debugging-port`` + CDP 提供真实浏览器交互，
覆盖 PRD 中「真实浏览器」类验收项（AC-01 / AC-19 / AC-31）。

依赖只有 ``websockets``（项目里已有）。用法见 ``run_browser_e2e.py``。
"""

from __future__ import annotations

import base64
import atexit
import json
import os
import pathlib
import shutil
import signal
import socket
import shutil
import subprocess
import tempfile
import time
import urllib.request
from typing import Any

from websockets.sync.client import connect

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)

#: PATH 里可执行名（Linux/macOS CI：GitHub Actions 的 ubuntu 镜像自带 google-chrome）。
CHROME_BINARIES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "Chrome",
)


def _user_data_base() -> pathlib.Path:
    """Chrome 用户数据目录的父目录。

    本机（以及部分 CI 镜像）``tempfile.gettempdir()`` 不可用时会回落到当前工作目录，
    直接在那里建 ``browser-e2e-*`` 会把临时目录漏进仓库根。这里统一改放到
    ``backend/.tmp_tests``（已 gitignore），并用 ``PM_BROWSER_E2E_TMP`` 可覆盖。
    """

    override = os.environ.get("PM_BROWSER_E2E_TMP")
    if override:
        base = pathlib.Path(override)
    else:
        tempdir = pathlib.Path(tempfile.gettempdir())
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        if tempdir.resolve() == repo_root.resolve():
            base = repo_root / "backend" / ".tmp_tests"
        else:
            base = tempdir
    base.mkdir(parents=True, exist_ok=True)
    return base


def find_chrome() -> str:
    """定位 Chrome 可执行文件：``CHROME_PATH`` → Windows 常见路径 → PATH 查找。"""

    override = os.environ.get("CHROME_PATH", "").strip()
    if override:
        if not pathlib.Path(override).is_file():
            raise RuntimeError(f"CHROME_PATH 指向的文件不存在：{override}")
        return override
    for candidate in CHROME_CANDIDATES:
        if pathlib.Path(candidate).is_file():
            return candidate
    for name in CHROME_BINARIES:
        located = shutil.which(name)
        if located:
            return located
    raise RuntimeError(
        "未找到 Chrome：可设 CHROME_PATH 环境变量，或安装 google-chrome / chromium"
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class CdpError(RuntimeError):
    pass


class Page:
    """一个标签页；所有方法在内部同步等待，失败抛 ``CdpError``。"""

    def __init__(self, browser: "Browser", session_id: str, target_id: str):
        self._browser = browser
        self.session_id = session_id
        self.target_id = target_id
        self.events: list[dict[str, Any]] = []

    # -- 基础设施 --------------------------------------------------------- #

    def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._browser.send(method, params, session_id=self.session_id)

    def _drain(self, seconds: float = 0.0) -> None:
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            try:
                raw = self._browser.ws.recv(timeout=remaining)
            except TimeoutError:
                return
            except Exception:  # noqa: BLE001 - 关闭中的连接不再等待
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "method" in message:
                if message.get("sessionId") in (None, self.session_id):
                    self.events.append(message)

    # -- 导航与求值 ------------------------------------------------------- #

    def goto(self, url: str, *, timeout: float = 45.0, expect: str | None = None) -> None:
        self._send("Page.navigate", {"url": url})
        self.wait_js("document.readyState === 'complete'", timeout=timeout)
        if expect is not None:
            self.wait_js(expect, timeout=timeout)
        self._drain(0.2)

    def reload(self, *, timeout: float = 30.0) -> None:
        self._send("Page.reload", {"ignoreCache": False})
        self.wait_js("document.readyState === 'complete'", timeout=timeout)
        self._drain(0.3)

    def current_url(self) -> str:
        result = self._send("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
        return str(result["result"].get("value", ""))

    def eval(self, expression: str, *, timeout: float = 20.0):
        result = self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
                "timeout": int(timeout * 1000),
            },
        )
        if "exceptionDetails" in result:
            raise CdpError(f"页面脚本异常：{result['exceptionDetails']}")
        return result["result"].get("value")

    def wait_js(self, expression: str, *, timeout: float = 30.0, interval: float = 0.1) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval(expression):
                    return
            except CdpError:
                pass  # 导航期间的执行上下文切换，重试即可
            time.sleep(interval)
        raise CdpError(f"等待超时：{expression}")

    def add_init_script(self, source: str) -> str:
        """在每次导航前注入脚本（用于替换外部语音服务，验证 UI 行为）。"""
        result = self._send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": source, "runImmediately": True},
        )
        return str(result.get("identifier") or "")

    def text(self) -> str:
        return str(self.eval("document.body ? document.body.innerText : ''") or "")

    def click(self, selector: str, *, timeout: float = 15.0) -> None:
        self.wait_js(f"Boolean(document.querySelector({json.dumps(selector)}))", timeout=timeout)
        self.eval(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            f" el.scrollIntoView({{block:'center'}}); el.click(); return true; }})()"
        )

    def screenshot(self, path: pathlib.Path) -> None:
        result = self._send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = result.get("data")
        if not data:
            raise CdpError("截图失败：CDP 未返回 data")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))

    # -- Cookie ----------------------------------------------------------- #

    def set_cookie(self, name: str, value: str, *, url: str) -> None:
        result = self._send(
            "Network.setCookie",
            {"name": name, "value": value, "url": url, "path": "/", "httpOnly": True},
        )
        if not result.get("success"):
            raise CdpError(f"设置 Cookie 失败：{result}")

    def clear_cookies(self) -> None:
        self._send("Network.clearBrowserCookies")

    def storage_keys(self) -> dict[str, list[str]]:
        return {
            "localStorage": list(self.eval("Object.keys(localStorage)") or []),
            "sessionStorage": list(self.eval("Object.keys(sessionStorage)") or []),
        }

    def page_errors(self) -> list[str]:
        errors: list[str] = []
        for event in self.events:
            if event.get("method") == "Runtime.exceptionThrown":
                details = event["params"].get("exceptionDetails", {})
                text = details.get("exception", {}).get("description") or details.get("text")
                errors.append(str(text))
        return errors



# 打开中的浏览器实例：进程异常退出（Ctrl-C、断言失败、被强杀）时也要收掉 Chrome 进程树，
# 否则 headless Chrome 的 crashpad/gpu/renderer 子进程会继续持有 user-data-dir，
# 临时配置目录删不掉、还会持续占内存与磁盘。
_OPEN_BROWSERS: set["Browser"] = set()


def _kill_process_tree(process: subprocess.Popen) -> None:
    """连同子进程一起结束 Chrome。

    Windows：``taskkill /F /T``；POSIX：浏览器以独立进程组（``start_new_session``）
    启动，直接对进程组发 SIGKILL，否则 renderer/zygote 子进程会挂住临时目录。
    """

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
        except Exception:  # noqa: BLE001 - 关闭期尽力而为
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001 - 关闭期尽力而为
            pass
    try:
        process.kill()
    except Exception:  # noqa: BLE001 - 关闭期尽力而为
        pass
    try:
        process.wait(timeout=15)
    except Exception:  # noqa: BLE001 - 关闭期尽力而为
        pass


def _remove_tree(path: pathlib.Path, attempts: int = 3) -> None:
    """尽量删除临时目录：Windows 上行方句柄释放有延迟，重试几次再放弃。"""

    for index in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if index == attempts - 1:
                return
            time.sleep(0.25)


def _shutdown_all_open_browsers() -> None:
    for browser in list(_OPEN_BROWSERS):
        try:
            browser._shutdown_process()
        except Exception:  # noqa: BLE001 - 解释器退出期
            pass


atexit.register(_shutdown_all_open_browsers)


class Browser:
    def __init__(self, *, user_data_dir: str | None = None, window_size: str = "1440,960"):
        self.chrome_path = os.environ.get("CHROME_PATH") or find_chrome()
        self.port = _free_port()
        self._tmpdir = None
        self._closed = False
        if user_data_dir is None:
            self._tmpdir = pathlib.Path(
                tempfile.mkdtemp(prefix="browser-e2e-", dir=str(_user_data_base()))
            )
            user_data_dir = str(self._tmpdir)
        log_base = self._tmpdir.parent if self._tmpdir is not None else _user_data_base()
        self._log_path = log_base / f"chrome-e2e-{self.port}.log"
        self._log = self._log_path.open("w", encoding="utf-8", errors="replace")
        self.process = subprocess.Popen(
            [
                self.chrome_path,
                "--headless=new",
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={user_data_dir}",
                "--remote-allow-origins=*",
                # venv 里的 Python 3.12 启动时会把句柄继承列表收窄，Chrome 的沙箱子进程
                # 在这种句柄环境下会以 STATUS_BREAKPOINT(0x80000003) 立刻退出；
                # 本地 headless 验收浏览器关掉 sandbox 即可（不影响被测应用的安全模型）。
                "--no-sandbox",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-features=Translate,MediaRouter",
                "--disk-cache-size=1048576",
                # 验收需要验证 TTS 播放态（麦克风随之禁用），headless 下放开自动播放限制
                "--autoplay-policy=no-user-gesture-required",
                f"--window-size={window_size}",
                "about:blank",
            ],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            # POSIX 上让 Chrome 自成进程组，关闭时按组清理（CI 里尤其重要）。
            start_new_session=(os.name != "nt"),
        )
        try:
            self._ws = self._connect()
        except Exception:
            self._shutdown_process()
            raise
        self._counter = 0
        self._pending: dict[int, dict[str, Any]] = {}
        _OPEN_BROWSERS.add(self)

    def _log_tail(self, limit: int = 1200) -> str:
        try:
            self._log.flush()
            return self._log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except Exception:  # noqa: BLE001 - 诊断信息尽力而为
            return ""

    def _shutdown_process(self) -> None:
        if self._closed:
            return
        _OPEN_BROWSERS.discard(self)
        _kill_process_tree(self.process)
        self._closed = True
        try:
            self._log.close()
        except Exception:  # noqa: BLE001 - 关闭期
            pass
        if self._tmpdir is not None:
            _remove_tree(self._tmpdir)

    def _endpoint(self) -> str:
        deadline = time.time() + 45
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise CdpError(
                    f"Chrome 启动即退出（code={self.process.returncode}）：{self._log_tail()}"
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/version", timeout=2
                ) as response:
                    return str(json.loads(response.read())["webSocketDebuggerUrl"])
            except Exception:  # noqa: BLE001 - Chrome 启动期
                time.sleep(0.2)
        raise CdpError(f"Chrome 未在 45s 内暴露 CDP 端口 {self.port}：{self._log_tail()}")

    def _connect(self):
        endpoint = self._endpoint()
        return connect(endpoint, max_size=None, open_timeout=20)

    @property
    def ws(self):
        return self._ws

    def send(
        self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None
    ) -> dict[str, Any]:
        self._counter += 1
        message_id = self._counter
        payload: dict[str, Any] = {"id": message_id, "method": method, "params": params or {}}
        if session_id is not None:
            payload["sessionId"] = session_id
        self._ws.send(json.dumps(payload))
        deadline = time.time() + 60
        while time.time() < deadline:
            raw = self._ws.recv(timeout=max(0.1, deadline - time.time()))
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise CdpError(f"{method} 失败：{message['error']}")
                return dict(message.get("result") or {})
        raise CdpError(f"{method} 超时")

    def new_page(self, url: str = "about:blank") -> Page:
        created = self.send("Target.createTarget", {"url": url})
        target_id = created["targetId"]
        attached = self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attached["sessionId"]
        page = Page(self, session_id, target_id)
        page._send("Page.enable")
        page._send("Runtime.enable")
        page._send("Network.enable")
        return page

    def close_page(self, page: Page) -> None:
        try:
            self.send("Target.closeTarget", {"targetId": page.target_id})
        except CdpError:
            pass

    def close(self) -> None:
        try:
            self.send("Browser.close")
        except CdpError:
            pass
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001 - 关闭期
            pass
        self._shutdown_process()

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
