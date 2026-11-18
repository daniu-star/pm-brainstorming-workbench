"""上下文构建（FR-02，M1 §4.2）。

一个函数、一条严格装配线，负责把"这一轮模型该看到什么"变成 ``ContextBundle``：

1. **角色职责与输出协议**（``role_protocol``，system）——来自 ``core.role_prompts``；
2. **受保护产品定义与约束**（``product_definition``）——Brief 七字段 + 状态；
3. **相关方案 / 决定 / 未决事项**（``product_state``）；
4. **相关证据**（``evidence``）；
5. **当前审计问题及回答**（``audit_question``）；
6. **最近相关对话**（``conversation``）；
7. **当前输入**（``current_input``）。

注入顺序即 ``included_sections`` 的顺序，也是消息数组的顺序（PRD FR-02 显式要求）。

预算策略（``max_input_tokens`` 默认 ``MAX_CONTEXT_INPUT_TOKENS``）：

* 1-3-5-7 属于"必需区"，只做整体判定：必需区自身超预算 → 抛
  ``AppError(CONTEXT_TOO_LARGE, http_status=422)``，**绝不静默截断核心内容**；
* 剩余预算按"证据高分优先 → 对话新消息优先"填充；预算不足时先丢最旧的对话，
  再丢低分的证据，并写入 ``omitted_sections`` / ``notes``；
* 受保护定义（标题、目标用户、核心问题、成功标准、硬约束）任何情况下都不移除，
  非受保护 Brief 字段仅在渲染时压缩长度，不参与丢弃。

纯读：本模块只接受 db session，不提交事务、不写库。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.role_prompts import PROMPT_VERSION, get_prompt, prompt_name_for_role
from domain.contracts import (
    BRIEF_FIELDS,
    MAX_CONTEXT_INPUT_TOKENS,
    ContextBundle,
    ContextSection,
    LLMMessage,
    MessageStage,
    ProductState,
    RoleCode,
    TaskKind,
    initial_product_state,
)
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

__all__ = [
    "INJECTION_ORDER",
    "MAX_CONVERSATION_MESSAGES",
    "MAX_MESSAGE_CHARS",
    "PROTECTED_BRIEF_FIELDS",
    "SECTION_TITLES",
    "build_context",
    "estimate_tokens",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 注入顺序（= ``included_sections`` 顺序 = 消息顺序），不可随意调整。
INJECTION_ORDER: tuple[ContextSection, ...] = (
    ContextSection.role_protocol,
    ContextSection.product_definition,
    ContextSection.product_state,
    ContextSection.evidence,
    ContextSection.audit_question,
    ContextSection.conversation,
    ContextSection.current_input,
)

#: 分节中文标题（写进 user 消息，模型据此定位信息）。
SECTION_TITLES: dict[ContextSection, str] = {
    ContextSection.role_protocol: "角色职责与输出协议",
    ContextSection.product_definition: "受保护产品定义与约束",
    ContextSection.product_state: "相关方案 / 决定 / 未决事项",
    ContextSection.evidence: "相关证据",
    ContextSection.audit_question: "当前审计问题及回答",
    ContextSection.conversation: "最近相关对话",
    ContextSection.current_input: "当前输入",
}

#: 受保护 Brief 字段：任何压缩都不得移除（PRD FR-02 / AC）。
PROTECTED_BRIEF_FIELDS: tuple[str, ...] = (
    "target_users",
    "core_problem",
    "success_criteria",
    "constraints",
)

#: 非受保护 Brief 字段的渲染上限（可压缩，不可删除整节）。
UNPROTECTED_FIELD_CHARS = 600

#: 最近对话：最多取 20 条，单条超 2000 字截断并标注。
MAX_CONVERSATION_MESSAGES = 20
MAX_MESSAGE_CHARS = 2000
MAX_EXCERPT_CHARS = 500
MAX_ITEM_CHARS = 240

#: 各列表进入上下文的条数上限（防止状态膨胀吞掉全部预算）。
MAX_OPTIONS_IN_CONTEXT = 5
MAX_DECISIONS_IN_CONTEXT = 5
MAX_ISSUES_IN_CONTEXT = 6
MAX_EXPERIMENTS_IN_CONTEXT = 3

#: 证据核验等级 → 保留权重（越高越先进入预算）。
VERIFICATION_WEIGHT: dict[str, int] = {
    "user_confirmed": 3,
    "source_checked": 2,
    "unverified": 1,
}

_TRUNCATED = "…（已截断）"

_CJK_RE = re.compile(
    "[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
)

_BRIEF_LABELS: tuple[tuple[str, str], ...] = (
    ("target_users", "目标用户"),
    ("usage_scenario", "使用场景"),
    ("core_problem", "核心问题"),
    ("current_alternatives", "现有替代方案"),
    ("product_form", "产品形态"),
    ("constraints", "硬约束"),
    ("success_criteria", "成功标准"),
)


# --------------------------------------------------------------------------- #
# Token 估算
# --------------------------------------------------------------------------- #


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 字符 ≈ 1 token，其余按 ``len/4``；空串 0，非空至少 1。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return max(1, cjk + other // 4)


# --------------------------------------------------------------------------- #
# 内部结构
# --------------------------------------------------------------------------- #


@dataclass
class _Unit:
    """一个不可再分的装配单元（证据一条 / 对话一条 / 整节一段）。"""

    text: str
    ref_id: str | None = None
    seq: int = 0


@dataclass
class _Section:
    section: ContextSection
    units: list[_Unit] = field(default_factory=list)
    droppable: bool = False
    #: 预算填充顺序（仅 droppable 节有意义）。
    fill_order: str = "as_units"
    kept: list[_Unit] = field(default_factory=list)


def _clip(text: str | None, limit: int, *, mark: bool = True) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    body = text[: max(0, limit - len(_TRUNCATED))]
    return body + _TRUNCATED if mark else body


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _join(units: list[_Unit]) -> str:
    return "\n".join(unit.text for unit in units if unit.text)


# --------------------------------------------------------------------------- #
# 只读数据访问（严格走 repository 公开 API，不做 ORM 回退）
# --------------------------------------------------------------------------- #


def _session_repo():
    from repositories import session_repo

    return session_repo


def _audit_repo():
    from repositories import audit_repo

    return audit_repo


def _load_session(db, session_id: str) -> Any:
    repo = _session_repo()
    session = repo.get_session(db, session_id, include_deleted=True)
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", details={"session_id": session_id})
    return session


def _load_product_state(db, session_id: str, product_version: int) -> ProductState:
    repo = _session_repo()
    state = repo.get_product_state(db, session_id, version=product_version)
    if state is None:  # pragma: no cover - 契约保证返回初始状态
        state = initial_product_state(session_id)
    return state


def _load_message(db, session_id: str, message_id: str) -> Any:
    repo = _session_repo()
    message = repo.get_message(db, session_id, message_id)
    if message is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            "当前输入消息不存在",
            details={"session_id": session_id, "message_id": message_id},
        )
    return message


def _list_messages(db, session_id: str, *, stage: MessageStage, audit_id: str | None) -> list[Any]:
    repo = _session_repo()
    kwargs: dict[str, Any] = {"stage": stage, "limit": 100}
    if audit_id is not None:
        kwargs["audit_id"] = audit_id
    return list(repo.list_messages(db, session_id, **kwargs) or [])


def _list_evidence(db, session_id: str) -> list[Any]:
    repo = _session_repo()
    return list(repo.list_evidence(db, session_id, limit=50) or [])


# --------------------------------------------------------------------------- #
# 分节构造
# --------------------------------------------------------------------------- #


def _allowed_stages(task_kind: TaskKind | str) -> tuple[MessageStage, ...]:
    """不同任务允许进入"最近对话"的 stage（始终排除 ``system``）。"""
    task = _enum_value(task_kind)
    if task.startswith("audit"):
        return (MessageStage.audit,)
    if task == TaskKind.clarify.value:
        return (MessageStage.clarify,)
    return (MessageStage.clarify, MessageStage.brainstorm)


def _role_protocol_section(role_code: RoleCode | str, prompt_version: str) -> _Section:
    prompt = get_prompt(prompt_name_for_role(role_code), prompt_version)
    return _Section(
        section=ContextSection.role_protocol,
        units=[_Unit(text=prompt)],
        droppable=False,
    )


def _product_definition_section(session: Any, state: ProductState) -> _Section:
    brief = state.brief
    title = getattr(session, "problem_statement", None) or "（未命名）"
    lines = [
        f"标题（受保护，不得改写）：{_clip(title, 300)}",
        f"简报状态：{_enum_value(brief.status)}",
    ]
    for name, label in _BRIEF_LABELS:
        item = getattr(brief, name, None)
        value = (item.value if item is not None else None) or ""
        status = _enum_value(item.status) if item is not None else "unknown"
        protected = name in PROTECTED_BRIEF_FIELDS
        if value:
            rendered = value if protected else _clip(value, UNPROTECTED_FIELD_CHARS)
        else:
            rendered = "（未填写）"
        marker = "（受保护）" if protected else ""
        lines.append(f"{label}{marker}[{status}]：{rendered}")
    missing = brief.required_missing()
    if missing:
        lines.append("仍缺必填：" + "、".join(missing))
    if brief.skipped:
        lines.append("用户已跳过澄清：本次按已知信息继续，不得虚构缺失内容。")
    return _Section(
        section=ContextSection.product_definition,
        units=[_Unit(text="\n".join(lines))],
        droppable=False,
    )


def _product_state_section(state: ProductState) -> _Section | None:
    lines: list[str] = []

    if state.options:
        lines.append("### 已有方案")
        for option in state.options[:MAX_OPTIONS_IN_CONTEXT]:
            detail = _clip(option.summary, MAX_ITEM_CHARS) if option.summary else ""
            lines.append(
                f"- [{option.id}] {option.title}"
                f"（状态：{_enum_value(option.status)}）" + (f"：{detail}" if detail else "")
            )

    if state.decisions:
        lines.append("### 已确认/候选决策")
        for decision in state.decisions[:MAX_DECISIONS_IN_CONTEXT]:
            lines.append(
                f"- [{decision.id}] 方案={decision.option_id or '未指定'}"
                f"（状态：{_enum_value(decision.status)}）："
                f"{_clip(decision.rationale, MAX_ITEM_CHARS)}"
            )

    if state.unresolved_issues:
        lines.append("### 未决事项")
        for issue in state.unresolved_issues[:MAX_ISSUES_IN_CONTEXT]:
            lines.append(
                f"- [{issue.id}]（{issue.kind}，{_enum_value(issue.status)}）"
                f"{_clip(issue.text, MAX_ITEM_CHARS)}"
            )

    if state.experiments:
        lines.append("### 验证实验")
        for experiment in state.experiments[:MAX_EXPERIMENTS_IN_CONTEXT]:
            lines.append(
                f"- [{experiment.id}]（{_enum_value(experiment.status)}）"
                f"{_clip(experiment.method, MAX_ITEM_CHARS)}"
            )

    if not lines:
        return None
    return _Section(
        section=ContextSection.product_state,
        units=[_Unit(text="\n".join(lines))],
        droppable=False,
    )


def _evidence_section(rows: list[Any], notes: list[str]) -> _Section | None:
    if not rows:
        return None

    def _weight(row: Any) -> int:
        return VERIFICATION_WEIGHT.get(_enum_value(row.verification_status), 1)

    ranked = sorted(enumerate(rows), key=lambda pair: (-_weight(pair[1]), pair[0]))
    units: list[_Unit] = []
    for _index, row in ranked:
        block = [
            f"[{row.id}] {getattr(row, 'title', None) or '（无标题）'}",
            f"类型：{_enum_value(getattr(row, 'kind', ''))}｜核验：{_enum_value(row.verification_status)}",
        ]
        excerpt = getattr(row, "excerpt", None)
        if excerpt:
            block.append("摘要：" + _clip(excerpt, MAX_EXCERPT_CHARS))
        if getattr(row, "source_uri", None):
            block.append(f"来源：{row.source_uri}")
        if getattr(row, "limitations", None):
            block.append("局限：" + _clip(row.limitations, MAX_ITEM_CHARS))
        units.append(_Unit(text="\n".join(block), ref_id=row.id))
    notes.append(f"证据 {len(units)} 条按核验等级排序（user_confirmed > source_checked > unverified）")
    return _Section(
        section=ContextSection.evidence,
        units=units,
        droppable=True,
        fill_order="score_desc",
    )


def _audit_section(db, audit_id: str | None) -> _Section | None:
    if audit_id is None:
        return None
    repo = _audit_repo()
    audit = repo.get_audit(db, audit_id)
    if audit is None:
        return None
    active_id = getattr(audit, "active_question_id", None)
    if not active_id:
        return None
    question = repo.get_question(db, audit_id, active_id)
    if question is None:
        return None
    lines = [
        f"问题编号：{getattr(question, 'id', active_id)}",
        f"维度：{_enum_value(getattr(question, 'dimension', ''))}",
        "问题：" + _clip(getattr(question, "question_text", "") or "", 1000),
    ]
    rationale = getattr(question, "rationale", None)
    if rationale:
        lines.append("提问理由：" + _clip(rationale, 600))
    answer_id = getattr(question, "answer_message_id", None)
    if answer_id:
        lines.append(f"回答消息 id：{answer_id}")
    assessment = getattr(question, "assessment", None)
    if assessment is not None:
        lines.append(f"当前评估：{_enum_value(assessment)}")
    return _Section(
        section=ContextSection.audit_question,
        units=[_Unit(text="\n".join(lines))],
        droppable=False,
    )


def _sender_label(row: Any) -> str:
    sender = _enum_value(row.sender_kind)
    if sender == "human":
        return "用户"
    role = getattr(row, "role_code", None)
    if role:
        return f"AI·{_enum_value(role)}"
    return "AI"


def _message_unit(row: Any) -> _Unit:
    content = row.content or ""
    if len(content) > MAX_MESSAGE_CHARS:
        content = _clip(content, MAX_MESSAGE_CHARS)
    text = f"[#{row.seq}] {_sender_label(row)}：{content}"
    return _Unit(text=text, ref_id=row.id, seq=int(row.seq or 0))


def _conversation_section(
    db,
    session_id: str,
    *,
    task_kind: TaskKind | str,
    audit_id: str | None,
    exclude_message_id: str | None,
    notes: list[str],
) -> _Section | None:
    rows: list[Any] = []
    seen: set[str] = set()
    for stage in _allowed_stages(task_kind):
        # 审计内部消息只在审计任务内出现；其余任务按 stage 过滤（system 永不带入）。
        stage_audit = audit_id if stage is MessageStage.audit else None
        for row in _list_messages(db, session_id, stage=stage, audit_id=stage_audit):
            if row.id in seen:
                continue
            if row.id == exclude_message_id:
                continue
            seen.add(row.id)
            rows.append(row)

    rows.sort(key=lambda row: int(row.seq or 0))
    truncated = [row.seq for row in rows if len(row.content or "") > MAX_MESSAGE_CHARS]
    newest = rows[-MAX_CONVERSATION_MESSAGES:] if rows else []
    if len(rows) > MAX_CONVERSATION_MESSAGES:
        notes.append(
            f"最近对话仅保留最新 {MAX_CONVERSATION_MESSAGES} 条，更早的 {len(rows) - MAX_CONVERSATION_MESSAGES} 条未纳入"
        )
    if truncated:
        notes.append(f"对话消息超过 {MAX_MESSAGE_CHARS} 字已截断：seq={truncated[:5]}")
    if not newest:
        return None
    units = [_message_unit(row) for row in newest]
    return _Section(
        section=ContextSection.conversation,
        units=units,
        droppable=True,
        fill_order="newest_first",
    )


def _current_input_section(row: Any) -> _Section:
    return _Section(
        section=ContextSection.current_input,
        units=[_message_unit(row)],
        droppable=False,
    )


# --------------------------------------------------------------------------- #
# 预算
# --------------------------------------------------------------------------- #


def _section_tokens(units: list[_Unit]) -> int:
    if not units:
        return 0
    return estimate_tokens(_join(units))


def _apply_budget(
    sections: list[_Section],
    *,
    max_input_tokens: int,
    notes: list[str],
) -> tuple[dict[ContextSection, list[_Unit]], list[ContextSection]]:
    """按优先级填充预算；返回 (保留的 unit, 被丢弃的 section)。"""
    if max_input_tokens <= 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "max_input_tokens 必须为正整数")

    required = [section for section in sections if not section.droppable]
    required_tokens = sum(_section_tokens(section.units) for section in required)
    if required_tokens > max_input_tokens:
        raise AppError(
            ErrorCode.CONTEXT_TOO_LARGE,
            "受保护的产品定义与当前输入已超过上下文预算，无法在不丢失核心内容的前提下继续",
            http_status=422,
            details={
                "required_tokens": required_tokens,
                "max_input_tokens": max_input_tokens,
            },
        )

    kept: dict[ContextSection, list[_Unit]] = {}
    for section in required:
        kept[section.section] = list(section.units)

    remaining = max_input_tokens - required_tokens
    omitted: list[ContextSection] = []

    # 填充顺序：先证据（高分优先），再对话（新消息优先）——因此预算不足时
    # 最先被丢弃的是最旧的对话，其次是低分证据（与 PRD FR-02 的优先级一致）。
    droppable = [section for section in sections if section.droppable]
    droppable.sort(key=lambda s: 0 if s.section is ContextSection.evidence else 1)

    for section in droppable:
        units = (
            list(section.units)
            if section.fill_order == "score_desc"
            else list(reversed(section.units))
        )
        picked: list[_Unit] = []
        for unit in units:
            cost = estimate_tokens(unit.text)
            if cost <= remaining:
                remaining -= cost
                picked.append(unit)
            else:
                notes.append(
                    f"预算不足，未纳入 {section.section.value} 中的 {len(units) - len(picked)} 条内容"
                )
                break
        if not picked:
            omitted.append(section.section)
            continue
        # 还原为自然顺序（对话按 seq 升序展示，证据按核验分降序已是目标顺序）。
        if section.fill_order != "score_desc":
            picked.reverse()
        kept[section.section] = picked

    return kept, omitted


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def build_context(
    db,
    *,
    session_id: str,
    task_kind: TaskKind,
    role_code: RoleCode,
    product_version: int,
    current_message_id: str | None = None,
    audit_id: str | None = None,
    prompt_version: str = PROMPT_VERSION,
    max_input_tokens: int = MAX_CONTEXT_INPUT_TOKENS,
) -> ContextBundle:
    """按固定顺序装配本轮上下文；纯读，不提交事务。"""
    if not session_id:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少 session_id")

    session = _load_session(db, session_id)
    state = _load_product_state(db, session_id, int(product_version))
    notes: list[str] = []
    if int(state.version) != int(product_version):
        notes.append(
            f"未找到版本 {product_version} 的产品状态，已使用版本 {state.version}"
        )

    current_row = None
    if current_message_id:
        current_row = _load_message(db, session_id, current_message_id)

    sections: list[_Section] = [
        _role_protocol_section(role_code, prompt_version),
        _product_definition_section(session, state),
    ]
    state_section = _product_state_section(state)
    if state_section is not None:
        sections.append(state_section)
    evidence_section = _evidence_section(_list_evidence(db, session_id), notes)
    if evidence_section is not None:
        sections.append(evidence_section)
    audit_section = _audit_section(db, audit_id)
    if audit_section is not None:
        sections.append(audit_section)
    conversation_section = _conversation_section(
        db,
        session_id,
        task_kind=task_kind,
        audit_id=audit_id,
        exclude_message_id=current_message_id,
        notes=notes,
    )
    if conversation_section is not None:
        sections.append(conversation_section)
    if current_row is not None:
        sections.append(_current_input_section(current_row))
    else:
        notes.append("本轮没有当前输入消息（current_message_id 为空）")

    sections.sort(key=lambda s: INJECTION_ORDER.index(s.section))

    kept, omitted = _apply_budget(
        sections, max_input_tokens=int(max_input_tokens), notes=notes
    )

    messages: list[LLMMessage] = []
    included: list[ContextSection] = []
    source_message_ids: list[str] = []
    evidence_refs: list[str] = []

    for section in INJECTION_ORDER:
        units = kept.get(section)
        if not units:
            continue
        body = _join(units)
        if not body:
            continue
        included.append(section)
        if section is ContextSection.role_protocol:
            messages.append(
                LLMMessage(role="system", content=body, source_label=section.value)
            )
        else:
            messages.append(
                LLMMessage(
                    role="user",
                    content=f"## {SECTION_TITLES[section]}\n{body}",
                    source_label=section.value,
                )
            )
        if section is ContextSection.conversation:
            source_message_ids.extend(
                unit.ref_id for unit in units if unit.ref_id is not None
            )
        elif section is ContextSection.current_input:
            source_message_ids.extend(
                unit.ref_id for unit in units if unit.ref_id is not None
            )
        elif section is ContextSection.evidence:
            evidence_refs.extend(unit.ref_id for unit in units if unit.ref_id is not None)

    history_cutoff_seq = 0
    for section in sections:
        if section.section is ContextSection.conversation and kept.get(section.section):
            history_cutoff_seq = max((unit.seq for unit in kept[section.section]), default=0)

    estimated = sum(estimate_tokens(message.content) for message in messages)

    return ContextBundle(
        messages=messages,
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        source_message_ids=list(dict.fromkeys(source_message_ids)),
        estimated_input_tokens=estimated,
        included_sections=included,
        omitted_sections=omitted,
        prompt_version=prompt_version,
        role_code=RoleCode(role_code) if not isinstance(role_code, RoleCode) else role_code,
        product_version=int(state.version),
        history_cutoff_seq=history_cutoff_seq,
        notes=notes,
    )
