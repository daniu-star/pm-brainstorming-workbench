"""后台 Run Worker（PRD FR-03）。

独立进程运行，**不**由 FastAPI 启动（``backend/main.py`` 不会自动起 worker）。
用法（cwd = backend）::

    python -m workers.run_worker                # 常驻
    python -m workers.run_worker --once         # 跑一轮（无任务正常退出）
    python -m workers.run_worker --interval 1 --max-runs 5
    python -m workers.run_worker --worker-id w-1

每轮顺序：``recover_expired_leases`` → ``requeue_interrupted`` →
``expire_queued_runs`` → ``reconcile_control_intents``（审计结束/取消的回执）→
``compact_events``（每小时一次，清理 7 天前已终结 Run 的历史事件）→
``prune_idempotency`` / ``prune_expired_codes``（每 6 小时一次，清掉过窗的幂等记录与过期验证码）→
检查运行中数量 → ``claim_next_run`` → ``execute_run``。
收到 SIGINT 后停止领取新任务，等待当前 Run 执行完再退出。
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Any, Sequence

from domain import contracts as C
from domain.contracts import utc_now_iso

logger = logging.getLogger("workers.run_worker")


def _iso_days_ago(days: int) -> str:
    """当前时间往前 N 天的 UTC ISO 文本（字典序可比较）。"""

    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _repositories():
    from repositories import run_repo

    return run_repo


def _database():
    from repositories import database

    return database


def _command_service():
    from services import command_service

    return command_service


def default_worker_id() -> str:
    """默认 worker id：``hostname-pid``（同一主机多进程可区分）。"""
    return f"{socket.gethostname()}-{os.getpid()}"


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


class RunWorker:
    """串行执行器：一次领取并执行一个 Run，直到收到停止信号/达到 max_runs。

    M1 采用串行执行（同一 Worker 同时最多 1 个 Run）；``count_running_runs``
    仍是全局运行上限 ``MAX_RUNNING_RUNS`` 的必须检查，避免多 Worker 超载。
    """

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        interval: float = 2.0,
        max_runs: int = 0,
    ) -> None:
        self.worker_id = worker_id or default_worker_id()
        self.interval = max(0.1, float(interval))
        self.max_runs = max(0, int(max_runs))
        self._stop = threading.Event()
        self.processed = 0
        self._last_compaction = 0.0
        self._last_maintenance = 0.0

    # -- 生命周期 ---------------------------------------------------------- #

    def request_stop(self) -> None:
        logger.info("event=worker.stop_requested worker=%s", self.worker_id)
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self, *, once: bool = False) -> int:
        logger.info("event=worker.start worker=%s interval=%s max_runs=%s",
                    self.worker_id, self.interval, self.max_runs or "∞")
        while not self._stop.is_set():
            try:
                claimed = self.tick()
            except Exception:  # noqa: BLE001 - 单轮失败不应杀死 Worker
                logger.exception("event=worker.tick_failed worker=%s", self.worker_id)
                claimed = False

            if claimed:
                self.processed += 1
            if once:
                break
            if self.max_runs and self.processed >= self.max_runs:
                logger.info(
                    "event=worker.max_runs_reached worker=%s processed=%s",
                    self.worker_id,
                    self.processed,
                )
                break
            if self._stop.is_set():
                break
            # 刚执行过任务则立即尝试下一个；空转才等待 interval（可被 SIGINT 唤醒）。
            if not claimed:
                self._stop.wait(self.interval)

        logger.info(
            "event=worker.exit worker=%s processed=%s", self.worker_id, self.processed
        )
        return 0

    # -- 单轮 -------------------------------------------------------------- #

    def tick(self) -> bool:
        """执行一轮维护 + 至多领取/执行一个 Run；返回是否执行了 Run。"""
        run_repo = _repositories()
        command_service = _command_service()
        claimed: tuple[str, int] | None = None
        recovered: list[str] = []
        interrupted: list[str] = []
        expired: list[str] = []
        running = 0

        with _database().session_scope() as db:
            now_epoch = int(time.time())
            now_iso = utc_now_iso()

            recovered = list(run_repo.recover_expired_leases(db, now_epoch=now_epoch, now_iso=now_iso))
            interrupted = list(run_repo.requeue_interrupted(db))
            expired = list(run_repo.expire_queued_runs(db, now_epoch=now_epoch, now_iso=now_iso))
            if now_epoch - self._last_compaction >= C.EVENT_COMPACT_INTERVAL_SECONDS:
                cutoff_iso = _iso_days_ago(C.EVENT_RETENTION_DAYS)
                try:
                    compacted = int(run_repo.compact_events(db, before_iso=cutoff_iso))
                except Exception:  # pragma: no cover - 压缩失败不应阻塞任务执行
                    logger.exception("event=worker.compact_failed worker=%s", self.worker_id)
                    compacted = 0
                self._last_compaction = now_epoch
                if compacted:
                    logger.info(
                        "event=worker.compacted worker=%s events=%d cutoff=%s",
                        self.worker_id, compacted, cutoff_iso,
                    )
            # 维护性清理：幂等记录与过期验证码只增不减，长期运行会把库撑大（没有读者）。
            # 与事件压缩同一模式：按间隔触发、失败只记日志，绝不阻塞任务执行。
            if now_epoch - self._last_maintenance >= C.MAINTENANCE_INTERVAL_SECONDS:
                from repositories import auth_repo

                try:
                    pruned_idempotency = int(
                        run_repo.prune_idempotency(
                            db, before_iso=_iso_days_ago(C.IDEMPOTENCY_RETENTION_DAYS)
                        )
                    )
                    pruned_codes = int(
                        auth_repo.prune_expired_codes(db, now_epoch=now_epoch)
                    )
                    pruned_payloads = 0
                    if int(C.LLM_PAYLOAD_RETENTION_DAYS) > 0:
                        payload_stats = run_repo.prune_intermediate_payloads(
                            db,
                            before_iso=_iso_days_ago(C.LLM_PAYLOAD_RETENTION_DAYS),
                        )
                        pruned_payloads = int(payload_stats.get("steps", 0))
                except Exception:  # pragma: no cover - 清理失败不应阻塞任务执行
                    logger.exception(
                        "event=worker.maintenance_failed worker=%s", self.worker_id
                    )
                    pruned_idempotency = 0
                    pruned_codes = 0
                    pruned_payloads = 0
                self._last_maintenance = now_epoch
                if pruned_idempotency or pruned_codes or pruned_payloads:
                    logger.info(
                        "event=worker.maintenance worker=%s idempotency=%d auth_codes=%d payloads=%d",
                        self.worker_id, pruned_idempotency, pruned_codes, pruned_payloads,
                    )
            # 控制意图只登记、不回执（PRD FR-07）：在活跃 Run 上提交 end_audit / cancel_audit 时
            # 命令层只写 control 行，保留旧 Run 正常收尾；必须由 Worker 每轮扫描、在旧 Run 终结后
            # 建唯一 closing Run（结束）或复位会话阶段（取消）。缺少这一步时结束审计会永久停在
            # closing，取消审计会让会话卡在 auditing 且 start/end/cancel 三动作全被拒。
            controls = list(
                command_service.reconcile_control_intents(db, now_iso=now_iso, now_epoch=now_epoch)
            )
            running = int(run_repo.count_running_runs(db))

            # 本机串行执行：本地活跃数（0 或 1）+ 全局运行数双重门禁。
            local_active = 0
            if local_active < C.MAX_RUNNING_RUNS and running < C.MAX_RUNNING_RUNS:
                run = run_repo.claim_next_run(
                    db,
                    worker_id=self.worker_id,
                    now_epoch=now_epoch,
                    lease_seconds=C.LEASE_SECONDS,
                )
                if run is not None:
                    claimed = (run.id, int(run.lease_epoch or 0))

        logger.info(
            "event=worker.tick worker=%s recovered=%d interrupted=%d expired=%d "
            "controls=%d running=%d claimed=%s",
            self.worker_id,
            len(recovered),
            len(interrupted),
            len(expired),
            len(controls),
            running,
            claimed[0] if claimed else "-",
        )
        if recovered:
            logger.info("event=worker.recovered ids=%s", ",".join(recovered))
        if interrupted:
            logger.info("event=worker.requeued ids=%s", ",".join(interrupted))
        if expired:
            logger.info("event=worker.expired ids=%s", ",".join(expired))
        if controls:
            logger.info("event=worker.controls ids=%s", ",".join(controls))

        if claimed is None:
            return False

        run_id, lease_epoch = claimed
        self._execute(run_id, lease_epoch=lease_epoch)
        return True

    def _execute(self, run_id: str, *, lease_epoch: int) -> None:
        from workflows.runner import execute_run, handle_run_exception

        started = time.monotonic()
        try:
            execute_run(run_id, worker_id=self.worker_id)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "event=run.executed worker=%s run=%s elapsed_ms=%d",
                self.worker_id,
                run_id,
                elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001 - execute_run 理论上自兜底
            logger.exception("event=run.execute_failed worker=%s run=%s", self.worker_id, run_id)
            try:
                handle_run_exception(
                    run_id,
                    worker_id=self.worker_id,
                    lease_epoch=lease_epoch,
                    exc=exc,
                )
            except Exception:  # noqa: BLE001 - 兜底失败只能记录
                logger.exception("event=run.finalize_failed worker=%s run=%s", self.worker_id, run_id)


# --------------------------------------------------------------------------- #
# 可选自启动钩子（main.py 由 api 队友维护，本文件只提供函数，不主动调用）
# --------------------------------------------------------------------------- #


def autostart_from_env() -> threading.Thread | None:
    """若 ``WORKER_AUTOSTART`` 为真则后台启动一个 Worker 并返回线程。

    供 API 进程按需调用（``api`` 队友在 lifespan 中可选接入）；默认不启动，
    保持 Worker 与 API 进程解耦。
    """
    flag = os.environ.get("WORKER_AUTOSTART", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    interval = float(os.environ.get("WORKER_INTERVAL", "2") or 2)
    worker = RunWorker(interval=interval)
    thread = threading.Thread(target=worker.run, name="run-worker", daemon=True)
    thread.start()
    logger.info("event=worker.autostarted worker=%s", worker.worker_id)
    return thread


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PM Brainstorming Run Worker")
    parser.add_argument("--once", action="store_true", help="只运行一轮，无任务时正常退出")
    parser.add_argument("--interval", type=float, default=2.0, help="空转轮询间隔（秒）")
    parser.add_argument("--worker-id", default=None, help="Worker ID（默认 hostname-pid）")
    parser.add_argument("--max-runs", type=int, default=0, help="最多执行多少个 Run（0=不限）")
    parser.add_argument(
        "--log-level", default="INFO", help="日志级别（DEBUG/INFO/WARNING/ERROR）"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(getattr(logging, str(args.log_level).upper(), logging.INFO))
    worker = RunWorker(
        worker_id=args.worker_id,
        interval=args.interval,
        max_runs=args.max_runs,
    )

    previous_handler: Any = None

    def _on_sigint(signum, frame):  # noqa: ANN001 - signal handler 签名固定
        worker.request_stop()

    try:
        previous_handler = signal.signal(signal.SIGINT, _on_sigint)
    except ValueError:  # pragma: no cover - 非主线程无法注册信号
        logger.warning("event=worker.signal_unavailable")

    try:
        return worker.run(once=bool(args.once))
    finally:
        if previous_handler is not None:
            try:
                signal.signal(signal.SIGINT, previous_handler)
            except ValueError:  # pragma: no cover
                pass


if __name__ == "__main__":
    raise SystemExit(main())