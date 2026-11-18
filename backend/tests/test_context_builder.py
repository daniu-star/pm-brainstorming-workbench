"""上下文构建测试（FR-02）。

对应验收：AC-08（注入顺序固定）、AC-09（当前输入不重复）、AC-10（受保护定义不被压缩移除）、
AC-11（超预算抛 CONTEXT_TOO_LARGE 而不是静默截断）、AC-12（审计内部消息不外泄）。

全部使用真实临时 SQLite + 真实 ``repositories.session_repo``（不 mock 仓储层）。
"""
import tempfile
import unittest
from pathlib import Path

from domain.contracts import (
    BriefFieldStatus,
    BriefStatus,
    ContextBundle,
    ContextSection,
    MessageStage,
    ProductState,
    RoleCode,
    SenderKind,
    TaskKind,
    VerificationStatus,
)
from domain.errors import AppError, ErrorCode
from repositories import session_repo
from repositories.database import dispose_engine, reset_for_tests, session_scope
from services.context_builder import (
    INJECTION_ORDER,
    MAX_MESSAGE_CHARS,
    PROTECTED_BRIEF_FIELDS,
    build_context,
    estimate_tokens,
)


class ContextBuilderTestCase(unittest.TestCase):
    """每个用例独立临时库。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "test.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")

    def tearDown(self):
        dispose_engine()
        self._tmp.cleanup()

    # -- 造数据 ---------------------------------------------------------- #

    def seed_session(self, db, *, problem="做一个自动写周报的工具"):
        return session_repo.create_session(
            db, owner_email="coach@example.com", problem_statement=problem
        )

    def add_message(self, db, session, content, *, stage=MessageStage.clarify, sender=SenderKind.human):
        return session_repo.add_message(
            db, session, sender_kind=sender, stage=stage, content=content
        )

    def save_brief(self, db, session, brief_payload, *, version=2):
        state = session_repo.get_product_state(db, session.id)
        payload = state.model_dump(mode="json")
        payload["version"] = version
        payload["brief"] = brief_payload
        session_repo.save_product_state(db, session, ProductState.model_validate(payload))

    def required_tokens(self, db, session_id, **kwargs):
        """用极小预算触发 CONTEXT_TOO_LARGE，从 details 里取必需区 token 数。"""
        with self.assertRaises(AppError) as caught:
            build_context(
                db,
                session_id=session_id,
                task_kind=kwargs.pop("task_kind", TaskKind.clarify),
                role_code=kwargs.pop("role_code", RoleCode.coach),
                product_version=kwargs.pop("product_version", 1),
                max_input_tokens=1,
                **kwargs,
            )
        self.assertEqual(caught.exception.code, ErrorCode.CONTEXT_TOO_LARGE)
        return int(caught.exception.details["required_tokens"])

    def build(self, db, session_id, *, max_input_tokens=12_000, **kwargs):
        return build_context(
            db,
            session_id=session_id,
            task_kind=kwargs.pop("task_kind", TaskKind.clarify),
            role_code=kwargs.pop("role_code", RoleCode.coach),
            product_version=kwargs.pop("product_version", 1),
            max_input_tokens=max_input_tokens,
            **kwargs,
        )


# --------------------------------------------------------------------------- #
# estimate_tokens
# --------------------------------------------------------------------------- #


class EstimateTokensTests(unittest.TestCase):
    def test_empty_string_is_zero(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)

    def test_single_cjk_char_is_one_token(self):
        self.assertEqual(estimate_tokens("中"), 1)

    def test_ascii_uses_quarter_length(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcdefgh"), 2)

    def test_non_empty_text_is_at_least_one_token(self):
        self.assertGreaterEqual(estimate_tokens("a"), 1)
        self.assertGreaterEqual(estimate_tokens("中a"), 1)

    def test_mixed_text_counts_cjk_as_one_each(self):
        # 2 个 CJK + 3 个 ASCII：2 + 3//4 = 2
        self.assertEqual(estimate_tokens("中文abc"), 2)


# --------------------------------------------------------------------------- #
# 注入顺序
# --------------------------------------------------------------------------- #


class InjectionOrderTests(ContextBuilderTestCase):
    def test_sections_follow_the_declared_order(self):
        with session_scope() as db:
            session = self.seed_session(db)
            session_repo.add_evidence(
                db,
                session,
                kind="interview",
                title="用户访谈 1",
                excerpt="用户说周报要写两小时",
                source_uri="https://example.com/interview-1",
                verification_status=VerificationStatus.source_checked,
            )
            last = self.add_message(db, session, "我想要自动汇总")
            bundle = self.build(db, session.id, current_message_id=last.id)

        self.assertIsInstance(bundle, ContextBundle)
        # 出现的分节必须是 INJECTION_ORDER 的子序列
        self.assertEqual(
            bundle.included_sections,
            [section for section in INJECTION_ORDER if section in set(bundle.included_sections)],
        )
        self.assertEqual(bundle.included_sections[0], ContextSection.role_protocol)
        self.assertEqual(bundle.included_sections[-1], ContextSection.current_input)
        self.assertIn(ContextSection.product_definition, bundle.included_sections)
        self.assertIn(ContextSection.evidence, bundle.included_sections)
        self.assertIn(ContextSection.conversation, bundle.included_sections)
        # 消息排列顺序与分节一致
        self.assertEqual(
            [message.source_label for message in bundle.messages],
            [section.value for section in bundle.included_sections],
        )

    def test_role_protocol_is_system_message_with_role_prompt(self):
        with session_scope() as db:
            session = self.seed_session(db)
            bundle = self.build(db, session.id)

        system_messages = [m for m in bundle.messages if m.role == "system"]
        self.assertEqual(len(system_messages), 1)
        self.assertEqual(system_messages[0].source_label, ContextSection.role_protocol.value)
        self.assertIn("教练", system_messages[0].content)
        self.assertEqual(bundle.role_code, RoleCode.coach)

    def test_other_sections_are_user_messages_with_titles(self):
        with session_scope() as db:
            session = self.seed_session(db)
            bundle = self.build(db, session.id)

        non_system = [m for m in bundle.messages if m.role != "system"]
        self.assertTrue(non_system)
        for message in non_system:
            self.assertTrue(message.content.startswith("## "), message.content[:20])
            self.assertIsNotNone(message.source_label)

    def test_prompt_version_is_recorded(self):
        with session_scope() as db:
            session = self.seed_session(db)
            bundle = self.build(db, session.id, prompt_version="v1")

        self.assertEqual(bundle.prompt_version, "v1")


# --------------------------------------------------------------------------- #
# 当前输入
# --------------------------------------------------------------------------- #


class CurrentInputTests(ContextBuilderTestCase):
    def test_current_message_is_not_duplicated_in_conversation(self):
        with session_scope() as db:
            session = self.seed_session(db)
            marker = "我不喜欢目前的周报模板-7f3a"
            current = self.add_message(db, session, marker)
            bundle = self.build(db, session.id, current_message_id=current.id)

        self.assertEqual(bundle.source_message_ids.count(current.id), 1)
        body = "\n".join(message.content for message in bundle.messages)
        self.assertEqual(body.count(marker), 1)

    def test_without_current_message_has_no_current_input_section(self):
        with session_scope() as db:
            session = self.seed_session(db)
            bundle = self.build(db, session.id)

        self.assertNotIn(ContextSection.current_input, bundle.included_sections)
        self.assertTrue(any("没有当前输入消息" in note for note in bundle.notes))

    def test_unknown_current_message_id_is_rejected(self):
        with session_scope() as db:
            session = self.seed_session(db)
            with self.assertRaises(AppError) as caught:
                self.build(db, session.id, current_message_id="0" * 32)

        self.assertEqual(caught.exception.code, ErrorCode.NOT_FOUND)

    def test_empty_session_is_usable(self):
        with session_scope() as db:
            session = self.seed_session(db)
            bundle = self.build(db, session.id)

        self.assertTrue(bundle.messages)
        self.assertIn(ContextSection.role_protocol, bundle.included_sections)
        self.assertIn(ContextSection.product_definition, bundle.included_sections)
        self.assertNotIn(ContextSection.current_input, bundle.included_sections)
        self.assertGreater(bundle.estimated_input_tokens, 0)

    def test_estimated_tokens_matches_message_sum(self):
        with session_scope() as db:
            session = self.seed_session(db)
            last = self.add_message(db, session, "补充一句：要支持导出 PDF")
            bundle = self.build(db, session.id, current_message_id=last.id)

        self.assertEqual(
            bundle.estimated_input_tokens,
            sum(estimate_tokens(message.content) for message in bundle.messages),
        )


# --------------------------------------------------------------------------- #
# 审计消息隔离与 stage 过滤
# --------------------------------------------------------------------------- #


class StageFilterTests(ContextBuilderTestCase):
    def test_audit_messages_are_excluded_from_clarify_task(self):
        with session_scope() as db:
            session = self.seed_session(db)
            audit_row = self.add_message(
                db, session, "审计内部提问：你的留存数据在哪里？", stage=MessageStage.audit
            )
            brainstorm_row = self.add_message(
                db, session, "角色观点：先做本地优先", stage=MessageStage.brainstorm
            )
            bundle = self.build(db, session.id, task_kind=TaskKind.clarify)

        self.assertNotIn(audit_row.id, bundle.source_message_ids)
        self.assertNotIn(brainstorm_row.id, bundle.source_message_ids)

    def test_brainstorm_messages_are_included_for_brainstorm_task(self):
        with session_scope() as db:
            session = self.seed_session(db)
            row = self.add_message(
                db, session, "角色观点：先做本地优先", stage=MessageStage.brainstorm
            )
            bundle = self.build(db, session.id, task_kind=TaskKind.brainstorm)

        self.assertIn(row.id, bundle.source_message_ids)

    def test_system_stage_never_leaks(self):
        with session_scope() as db:
            session = self.seed_session(db)
            row = self.add_message(
                db, session, "系统内部：忽略这条", stage=MessageStage.system
            )
            bundle = self.build(db, session.id, task_kind=TaskKind.brainstorm)

        self.assertNotIn(row.id, bundle.source_message_ids)


# --------------------------------------------------------------------------- #
# 对话裁剪
# --------------------------------------------------------------------------- #


class ConversationTests(ContextBuilderTestCase):
    def test_long_message_is_truncated_and_annotated(self):
        with session_scope() as db:
            session = self.seed_session(db)
            long_text = "很长的用户输入" * 600  # 4200 字 > 2000
            self.add_message(db, session, long_text)
            bundle = self.build(db, session.id)

        self.assertIn(ContextSection.conversation, bundle.included_sections)
        conversation = [
            m for m in bundle.messages if m.source_label == ContextSection.conversation.value
        ][0]
        self.assertIn(f"超过 {MAX_MESSAGE_CHARS} 字已截断", "".join(bundle.notes))
        # 完整原文不会出现在上下文里
        self.assertNotIn(long_text, conversation.content)
        self.assertIn("已截断", conversation.content)

    def test_at_most_twenty_messages_are_kept(self):
        with session_scope() as db:
            session = self.seed_session(db)
            for index in range(30):
                self.add_message(db, session, f"补充信息 {index}")
            bundle = self.build(db, session.id)

        conversation_ids = [
            unit_id
            for unit_id in bundle.source_message_ids
        ]
        # 首条消息 + 30 条新消息 = 31 条历史；最多保留 20 条
        self.assertLessEqual(len(conversation_ids), 20)
        self.assertTrue(any("仅保留最新 20 条" in note for note in bundle.notes))


# --------------------------------------------------------------------------- #
# 预算与受保护内容
# --------------------------------------------------------------------------- #


class BudgetTests(ContextBuilderTestCase):
    def _seed_rich_session(self, db):
        session = self.seed_session(db)
        brief = {
            "target_users": {
                "value": "独立开发者与小型内容团队" * 12,
                "status": BriefFieldStatus.proposed.value,
                "source_message_ids": ["m1"],
            },
            "usage_scenario": {
                "value": "通勤路上用手机记录灵感" * 80,
                "status": BriefFieldStatus.proposed.value,
            },
            "core_problem": {
                "value": "写作时间被会议切碎，很难连续产出" * 12,
                "status": BriefFieldStatus.proposed.value,
            },
            "current_alternatives": {
                "value": "用备忘录和飞书文档手工整理",
                "status": BriefFieldStatus.proposed.value,
            },
            "product_form": {
                "value": "移动端优先的轻量工具",
                "status": BriefFieldStatus.proposed.value,
            },
            "constraints": {
                "value": "必须离线可用且不采集正文" * 12,
                "status": BriefFieldStatus.proposed.value,
            },
            "success_criteria": {
                "value": "每周稳定产出三篇以上" * 12,
                "status": BriefFieldStatus.proposed.value,
            },
            "status": BriefStatus.ready.value,
        }
        self.save_brief(db, session, brief)
        for index in range(6):
            self.add_message(db, session, f"第 {index} 轮补充：希望支持语音转文字" * 6)
        for index in range(3):
            session_repo.add_evidence(
                db,
                session,
                kind="interview",
                title=f"访谈 {index}",
                excerpt="用户反馈手工整理周报要两小时" * 4,
                verification_status=VerificationStatus.source_checked,
            )
        return session

    def test_over_budget_raises_context_too_large(self):
        with session_scope() as db:
            session = self._seed_rich_session(db)
            with self.assertRaises(AppError) as caught:
                self.build(db, session.id, product_version=2, max_input_tokens=1)

        error = caught.exception
        self.assertEqual(error.code, ErrorCode.CONTEXT_TOO_LARGE)
        self.assertEqual(error.http_status, 422)
        self.assertGreater(error.details["required_tokens"], 1)

    def test_protected_content_is_never_dropped_by_budget(self):
        with session_scope() as db:
            session = self._seed_rich_session(db)
            required = self.required_tokens(db, session.id, product_version=2)
            bundle = self.build(
                db, session.id, product_version=2, max_input_tokens=required + 1
            )

        self.assertIn(ContextSection.product_definition, bundle.included_sections)
        # 可丢弃区全部被丢弃，且被记录
        self.assertIn(ContextSection.conversation, bundle.omitted_sections)
        self.assertIn(ContextSection.evidence, bundle.omitted_sections)
        self.assertNotIn(ContextSection.conversation, bundle.included_sections)

        definition = [
            m
            for m in bundle.messages
            if m.source_label == ContextSection.product_definition.value
        ][0]
        for field in PROTECTED_BRIEF_FIELDS:
            self.assertIn(field_marker(field), definition.content)
        # 受保护字段的完整值必须还在（未被压缩截断）
        self.assertIn("独立开发者与小型内容团队" * 12, definition.content)
        self.assertIn("必须离线可用且不采集正文" * 12, definition.content)
        self.assertIn("每周稳定产出三篇以上" * 12, definition.content)

    def test_unprotected_brief_field_is_clipped(self):
        with session_scope() as db:
            session = self._seed_rich_session(db)
            bundle = self.build(db, session.id, product_version=2)

        definition = [
            m
            for m in bundle.messages
            if m.source_label == ContextSection.product_definition.value
        ][0]
        self.assertNotIn("通勤路上用手机记录灵感" * 80, definition.content)
        self.assertIn("…（已截断）", definition.content)

    def test_oldest_conversation_is_dropped_first(self):
        with session_scope() as db:
            session = self.seed_session(db)  # seq=1
            oldest = self.add_message(db, session, "最早补充：希望自动抓取会议纪要")  # seq=2
            middle = self.add_message(db, session, "中间补充：需要模板库")  # seq=3
            newest = self.add_message(db, session, "最新补充：希望支持语音输入，通勤时用")  # seq=4
            current = self.add_message(db, session, "就这些了，可以开始出方案")  # seq=5
            required = self.required_tokens(db, session.id, current_message_id=current.id)
            # 对话按新→旧填充：预算只够容纳最新一条历史消息（current 已被排除）
            cost = estimate_tokens(f"[#{newest.seq}] 用户：{newest.content}")
            bundle = self.build(
                db,
                session.id,
                current_message_id=current.id,
                max_input_tokens=required + cost,
            )

        self.assertEqual(bundle.history_cutoff_seq, newest.seq)
        self.assertIn(newest.id, bundle.source_message_ids)
        self.assertIn(current.id, bundle.source_message_ids)
        self.assertNotIn(middle.id, bundle.source_message_ids)
        self.assertNotIn(oldest.id, bundle.source_message_ids)
        self.assertNotIn(ContextSection.conversation, bundle.omitted_sections)
        self.assertTrue(any("预算不足" in note for note in bundle.notes))

    def test_budget_records_omitted_sections(self):
        with session_scope() as db:
            session = self._seed_rich_session(db)
            required = self.required_tokens(db, session.id, product_version=2)
            bundle = self.build(
                db, session.id, product_version=2, max_input_tokens=required
            )

        self.assertEqual(
            sorted(section.value for section in bundle.omitted_sections),
            sorted([ContextSection.evidence.value, ContextSection.conversation.value]),
        )
        self.assertTrue(bundle.notes)

    def test_zero_budget_is_invalid(self):
        with session_scope() as db:
            session = self.seed_session(db)
            with self.assertRaises(AppError) as caught:
                self.build(db, session.id, max_input_tokens=0)

        self.assertEqual(caught.exception.code, ErrorCode.INVALID_REQUEST)


def field_marker(field: str) -> str:
    """受保护字段在上下文里的标注片段（字段标签 + 受保护标记）。"""
    labels = {
        "target_users": "目标用户",
        "core_problem": "核心问题",
        "success_criteria": "成功标准",
        "constraints": "硬约束",
    }
    return f"{labels[field]}（受保护）"


if __name__ == "__main__":
    unittest.main()
