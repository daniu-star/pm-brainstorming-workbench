"""数据库与 ORM schema（PRD ADR-03 / WP-02）。

约定：
- 默认 SQLite（WAL、foreign_keys=ON、busy_timeout=5000ms），可被 DATABASE_URL 覆盖；
- 每线程/任务独立 Session，事务中禁止等待模型或网络；
- 时间戳统一存 UTC ISO-8601 文本；租约/截止时间另存 epoch 秒整数；
- 消息、任务、事件、审计问题、幂等键与权限字段均可独立查询，并带约束。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# --------------------------------------------------------------------------- #
# 路径与 URL
# --------------------------------------------------------------------------- #

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = BACKEND_DIR / "data"
DEFAULT_DB_FILE = DEFAULT_DATA_DIR / "app.db"


def default_database_url() -> str:
    """默认使用 backend/data/app.db；测试可用 DATABASE_URL 或 configure() 覆盖。"""
    configured = os.environ.get("DATABASE_URL")
    if configured:
        return configured
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{DEFAULT_DB_FILE.as_posix()}"


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """每个新连接都设置 WAL / 外键 / 忙等，保证多进程下行为一致。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# 枚举取值（与 domain.contracts 保持一致；这里用字符串常量避免循环导入）
# --------------------------------------------------------------------------- #

SESSION_PHASES = "'clarifying','brainstorming','auditing','audit_completed'"
RUN_STATUSES = "'queued','running','interrupted','cancel_requested','succeeded','partial_succeeded','failed','cancelled'"
STEP_STATUSES = "'pending','running','interrupted','succeeded','failed','cancelled','skipped'"
MESSAGE_STATUSES = "'draft','completed','interrupted','cancelled'"
SENDER_KINDS = "'human','agent','system'"
MESSAGE_STAGES = "'clarify','brainstorm','audit','system'"
AUDIT_STATUSES = "'created','opening','active','closing','completed','failed','cancelled'"
CONTROL_STATUSES = "'accepted','scheduled','applied','failed'"
CONTROL_ACTIONS = "'end_audit','cancel_audit'"


def _in(column: str, values: str) -> str:
    return f"{column} IN ({values})"


# --------------------------------------------------------------------------- #
# ORM 行定义
# --------------------------------------------------------------------------- #


class SessionRow(Base):
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    owner_email = Column(String(320), nullable=False)
    phase = Column(String(32), nullable=False, default="clarifying")
    session_version = Column(Integer, nullable=False, default=1, server_default="1")
    current_product_version = Column(Integer, nullable=False, default=1, server_default="1")
    current_audit_id = Column(String(64), nullable=True)
    active_run_id = Column(String(64), nullable=True)
    current_control_id = Column(String(64), nullable=True)
    problem_statement = Column(Text, nullable=False, default="")
    brief_status = Column(String(32), nullable=False, default="draft", server_default="draft")
    next_message_seq = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)
    deleted_at = Column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(_in("phase", SESSION_PHASES), name="ck_sessions_phase"),
        Index("ix_sessions_owner_updated", "owner_email", "updated_at"),
        Index("ix_sessions_owner_deleted", "owner_email", "deleted_at"),
        Index("ix_sessions_active_run", "active_run_id"),
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    sender_kind = Column(String(16), nullable=False)
    role_code = Column(String(32), nullable=True)
    stage = Column(String(16), nullable=False)
    content = Column(Text, nullable=False, default="")
    status = Column(String(16), nullable=False, default="completed")
    run_id = Column(String(64), nullable=True)
    audit_id = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
        CheckConstraint(_in("sender_kind", SENDER_KINDS), name="ck_messages_sender_kind"),
        CheckConstraint(_in("status", MESSAGE_STATUSES), name="ck_messages_status"),
        CheckConstraint(_in("stage", MESSAGE_STAGES), name="ck_messages_stage"),
        Index("ix_messages_session_seq", "session_id", "seq"),
        Index("ix_messages_run", "run_id"),
        Index("ix_messages_audit", "audit_id"),
    )


class ProductStateRow(Base):
    """版本快照不可原地改写：只插入新 version 行。"""

    __tablename__ = "product_states"

    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    version = Column(Integer, primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1, server_default="1")
    payload_json = Column(Text, nullable=False)
    source_run_id = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True)
    kind = Column(String(32), nullable=False)
    source_uri = Column(Text, nullable=True)
    title = Column(Text, nullable=False, default="")
    excerpt = Column(Text, nullable=False, default="")
    content_hash = Column(String(64), nullable=False, default="")
    source_date = Column(String(64), nullable=True)
    retrieved_at = Column(String(32), nullable=False)
    verification_status = Column(String(32), nullable=False, default="unverified")
    limitations = Column(Text, nullable=True)
    created_by = Column(String(320), nullable=True)
    created_at = Column(String(32), nullable=False)

    __table_args__ = (
        Index("ix_evidence_session", "session_id"),
        Index("ix_evidence_hash", "content_hash"),
    )


class RunRow(Base):
    __tablename__ = "runs"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    owner_email = Column(String(320), nullable=False)
    request_id = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)
    input_hash = Column(String(64), nullable=False, default="")
    base_session_version = Column(Integer, nullable=False, default=0)
    base_product_version = Column(Integer, nullable=False, default=1)
    result_product_version = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="queued")
    budget_json = Column(Text, nullable=False, default="{}")
    usage_json = Column(Text, nullable=False, default="{}")
    lease_owner = Column(String(128), nullable=True)
    lease_epoch = Column(Integer, nullable=False, default=0, server_default="0")
    lease_expires_at = Column(Integer, nullable=True)
    heartbeat_at = Column(Integer, nullable=True)
    deadline_at = Column(Integer, nullable=True)
    queue_deadline_at = Column(Integer, nullable=True)
    parent_run_id = Column(String(64), nullable=True)
    audit_id = Column(String(64), nullable=True)
    base_audit_version = Column(Integer, nullable=True)
    retry_audit_version = Column(Integer, nullable=True)
    #: 重试子 Run 只调度这些 logical_key（retry_run 从父 Run 的失败/未完成步骤复制）。
    failed_steps_json = Column(Text, nullable=False, default="[]", server_default="[]")
    loop_id = Column(String(64), nullable=True)
    question_id = Column(String(64), nullable=True)
    continuation_token = Column(String(64), nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=False, default="[]")
    created_at = Column(String(32), nullable=False)
    started_at = Column(String(32), nullable=True)
    finished_at = Column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_runs_status"),
        Index("ix_runs_session", "session_id"),
        Index("ix_runs_claim", "status", "lease_expires_at"),
        Index("ix_runs_owner_status", "owner_email", "status"),
        Index("ix_runs_parent", "parent_run_id"),
        Index("ix_runs_audit", "audit_id"),
        Index("ix_runs_loop", "loop_id"),
    )


class StepRow(Base):
    __tablename__ = "steps"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    logical_key = Column(String(128), nullable=False)
    kind = Column(String(32), nullable=False)
    role_code = Column(String(32), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    input_hash = Column(String(64), nullable=False, default="")
    input_snapshot_json = Column(Text, nullable=False, default="{}")
    output_json = Column(Text, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    previous_step_id = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)
    started_at = Column(String(32), nullable=True)
    finished_at = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "logical_key", name="uq_steps_run_logical"),
        CheckConstraint(_in("status", STEP_STATUSES), name="ck_steps_status"),
        Index("ix_steps_run", "run_id"),
    )


class StepCandidateRow(Base):
    """候选登记器分配服务器 ID；staging 阶段尚不属于权威 ProductState。"""

    __tablename__ = "step_candidates"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_id = Column(String(64), ForeignKey("steps.id", ondelete="CASCADE"), nullable=False)
    local_id = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "step_id", "local_id", name="uq_step_candidates_scope"),
        Index("ix_step_candidates_step", "step_id"),
    )


class EventRow(Base):
    __tablename__ = "events"

    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1, server_default="1")
    type = Column(String(48), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
        Index("ix_events_session", "session_id", "created_at"),
        Index("ix_events_type", "type"),
    )


class AuditRow(Base):
    __tablename__ = "audits"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    product_version = Column(Integer, nullable=False)
    audit_version = Column(Integer, nullable=False, default=1, server_default="1")
    status = Column(String(32), nullable=False, default="created")
    question_count = Column(Integer, nullable=False, default=0, server_default="0")
    end_reason = Column(String(32), nullable=True)
    dimension_states_json = Column(Text, nullable=False, default="[]")
    report_json = Column(Text, nullable=True)
    active_question_id = Column(String(64), nullable=True)
    pending_continuation = Column(Integer, nullable=False, default=0, server_default="0")
    close_requested = Column(Integer, nullable=False, default=0, server_default="0")
    closing_control_id = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(_in("status", AUDIT_STATUSES), name="ck_audits_status"),
        CheckConstraint("question_count >= 0 AND question_count <= 18", name="ck_audits_question_count"),
        Index("ix_audits_session", "session_id"),
        Index("ix_audits_session_version", "session_id", "product_version"),
    )


class AuditQuestionRow(Base):
    __tablename__ = "audit_questions"

    id = Column(String(64), primary_key=True)
    audit_id = Column(String(64), ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    dimension = Column(String(48), nullable=False)
    issue_refs_json = Column(Text, nullable=False, default="[]")
    question_text = Column(Text, nullable=False, default="")
    question_message_id = Column(String(64), nullable=True)
    answer_message_id = Column(String(64), nullable=True)
    assessment = Column(String(32), nullable=False, default="awaiting_answer")
    rationale = Column(Text, nullable=False, default="")
    evidence_refs_json = Column(Text, nullable=False, default="[]")
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("audit_id", "ordinal", name="uq_audit_questions_ordinal"),
        CheckConstraint("ordinal >= 1 AND ordinal <= 18", name="ck_audit_questions_ordinal"),
        Index("ix_audit_questions_audit", "audit_id"),
    )


class AuditAssessmentRevisionRow(Base):
    """旧修订不可覆盖（PRD §5.2）。"""

    __tablename__ = "audit_assessment_revisions"

    id = Column(String(64), primary_key=True)
    audit_id = Column(String(64), ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    audit_version = Column(Integer, nullable=False)
    dimension = Column(String(48), nullable=True)
    question_id = Column(String(64), nullable=True)
    before_json = Column(Text, nullable=False, default="{}")
    after_json = Column(Text, nullable=False, default="{}")
    origin = Column(String(16), nullable=False, default="human")
    source_message_ids_json = Column(Text, nullable=False, default="[]")
    evidence_refs_json = Column(Text, nullable=False, default="[]")
    rationale = Column(Text, nullable=False, default="")
    created_at = Column(String(32), nullable=False)

    __table_args__ = (Index("ix_audit_revisions_audit", "audit_id"),)


class AuditReportRevisionRow(Base):
    __tablename__ = "audit_report_revisions"

    id = Column(String(64), primary_key=True)
    audit_id = Column(String(64), ForeignKey("audits.id", ondelete="CASCADE"), nullable=False)
    audit_version = Column(Integer, nullable=False)
    report_json = Column(Text, nullable=False)
    generated_by = Column(String(16), nullable=False, default="model")
    created_at = Column(String(32), nullable=False)

    __table_args__ = (Index("ix_audit_report_revisions_audit", "audit_id"),)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_records"

    owner_email = Column(String(320), primary_key=True)
    request_id = Column(String(64), primary_key=True)
    action = Column(String(32), nullable=False)
    resource_scope = Column(String(128), nullable=False, default="")
    input_hash = Column(String(64), nullable=False, default="")
    response_status = Column(Integer, nullable=False, default=202)
    response_json = Column(Text, nullable=False, default="{}")
    related_run_id = Column(String(64), nullable=True)
    related_control_id = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)
    #: 幂等记录保留 30 天（PRD §5.3）；epoch 秒整数，便于 SQL 比较与索引。
    expires_at = Column(Integer, nullable=False)

    __table_args__ = (Index("ix_idempotency_expires", "expires_at"),)


class ControlIntentRow(Base):
    __tablename__ = "control_intents"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    audit_id = Column(String(64), nullable=False)
    owner_email = Column(String(320), nullable=False)
    request_id = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)
    target_run_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="accepted")
    related_run_id = Column(String(64), nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_email", "request_id", name="uq_control_intents_request"),
        CheckConstraint(_in("action", CONTROL_ACTIONS), name="ck_control_intents_action"),
        CheckConstraint(_in("status", CONTROL_STATUSES), name="ck_control_intents_status"),
        Index("ix_control_intents_audit", "audit_id", "status"),
        Index("ix_control_intents_session", "session_id"),
    )


class AuthCodeRow(Base):
    __tablename__ = "auth_codes"

    id = Column(String(64), primary_key=True)
    email = Column(String(320), nullable=False)
    code_hash = Column(String(128), nullable=False)
    expires_at = Column(Integer, nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    consumed_at = Column(Integer, nullable=True)
    created_at = Column(Integer, nullable=False)
    last_sent_at = Column(Integer, nullable=False)

    __table_args__ = (Index("ix_auth_codes_email", "email", "created_at"),)


class RateLimitRow(Base):
    """滑动窗口计数：scope+key+window_start 唯一。"""

    __tablename__ = "rate_limits"

    id = Column(String(64), primary_key=True)
    scope = Column(String(48), nullable=False)
    key = Column(String(320), nullable=False)
    window_start = Column(Integer, nullable=False)
    count = Column(Integer, nullable=False, default=1, server_default="1")
    updated_at = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", "key", "window_start", name="uq_rate_limits_window"),
        Index("ix_rate_limits_lookup", "scope", "key", "window_start"),
    )


class MigrationRecordRow(Base):
    __tablename__ = "migration_records"

    source_path = Column(String(1024), primary_key=True)
    source_hash = Column(String(64), nullable=False)
    session_id = Column(String(64), nullable=True)
    import_status = Column(String(32), nullable=False, default="imported")
    warnings_json = Column(Text, nullable=False, default="[]")
    imported_at = Column(String(32), nullable=False)


class LegacyArtifactRow(Base):
    """旧 canvas / 旧面试等不可直接结构化内容的归档（PRD §8）。"""

    __tablename__ = "legacy_artifacts"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(32), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    source_path = Column(String(1024), nullable=True)
    created_at = Column(String(32), nullable=False)

    __table_args__ = (Index("ix_legacy_artifacts_session", "session_id"),)


class SessionIdAliasRow(Base):
    """旧会话 ID（12 hex 等非 32 hex 形态）→ 当前 32 hex ID 的别名表（PRD §8.1）。

    迁移器把旧 ID 确定性重映射为 ``sha256(legacy_id)[:32]``，旧书签/旧链接里的
    12 hex ID 直接访问会 404。这里保留只读别名，让 ``/api/v2/sessions/{legacy}``
    仍能解析到同一会话；别名只是入口重定向，不参与任何写路径。
    """

    __tablename__ = "session_id_aliases"

    legacy_id = Column(String(128), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    source_path = Column(String(1024), nullable=True)
    created_at = Column(String(32), nullable=False)

    __table_args__ = (Index("ix_session_id_aliases_session", "session_id"),)


class SessionShareRow(Base):
    """会话共享（查看 / 可提交命令两种角色）。

    sessions 仍只有唯一的 ``owner_email``；共享只授予访问角色，不改变归属、
    不复制数据。删除共享行即撤销访问。
    """

    __tablename__ = "session_shares"

    session_id = Column(
        String(64), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    shared_with_email = Column(String(320), primary_key=True)
    role = Column(String(16), nullable=False, default="viewer")
    created_by = Column(String(320), nullable=False)
    created_at = Column(String(32), nullable=False)

    __table_args__ = (Index("ix_session_shares_email", "shared_with_email"),)


class LlmConfigRow(Base):
    """账户自带模型（BYOK）配置：一个 owner 一行（PRD 扩展：平台免费、模型自付）。

    ``api_key_enc`` 只存 ``core.crypto`` 加密后的密文；``key_hint`` 是末 4 位，
    用于前端回显。``disable_reasoning`` 用 0/1 存（推理型模型默认关闭思考）。
    """

    __tablename__ = "llm_configs"

    owner_email = Column(String(320), primary_key=True)
    base_url = Column(String(512), nullable=False)
    model = Column(String(128), nullable=False)
    api_key_enc = Column(Text, nullable=False)
    key_hint = Column(String(32), nullable=False, default="")
    disable_reasoning = Column(Integer, nullable=False, default=1)
    updated_at = Column(String(32), nullable=False)
    last_verified_at = Column(String(32), nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (Index("ix_llm_configs_updated", "updated_at"),)


class LoopRow(Base):
    """Loop Studio 闭环实例（设想→检验→开发→测试→评测）。"""

    __tablename__ = "loops"

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    owner_email = Column(String(320), nullable=False)
    scope = Column(String(16), nullable=False, default="mvp")
    status = Column(String(32), nullable=False, default="running")
    current_round = Column(Integer, nullable=False, default=1, server_default="1")
    max_rounds = Column(Integer, nullable=False, default=3, server_default="3")
    target_score = Column(Integer, nullable=False, default=85, server_default="85")
    loop_version = Column(Integer, nullable=False, default=1, server_default="1")
    focus = Column(Text, nullable=False, default="", server_default="")
    latest_run_id = Column(String(64), nullable=True)
    decision = Column(String(16), nullable=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)
    closed_at = Column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(
            _in("status", "'running','awaiting_decision','shipped','stopped','failed'"),
            name="ck_loops_status",
        ),
        Index("ix_loops_session", "session_id"),
        Index("ix_loops_owner", "owner_email"),
    )


class LoopRoundRow(Base):
    __tablename__ = "loop_rounds"

    id = Column(String(64), primary_key=True)
    loop_id = Column(String(64), ForeignKey("loops.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    run_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="running")
    score = Column(Integer, nullable=True)
    verdict = Column(String(16), nullable=False, default="pending")
    started_at = Column(String(32), nullable=False)
    finished_at = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("loop_id", "ordinal", name="uq_loop_rounds_ordinal"),
        CheckConstraint(_in("status", "'running','done','failed'"), name="ck_loop_rounds_status"),
        Index("ix_loop_rounds_loop", "loop_id"),
    )


class LoopStageResultRow(Base):
    __tablename__ = "loop_stage_results"

    id = Column(String(64), primary_key=True)
    loop_id = Column(String(64), ForeignKey("loops.id", ondelete="CASCADE"), nullable=False)
    round_ordinal = Column(Integer, nullable=False)
    stage = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    summary = Column(Text, nullable=False, default="", server_default="")
    payload_json = Column(Text, nullable=False, default="{}", server_default="{}")
    error_code = Column(String(64), nullable=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("loop_id", "round_ordinal", "stage", name="uq_loop_stage_results_key"),
        CheckConstraint(
            _in("stage", "'ideate','validate','develop','test','evaluate'"),
            name="ck_loop_stage_results_stage",
        ),
        CheckConstraint(
            _in("status", "'pending','running','done','failed','skipped'"),
            name="ck_loop_stage_results_status",
        ),
        Index("ix_loop_stage_results_loop", "loop_id", "round_ordinal"),
    )


class LoopArtifactRow(Base):
    __tablename__ = "loop_artifacts"

    id = Column(String(64), primary_key=True)
    loop_id = Column(String(64), ForeignKey("loops.id", ondelete="CASCADE"), nullable=False)
    round_ordinal = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    title = Column(String(300), nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            _in("kind", "'mvp_spec','validation','prototype','test_report','scorecard'"),
            name="ck_loop_artifacts_kind",
        ),
        Index("ix_loop_artifacts_loop", "loop_id", "round_ordinal"),
    )


ALL_TABLES = (
    SessionRow,
    MessageRow,
    ProductStateRow,
    EvidenceRow,
    RunRow,
    StepRow,
    StepCandidateRow,
    EventRow,
    AuditRow,
    AuditQuestionRow,
    AuditAssessmentRevisionRow,
    AuditReportRevisionRow,
    IdempotencyRow,
    ControlIntentRow,
    AuthCodeRow,
    RateLimitRow,
    MigrationRecordRow,
    LegacyArtifactRow,
    LoopRow,
    LoopRoundRow,
    LoopStageResultRow,
    LoopArtifactRow,
)


# --------------------------------------------------------------------------- #
# Engine / Session 管理
# --------------------------------------------------------------------------- #

_lock = threading.Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_url: str | None = None


def _build_engine(url: str) -> Engine:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def configure(database_url: str | None = None, *, force: bool = False) -> Engine:
    """配置全局 engine。测试可用 ``configure("sqlite+pysqlite:///:memory:", force=True)``。"""
    global _engine, _session_factory, _engine_url

    url = database_url or default_database_url()
    with _lock:
        if _engine is not None and not force:
            if _engine_url == url:
                return _engine
            raise RuntimeError(
                f"database already configured as {_engine_url}; call configure(force=True) to switch"
            )
        if _engine is not None and force:
            _engine.dispose()
        _engine = _build_engine(url)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False, future=True)
        _engine_url = url
        return _engine


def get_engine() -> Engine:
    if _engine is None:
        return configure()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """一个短事务：正常退出提交，异常回滚。禁止在此块内等待模型/网络。"""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def new_session() -> Session:
    """给调用方自行管理生命周期的 Session（Worker 每个任务一个）。"""
    return get_session_factory()()


def initialize_database(engine: Engine | None = None, *, drop_first: bool = False) -> Engine:
    """建表 + 基础索引。生产迁移由 Alembic 负责，这里是本地/测试的快速路径。"""
    target = engine or get_engine()
    if drop_first:
        Base.metadata.drop_all(target)
    Base.metadata.create_all(target)
    _additive_schema_upgrade(target)
    return target


def _additive_schema_upgrade(engine: Engine) -> None:
    """给已存在的 SQLite 库补“新增列”（无 Alembic 的本地最小升级路径）。

    只做向后兼容的加列：旧库（backend/data/app.db）在 ``create_all`` 之后不会自动
    获得新列，缺列会让 ORM 的 INSERT/SELECT 直接失败。这里按列名探测后 ``ALTER``，
    可重复执行；非 SQLite 方言交给正式迁移，不做任何写入。
    """

    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(runs)")}
        if "failed_steps_json" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE runs ADD COLUMN failed_steps_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "loop_id" not in columns:
            conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN loop_id VARCHAR(64)")

        # steps.created_at 是后加的 NOT NULL 列。旧库缺列时 ensure_step/get_step 会直接抛
        # OperationalError("no such column: steps.created_at")，用户侧表现就是「发消息一律失败」。
        # 补列后回填历史行，避免 NOT NULL 违约。
        step_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(steps)")}
        if step_columns and "created_at" not in step_columns:
            conn.exec_driver_sql(
                "ALTER TABLE steps ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''"
            )
            conn.exec_driver_sql(
                "UPDATE steps SET created_at = "
                "COALESCE(started_at, finished_at, '1970-01-01T00:00:00Z') "
                "WHERE created_at = ''"
            )


def dispose_engine() -> None:
    """释放当前引擎的所有连接（Windows 上不释放会锁住 sqlite 文件，导致临时目录删不掉）。"""
    global _engine, _session_factory, _engine_url
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _engine_url = None


def reset_for_tests(database_url: str) -> Engine:
    """测试专用：切换到一个全新数据库并建表。"""
    dispose_engine()
    engine = configure(database_url, force=True)
    initialize_database(engine, drop_first=True)
    return engine


def healthcheck() -> bool:
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - 健康检查只返回布尔
        return False


__all__ = [
    "ALL_TABLES",
    "AuditAssessmentRevisionRow",
    "AuditQuestionRow",
    "AuditReportRevisionRow",
    "AuditRow",
    "AuthCodeRow",
    "Base",
    "ControlIntentRow",
    "EventRow",
    "EvidenceRow",
    "IdempotencyRow",
    "LegacyArtifactRow",
    "LlmConfigRow",
    "LoopArtifactRow",
    "LoopRow",
    "LoopRoundRow",
    "LoopStageResultRow",
    "MessageRow",
    "MigrationRecordRow",
    "ProductStateRow",
    "RateLimitRow",
    "RunRow",
    "SessionIdAliasRow",
    "SessionRow",
    "SessionShareRow",
    "StepCandidateRow",
    "StepRow",
    "configure",
    "default_database_url",
    "get_engine",
    "get_session_factory",
    "dispose_engine",
    "healthcheck",
    "initialize_database",
    "new_session",
    "reset_for_tests",
    "session_scope",
]