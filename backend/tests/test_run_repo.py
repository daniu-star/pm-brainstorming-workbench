"""run_repo 集成测试：租约、领取、幂等、事件序号、队列超时。

对应验收：AC-03（同会话单活动 Run）、AC-05（租约接管 epoch 递增且旧 Worker 无法提交）、
AC-08（租约过期恢复）、AC-09（步骤幂等不重跑）、AC-10（排队超时失败）、AC-29（事件 seq 单调）。
"""
import tempfile
import time
import unittest
from pathlib import Path

from domain.contracts import EventType, RunStatus, StepStatus, new_request_id, utc_now_iso
from domain.errors import AppError, ErrorCode
from repositories import run_repo, session_repo
from repositories.database import dispose_engine, reset_for_tests, session_scope


class RunRepoTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "run.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")
        self.now = int(time.time())
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="做一个自动写周报的工具"
            )
            self.session_id = session.id

    def tearDown(self):
        dispose_engine()
        self._tmp.cleanup()

    def _create_run(self, action="clarify", **kwargs):
        with session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email="a@example.com",
                request_id=new_request_id(),
                action=action,
                input_hash="a" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=utc_now_iso(),
                deadline_at=self.now + 300,
                queue_deadline_at=self.now + 600,
                **kwargs,
            )
            return run.id

    def _claim(self, worker_id="w1"):
        with session_scope() as db:
            run = run_repo.claim_next_run(db, worker_id=worker_id, now_epoch=self.now)
            if run is None:
                return None
            return run.id, run.lease_epoch


class CreateRunTests(RunRepoTestCase):
    def test_creates_queued_run_with_single_queued_event(self):
        with session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email="a@example.com",
                request_id=new_request_id(),
                action="clarify",
                input_hash="a" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=utc_now_iso(),
            )
            self.assertEqual(run.status, RunStatus.queued.value)
            self.assertEqual(run.lease_epoch, 0)
            self.assertIsNone(run.lease_owner)
            events = run_repo.events_after(db, run.id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].type, EventType.run_queued.value)
            self.assertEqual(events[0].seq, 1)

    def test_active_run_for_session_includes_queued(self):
        run_id = self._create_run()
        with session_scope() as db:
            active = run_repo.active_run_for_session(db, self.session_id)
            self.assertIsNotNone(active)
            self.assertEqual(active.id, run_id)

    def test_require_run_checks_owner(self):
        run_id = self._create_run()
        with session_scope() as db:
            with self.assertRaises(AppError) as ctx:
                run_repo.require_run(db, run_id, owner_email="b@example.com")
            self.assertEqual(ctx.exception.code, ErrorCode.FORBIDDEN)


class ClaimTests(RunRepoTestCase):
    def test_claim_increments_lease_epoch_and_writes_started_event(self):
        run_id = self._create_run()
        claimed = self._claim("w1")
        self.assertEqual(claimed[0], run_id)
        self.assertEqual(claimed[1], 1)
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertEqual(run.status, RunStatus.running.value)
            self.assertEqual(run.lease_owner, "w1")
            self.assertEqual(run.lease_epoch, 1)
            self.assertIsNotNone(run.started_at)
            self.assertIsNotNone(run.deadline_at)
            types = [e.type for e in run_repo.events_after(db, run_id)]
            self.assertEqual(types, [EventType.run_queued.value, EventType.run_started.value])

    def test_second_claim_returns_none_while_lease_valid(self):
        self._create_run()
        self.assertIsNotNone(self._claim("w1"))
        self.assertIsNone(self._claim("w2"))

    def test_expired_lease_can_be_taken_over_with_higher_epoch(self):
        run_id = self._create_run()
        self._claim("w1")
        later = self.now + 120  # 超过 30s 租约
        with session_scope() as db:
            recovered = run_repo.recover_expired_leases(db, now_epoch=later, now_iso=utc_now_iso())
            self.assertEqual(recovered, [run_id])
            run = run_repo.get_run(db, run_id)
            self.assertEqual(run.status, RunStatus.interrupted.value)
            self.assertIsNone(run.lease_owner)
            types = [e.type for e in run_repo.events_after(db, run_id)]
            self.assertIn(EventType.run_recovered.value, types)

        with session_scope() as db:
            requeued = run_repo.requeue_interrupted(db, limit=10)
            self.assertEqual(requeued, [run_id])

        with session_scope() as db:
            taken = run_repo.claim_next_run(db, worker_id="w2", now_epoch=later)
            self.assertIsNotNone(taken)
            self.assertEqual(taken.lease_owner, "w2")
            self.assertEqual(taken.lease_epoch, 2)  # 递增，旧 Worker(epoch=1) 提交必被拒

    def test_old_worker_submit_after_takeover_is_rejected(self):
        run_id = self._create_run()
        self._claim("w1")
        later = self.now + 120
        with session_scope() as db:
            run_repo.recover_expired_leases(db, now_epoch=later)
        with session_scope() as db:
            run_repo.requeue_interrupted(db, limit=10)
        with session_scope() as db:
            taken = run_repo.claim_next_run(db, worker_id="w2", now_epoch=later)
            self.assertEqual(taken.lease_epoch, 2)
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            with self.assertRaises(AppError) as ctx:
                run_repo.release_run(
                    db,
                    run,
                    worker_id="w1",
                    lease_epoch=1,
                    status=RunStatus.succeeded,
                    now_iso=utc_now_iso(),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.LEASE_LOST)

    def test_renew_lease_rejects_wrong_owner_or_epoch(self):
        run_id = self._create_run()
        _, epoch = self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertTrue(
                run_repo.renew_lease(db, run, worker_id="w1", lease_epoch=epoch, now_epoch=self.now)
            )
            self.assertFalse(
                run_repo.renew_lease(db, run, worker_id="w2", lease_epoch=epoch, now_epoch=self.now)
            )
            self.assertFalse(
                run_repo.renew_lease(db, run, worker_id="w1", lease_epoch=epoch + 1, now_epoch=self.now)
            )


class ReleaseTests(RunRepoTestCase):
    def test_release_sets_terminal_status_and_clears_lease(self):
        run_id = self._create_run()
        _, epoch = self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            run_repo.release_run(
                db,
                run,
                worker_id="w1",
                lease_epoch=epoch,
                status=RunStatus.succeeded,
                result_product_version=2,
                now_iso=utc_now_iso(),
            )
            self.assertEqual(run.status, RunStatus.succeeded.value)
            self.assertEqual(run.result_product_version, 2)
            self.assertIsNone(run.lease_owner)
            self.assertIsNotNone(run.finished_at)

    def test_illegal_transition_rejected(self):
        run_id = self._create_run()
        _, epoch = self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            with self.assertRaises(AppError):
                run_repo.release_run(
                    db, run, worker_id="w1", lease_epoch=epoch, status=RunStatus.running
                )


class QueueExpiryTests(RunRepoTestCase):
    def test_queued_run_expires_after_ten_minutes(self):
        run_id = self._create_run()
        with session_scope() as db:
            expired = run_repo.expire_queued_runs(db, now_epoch=self.now + 601, now_iso=utc_now_iso())
            self.assertEqual(expired, [run_id])
            run = run_repo.get_run(db, run_id)
            self.assertEqual(run.status, RunStatus.failed.value)
            self.assertEqual(run.error_code, ErrorCode.QUEUE_WAIT_EXCEEDED)
            events = run_repo.events_after(db, run_id)
            self.assertEqual(events[-1].type, EventType.run_failed.value)

    def test_running_run_is_not_expired_by_queue_scanner(self):
        run_id = self._create_run()
        self._claim("w1")
        with session_scope() as db:
            self.assertEqual(run_repo.expire_queued_runs(db, now_epoch=self.now + 601), [])
            self.assertEqual(run_repo.get_run(db, run_id).status, RunStatus.running.value)

    def test_expiry_releases_session_active_run(self):
        """排队超时是终结态：必须释放 ``session.active_run_id`` 并推进会话版本。

        否则页面仍以为有任务在跑（输入框禁用/SSE 等 Run），刷新后也拿不到可恢复态。
        """

        run_id = self._create_run()
        with session_scope() as db:
            session = session_repo.require_session(
                db, self.session_id, owner_email="a@example.com"
            )
            session_repo.touch_session(db, session, active_run_id=run_id)
            before = int(session.session_version)
        with session_scope() as db:
            expired = run_repo.expire_queued_runs(db, now_epoch=self.now + 601)
            self.assertEqual(expired, [run_id])
            session = session_repo.require_session(
                db, self.session_id, owner_email="a@example.com"
            )
            self.assertIsNone(session.active_run_id)
            self.assertEqual(int(session.session_version), before + 1)


class StepTests(RunRepoTestCase):
    def test_ensure_step_is_idempotent(self):
        run_id = self._create_run()
        self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            step = run_repo.ensure_step(db, run, logical_key="clarify.main", kind="llm", role_code="coach")
            self.assertEqual(step.status, StepStatus.pending.value)
            self.assertEqual(step.attempt_count, 0)
            again = run_repo.ensure_step(db, run, logical_key="clarify.main", kind="llm")
            self.assertEqual(again.id, step.id)
            self.assertEqual(len(run_repo.list_steps(db, run_id)), 1)

    def test_start_and_finish_step_records_attempt(self):
        run_id = self._create_run()
        self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            step = run_repo.ensure_step(db, run, logical_key="s1", kind="llm")
            run_repo.start_step(db, step)
            self.assertEqual(step.status, StepStatus.running.value)
            self.assertEqual(step.attempt_count, 1)
            run_repo.finish_step(db, step, status=StepStatus.succeeded, output={"ok": True})
            self.assertEqual(step.status, StepStatus.succeeded.value)
            # 重复 finish 同状态是 no-op，不会二次应用结果
            again = run_repo.finish_step(db, step, status=StepStatus.succeeded, output={"ok": False})
            self.assertEqual(again.output_json, step.output_json)

    def test_get_step_by_key(self):
        run_id = self._create_run()
        self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            step = run_repo.ensure_step(db, run, logical_key="k", kind="llm")
            self.assertEqual(run_repo.get_step_by_key(db, run_id, "k").id, step.id)
            self.assertIsNone(run_repo.get_step_by_key(db, run_id, "missing"))


class EventSeqTests(RunRepoTestCase):
    def test_event_seq_is_monotonic_and_unique(self):
        run_id = self._create_run()
        with session_scope() as db:
            for index in range(5):
                run_repo.append_event(
                    db,
                    session_id=self.session_id,
                    run_id=run_id,
                    type=EventType.step_started,
                    payload={"i": index},
                )
            seqs = [event.seq for event in run_repo.events_after(db, run_id)]
            self.assertEqual(seqs, [1, 2, 3, 4, 5, 6])
            self.assertEqual(run_repo.latest_event_seq(db, run_id), 6)
            self.assertEqual([e.seq for e in run_repo.events_after(db, run_id, after_seq=5)], [6])


class CandidateTests(RunRepoTestCase):
    def test_reserve_candidate_id_is_stable(self):
        run_id = self._create_run()
        self._claim("w1")
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            step = run_repo.ensure_step(db, run, logical_key="s1", kind="llm")
            first = run_repo.reserve_candidate_id(
                db, run_id=run_id, step_id=step.id, local_id="opt-1", kind="option", payload={"t": 1}
            )
            second = run_repo.reserve_candidate_id(
                db, run_id=run_id, step_id=step.id, local_id="opt-1", kind="option", payload={"t": 2}
            )
            self.assertEqual(first, second)
            other = run_repo.reserve_candidate_id(
                db, run_id=run_id, step_id=step.id, local_id="opt-2", kind="option", payload={}
            )
            self.assertNotEqual(first, other)


class IdempotencyTests(RunRepoTestCase):
    def test_save_then_get_returns_same_record_and_keeps_expiry_epoch(self):
        with session_scope() as db:
            run_repo.save_idempotency(
                db,
                owner_email="a@example.com",
                request_id="req-1",
                action="clarify",
                resource_scope=self.session_id,
                input_hash="a" * 64,
                response_status=202,
                response={"run_id": "r1"},
                related_run_id="r1",
            )
            row = run_repo.get_idempotency(db, "a@example.com", "req-1")
            self.assertIsNotNone(row)
            self.assertEqual(row.response_status, 202)
            self.assertGreater(int(row.expires_at), int(time.time()) + 29 * 86400)
            # 重复保存不覆盖（第一条为准）
            run_repo.save_idempotency(
                db,
                owner_email="a@example.com",
                request_id="req-1",
                action="clarify",
                resource_scope=self.session_id,
                input_hash="b" * 64,
                response_status=200,
                response={"run_id": "r2"},
            )
            self.assertEqual(run_repo.get_idempotency(db, "a@example.com", "req-1").input_hash, "a" * 64)

    def test_idempotency_is_scoped_by_owner(self):
        with session_scope() as db:
            run_repo.save_idempotency(
                db,
                owner_email="a@example.com",
                request_id="same",
                action="clarify",
                resource_scope=self.session_id,
                input_hash="a" * 64,
                response_status=202,
                response={},
            )
            self.assertIsNone(run_repo.get_idempotency(db, "b@example.com", "same"))


if __name__ == "__main__":
    unittest.main()
