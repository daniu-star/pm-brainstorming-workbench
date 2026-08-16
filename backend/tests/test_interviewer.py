"""interviewer 单元测试：维度分类、问题计数只 +1 一次、dimensions_update 事件、18 题兜底。"""
import json
import tempfile
import unittest
from unittest.mock import patch

from core import interviewer
from core.interviewer import INTERVIEW_DIMENSIONS, _classify_dimension, run_interview_respond, run_interview_start
from db.session_store import SessionStore


def make_stream(chunks, captured=None):
    async def fake_stream(messages, **kwargs):
        if captured is not None:
            captured.append(messages)
        for c in chunks:
            yield c

    return fake_stream


async def collect_events(async_gen):
    events = []
    async for frame in async_gen:
        assert frame.startswith("data: ") and frame.endswith("\n\n")
        events.append(json.loads(frame[len("data: "):].strip()))
    return events


class ClassifyDimensionTests(unittest.TestCase):
    def test_keyword_matches(self):
        self.assertEqual(_classify_dimension("这个技术架构能扩展吗", []), "technical_risk")
        self.assertEqual(_classify_dimension("谁会付费，收入模式是什么", []), "business_loop")
        self.assertEqual(_classify_dimension("用户为什么要切换习惯", ["problem_validity"]), "user_adoption")

    def test_skips_covered_dimensions(self):
        self.assertEqual(
            _classify_dimension("技术风险和付费模式", ["technical_risk"]),
            "business_loop",
        )

    def test_returns_none_when_nothing_matches_or_all_covered(self):
        self.assertIsNone(_classify_dimension("zzz qqq", []))
        self.assertIsNone(_classify_dimension("技术架构", INTERVIEW_DIMENSIONS))

    def test_business_loop_replaces_legacy_key(self):
        self.assertIn("business_loop", INTERVIEW_DIMENSIONS)
        self.assertNotIn("business_viability", INTERVIEW_DIMENSIONS)
        self.assertEqual(len(INTERVIEW_DIMENSIONS), 6)


class InterviewFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(data_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_start_sets_count_once_and_emits_dimensions_update(self):
        session = self.store.create("测试", owner_email="u@example.com")
        self.store.update(session["id"], {"phase": "brainstorm"})
        captured = []
        fake = make_stream(["这个痛点真实存在吗？", "到底谁有这个问题？"], captured)
        with patch.object(interviewer, "llm_stream", new=fake), \
                patch.object(interviewer, "session_store", self.store):
            events = await collect_events(run_interview_start(session["id"]))

        stored = self.store.get(session["id"])
        self.assertEqual(stored["interview_question_count"], 1)
        self.assertEqual(stored["phase"], "interview")
        self.assertEqual(len(stored["interview_dimensions_covered"]), 1)
        self.assertEqual(stored["interview_dimensions_covered"][0], "problem_validity")

        types = [e["type"] for e in events]
        self.assertIn("dimensions_update", types)
        update = next(e for e in events if e["type"] == "dimensions_update")
        self.assertEqual(update["total"], 6)
        self.assertEqual(update["covered"], ["problem_validity"])
        # 事件类型向后兼容
        for expected in ("phase_change", "interview_start", "token", "role_done", "done"):
            self.assertIn(expected, types)

    async def test_respond_increments_count_exactly_once(self):
        session = self.store.create("测试", owner_email="u@example.com")
        self.store.update(session["id"], {
            "phase": "interview",
            "interview_question_count": 1,
            "interview_dimensions_covered": ["problem_validity"],
        })
        fake = make_stream(["追问：", "技术架构风险？"])
        with patch.object(interviewer, "llm_stream", new=fake), \
                patch.object(interviewer, "session_store", self.store):
            events = await collect_events(run_interview_respond(session["id"], "我的回答"))

        stored = self.store.get(session["id"])
        # 旧实现会双重递增到 3；修复后应恰好 +1。
        self.assertEqual(stored["interview_question_count"], 2)
        self.assertIn("technical_risk", stored["interview_dimensions_covered"])
        update = next(e for e in events if e["type"] == "dimensions_update")
        self.assertEqual(update["covered"], stored["interview_dimensions_covered"])
        self.assertEqual(update["total"], 6)

    async def test_respond_forces_end_at_question_limit(self):
        session = self.store.create("测试", owner_email="u@example.com")
        self.store.update(session["id"], {
            "phase": "interview",
            "interview_question_count": 18,
            "interview_dimensions_covered": list(INTERVIEW_DIMENSIONS),
        })
        captured = []
        fake = make_stream(["面试结束。"], captured)
        with patch.object(interviewer, "llm_stream", new=fake), \
                patch.object(interviewer, "session_store", self.store):
            await collect_events(run_interview_respond(session["id"], "最后一个回答"))

        system_prompt = captured[0][0]["content"]
        self.assertIn("结束面试", system_prompt)
        self.assertEqual(self.store.get(session["id"])["interview_question_count"], 19)

    async def test_stream_failure_yields_error_and_done(self):
        from core.llm_client import LLMUnavailableError

        session = self.store.create("测试", owner_email="u@example.com")
        self.store.update(session["id"], {
            "phase": "interview",
            "interview_question_count": 1,
        })

        async def broken_stream(messages, **kwargs):
            yield "已生成的"
            raise LLMUnavailableError("LLM 服务暂时不可用")

        with patch.object(interviewer, "llm_stream", new=broken_stream), \
                patch.object(interviewer, "session_store", self.store):
            events = await collect_events(run_interview_respond(session["id"], "回答"))

        types = [e["type"] for e in events]
        self.assertIn("error", types)
        self.assertEqual(types[-1], "done")
        stored = self.store.get(session["id"])
        assistant_msgs = [m for m in stored["messages"] if m["role"] == "assistant"]
        self.assertTrue(assistant_msgs)
        self.assertIn("[生成中断]", assistant_msgs[-1]["content"])
        # 失败回合不应推进问题计数
        self.assertEqual(stored["interview_question_count"], 1)


if __name__ == "__main__":
    unittest.main()
