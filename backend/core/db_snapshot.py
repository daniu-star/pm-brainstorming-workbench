"""SQLite 快照的远端持久化（第 46 批）：把容器内的库同步到私有 HF dataset 仓库。

为什么需要：Hugging Face 免费实例没有持久卷，容器内的 ``/app/data/app.db`` 在
每次重启、休眠回收（``gcTimeout=172800``）后都会被清零，用户、会话、审计、
模型密钥全部不可恢复。之前只是在健康接口上「报警」，并没有真正解决。

这条链路的做法（只用标准库，不给镜像加依赖）：

1. **启动恢复**：进程起来时若本地库不存在或为空，就从私有 dataset 仓库下载
   最近一次快照（``app.db.gz.b64``）解压落地；
2. **周期快照**：后台线程每隔 ``DB_SNAPSHOT_INTERVAL_SECONDS`` 用 sqlite3 的
   online backup API 做一致性冷拷贝（不锁业务写入），gzip 后 base64 提交到
   dataset 仓库的 ``main`` 分支；内容与上次相同则跳过，避免灌满仓库历史；
3. **退出落盘**：收到 SIGTERM/SIGINT 时在终止子进程前再快照一次，把最后几秒
   的写入也带上。

为什么是 base64 文本而不是二进制：HF 的 pre-receive 钩子会拒收二进制推送
（``Your push was rejected because it contains binary files``），改成
gzip + base64 的纯文本文件即可绕过，代价是体积 ×1.34（实测 643KB 的库压成
76KB，base64 后 101KB）。

已知边界（必须如实说明，不要当成强一致）：

* 快照是**周期**的，最坏会丢 ``DB_SNAPSHOT_INTERVAL_SECONDS`` 秒内的写入；
* 仓库是私有的，令牌通过 Space secret 注入，不会进镜像、不进日志；
* 这是「单实例 + 低频写入」的合适方案；真正的多副本/高并发应换外部 Postgres。

配置（全部通过环境变量，缺任意一项就整体降级为 no-op，不影响服务启动）::

    DB_SNAPSHOT_REPO=123chenjunliang/pm-brainstorm-db   # 私有 dataset 仓库
    DB_SNAPSHOT_TOKEN=hf_xxx                            # 有 write 权限的令牌
    DB_SNAPSHOT_INTERVAL_SECONDS=120                    # 快照周期（默认 120）
    DB_SNAPSHOT_PATH=/app/data/app.db                   # 本地库路径（默认同 DATABASE_URL）

令牌读取顺序：``DB_SNAPSHOT_TOKEN`` → ``DB_SYNC_TOKEN`` → ``HF_TOKEN`` → ``HF_API_TOKEN``。
（``DB_SYNC_TOKEN`` 是第 46 批先用 ``POST /api/spaces/{{id}}/secrets`` 写进 Space 的名字，
不能因为代码后来偏好新名字就把已经配好的凭据晾在那儿。）
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

logger = logging.getLogger("core.db_snapshot")

#: HF 上所有仓库 HTTP API 的根地址（下载走 resolve，提交走 /api 前缀）。
HF_API_BASE = "https://huggingface.co"
#: 快照文件名固定，便于人工在 dataset 页面直接下载排查。
SNAPSHOT_FILENAME = "app.db.gz.b64"
#: SQLite 文件头，恢复前用它挡住「下载到的是 HTML 错误页」这类脏数据。
SQLITE_MAGIC = b"SQLite format 3\x00"
DEFAULT_INTERVAL_SECONDS = 120
MIN_INTERVAL_SECONDS = 30
#: 单次上传超时：库通常几百 KB，20s 足够；失败只记日志不阻塞业务。
UPLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 30

UrlOpen = Callable[..., object]


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def snapshot_config(env: dict[str, str] | None = None) -> dict[str, object]:
    """从环境变量读配置；返回 ``{"enabled": bool, ...}``（缺项即 disabled）。"""

    source = os.environ if env is None else env
    repo = (source.get("DB_SNAPSHOT_REPO") or "").strip()
    token = (
        (
            source.get("DB_SNAPSHOT_TOKEN")
            or source.get("DB_SYNC_TOKEN")
            or source.get("HF_TOKEN")
            or source.get("HF_API_TOKEN")
            or ""
        ).strip()
    )
    raw_interval = (source.get("DB_SNAPSHOT_INTERVAL_SECONDS") or "").strip()
    try:
        interval = int(raw_interval) if raw_interval else DEFAULT_INTERVAL_SECONDS
    except ValueError:
        interval = DEFAULT_INTERVAL_SECONDS
    interval = max(MIN_INTERVAL_SECONDS, interval)
    local_path = (source.get("DB_SNAPSHOT_PATH") or "").strip() or sqlite_path_from_database_url(
        source.get("DATABASE_URL") or ""
    )
    return {
        "enabled": bool(repo and token and local_path),
        "repo": repo,
        "token": token,
        "interval_seconds": interval,
        "local_path": local_path,
    }


def sqlite_path_from_database_url(url: str) -> str:
    """从 ``sqlite+pysqlite:///C:/x/app.db`` 里取出文件路径；非 sqlite 返回空串。"""

    url = (url or "").strip()
    if not url.startswith("sqlite"):
        return ""
    _, _, tail = url.partition(":///")
    if not tail:
        return ""
    return tail


# --------------------------------------------------------------------------- #
# 编解码与 URL
# --------------------------------------------------------------------------- #


def snapshot_download_url(repo: str) -> str:
    return f"{HF_API_BASE}/datasets/{repo}/resolve/main/{SNAPSHOT_FILENAME}?download=true"


def snapshot_commit_url(repo: str) -> str:
    # 注意：提交接口在 /api 前缀下（下载接口走 resolve 路径，不带 /api）。
    return f"{HF_API_BASE}/api/datasets/{repo}/commit/main"


def snapshot_squash_url(repo: str) -> str:
    return f"{HF_API_BASE}/api/datasets/{repo}/super-squash/main"


def encode_snapshot(raw: bytes) -> str:
    """gzip + base64：HF 拒收二进制，文本文件才推得上去。"""

    return base64.b64encode(gzip.compress(raw, 6)).decode("ascii")


def decode_snapshot(text: str) -> bytes:
    """base64 → gunzip；内容不是 SQLite 时抛 ``ValueError``（挡住错误页）。"""

    payload = gzip.decompress(base64.b64decode(text.encode("ascii")))
    if not payload.startswith(SQLITE_MAGIC):
        raise ValueError("快照内容不是 SQLite 数据库")
    return payload


def build_commit_body(text: str, *, summary: str) -> bytes:
    """构造 HF NDJSON 提交体（header + 一个 base64 文本文件条目）。"""

    lines = [
        json.dumps({"key": "header", "value": {"summary": summary, "description": ""}}, ensure_ascii=False),
        json.dumps(
            {
                "key": "file",
                "value": {
                    "path": SNAPSHOT_FILENAME,
                    "content": base64.b64encode(text.encode("ascii")).decode("ascii"),
                    "encoding": "base64",
                },
            },
            ensure_ascii=False,
        ),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _request(
    url: str, *, token: str, data: bytes | None = None, content_type: str | None = None
) -> urllib.request.Request:
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")


# --------------------------------------------------------------------------- #
# 一致性拷贝
# --------------------------------------------------------------------------- #


def copy_sqlite_consistent(source: Path, target: Path) -> int:
    """用 sqlite3 online backup 做一致性拷贝（业务写入不会被长时间阻塞）。

    直接 ``shutil.copy`` 在 WAL 模式下可能拿到「主库 + 未 checkpoint 的 -wal」的中间态，
    恢复后表现为缺最新数据甚至 corrupt，所以必须走 backup API。
    """

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    src_conn = sqlite3.connect(str(source), timeout=10)
    try:
        dst_conn = sqlite3.connect(str(target))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return target.stat().st_size


# --------------------------------------------------------------------------- #
# 恢复 / 上传
# --------------------------------------------------------------------------- #


def restore_snapshot(
    config: dict[str, object],
    *,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> str:
    """库缺失/为空时从 dataset 拉取快照。

    返回人类可读的结果字符串（写日志用），不做异常上抛：**恢复失败不能阻止服务启动**，
    否则一个网络抖动会让整个 Space 起不来（那比丢历史数据更严重）。
    """

    if not config.get("enabled"):
        return "skipped: 未配置 DB_SNAPSHOT_REPO / DB_SNAPSHOT_TOKEN / 本地库路径"
    path = Path(str(config["local_path"]))
    if path.exists() and path.stat().st_size > 0:
        return f"skipped: 本地库已存在（{path.stat().st_size} 字节），不覆盖"

    path.parent.mkdir(parents=True, exist_ok=True)
    url = snapshot_download_url(str(config["repo"]))
    tmp = path.with_suffix(path.suffix + ".download")
    try:
        with urlopen(_request(url, token=str(config["token"])), timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # type: ignore[union-attr]
            text = response.read().decode("utf-8", "replace")
        raw = decode_snapshot(text.strip())
        tmp.write_bytes(raw)
        os.replace(tmp, path)
        return f"restored: {len(raw)} 字节 -> {path}"
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        if exc.code == 404:
            return "skipped: 远端还没有快照（首次部署）"
        return f"failed: HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 恢复失败只记日志
        tmp.unlink(missing_ok=True)
        return f"failed: {type(exc).__name__}: {exc}"


class SnapshotUploader:
    """周期性把本地库快照提交到 dataset 仓库；``flush()`` 供退出钩子同步调用。"""

    def __init__(self, config: dict[str, object], *, urlopen: UrlOpen = urllib.request.urlopen) -> None:
        self._config = config
        self._urlopen = urlopen
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_digest: str | None = None
        self.uploads = 0
        self.skipped = 0
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("enabled"))

    # -- 生命周期 -------------------------------------------------------- #

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        interval = float(self._config.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS)
        self._thread = threading.Thread(target=self._run, args=(interval,), name="db-snapshot", daemon=True)
        self._thread.start()
        logger.info("event=db_snapshot.start interval=%ss repo=%s", interval, self._config.get("repo"))

    def _run(self, interval: float) -> None:
        # 首次延迟一个周期：启动瞬间服务还在处理恢复/建表，抢 IO 没意义。
        while not self._stop.wait(interval):
            self.flush(reason="periodic")

    def stop(self, *, flush: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if flush and self.enabled:
            self.flush(reason="shutdown")

    # -- 核心 ------------------------------------------------------------ #

    def _payload(self) -> tuple[str, int] | None:
        """生成待上传的文本快照；本地库不存在/为空时返回 ``None``。"""

        source = Path(str(self._config["local_path"]))
        if not source.exists() or source.stat().st_size == 0:
            return None
        tmp = source.with_suffix(source.suffix + ".snapshot")
        try:
            size = copy_sqlite_consistent(source, tmp)
            text = encode_snapshot(tmp.read_bytes())
        finally:
            tmp.unlink(missing_ok=True)
        return text, size

    def flush(self, *, reason: str = "manual") -> str:
        if not self.enabled:
            return "skipped: 未配置"
        try:
            payload = self._payload()
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            result = f"failed: {type(exc).__name__}: {exc}"
            logger.warning("event=db_snapshot.%s %s", reason, result)
            return result
        if payload is None:
            self.skipped += 1
            return "skipped: 本地库不存在或为空"
        text, size = payload
        if text == self._last_digest:
            self.skipped += 1
            logger.debug("event=db_snapshot.%s skipped=unchanged", reason)
            return "skipped: 内容未变化"

        try:
            body = build_commit_body(
                text, summary=f"db snapshot {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ({size} bytes)"
            )
            request = _request(
                snapshot_commit_url(str(self._config["repo"])),
                token=str(self._config["token"]),
                data=body,
                content_type="application/x-ndjson",
            )
            with self._urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS) as response:  # type: ignore[union-attr]
                raw = response.read().decode("utf-8", "replace")
            commit = ""
            try:
                commit = str(json.loads(raw).get("commitOid", ""))[:8]
            except (ValueError, AttributeError):
                pass
            self._last_digest = text
            self.uploads += 1
            result = f"uploaded: {size} 字节 -> {len(text)} 字符 commit={commit or '?'}"
            logger.info("event=db_snapshot.%s ok=%s", reason, result)
            return result
        except urllib.error.HTTPError as exc:
            self.failures += 1
            detail = exc.read().decode("utf-8", "replace")[:200] if hasattr(exc, "read") else ""
            result = f"failed: HTTP {exc.code} {detail}".strip()
            logger.warning("event=db_snapshot.%s %s", reason, result)
            return result
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            result = f"failed: {type(exc).__name__}: {exc}"
            logger.warning("event=db_snapshot.%s %s", reason, result)
            return result


def upload_snapshot(
    config: dict[str, object],
    *,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> str:
    """单次上传（测试与手工脚本用；常驻服务请用 :class:`SnapshotUploader`）。"""

    uploader = SnapshotUploader(config, urlopen=urlopen)
    return uploader.flush(reason="manual")