"""领域契约测试（PRD §5、FR-01、FR-04C）：结构上限、枚举、幂等哈希、事件体。

对应验收：AC-05（结构非法必须拒绝）、AC-29（输入长度上限）、AC-02（Brief 字段语义）。
"""
import json
import unittest

from pydantic import ValidationError

from domain import contracts as C
from domain.contracts import (
    Action,
    BriefField,
    BriefFieldStatus,
    BriefStatus,
    CandidateClaim,
    CandidateOption,
    ClaimKind,
    EvidenceKind,
    EventType,
    MessageStage,
    ProductBrief,
    ProductState,
    RoleCode,
    RoleResult,
    RunEvent,
    RunStatus,
    SenderKind,
    canonical_json,
    empty_brief,
    initial_product_state,
    input_hash,
    is_session_id,
    new_id,
    parse_command,
)


class IdAndTimeTests(unittest.TestCase):
    def test_new_id_is_32_lower_hex(self):
        value = new_id()
        self.assertTrue(is_session_id(value))
        self.assertEqual(len(value), 32)
        self.assertNotEqual(new_id(), new_id())

    def test_utc_now_iso_is_sortable_utc(self):
        value = C.utc_now_iso()
        self.assertTrue(value.endswith("Z"))
        parsed = C.parse_iso(value)
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_session_id_rejects_non_hex(self):
        self.assertFalse(is_session_id("A" * 32))
        self.assertFalse(is_session_id("z" * 32))
        self.assertFalse(is_session_id("a" * 31))


class CanonicalJsonTests(unittest.TestCase):
    def test_key_order_does_not_change_output(self):
        self.assertEqual(canonical_json({"b": 1, "a": 2}), canonical_json({"a": 2, "b": 1}))

    def test_chinese_not_escaped(self):
        self.assertIn("用户", canonical_json({"k": "用户"}))

    def test_input_hash_depends_on_body_and_version(self):
        base = input_hash("clarify", "session:1", {"content": "x"}, 3)
        self.assertEqual(base, input_hash("clarify", "session:1", {"content": "x"}, 3))
        self.assertNotEqual(base, input_hash("clarify", "session:1", {"content": "y"}, 3))
        self.assertNotEqual(base, input_hash("clarify", "session:1", {"content": "x"}, 4))
        self.assertNotEqual(base, input_hash("clarify", "session:2", {"content": "x"}, 3))
        self.assertEqual(len(base), 64)


class StrictModelTests(unittest.TestCase):
    def test_extra_fields_rejected(self):
        with self.assertRaises(ValidationError):
            C.ClarifyPayload.model_validate({"content": "hi", "injected": 1})

    def test_blank_content_rejected(self):
        with self.assertRaises(ValidationError):
            C.ClarifyPayload.model_validate({"content": ""})

    def test_input_length_limit(self):
        C.ClarifyPayload.model_validate({"content": "x" * C.MAX_USER_INPUT_CHARS})
        with self.assertRaises(ValidationError):
            C.ClarifyPayload.model_validate({"content": "x" * (C.MAX_USER_INPUT_CHARS + 1)})

    def test_evidence_excerpt_limit(self):
        payload = {
            "kind": EvidenceKind.user_statement.value,
            "excerpt": "x" * (C.MAX_EVIDENCE_EXCERPT_CHARS + 1),
        }
        with self.assertRaises(ValidationError):
            C.AddEvidencePayload.model_validate(payload)


class ProductBriefTests(unittest.TestCase):
    def test_empty_brief_has_seven_fields_and_four_required(self):
        brief = empty_brief()
        self.assertEqual(len(C.BRIEF_FIELDS), 7)
        self.assertEqual(
            set(C.BRIEF_REQUIRED_FIELDS),
            {"target_users", "usage_scenario", "core_problem", "success_criteria"},
        )
        self.assertEqual(set(brief.required_missing()), set(C.BRIEF_REQUIRED_FIELDS))
        self.assertFalse(brief.ready())
        self.assertEqual(brief.status, BriefStatus.draft)

    def test_proposed_required_fields_are_not_missing_for_ready(self):
        brief = empty_brief()
        for name in C.BRIEF_REQUIRED_FIELDS:
            setattr(
                brief,
                name,
                BriefField(value="v", status=BriefFieldStatus.proposed),
            )
        self.assertEqual(brief.required_missing(), [])
        self.assertTrue(brief.ready())
        # proposed 不等于 confirmed：用户未确认时不能自称已确认
        self.assertEqual(brief.confirmed_fields(), {})

    def test_known_includes_proposed_and_confirmed(self):
        brief = empty_brief()
        brief.target_users = BriefField(value="财务", status=BriefFieldStatus.proposed)
        self.assertEqual(brief.known("target_users"), True)
        self.assertEqual(brief.confirmed("target_users"), False)
        brief.target_users = BriefField(value="财务", status=BriefFieldStatus.confirmed)
        self.assertEqual(brief.confirmed("target_users"), True)


class ProductStateTests(unittest.TestCase):
    def test_initial_state(self):
        sid = new_id()
        state = initial_product_state(sid)
        self.assertEqual(state.version, 1)
        self.assertEqual(state.session_id, sid)
        self.assertEqual(state.claims, [])
        self.assertEqual(state.brief.status, BriefStatus.draft)
        self.assertTrue(state.created_at.endswith("Z"))

    def test_session_id_must_be_32_hex(self):
        with self.assertRaises(ValidationError):
            ProductState(session_id="not-a-session-id")

    def test_state_roundtrip_is_stable(self):
        sid = new_id()
        state = initial_product_state(sid)
        restored = ProductState.model_validate_json(state.model_dump_json())
        self.assertEqual(state, restored)


class RoleResultTests(unittest.TestCase):
    def _claim(self, local_id="c1", text="主张"):
        return CandidateClaim(local_id=local_id, kind=ClaimKind.fact, text=text)

    def test_duplicate_local_id_rejected(self):
        with self.assertRaises(ValidationError):
            RoleResult(role_code=RoleCode.cto, claims=[self._claim("x"), self._claim("x")])

    def test_claims_limit(self):
        claims = [self._claim(f"c{i}") for i in range(C.MAX_CLAIMS_PER_ROLE_RESULT + 1)]
        with self.assertRaises(ValidationError):
            RoleResult(role_code=RoleCode.cto, claims=claims)

    def test_claim_text_limit(self):
        with self.assertRaises(ValidationError):
            RoleResult(
                role_code=RoleCode.cto,
                claims=[self._claim(text="x" * (C.MAX_CLAIM_TEXT_CHARS + 1))],
            )

    def test_local_id_length_limit(self):
        with self.assertRaises(ValidationError):
            RoleResult(
                role_code=RoleCode.cto,
                claims=[self._claim("l" * (C.MAX_LOCAL_ID_CHARS + 1))],
            )

    def test_options_limit(self):
        options = [
            CandidateOption(local_id=f"o{i}", title=f"选项{i}")
            for i in range(C.MAX_OPTIONS_PER_ROLE_RESULT + 1)
        ]
        with self.assertRaises(ValidationError):
            RoleResult(role_code=RoleCode.designer, option_proposals=options)

    def test_option_local_refs_must_exist(self):
        with self.assertRaises(ValidationError):
            RoleResult(
                role_code=RoleCode.designer,
                option_proposals=[
                    CandidateOption(local_id="o1", title="方案", claim_local_ids=["ghost"])
                ],
            )

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValidationError):
            RoleResult.model_validate({"role_code": "marketer"})

    def test_display_text_limit(self):
        with self.assertRaises(ValidationError):
            RoleResult(
                role_code=RoleCode.ops,
                display_text="x" * (C.MAX_DISPLAY_TEXT_CHARS + 1),
            )


class ParseCommandTests(unittest.TestCase):
    def test_known_action_parses(self):
        parsed = parse_command(
            Action.clarify, "req-1", {"content": "目标是财务"}, 2, "session:abc"
        )
        self.assertEqual(parsed.action, Action.clarify)
        self.assertEqual(parsed.expected_session_version, 2)
        self.assertEqual(parsed.payload["content"], "目标是财务")
        self.assertEqual(len(parsed.input_hash), 64)

    def test_same_input_same_hash(self):
        a = parse_command(Action.clarify, "r1", {"content": "x"}, 1, "session:s")
        b = parse_command(Action.clarify, "r1", {"content": "x"}, 1, "session:s")
        self.assertEqual(a.input_hash, b.input_hash)

    def test_extra_field_rejected(self):
        with self.assertRaises(ValidationError):
            parse_command(
                Action.clarify, "r1", {"content": "x", "y": 1}, 1, "session:s"
            )

    def test_every_action_has_payload_model(self):
        for action in C.NORMAL_ACTIONS | C.CONTROL_ACTIONS:
            self.assertIn(action, C.PAYLOAD_MODELS, msg=f"{action} 缺少 payload 模型")

    def test_control_actions_are_disjoint_from_normal(self):
        self.assertEqual(C.NORMAL_ACTIONS & C.CONTROL_ACTIONS, frozenset())
        self.assertEqual(len(C.NORMAL_ACTIONS), 13)
        self.assertEqual(len(C.CONTROL_ACTIONS), 2)


class RunEventTests(unittest.TestCase):
    def test_event_to_sse_data_has_required_fields(self):
        event = RunEvent(
            session_id=new_id(),
            run_id=new_id(),
            event_seq=3,
            type=EventType.run_started,
            occurred_at=C.utc_now_iso(),
            payload={"a": 1},
        )
        data = json.loads(event.to_sse_data())
        self.assertEqual(data["schema_version"], C.SCHEMA_VERSION)
        self.assertEqual(data["type"], "run.started")
        self.assertEqual(data["event_seq"], 3)
        self.assertFalse(event.is_terminal)

    def test_event_seq_must_be_positive(self):
        with self.assertRaises(ValidationError):
            RunEvent(
                session_id=new_id(),
                run_id=new_id(),
                event_seq=0,
                type=EventType.run_started,
                occurred_at=C.utc_now_iso(),
            )

    def test_terminal_event_types_are_run_level(self):
        self.assertEqual(
            {e.value for e in C.TERMINAL_EVENT_TYPES},
            {"run.completed", "run.failed", "run.cancelled"},
        )
        for event_type in C.TERMINAL_EVENT_TYPES:
            self.assertTrue(event_type.value.startswith("run."))


class EnumCoverageTests(unittest.TestCase):
    def test_session_phases(self):
        self.assertEqual(
            {p.value for p in C.SessionPhase},
            {"clarifying", "brainstorming", "auditing", "audit_completed"},
        )

    def test_run_status_has_eight_values(self):
        self.assertEqual(len(list(RunStatus)), 8)

    def test_role_codes_include_target_user(self):
        self.assertEqual(
            {r.value for r in RoleCode},
            {"coach", "cto", "designer", "ops", "target_user", "moderator", "interviewer"},
        )

    def test_audit_dimensions_six(self):
        self.assertEqual(len(C.AUDIT_DIMENSIONS), 6)
        self.assertEqual(
            set(C.AUDIT_DIMENSIONS),
            {
                "problem_validity",
                "solution_effectiveness",
                "technical_risk",
                "business_loop",
                "user_adoption",
                "execution_risk",
            },
        )

    def test_audit_question_limits(self):
        self.assertEqual(C.AUDIT_QUESTION_TARGET, 12)
        self.assertEqual(C.AUDIT_QUESTION_HARD_LIMIT, 18)

    def test_event_types_twelve(self):
        self.assertEqual(len(list(EventType)), 12)
        self.assertEqual(
            {e.value for e in C.TERMINAL_EVENT_TYPES},
            {"run.completed", "run.failed", "run.cancelled"},
        )

    def test_message_stage_and_sender_enums(self):
        self.assertEqual(set(m.value for m in MessageStage), {"clarify", "brainstorm", "audit", "system"})
        self.assertEqual(set(s.value for s in SenderKind), {"human", "agent", "system"})


if __name__ == "__main__":
    unittest.main()
