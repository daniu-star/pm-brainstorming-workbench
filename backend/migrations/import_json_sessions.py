"""旧 JSON 会话导入器（PRD §8.1）。

用法（在 ``backend`` 目录下运行）::

    python -m migrations.import_json_sessions --source <绝对目录> --dry-run
    python -m migrations.import_json_sessions --source <绝对目录> --apply

行为约定：

- 只读取 ``--source`` 目录顶层的 ``*.json``，**绝不删除、移动或覆盖源文件**；
- ``--dry-run`` 只打印迁移计划与统计（数量、格式错误、空 owner、重复 ID、
  已截断历史、旧阶段），不写数据库，也不输出用户正文或邮箱；
- ``--apply`` 先写出备份清单（路径 + SHA-256 + 大小），再在单个事务中逐文件导入；
- 相同 ``source_path`` + 相同内容哈希重跑时跳过；同 ID 不同内容只进冲突清单，
  绝不覆盖已有在线数据；
- 旧会话 ID 不是 32 位小写 hex 时，用 ``sha256(legacy_id)[:32]`` 确定性重映射
  （``ProductState.session_id`` 等契约要求 32 hex），原 ID 写入迁移记录告警；
- 旧 ``phase`` 映射：``define``/``coach`` → ``clarifying``、``brainstorm`` →
  ``brainstorming``、``interview`` → ``auditing``；
- 空 owner 归入隔离账号 ``legacy-unknown@local``，不分配给任何真实用户；
- 旧画布原样存入 ``legacy_artifacts``（``source_text`` 标记未验证）；
- 旧活动面试（phase=interview）写入历史审计实例：``status='cancelled'``、
  ``end_reason='legacy_unstructured'``，不伪造可恢复的当前问题。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from domain.contracts import (
    MessageStage,
    RoleCode,
    SenderKind,
    SessionPhase,
    canonical_json,
    initial_product_state,
    is_session_id,
    new_id,
    sha256_text,
    utc_now_iso,
)
from repositories import database
from repositories.database import (
    AuditRow,
    LegacyArtifactRow,
    MessageRow,
    MigrationRecordRow,
    ProductStateRow,
    SessionIdAliasRow,
    SessionRow,
)

LEGACY_UNKNOWN_OWNER = "legacy-unknown@local"
"""空 owner 旧会话的隔离账号，避免被分配给首个登录者或公开展示。"""

LEGACY_MAX_MESSAGES = 200
"""旧 ``db/session_store.py`` 的 MAX_MESSAGES 截断阈值（达到即标记不完整）。"""

LEGACY_PHASE_MAP: dict[str, str] = {
    "define": SessionPhase.clarifying.value,
    "coach": SessionPhase.clarifying.value,
    "brainstorm": SessionPhase.brainstorming.value,
    "interview": SessionPhase.auditing.value,
}
"""PRD §8.1 规定的旧阶段到 v1 阶段映射。"""

ARTIFACT_KIND_CANVAS = "legacy_canvas"
ARTIFACT_KIND_INTERVIEW = "legacy_interview"

_HUMAN_ROLES = frozenset({"user", "human"})
_AGENT_ROLES = frozenset({"assistant", "agent", "model", "bot"})
_EXPERT_ROLE_VALUES = frozenset(role.value for role in RoleCode)
_STAGE_BY_PHASE: dict[str, str] = {
    SessionPhase.clarifying.value: MessageStage.clarify.value,
    SessionPhase.brainstorming.value: MessageStage.brainstorm.value,
    SessionPhase.auditing.value: MessageStage.audit.value,
    SessionPhase.audit_completed.value: MessageStage.audit.value,
}

_IMPORTED = "imported"
_CONFLICT = "conflict"
_INVALID = "invalid"
_SKIPPED = "skipped"


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SourceFile:
    """一个源文件的读取结果（解析失败时 ``payload`` 为 None）。"""

    path: Path
    source_path: str
    size_bytes: int
    source_hash: str
    payload: dict[str, Any] | None = None
    error: str | None = None


@dataclass(slots=True)
class ImportPlan:
    """单个源文件的导入计划（全部为可序列化的纯数据）。"""

    source: SourceFile
    legacy_id: str
    session_id: str
    phase: str
    owner_email: str
    owner_isolated: bool
    problem_statement: str
    created_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    canvas_tree: Any = None
    canvas_last_msg_index: Any = None
    interview_question_count: int = 0
    interview_dimensions: list[str] = field(default_factory=list)
    is_legacy_interview: bool = False
    history_incomplete: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.messages)


# --------------------------------------------------------------------------- #
# 纯函数工具
# --------------------------------------------------------------------------- #
def map_legacy_session_id(legacy_id: str, warnings: list[str] | None = None) -> str:
    """把旧 ID 映射为 32 位小写 hex；非 32 hex 时确定性哈希，避免随机 ID。"""

    value = (legacy_id or "").strip()
    if not value:
        raise ValueError("缺少会话 ID")
    if is_session_id(value):
        return value
    mapped = sha256_text(value)[:32]
    if warnings is not None:
        warnings.append(f"legacy_id_remapped:{value}->{mapped}")
    return mapped


def normalize_timestamp(raw: Any, *, fallback: str, warnings: list[str] | None = None) -> str:
    """归一化旧时间戳：缺时区按 UTC 处理并记录迁移假设，无法解析则回退。"""

    if not isinstance(raw, str) or not raw.strip():
        return fallback
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        if warnings is not None:
            warnings.append("invalid_timestamp_fallback")
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
        if warnings is not None:
            warnings.append("timestamp_assumed_utc")
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _message_stage(phase: str) -> str:
    return _STAGE_BY_PHASE.get(phase, MessageStage.clarify.value)


def _map_role(role: Any, warnings: list[str]) -> tuple[str, str | None]:
    """旧消息角色 → (sender_kind, role_code|None)。"""

    name = str(role or "").strip().lower()
    if name in _HUMAN_ROLES:
        return SenderKind.human.value, None
    if name in _AGENT_ROLES:
        return SenderKind.agent.value, None
    if name in _EXPERT_ROLE_VALUES:
        return SenderKind.agent.value, name
    if name:
        warnings.append(f"unknown_message_role:{name}")
    else:
        warnings.append("missing_message_role")
    return SenderKind.agent.value, None


def _parse_messages(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("messages 不是数组")
    messages: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            messages.append(item)
    return messages


def _first_human_content(messages: Iterable[dict[str, Any]]) -> str:
    for item in messages:
        role = str(item.get("role") or "").strip().lower()
        if role in _HUMAN_ROLES:
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return ""


# --------------------------------------------------------------------------- #
# 读取与计划
# --------------------------------------------------------------------------- #
def load_sources(source_dir: Path) -> list[SourceFile]:
    """读取目录顶层所有 ``*.json``（按文件名排序，保证计划确定性）。"""

    files = sorted(source_dir.glob("*.json"), key=lambda item: item.name)
    sources: list[SourceFile] = []
    for path in files:
        data = path.read_bytes()
        entry = SourceFile(
            path=path,
            source_path=str(path.resolve()),
            size_bytes=len(data),
            source_hash=hashlib.sha256(data).hexdigest(),
        )
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            entry.error = f"JSON 解析失败：{type(exc).__name__}"
            sources.append(entry)
            continue
        if not isinstance(payload, dict):
            entry.error = "顶层 JSON 不是对象"
            sources.append(entry)
            continue
        entry.payload = payload
        sources.append(entry)
    return sources


def build_plan(source: SourceFile, *, now_iso: str) -> ImportPlan:
    """把已解析的源文件转换为导入计划；结构非法时抛 ``ValueError``。"""

    if source.payload is None:
        raise ValueError(source.error or "源文件没有可用内容")
    payload = source.payload
    warnings: list[str] = []

    raw_id = payload.get("id")
    legacy_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else ""
    if not legacy_id:
        legacy_id = source.path.stem.strip()
        warnings.append("id_missing_using_filename")
    if not legacy_id:
        raise ValueError("缺少会话 ID")

    session_id = map_legacy_session_id(legacy_id, warnings)
    raw_phase = payload.get("phase")
    legacy_phase = raw_phase.strip().lower() if isinstance(raw_phase, str) else ""
    if legacy_phase not in LEGACY_PHASE_MAP:
        warnings.append(f"unknown_legacy_phase:{legacy_phase or '<empty>'}")
        legacy_phase = "brainstorm"
    phase = LEGACY_PHASE_MAP[legacy_phase]

    raw_owner = payload.get("owner_email")
    owner = raw_owner.strip() if isinstance(raw_owner, str) else ""
    owner_isolated = not owner
    if owner_isolated:
        owner = LEGACY_UNKNOWN_OWNER
        warnings.append("owner_missing_isolated")

    messages = _parse_messages(payload.get("messages"))
    history_incomplete = len(messages) >= LEGACY_MAX_MESSAGES
    if history_incomplete:
        warnings.append("history_incomplete")

    problem_statement = payload.get("problem_statement")
    if not isinstance(problem_statement, str) or not problem_statement.strip():
        problem_statement = _first_human_content(messages)

    created_at = normalize_timestamp(
        payload.get("created_at"), fallback=now_iso, warnings=warnings
    )
    for item in messages:
        normalize_timestamp(item.get("timestamp"), fallback=created_at, warnings=warnings)

    canvas_tree = payload.get("canvas_tree")
    canvas_last_msg_index = payload.get("canvas_last_msg_index")
    if canvas_tree is not None:
        warnings.append("legacy_canvas_unverified")

    is_legacy_interview = legacy_phase == "interview"
    try:
        question_count = int(payload.get("interview_question_count") or 0)
    except (TypeError, ValueError):
        question_count = 0
        warnings.append("invalid_interview_question_count")
    question_count = max(0, min(question_count, 18))
    dimensions_raw = payload.get("interview_dimensions_covered")
    dimensions = (
        [str(item) for item in dimensions_raw if isinstance(item, str)]
        if isinstance(dimensions_raw, list)
        else []
    )
    if is_legacy_interview:
        warnings.append("legacy_interview_cancelled:legacy_unstructured")

    return ImportPlan(
        source=source,
        legacy_id=legacy_id,
        session_id=session_id,
        phase=phase,
        owner_email=owner,
        owner_isolated=owner_isolated,
        problem_statement=problem_statement if isinstance(problem_statement, str) else "",
        created_at=created_at,
        messages=messages,
        canvas_tree=canvas_tree,
        canvas_last_msg_index=canvas_last_msg_index,
        interview_question_count=question_count,
        interview_dimensions=dimensions,
        is_legacy_interview=is_legacy_interview,
        history_incomplete=history_incomplete,
        warnings=warnings,
    )


def _dedupe_duplicates(plans: Sequence[ImportPlan]) -> dict[str, list[str]]:
    """返回 ``legacy_id -> [source_path, ...]``（仅出现多次的 ID）。"""

    grouped: dict[str, list[str]] = {}
    for plan in plans:
        grouped.setdefault(plan.legacy_id, []).append(plan.source.source_path)
    return {legacy_id: paths for legacy_id, paths in grouped.items() if len(paths) > 1}


# --------------------------------------------------------------------------- #
# 行构造
# --------------------------------------------------------------------------- #
def _build_session_row(plan: ImportPlan, now_iso: str) -> SessionRow:
    return SessionRow(
        id=plan.session_id,
        owner_email=plan.owner_email,
        phase=plan.phase,
        session_version=1,
        current_product_version=1,
        current_audit_id=None,
        active_run_id=None,
        current_control_id=None,
        problem_statement=plan.problem_statement,
        brief_status="draft",
        next_message_seq=plan.message_count + 1,
        created_at=plan.created_at,
        updated_at=now_iso,
        deleted_at=None,
    )


def _build_alias_row(plan: ImportPlan, now_iso: str) -> SessionIdAliasRow | None:
    """旧 ID 被确定性重映射时登记别名，让旧书签链接仍可打开（PRD §8.1）。"""

    if plan.legacy_id == plan.session_id:
        return None
    return SessionIdAliasRow(
        legacy_id=plan.legacy_id,
        session_id=plan.session_id,
        source_path=plan.source.source_path,
        created_at=now_iso,
    )


def _build_message_rows(plan: ImportPlan, now_iso: str) -> list[MessageRow]:
    rows: list[MessageRow] = []
    stage = _message_stage(plan.phase)
    for index, item in enumerate(plan.messages, start=1):
        sender_kind, role_code = _map_role(item.get("role"), plan.warnings)
        content = item.get("content")
        rows.append(
            MessageRow(
                id=new_id(),
                session_id=plan.session_id,
                seq=index,
                sender_kind=sender_kind,
                role_code=role_code,
                stage=stage,
                content=content if isinstance(content, str) else "",
                status="completed",
                run_id=None,
                audit_id=None,
                created_at=normalize_timestamp(
                    item.get("timestamp"), fallback=plan.created_at
                ),
            )
        )
    return rows


def _build_product_state_row(plan: ImportPlan, now_iso: str) -> ProductStateRow:
    state = initial_product_state(plan.session_id)
    return ProductStateRow(
        session_id=plan.session_id,
        version=state.version,
        schema_version=state.schema_version,
        payload_json=canonical_json(state.model_dump(mode="json")),
        source_run_id=None,
        created_at=plan.created_at,
    )


def _build_canvas_artifact(plan: ImportPlan, now_iso: str) -> LegacyArtifactRow | None:
    if plan.canvas_tree is None:
        return None
    payload = {
        "canvas_tree": plan.canvas_tree,
        "canvas_last_msg_index": plan.canvas_last_msg_index,
        "source_text_verified": False,
        "note": "旧画布原样归档；source_text 未经验证，仅供浏览历史。",
    }
    return LegacyArtifactRow(
        id=new_id(),
        session_id=plan.session_id,
        kind=ARTIFACT_KIND_CANVAS,
        payload_json=canonical_json(payload),
        source_path=plan.source.source_path,
        created_at=now_iso,
    )


def _build_interview_rows(
    plan: ImportPlan, now_iso: str
) -> tuple[AuditRow, LegacyArtifactRow]:
    audit = AuditRow(
        id=new_id(),
        session_id=plan.session_id,
        product_version=1,
        audit_version=1,
        status="cancelled",
        question_count=plan.interview_question_count,
        end_reason="legacy_unstructured",
        dimension_states_json=canonical_json([]),
        report_json=None,
        active_question_id=None,
        pending_continuation=0,
        close_requested=0,
        closing_control_id=None,
        created_at=plan.created_at,
        updated_at=now_iso,
    )
    artifact_payload = {
        "legacy_phase": "interview",
        "interview_dimensions_covered": plan.interview_dimensions,
        "interview_question_count": plan.interview_question_count,
        "note": "旧关键词维度仅表示“曾讨论”，未映射为 supported；活动面试已结束为 cancelled。",
    }
    artifact = LegacyArtifactRow(
        id=new_id(),
        session_id=plan.session_id,
        kind=ARTIFACT_KIND_INTERVIEW,
        payload_json=canonical_json(artifact_payload),
        source_path=plan.source.source_path,
        created_at=now_iso,
    )
    return audit, artifact


def _build_record(
    *,
    source: SourceFile,
    session_id: str | None,
    status: str,
    warnings: Sequence[str],
    now_iso: str,
) -> MigrationRecordRow:
    return MigrationRecordRow(
        source_path=source.source_path,
        source_hash=source.source_hash,
        session_id=session_id,
        import_status=status,
        warnings_json=canonical_json(list(warnings)),
        imported_at=now_iso,
    )


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def dry_run_report(source_dir: Path, plans: Sequence[ImportPlan], sources: Sequence[SourceFile]) -> dict[str, Any]:
    """构造 dry-run 摘要：只含计数与旧 ID，不含正文/邮箱。"""

    duplicates = _dedupe_duplicates(plans)
    invalid = [
        {"source_path": source.source_path, "error": source.error or "结构非法"}
        for source in sources
        if source.payload is None
    ]
    planned_message_count = sum(plan.message_count for plan in plans)
    planned_canvases = sum(1 for plan in plans if plan.canvas_tree is not None)
    planned_interviews = sum(1 for plan in plans if plan.is_legacy_interview)
    warning_counts = Counter(
        warning.split(":", 1)[0] for plan in plans for warning in plan.warnings
    )
    return {
        "mode": "dry-run",
        "source": str(source_dir),
        "scanned": len(sources),
        "valid": len(plans),
        "invalid": invalid,
        "empty_owner_sessions": sum(1 for plan in plans if plan.owner_isolated),
        "remapped_legacy_ids": sum(
            1 for plan in plans if not is_session_id(plan.legacy_id)
        ),
        "duplicate_legacy_ids": duplicates,
        "history_incomplete_sessions": sum(1 for plan in plans if plan.history_incomplete),
        "legacy_phases": dict(Counter(plan.phase for plan in plans)),
        "interview_sessions": planned_interviews,
        "canvas_artifacts": planned_canvases,
        "planned": {
            "sessions": len(plans),
            "messages": planned_message_count,
            "product_states": len(plans),
            "legacy_audits": planned_interviews,
            "legacy_artifacts": planned_canvases + planned_interviews,
        },
        "warning_counts": dict(warning_counts),
    }


def _write_manifest(
    manifest_dir: Path,
    source_dir: Path,
    sources: Sequence[SourceFile],
    plans: Sequence[ImportPlan],
    now_iso: str,
) -> Path:
    """在写库前落盘备份清单（源路径 + SHA-256 + 大小），不复制/移动源文件。"""

    manifest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = manifest_dir / f"import_manifest_{stamp}.json"
    plan_by_path = {plan.source.source_path: plan for plan in plans}
    entries: list[dict[str, Any]] = []
    for source in sources:
        plan = plan_by_path.get(source.source_path)
        entries.append(
            {
                "source_path": source.source_path,
                "source_hash": source.source_hash,
                "size_bytes": source.size_bytes,
                "valid": source.payload is not None,
                "legacy_id": plan.legacy_id if plan is not None else None,
                "session_id": plan.session_id if plan is not None else None,
                "error": source.error,
            }
        )
    payload = {
        "generated_at": now_iso,
        "source": str(source_dir),
        "files": entries,
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


# --------------------------------------------------------------------------- #
# 应用导入
# --------------------------------------------------------------------------- #
def _import_plan(db: Session, plan: ImportPlan, now_iso: str) -> None:
    """在同一事务中写入会话、消息、产品状态、归档与控制记录。"""

    db.add(_build_session_row(plan, now_iso))
    db.flush()
    db.add(_build_product_state_row(plan, now_iso))
    alias = _build_alias_row(plan, now_iso)
    if alias is not None:
        db.add(alias)
    for row in _build_message_rows(plan, now_iso):
        db.add(row)
    if plan.canvas_tree is not None:
        db.add(_build_canvas_artifact(plan, now_iso))
    if plan.is_legacy_interview:
        audit, artifact = _build_interview_rows(plan, now_iso)
        db.add(audit)
        db.add(artifact)
    db.flush()


def apply_import(source_dir: Path, manifest_dir: Path) -> dict[str, Any]:
    """执行导入并返回摘要；源文件只读，重复执行幂等。"""

    sources = load_sources(source_dir)
    now_iso = utc_now_iso()
    plans: list[ImportPlan] = []
    invalid: list[dict[str, Any]] = []
    for source in sources:
        if source.payload is None:
            invalid.append({"source_path": source.source_path, "error": source.error or "结构非法"})
            continue
        try:
            plans.append(build_plan(source, now_iso=now_iso))
        except ValueError as exc:
            invalid.append({"source_path": source.source_path, "error": str(exc)})

    duplicates = _dedupe_duplicates(plans)
    duplicate_paths = {
        path for paths in duplicates.values() for path in paths[1:]
    }

    manifest_path = _write_manifest(manifest_dir, source_dir, sources, plans, now_iso)

    database.initialize_database()
    imported = 0
    skipped = 0
    conflicts: list[dict[str, Any]] = []
    counts = Counter()

    with database.session_scope() as db:
        for source in sources:
            if source.payload is None:
                existing = db.get(MigrationRecordRow, source.source_path)
                if existing is not None:
                    if existing.source_hash == source.source_hash:
                        skipped += 1
                    else:
                        conflicts.append(
                            {
                                "source_path": source.source_path,
                                "reason": "源文件内容已变化但已有迁移记录",
                            }
                        )
                    continue
                db.add(
                    _build_record(
                        source=source,
                        session_id=None,
                        status=_INVALID,
                        warnings=[f"invalid_source:{source.error or '结构非法'}"],
                        now_iso=now_iso,
                    )
                )
                db.flush()
                continue

            try:
                plan = build_plan(source, now_iso=now_iso)
            except ValueError as exc:
                db.add(
                    _build_record(
                        source=source,
                        session_id=None,
                        status=_INVALID,
                        warnings=[f"invalid_source:{exc}"],
                        now_iso=now_iso,
                    )
                )
                db.flush()
                continue

            existing = db.get(MigrationRecordRow, plan.source.source_path)
            if existing is not None:
                if existing.source_hash == plan.source.source_hash:
                    skipped += 1
                else:
                    conflicts.append(
                        {
                            "source_path": plan.source.source_path,
                            "reason": "源文件内容已变化但已有迁移记录",
                        }
                    )
                continue

            if plan.source.source_path in duplicate_paths:
                db.add(
                    _build_record(
                        source=plan.source,
                        session_id=plan.session_id,
                        status=_CONFLICT,
                        warnings=[f"duplicate_legacy_id:{plan.legacy_id}", *plan.warnings],
                        now_iso=now_iso,
                    )
                )
                db.flush()
                conflicts.append(
                    {"source_path": plan.source.source_path, "reason": f"重复旧 ID：{plan.legacy_id}"}
                )
                continue

            if db.get(SessionRow, plan.session_id) is not None:
                db.add(
                    _build_record(
                        source=plan.source,
                        session_id=plan.session_id,
                        status=_CONFLICT,
                        warnings=["session_id_conflict:目标会话已存在", *plan.warnings],
                        now_iso=now_iso,
                    )
                )
                db.flush()
                conflicts.append(
                    {"source_path": plan.source.source_path, "reason": "目标会话 ID 已存在"}
                )
                continue

            _import_plan(db, plan, now_iso)
            db.add(
                _build_record(
                    source=plan.source,
                    session_id=plan.session_id,
                    status=_IMPORTED,
                    warnings=plan.warnings,
                    now_iso=now_iso,
                )
            )
            db.flush()
            imported += 1
            counts["messages"] += plan.message_count
            counts["product_states"] += 1
            if plan.canvas_tree is not None:
                counts["legacy_artifacts"] += 1
            if plan.is_legacy_interview:
                counts["legacy_audits"] += 1
                counts["legacy_artifacts"] += 1

    return {
        "mode": "apply",
        "source": str(source_dir),
        "manifest": str(manifest_path),
        "scanned": len(sources),
        "imported_sessions": imported,
        "skipped": skipped,
        "conflicts": conflicts,
        "invalid": invalid,
        "messages_imported": counts["messages"],
        "product_states_imported": counts["product_states"],
        "legacy_audits_imported": counts["legacy_audits"],
        "legacy_artifacts_imported": counts["legacy_artifacts"],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m migrations.import_json_sessions",
        description="把旧 data/sessions/*.json 导入 v4 SQLite 数据库（只读源文件）。",
    )
    parser.add_argument("--source", required=True, help="旧会话 JSON 所在目录（绝对路径）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只打印迁移计划，不写数据库")
    mode.add_argument("--apply", action="store_true", help="执行导入（写备份清单后写库）")
    parser.add_argument(
        "--manifest-dir",
        default=None,
        help="备份清单输出目录（默认 backend/data/migration_backups）",
    )
    return parser


def default_manifest_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "migration_backups"


def _emit_json(payload: Mapping[str, Any]) -> None:
    """把结果以 UTF-8 JSON 打到标准输出。

    Windows 下 Python 默认按 locale（多为 cp936）编码管道输出，Git Bash 等 UTF-8
    终端会把中文原因/路径显示成乱码。这里显式切到 UTF-8；测试用 ``StringIO``
    重定向时没有 ``reconfigure``，忽略即可（字符串本身与编码无关）。
    """

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    print(text)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口；返回进程退出码。"""

    args = build_parser().parse_args(argv)
    source_dir = Path(args.source).expanduser()
    if not source_dir.is_absolute():
        source_dir = (Path.cwd() / source_dir).resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        _emit_json({"mode": "error", "error": f"源目录不存在或不是目录：{source_dir}"})
        return 2

    if args.dry_run:
        sources = load_sources(source_dir)
        plans: list[ImportPlan] = []
        now_iso = utc_now_iso()
        for source in sources:
            if source.payload is None:
                continue
            try:
                plans.append(build_plan(source, now_iso=now_iso))
            except ValueError:
                continue
        _emit_json(dry_run_report(source_dir, plans, sources))
        return 0

    manifest_dir = Path(args.manifest_dir) if args.manifest_dir else default_manifest_dir()
    summary = apply_import(source_dir, manifest_dir.resolve())
    _emit_json(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())