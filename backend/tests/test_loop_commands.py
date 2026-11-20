"""Loop Studio 命令层验收：start_loop / loop_decision 的相位、版本、轮次与收口语义。"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import json

from sqlalchemy import func, select

import domain.contracts as C
import domain.state_machine as SM
from domain.contracts import Action, CommandRequest, LoopDecisionKind, LoopStatus, RunStatus
from domain.errors import AppError, ErrorCode
from repositories import database, loop_repo, run_repo, session_repo
from repositories.database import LoopRow, LoopRoundRow, RunRow, session_scope
from services import command_service

NOW_ISO = "2026-11-19T09:00:00Z"
NOW_EPOCH = 1_764_224_400
#: 每条命令推进一个限流窗口，避免同一测试类内的 20 次/分钟上限互相干扰。
CLOCK = {"epoch": NOW_EPOCH}
OWNER = "loop-owner@example.com"


class LoopCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 测试结束会清理 .tmp_tests；从干净工作区重跑时必须能自建该目录，
        # 否则模块加载即 FileNotFoundError（与 test_loop_runner 保持一致）。
        os.makedirs(".tmp_tests", exist_ok=True)
        cls._tmpdir = tempfile.mkdtemp(prefix="loop_cmd_", dir=os.path.abspath(".tmp_tests"))
        cls._db_url = "sqlite+pysqlite:///" + os.path.join(cls._tmpdir, "loop.db").replace("\\", "/")
        database.configure(cls._db_url, force=True)
        database.initialize_database(database.get_engine(), drop_first=True)

    @classmethod
    def tearDownClass(cls):
        database.dispose_engine()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ 夹具

    def setUp(self):
        """清掉上一个用例遗留的非终结 Run（共享库 + 单 owner 的排队上限 3）。"""

        with session_scope() as db:
            rows = db.execute(select(RunRow).where(RunRow.owner_email == OWNER)).scalars().all()
            for row in rows:
                if not SM.is_terminal_run(RunStatus(row.status)):
                    command_service.cancel_run(db, owner_email=OWNER, run_id=row.id, now_iso=NOW_ISO)

    def _new_session(self, owner: str = OWNER) -> str:
        with session_scope() as db:
            session = session_repo.create_session(
                db,
                session_id=C.new_id(),
                owner_email=owner,
                problem_statement="闭环命令验收：预算协同工具",
                now_iso=NOW_ISO,
            )
            return session.id

    def _submit(self, session_id: str, action: Action, payload: dict, *, owner: str = OWNER, version: int | None = None):
        CLOCK["epoch"] += 70
        epoch = CLOCK["epoch"]
        with session_scope() as db:
            command = CommandRequest(
                request_id=C.new_request_id(),
                action=action,
                expected_session_version=version,
                payload=payload,
            )
            return command_service.submit_command(
                db,
                owner_email=owner,
                session_id=session_id,
                command=command,
                now_iso=NOW_ISO,
                now_epoch=epoch,
            )

    def _advance_to_brainstorming(self, session_id: str) -> None:
        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            state = session_repo.get_product_state(db, session_id)
            self._submit(
                session_id,
                Action.revise_brief,
                {
                    "expected_product_version": state.version,
                    "fields": {
                        "target_users": {"value": "中小企业财务团队", "status": "proposed"},
                        "usage_scenario": {"value": "月度预算协同与审批", "status": "proposed"},
                        "core_problem": {"value": "预算协作靠表格来回传", "status": "proposed"},
                        "success_criteria": {"value": "月度预算对齐时间缩短一半", "status": "proposed"},
                    },
                },
            )
            state = session_repo.get_product_state(db, session_id)
            self._submit(
                session_id,
                Action.confirm_brief,
                {"expected_product_version": state.version, "fields": {}},
            )

    def _start_loop(self, session_id: str, **overrides) -> str:
        payload = {
            "scope": "mvp",
            "max_rounds": 3,
            "target_score": 85,
            "focus": "先做预算协同主流程",
            "expected_product_version": None,
        }
        payload.update(overrides)
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        payload["expected_product_version"] = payload.get("expected_product_version") or state.version
        receipt = self._submit(session_id, Action.start_loop, payload)
        return receipt

    def _release_active(self, run_id: str) -> None:
        """把入队后占位的 Run 终结掉，模拟 Worker 已消费完本轮（释放 active_run_id）。"""

        with session_scope() as db:
            command_service.cancel_run(db, owner_email=OWNER, run_id=run_id, now_iso=NOW_ISO)

    @staticmethod
    def _loop_row(loop_id: str) -> LoopRow:
        with session_scope() as db:
            row = loop_repo.get_loop(db, loop_id)
            assert row is not None
            db.expunge(row)
            return row

    @staticmethod
    def _rounds(loop_id: str) -> list[LoopRoundRow]:
        with session_scope() as db:
            rows = loop_repo.list_rounds(db, loop_id)
            for row in rows:
                db.expunge(row)
            return rows

    # ------------------------------------------------------------------ 用例

    def test_start_loop_creates_loop_and_first_round_run(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self.assertEqual(receipt.action, Action.start_loop)
        self.assertIsNotNone(receipt.loop_id)
        loop = self._loop_row(receipt.loop_id)
        self.assertEqual(loop.status, LoopStatus.running.value)
        self.assertEqual(loop.current_round, 1)
        self.assertEqual(loop.max_rounds, 3)
        self.assertEqual(loop.target_score, 85)
        self.assertIn("预算协同", loop.focus)
        rounds = self._rounds(loop.id)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].ordinal, 1)
        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            self.assertEqual(run.action, Action.start_loop.value)
            self.assertEqual(run.loop_id, loop.id)
            budget = json.loads(run.budget_json)
            self.assertEqual(budget["loop_id"], loop.id)
            self.assertEqual(budget["loop_round"], 1)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertEqual(session.active_run_id, receipt.run_id)

    def test_start_loop_twice_returns_loop_active(self):
        """存在未关闭闭环（含 awaiting_decision）时不能再开新闭环。

        这里显式把 loop 推进到 awaiting_decision（一轮结束后的真实形态），
        不再依赖「取消 Run 后 loop 仍是 running」这一缺陷语义；取消路径的
        回归见 test_cancel_run_releases_loop_and_allows_restart（AUD-01）。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        first = self._start_loop(sid)
        self._release_active(first.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, first.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision, now_iso=NOW_ISO)
        with self.assertRaises(AppError) as ctx:
            self._start_loop(sid)
        self.assertEqual(ctx.exception.code, ErrorCode.LOOP_ACTIVE)
        self.assertIn("loop_id", ctx.exception.details or {})

    def test_start_loop_version_conflict(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        with session_scope() as db:
            state = session_repo.get_product_state(db, sid)
        stale = state.version - 1
        with self.assertRaises(AppError) as ctx:
            self._submit(sid, Action.start_loop, {"expected_product_version": stale})
        self.assertEqual(ctx.exception.code, ErrorCode.VERSION_CONFLICT)

    def test_loop_actions_are_phase_gated(self):
        sid = self._new_session()
        # clarifying 阶段不允许 start_loop
        with self.assertRaises(AppError) as ctx:
            self._submit(sid, Action.start_loop, {"expected_product_version": 1})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertIn("clarifying", (ctx.exception.message or "") + str(ctx.exception.details))

    def test_decision_requires_awaiting_decision(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        loop = self._loop_row(receipt.loop_id)
        with self.assertRaises(AppError) as ctx:
            self._submit(
                sid,
                Action.loop_decision,
                {"loop_id": loop.id, "expected_loop_version": loop.loop_version, "decision": "ship"},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.LOOP_NOT_AWAITING_DECISION)

    def test_decision_version_conflict(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision, loop_version=loop.loop_version)
            current = int(loop.loop_version)
        with self.assertRaises(AppError) as ctx:
            self._submit(
                sid,
                Action.loop_decision,
                {"loop_id": receipt.loop_id, "expected_loop_version": current - 1, "decision": "ship"},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.LOOP_VERSION_CONFLICT)

    def test_ship_decision_closes_loop_synchronously(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision)
            version = int(loop.loop_version)
        decision = self._submit(
            sid,
            Action.loop_decision,
            {"loop_id": receipt.loop_id, "expected_loop_version": version, "decision": "ship", "note": "先交付主流程"},
        )
        self.assertEqual(decision.status, C.RunStatus.succeeded)
        loop = self._loop_row(receipt.loop_id)
        self.assertEqual(loop.status, LoopStatus.shipped.value)
        self.assertEqual(loop.decision, LoopDecisionKind.ship.value)
        self.assertIsNotNone(loop.closed_at)
        self.assertEqual(loop.decision_note, "先交付主流程")
        self.assertEqual(loop.loop_version, version + 1)
        with session_scope() as db:
            snapshot = loop_repo.loop_to_snapshot(db, loop_repo.get_loop(db, receipt.loop_id))
        self.assertEqual(snapshot.decision, LoopDecisionKind.ship)
        self.assertEqual(snapshot.decision_note, "先交付主流程")
        self.assertEqual(snapshot.status, LoopStatus.shipped)
        self.assertIsNotNone(snapshot.closed_at)
        with session_scope() as db:
            events = run_repo.events_after(db, run_id=decision.run_id)
            types = [event.type for event in events]
            self.assertEqual(types[0], C.EventType.run_queued.value)
            self.assertIn(C.EventType.loop_updated.value, types)
            self.assertEqual(len([t for t in types if t in C.TERMINAL_EVENT_TYPES]), 1)
            # 决策事件必须反映“决策之后”的状态与版本，否则前端版本守卫会丢弃它（P2）
            loop_events = [e for e in events if e.type == C.EventType.loop_updated.value]
            payload = json.loads(loop_events[-1].payload_json)
            self.assertEqual(payload["status"], LoopStatus.shipped.value)
            self.assertEqual(int(payload["loop_version"]), version + 1)
            self.assertEqual(payload["current_round"], int(loop.current_round))
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertIsNone(session.active_run_id)
            messages = session_repo.list_messages(db, sid)
            self.assertTrue(any("可交付" in (row.content or "") for row in messages))

    def _seed_loop_decision(self, session_id: str, run_id: str) -> int:
        """写入一条与该闭环轮次关联的 proposed 决策，返回新的产品状态版本。"""

        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            state = session_repo.get_product_state(db, session_id)
            state.decisions = [
                *state.decisions,
                C.DecisionItem(
                    id=C.new_id(),
                    rationale="第 1 轮闭环评分 88 ≥ 85，建议按主流程交付",
                    status=C.DecisionStatus.proposed,
                    source_run_id=run_id,
                ),
            ]
            state.version = state.version + 1
            session_repo.save_product_state(db, session, state, now_iso=NOW_ISO)
            return state.version

    def test_ship_decision_confirms_loop_decisions_and_bumps_product_version(self):
        """PRD-04：交付即签字，本闭环轮次生成的 proposed 决策必须收口为 confirmed。

        修复前 ship 返回 ``product_state=None``：产品状态不 bump、决策仍 proposed，
        画布继续显示「确认决策」，与「闭环已交付」自相矛盾。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        seeded_version = self._seed_loop_decision(sid, receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision)
            loop_version = int(loop.loop_version)

        decision = self._submit(
            sid,
            Action.loop_decision,
            {"loop_id": receipt.loop_id, "expected_loop_version": loop_version, "decision": "ship"},
        )
        self.assertEqual(decision.status, C.RunStatus.succeeded)

        with session_scope() as db:
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            state = session_repo.get_product_state(db, sid)
        self.assertEqual(session.current_product_version, seeded_version + 1)
        self.assertEqual(state.version, seeded_version + 1)
        seeded = [item for item in state.decisions if item.source_run_id == receipt.run_id]
        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0].status, C.DecisionStatus.confirmed)
        self.assertFalse(seeded[0].needs_review)
        self.assertEqual(seeded[0].confirmed_by, OWNER)
        self.assertIsNotNone(seeded[0].confirmed_at)

    def test_stop_decision_does_not_confirm_loop_decisions(self):
        """停止闭环不等于签字交付：proposed 决策保持 proposed，产品状态不 bump。"""

        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        seeded_version = self._seed_loop_decision(sid, receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision)
            loop_version = int(loop.loop_version)

        self._submit(
            sid,
            Action.loop_decision,
            {"loop_id": receipt.loop_id, "expected_loop_version": loop_version, "decision": "stop"},
        )

        with session_scope() as db:
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            state = session_repo.get_product_state(db, sid)
        self.assertEqual(session.current_product_version, seeded_version)
        seeded = [item for item in state.decisions if item.source_run_id == receipt.run_id]
        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0].status, C.DecisionStatus.proposed)
        self.assertIsNone(seeded[0].confirmed_by)

    def test_stop_decision_closes_loop(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision)
            version = int(loop.loop_version)
        self._submit(
            sid,
            Action.loop_decision,
            {"loop_id": receipt.loop_id, "expected_loop_version": version, "decision": "stop"},
        )
        loop = self._loop_row(receipt.loop_id)
        self.assertEqual(loop.status, LoopStatus.stopped.value)
        self.assertEqual(loop.decision, LoopDecisionKind.stop.value)

    def test_iterate_decision_creates_next_round_run(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid, max_rounds=3)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision, current_round=1)
            version = int(loop.loop_version)
        next_receipt = self._submit(
            sid,
            Action.loop_decision,
            {"loop_id": receipt.loop_id, "expected_loop_version": version, "decision": "iterate"},
        )
        self.assertEqual(next_receipt.action, Action.loop_decision)
        self.assertNotEqual(next_receipt.run_id, receipt.run_id)
        loop = self._loop_row(receipt.loop_id)
        self.assertEqual(loop.status, LoopStatus.running.value)
        self.assertEqual(loop.current_round, 2)
        self.assertEqual(loop.latest_run_id, next_receipt.run_id)
        self.assertEqual(len(self._rounds(loop.id)), 2)
        with session_scope() as db:
            run = run_repo.get_run(db, next_receipt.run_id)
            self.assertEqual(json.loads(run.budget_json)["loop_round"], 2)

    def test_iterate_note_is_carried_into_next_round(self):
        """AUD-03 回归：iterate 时填写的改进备注必须进入下一轮 Run 的预算与 prompt。

        修复前 iterate 分支不读 payload["note"]，备注被静默丢弃，下一轮 prompt
        看不到用户要求。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid, max_rounds=3)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision, current_round=1)
            version = int(loop.loop_version)
        note = "补齐登录态用例后再交付"
        next_receipt = self._submit(
            sid,
            Action.loop_decision,
            {
                "loop_id": receipt.loop_id,
                "expected_loop_version": version,
                "decision": "iterate",
                "note": note,
            },
        )
        with session_scope() as db:
            run = run_repo.get_run(db, next_receipt.run_id)
            budget = json.loads(run.budget_json)
        self.assertEqual(budget.get("loop_decision_note"), note)

        from core.loop_prompts import build_stage_messages

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.budget = budget
        ctx.product_state = None
        ctx.evidence = None
        messages = build_stage_messages(ctx, stage=C.LoopStage.ideate, prior={})
        self.assertIn(note, messages[1].content)

    def test_scope_is_injected_into_stage_prompt(self):
        from core.loop_prompts import build_stage_messages

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.budget = {"loop_scope": "optimize", "loop_focus": "留存"}
        ctx.product_state = None
        ctx.evidence = None
        messages = build_stage_messages(ctx, stage=C.LoopStage.ideate, prior={})
        self.assertIn("既有产品优化", messages[1].content, "scope 必须真正影响提示词")

        ctx.budget = {"loop_scope": "", "loop_focus": ""}
        messages = build_stage_messages(ctx, stage=C.LoopStage.ideate, prior={})
        self.assertNotIn("本轮范围", messages[1].content)

    def test_iterate_beyond_max_rounds_is_rejected(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid, max_rounds=1)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision, current_round=1)
            version = int(loop.loop_version)
        with self.assertRaises(AppError) as ctx:
            self._submit(
                sid,
                Action.loop_decision,
                {"loop_id": receipt.loop_id, "expected_loop_version": version, "decision": "iterate"},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.LOOP_ROUNDS_EXHAUSTED)

    def test_loop_decision_is_idempotent_on_replay(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision)
            version = int(loop.loop_version)
        request_id = C.new_request_id()
        payload = {"loop_id": receipt.loop_id, "expected_loop_version": version, "decision": "ship"}
        first = self._submit(sid, Action.loop_decision, dict(payload, request_id=request_id)) if False else None
        with session_scope() as db:
            CLOCK["epoch"] += 70
            command = CommandRequest(request_id=request_id, action=Action.loop_decision, payload=payload)
            first = command_service.submit_command(
                db, owner_email=OWNER, session_id=sid, command=command, now_iso=NOW_ISO, now_epoch=CLOCK["epoch"]
            )
        with session_scope() as db:
            CLOCK["epoch"] += 70
            command = CommandRequest(request_id=request_id, action=Action.loop_decision, payload=payload)
            second = command_service.submit_command(
                db, owner_email=OWNER, session_id=sid, command=command, now_iso=NOW_ISO, now_epoch=CLOCK["epoch"]
            )
        self.assertEqual(second.run_id, first.run_id)
        self.assertTrue(second.reused)
        with session_scope() as db:
            count = db.execute(
                select(func.count())
                .select_from(RunRow)
                .where(RunRow.action == "loop_decision", RunRow.session_id == sid)
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_other_owner_cannot_decision(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._release_active(receipt.run_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            assert loop is not None
            loop_repo.touch_loop(db, loop, status=LoopStatus.awaiting_decision)
            version = int(loop.loop_version)
        with self.assertRaises(AppError) as ctx:
            self._submit(
                sid,
                Action.loop_decision,
                {"loop_id": receipt.loop_id, "expected_loop_version": version, "decision": "ship"},
                owner="intruder@example.com",
            )
        self.assertIn(ctx.exception.code, (ErrorCode.FORBIDDEN, ErrorCode.NOT_FOUND))

    def test_session_view_exposes_current_loop(self):
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        with session_scope() as db:
            view = command_service.session_to_view(db, session_repo.require_session(db, sid, owner_email=OWNER))
        self.assertEqual(view.current_loop_id, receipt.loop_id)

    # ------------------------------------------------------------------ AUD-01

    def test_cancel_run_releases_loop_and_allows_restart(self):
        """P0 回归（AUD-01）：中止本轮必须收口闭环，用户能重新开启闭环。

        修复前：cancel_run 只终结 Run，不写 loop 行；loop 仍是 running，
        active_loop_for_session 命中，再 start_loop 返回 LOOP_ACTIVE——页面
        「中止本轮」后既不能决策也不能开新闭环，只能改库恢复。

        现在 cancel_run 会收口 loop，因此断言：loop 离开 running、closed_at 落库、
        active_loop_for_session 为 None、新 start_loop 成功。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        first = self._start_loop(sid)
        self._release_active(first.run_id)

        loop = self._loop_row(first.loop_id)
        self.assertNotEqual(loop.status, LoopStatus.running.value)
        self.assertIsNotNone(loop.closed_at, "闭环收口必须写 closed_at，前端据此进入 finished 分支")

        with session_scope() as db:
            self.assertIsNone(loop_repo.active_loop_for_session(db, sid))

        second = self._start_loop(sid)
        self.assertNotEqual(second.loop_id, first.loop_id)

    def test_deadline_failure_without_apply_closes_loop(self):
        """P0 兜底（AUD-01）：没有 apply 的失败终结也必须收口闭环。

        runner 的 deadline / 内部异常走 ``_finalize_failure``，其 outcome 不带
        apply；若只依赖 loop workflow 的 apply，闭环会永久停在 running。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            assert run is not None
            run.status = RunStatus.running.value
            run.started_at = NOW_ISO
            db.flush()
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            command_service._commit_outcome(
                db,
                run=run,
                session=session,
                outcome=command_service.RunOutcome(
                    status=RunStatus.failed,
                    error_code=ErrorCode.RUN_DEADLINE_EXCEEDED,
                    error_message="运行超时",
                ),
                now_iso=NOW_ISO,
                lease_required=False,
            )

        loop = self._loop_row(receipt.loop_id)
        self.assertEqual(loop.status, LoopStatus.failed.value)
        self.assertIsNotNone(loop.closed_at)
        with session_scope() as db:
            self.assertIsNone(loop_repo.active_loop_for_session(db, sid))

    def test_cancel_requested_run_emits_run_cancelled_event(self):
        """P0 回归（AUD-01）：running → cancel_requested 的事件必须是 run_cancelled。

        修复前 cancel_run 的 running 分支误用 EventType.audit_updated，SSE 订阅方
        与审计视图会把「取消 Run」当成审计更新。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            assert run is not None
            run.status = RunStatus.running.value
            run.started_at = NOW_ISO
            db.flush()
            view = command_service.cancel_run(
                db, owner_email=OWNER, run_id=receipt.run_id, now_iso=NOW_ISO
            )
        self.assertEqual(view.status, RunStatus.cancel_requested.value)
        # Worker 收尾时不得再追加第二个 run_cancelled（cancel_run 已写过一次）。
        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            assert run is not None
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            command_service._commit_outcome(
                db,
                run=run,
                session=session,
                outcome=command_service.RunOutcome(
                    status=RunStatus.cancelled,
                    error_message="收到取消请求，任务已终止",
                ),
                now_iso=NOW_ISO,
                lease_required=False,
            )
        with session_scope() as db:
            types = [event.type for event in run_repo.events_after(db, receipt.run_id)]
        self.assertEqual(types.count(C.EventType.run_cancelled.value), 1)
        self.assertIn(C.EventType.run_cancelled.value, types)
        self.assertNotIn(C.EventType.audit_updated.value, types)

    # ------------------------------------------------------- 排队超时（P1 回归）

    def test_queue_timeout_closes_loop_and_allows_restart(self):
        """P1 回归：排队超时是终结态，必须同步收口闭环与会话占位。

        修复前 ``expire_queued_runs`` 只把 Run 置 failed：``loop.status`` 仍
        running、``round.status`` 仍 running、``session.active_run_id`` 仍指向
        失败 Run，于是 ``start_loop`` → 409 LOOP_ACTIVE、``loop_decision`` →
        409 LOOP_NOT_AWAITING_DECISION，闭环永久死锁。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        first = self._start_loop(sid)
        with session_scope() as db:
            run = run_repo.get_run(db, first.run_id)
            assert run is not None
            run.queue_deadline_at = NOW_EPOCH - 1
            db.flush()
            expired = run_repo.expire_queued_runs(db, now_epoch=NOW_EPOCH, now_iso=NOW_ISO)
            self.assertEqual(expired, [first.run_id])
            failed = run_repo.get_run(db, first.run_id)
            self.assertEqual(failed.status, RunStatus.failed.value)
            self.assertEqual(failed.error_code, ErrorCode.QUEUE_WAIT_EXCEEDED)
            types = [event.type for event in run_repo.events_after(db, first.run_id)]
            self.assertIn(C.EventType.run_failed.value, types)
            self.assertIn(C.EventType.loop_updated.value, types)

        loop = self._loop_row(first.loop_id)
        self.assertEqual(loop.status, LoopStatus.failed.value)
        self.assertIsNotNone(loop.closed_at, "闭环收口必须写 closed_at")
        rounds = self._rounds(first.loop_id)
        self.assertEqual(rounds[0].status, "failed")
        with session_scope() as db:
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertIsNone(session.active_run_id)
            self.assertIsNone(run_repo.active_run_for_session(db, sid))
            self.assertIsNone(loop_repo.active_loop_for_session(db, sid))

        # 用户可恢复：直接开启新闭环（不再 409 LOOP_ACTIVE）。
        second = self._start_loop(sid)
        self.assertNotEqual(second.loop_id, first.loop_id)

    def test_retry_failed_loop_run_inherits_loop_and_reopens_round(self):
        """P1 回归：重试闭环轮次 Run 必须回到闭环内，且能重新收口。

        修复前子 Run 的 ``loop_id=None``：取消子 Run 时 ``_abort_loop_for_run``
        找不到闭环，闭环/轮次永远停在 running。
        """
        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        first = self._start_loop(sid)
        with session_scope() as db:
            run = run_repo.get_run(db, first.run_id)
            assert run is not None
            run.status = RunStatus.running.value
            run.started_at = NOW_ISO
            db.flush()
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            command_service._commit_outcome(
                db,
                run=run,
                session=session,
                outcome=command_service.RunOutcome(
                    status=RunStatus.failed,
                    error_code=ErrorCode.MODEL_OUTPUT_INVALID,
                    error_message="阶段执行失败",
                ),
                now_iso=NOW_ISO,
                lease_required=False,
            )
        self.assertEqual(self._loop_row(first.loop_id).status, LoopStatus.failed.value)

        with session_scope() as db:
            receipt = command_service.retry_run(
                db,
                owner_email=OWNER,
                run_id=first.run_id,
                request_id=C.new_request_id(),
                now_iso=NOW_ISO,
                now_epoch=CLOCK["epoch"],
            )
            self.assertEqual(receipt.loop_id, first.loop_id)
            child = run_repo.get_run(db, receipt.run_id)
            self.assertEqual(child.loop_id, first.loop_id)
            self.assertEqual(child.parent_run_id, first.run_id)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertEqual(session.active_run_id, child.id)
            types = [event.type for event in run_repo.events_after(db, child.id)]
            self.assertIn(C.EventType.loop_updated.value, types)

        loop = self._loop_row(first.loop_id)
        self.assertEqual(loop.status, LoopStatus.running.value)
        self.assertIsNone(loop.closed_at)
        self.assertEqual(loop.latest_run_id, receipt.run_id)
        rounds = self._rounds(first.loop_id)
        self.assertEqual(rounds[0].status, "running")
        self.assertEqual(rounds[0].run_id, receipt.run_id)

        # 重试后的子 Run 是 latest_run_id：取消它能再次收口闭环（原缺陷闭环仍 running）。
        with session_scope() as db:
            command_service.cancel_run(
                db, owner_email=OWNER, run_id=receipt.run_id, now_iso=NOW_ISO
            )
        loop = self._loop_row(first.loop_id)
        self.assertEqual(loop.status, LoopStatus.failed.value)
        self.assertIsNotNone(loop.closed_at)
        rounds = self._rounds(first.loop_id)
        self.assertEqual(rounds[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
