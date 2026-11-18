"""澄清工作流测试（FR-01 / §4.3）。

覆盖：
- 正常 JSON → 一条澄清问题 + 候选 Brief 更新（unknown→proposed）+ 版本 +1 + 事件；
- 坏 JSON 修复一次后成功；两次不合规 → 确定性降级提问（带"（系统降级提问）"）；
- ``confirmed`` 永远只能由用户命令产生，模型不得改写已确认字段；
- 追问上限 2、clarify_round ≥ 6 时不再提问、run_clarify 不写库（事务归 runner）。

使用真实临时 SQLite + duck-typed ``FakeCtx``（runner 的 ``StepContext`` 尚未交付时也能跑）。
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.llm_client import LLMResult, LLMUsage
from domain.contracts import (
    BriefFieldStatus,
    BriefStatus,
    MessageStage,
    ProductState,
    RoleCode,
    RunStatus,
    SenderKind,
    TaskKind,
    new_id,
)
from domain.errors import ErrorCode
from repositories import session_repo
from repositories.database import dispose_engine, new_session, reset_for_tests, session_scope
from workflows.coach import (
    CLARIFY_ROUND_LIMIT,
    coach_question_plan,
    extract_json_object,
    parse_coach_output,
    run_clarify,
)


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #


class FakeLLM:
    """按顺序吐出预设文本，记录每次调用参数（``LLMClient.complete`` 契约替身）。"""

    def __init__(self, *texts: str):
        self._texts = list(texts)
        self.calls: list[dict] = []

    async def complete(
        self,
        *,
        messages,
        max_output_tokens=None,
        timeout_seconds=30.0,
        purpose="",
        json_schema=None,
        temperature=0.3,
    ):
        self.calls.append(
            {
                "messages": messages,
                "max_output_tokens": max_output_tokens,
                "timeout_seconds": timeout_seconds,
                "purpose": purpose,
                "json_schema": json_schema,
                "temperature": temperature,
                "shapes": [dict(message) for message in messages],
            }
        )
        if len(self._texts) > 1:
            text = self._texts.pop(0)
        else:
            text = self._texts[0] if self._texts else ""
        return LLMResult(
            text=text,
            usage=LLMUsage(prompt_tokens=120, completion_tokens=60, total_tokens=180),
            model="fake-model",
            latency_ms=1.0,
            attempts=1,
            finish_reason="stop",
        )


class FakeCtx:
    """``StepContext`` 的 duck-typed 替身（只提供 run_clarify 用到的字段）。"""

    def __init__(
        self,
        *,
        db,
        session,
        product_state,
        current_message,
        llm,
        clarify_round=None,
        task_kind=TaskKind.clarify,
        run_id=None,
    ):
        self.db = db
        self.session = session
        self.product_state = product_state
        self.current_message = current_message
        self.llm = llm
        self.task_kind = task_kind
        self.run = SimpleNamespace(id=run_id or new_id())
        self.deadline_epoch = time.time() + 60
        if clarify_round is not None:
            self.clarify_round = clarify_round


def coach_json(*, message="请说说这个产品最主要面向谁？", follow_ups=(), brief_updates=None, brief_ready=False):
    return json.dumps(
        {
            "message": message,
            "follow_ups": list(follow_ups),
            "brief_updates": brief_updates,
            "brief_ready": brief_ready,
        },
        ensure_ascii=False,
    )


REQUIRED_UPDATES = {
    "target_users": "独立开发者与小型内容团队",
    "usage_scenario": "通勤路上用手机记录灵感",
    "core_problem": "写作时间被会议切碎",
    "success_criteria": "每周稳定产出三篇",
    "current_alternatives": None,
    "product_form": None,
    "constraints": None,
}

ALL_UPDATES = {
    "target_users": "独立开发者",
    "usage_scenario": "写周报时",
    "core_problem": "周报耗时两小时",
    "current_alternatives": "手工整理备忘录",
    "product_form": "移动端网页",
    "constraints": "必须离线可用",
    "success_criteria": "每周三篇",
}


# --------------------------------------------------------------------------- #
# 问题计划（纯函数）
# --------------------------------------------------------------------------- #


class CoachQuestionPlanTests(unittest.TestCase):
    def setUp(self):
        from domain.contracts import empty_brief

        self.empty = empty_brief()

    def test_empty_brief_targets_first_required_field(self):
        plan = coach_question_plan(self.empty, clarify_round=0)
        self.assertEqual(plan, {"target_field": "target_users", "should_ask": True, "brief_ready": False})

    def test_required_fields_are_prioritised_over_optional(self):
        brief = self.empty.model_copy(deep=True)
        brief.target_users = brief.target_users.model_copy(
            update={"value": "独立开发者", "status": BriefFieldStatus.proposed}
        )
        plan = coach_question_plan(brief, clarify_round=1)
        self.assertEqual(plan["target_field"], "usage_scenario")

    def test_round_limit_stops_asking(self):
        plan = coach_question_plan(self.empty, clarify_round=CLARIFY_ROUND_LIMIT)
        self.assertFalse(plan["should_ask"])
        self.assertIsNotNone(plan["target_field"])

    def test_round_before_limit_keeps_asking(self):
        plan = coach_question_plan(self.empty, clarify_round=CLARIFY_ROUND_LIMIT - 1)
        self.assertTrue(plan["should_ask"])

    def test_ready_brief_does_not_ask(self):
        updates = {field: {"value": value, "status": "proposed"} for field, value in REQUIRED_UPDATES.items() if value}
        brief = self.empty.model_validate(
            {**self.empty.model_dump(), **updates}
        )
        plan = coach_question_plan(brief, clarify_round=0)
        self.assertTrue(plan["brief_ready"])
        self.assertFalse(plan["should_ask"])

    def test_negative_round_is_normalised(self):
        self.assertTrue(coach_question_plan(self.empty, clarify_round=-3)["should_ask"])


# --------------------------------------------------------------------------- #
# 输出解析
# --------------------------------------------------------------------------- #


class ParseOutputTests(unittest.TestCase):
    def test_parses_code_fenced_json(self):
        text = "```json\n" + coach_json() + "\n```"
        payload = extract_json_object(text)
        self.assertEqual(payload["message"], "请说说这个产品最主要面向谁？")

    def test_tolerates_surrounding_prose(self):
        payload = extract_json_object("好的，问题如下：\n" + coach_json() + "\n希望有帮助。")
        self.assertIn("follow_ups", payload)

    def test_empty_output_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_json_object("   ")

    def test_non_object_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_json_object("[1, 2, 3]")

    def test_extra_fields_are_forbidden(self):
        bad = json.dumps({"message": "问", "follow_ups": [], "brief_updates": None, "brief_ready": False, "extra": 1})
        with self.assertRaises(ValueError) as caught:
            parse_coach_output(bad)
        self.assertIn("extra", str(caught.exception))

    def test_valid_output_parses(self):
        parsed = parse_coach_output(coach_json(follow_ups=["那他们现在怎么解决？"]))
        self.assertEqual(parsed.follow_ups, ["那他们现在怎么解决？"])


# --------------------------------------------------------------------------- #
# run_clarify
# --------------------------------------------------------------------------- #


class RunClarifyTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "test.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")
        self.db = new_session()
        with session_scope() as seed_db:
            self.session_row = session_repo.create_session(
                seed_db,
                owner_email="coach@example.com",
                problem_statement="做一个自动写周报的工具",
            )
            self.session_id = self.session_row.id
            self.current_row = session_repo.add_message(
                seed_db,
                self.session_row,
                sender_kind=SenderKind.human,
                stage=MessageStage.clarify,
                content="我想要一个能自动汇总本周工作的工具",
            )
        # 重新读到当前事务可见的 session/state（弱隔离下与新 session 共用同一库）
        self.session = session_repo.get_session(self.db, self.session_id)
        self.state = session_repo.get_product_state(self.db, self.session_id)
        self.current = session_repo.get_message(self.db, self.session_id, self.current_row.id)

    def tearDown(self):
        self.db.close()
        dispose_engine()
        self._tmp.cleanup()

    # -- 辅助 ------------------------------------------------------------ #

    def make_ctx(self, llm, **kwargs):
        return FakeCtx(
            db=self.db,
            session=self.session,
            product_state=kwargs.pop("product_state", self.state),
            current_message=kwargs.pop("current_message", self.current),
            llm=llm,
            **kwargs,
        )

    def save_brief(self, brief_payload, *, version=2):
        with session_scope() as db:
            session = session_repo.get_session(db, self.session_id)
            state = session_repo.get_product_state(db, self.session_id)
            payload = state.model_dump(mode="json")
            payload["version"] = version
            payload["brief"] = brief_payload
            session_repo.save_product_state(db, session, ProductState.model_validate(payload))
        self.session = session_repo.get_session(self.db, self.session_id)
        self.state = session_repo.get_product_state(self.db, self.session_id)

    # -- 正常路径 -------------------------------------------------------- #

    async def test_valid_output_produces_question_and_proposed_brief(self):
        llm = FakeLLM(
            coach_json(
                message="这个工具主要给谁用？",
                follow_ups=["他们现在怎么做周报？", "最痛苦的是哪一步？"],
                brief_updates=REQUIRED_UPDATES,
            )
        )
        outcome = await run_clarify(self.make_ctx(llm))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(outcome.messages), 1)
        message = outcome.messages[0]
        self.assertEqual(message.sender_kind, SenderKind.agent)
        self.assertEqual(message.role_code, RoleCode.coach)
        self.assertEqual(message.stage, MessageStage.clarify)
        self.assertIn("这个工具主要给谁用？", message.content)
        self.assertIn("追问：", message.content)

        self.assertEqual(outcome.product_state.version, self.state.version + 1)
        self.assertEqual(outcome.result_product_version, outcome.product_state.version)
        brief = outcome.product_state.brief
        self.assertEqual(brief.target_users.value, "独立开发者与小型内容团队")
        self.assertEqual(brief.target_users.status, BriefFieldStatus.proposed)
        self.assertEqual(brief.usage_scenario.status, BriefFieldStatus.proposed)
        self.assertNotEqual(brief.status, BriefStatus.confirmed)
        # 候选值要挂到当前消息上，便于追溯
        self.assertIn(self.current.id, brief.target_users.source_message_ids)

        self.assertEqual(len(outcome.events), 1)
        self.assertEqual(outcome.events[0]["type"], "product_state.updated")
        self.assertEqual(
            outcome.events[0]["payload"]["product_version"], outcome.product_state.version
        )
        # 使用量透传给 runner
        self.assertEqual(outcome.usage["total_tokens"], 180)

        # 只调一次模型，并带上 JSON Schema 与 purpose
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["purpose"], "coach.clarify")
        self.assertIsNotNone(llm.calls[0]["json_schema"])
        self.assertEqual(llm.calls[0]["messages"][0]["role"], "system")

    async def test_ready_brief_is_marked_ready_but_never_confirmed(self):
        llm = FakeLLM(coach_json(brief_updates=ALL_UPDATES, brief_ready=True))
        outcome = await run_clarify(self.make_ctx(llm))

        brief = outcome.product_state.brief
        self.assertEqual(brief.status, BriefStatus.ready)
        # session 侧 brief_status 由 state 推导（command_service 负责），coach 不传
        self.assertIsNone(outcome.brief_status)
        for field in ALL_UPDATES:
            item = getattr(brief, field)
            self.assertEqual(item.status, BriefFieldStatus.proposed, field)
            self.assertTrue(item.value, field)
        self.assertFalse(any(brief.confirmed(field) for field in ALL_UPDATES))

    async def test_run_clarify_does_not_persist_state(self):
        """事务由 runner/command_service 负责：workflow 只读库。"""
        llm = FakeLLM(coach_json(brief_updates=REQUIRED_UPDATES))
        await run_clarify(self.make_ctx(llm))

        persisted = session_repo.get_product_state(self.db, self.session_id)
        self.assertEqual(persisted.version, 1)

    # -- 修复与降级 ------------------------------------------------------ #

    async def test_bad_json_is_repaired_once(self):
        llm = FakeLLM("这不是 JSON", coach_json(message="修复后的主问题"))
        outcome = await run_clarify(self.make_ctx(llm))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("修复后的主问题", outcome.messages[0].content)
        self.assertTrue(any("修复一次" in warning for warning in outcome.warnings))
        # 修复轮把原始输出与校验错误回喂给模型
        repair_shapes = llm.calls[1]["shapes"]
        self.assertEqual(repair_shapes[-2]["role"], "assistant")
        self.assertEqual(repair_shapes[-1]["role"], "user")
        self.assertIn("JSON", repair_shapes[-1]["content"])

    async def test_extra_field_is_repaired_once(self):
        bad = json.dumps(
            {
                "message": "问一句",
                "follow_ups": [],
                "brief_updates": None,
                "brief_ready": False,
                "unexpected": "x",
            }
        )
        llm = FakeLLM(bad, coach_json(message="合规的主问题"))
        outcome = await run_clarify(self.make_ctx(llm))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("合规的主问题", outcome.messages[0].content)

    async def test_two_bad_outputs_fall_back_deterministically(self):
        llm = FakeLLM("仍然不是 JSON", "还是不是 JSON")
        outcome = await run_clarify(self.make_ctx(llm))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(llm.calls), 2)
        content = outcome.messages[0].content
        self.assertTrue(content.endswith("（系统降级提问）"), content)
        self.assertIn("目标用户", content)
        self.assertTrue(any("降级" in warning for warning in outcome.warnings))
        self.assertTrue(any("仍不符合输出协议" in warning for warning in outcome.warnings))
        # 降级也不改 Brief
        self.assertEqual(outcome.product_state.brief.target_users.value, None)

    # -- 追问上限 -------------------------------------------------------- #

    async def test_follow_ups_are_truncated_to_two(self):
        llm = FakeLLM(
            coach_json(follow_ups=["追问一", "追问二", "追问三", "追问四"])
        )
        outcome = await run_clarify(self.make_ctx(llm))

        content = outcome.messages[0].content
        self.assertIn("1. 追问一", content)
        self.assertIn("2. 追问二", content)
        self.assertNotIn("追问三", content)
        self.assertNotIn("追问四", content)
        self.assertTrue(any("截断" in warning for warning in outcome.warnings))

    # -- confirmed 保护 -------------------------------------------------- #

    async def test_confirmed_field_is_never_overwritten_by_model(self):
        self.save_brief(
            {
                "target_users": {
                    "value": "已由用户确认的用户群",
                    "status": BriefFieldStatus.confirmed.value,
                    "source_message_ids": ["m1"],
                },
            }
        )
        llm = FakeLLM(
            coach_json(
                brief_updates={"target_users": "模型擅自改写的用户群"},
            )
        )
        outcome = await run_clarify(self.make_ctx(llm))

        target = outcome.product_state.brief.target_users
        self.assertEqual(target.value, "已由用户确认的用户群")
        self.assertEqual(target.status, BriefFieldStatus.confirmed)
        self.assertTrue(any("已由用户确认" in warning for warning in outcome.warnings))
        self.assertNotEqual(outcome.product_state.brief.status, BriefStatus.confirmed)

    async def test_model_cannot_claim_ready_with_missing_required_fields(self):
        llm = FakeLLM(coach_json(brief_ready=True))  # 不带 brief_updates，必填仍缺
        outcome = await run_clarify(self.make_ctx(llm))

        self.assertTrue(any("仍有缺口" in warning for warning in outcome.warnings))
        self.assertFalse(outcome.product_state.brief.ready())

    # -- 轮次上限 -------------------------------------------------------- #

    async def test_round_limit_closes_without_calling_llm(self):
        llm = FakeLLM(coach_json())
        outcome = await run_clarify(self.make_ctx(llm, clarify_round=CLARIFY_ROUND_LIMIT))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(llm.calls, [])
        self.assertIn("先停在这里", outcome.messages[0].content)
        self.assertEqual(outcome.product_state.version, self.state.version + 1)
        # 收敛路径同样不猜 phase/brief_status；必填不齐时 brief 仍是 draft
        self.assertIsNone(outcome.brief_status)
        self.assertIsNone(outcome.session_phase)
        self.assertEqual(outcome.product_state.brief.status, BriefStatus.draft)

    async def test_ready_brief_closes_without_calling_llm_and_marks_ready(self):
        self.save_brief(
            {
                field: {
                    "value": value,
                    "status": BriefFieldStatus.proposed.value,
                    "source_message_ids": ["m1"],
                }
                for field, value in REQUIRED_UPDATES.items()
                if value is not None
            }
        )
        self.assertTrue(self.state.brief.ready())

        llm = FakeLLM(coach_json())
        outcome = await run_clarify(self.make_ctx(llm, clarify_round=CLARIFY_ROUND_LIMIT))

        self.assertEqual(llm.calls, [])
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(
            outcome.product_state.brief.status, BriefStatus.ready
        )
        self.assertNotEqual(outcome.product_state.brief.status, BriefStatus.confirmed)
        self.assertIsNone(outcome.brief_status)
        self.assertIsNone(outcome.session_phase)
        self.assertEqual(outcome.product_state.version, self.state.version + 1)
        self.assertIn("如还有可补充的约束", outcome.messages[0].content)

    # -- 失败路径 -------------------------------------------------------- #

    async def test_missing_product_state_returns_failed_outcome(self):
        ctx = self.make_ctx(FakeLLM(coach_json()), product_state=None)
        outcome = await run_clarify(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_REQUEST)

    async def test_llm_error_propagates_for_runner_retry(self):
        class BoomLLM:
            async def complete(self, **_kwargs):
                raise RuntimeError("upstream down")

        with self.assertRaises(RuntimeError):
            await run_clarify(self.make_ctx(BoomLLM()))


if __name__ == "__main__":
    unittest.main()
