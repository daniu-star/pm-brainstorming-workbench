"""Loop Studio 五阶段提示词（对应契约 §5.2）。

本模块**确定性、纯函数**：同样的 ``ctx`` / ``stage`` / ``prior`` 必然拼出同样的消息。
提示词只注入「产品定义 + 上阶段产物摘要 + focus + 证据段」，**绝不注入原始 JSON**，
避免把模型上一阶段的完整输出（尤其是代码原型）重新灌回上下文。

设计约束：

* 五阶段共用同一套硬约束（不得编造 ``evidence_id``、不确定即标 unknown、id 唯一、
  数量上限），差异只在「阶段职责」与「输出协议」；
* 输出协议直接声明字段名与取值范围，与 :mod:`domain.contracts` 的模型一致；
* ``<evidence>`` 段为空时必须在提示中明确「不得引用任何 evidence_id」。
"""
from __future__ import annotations

from typing import Any, Mapping

from domain.contracts import (
    BRIEF_FIELDS,
    LLMMessage,
    MAX_LOOP_ASSUMPTIONS,
    MAX_LOOP_BLOCKERS,
    MAX_LOOP_CASES,
    MAX_LOOP_CHECKS,
    MAX_LOOP_DIMENSIONS,
    MAX_LOOP_FEATURES,
    MAX_LOOP_FILE_CHARS,
    MAX_LOOP_FILES,
    LoopStage,
)

__all__ = [
    "PROMPT_VERSION",
    "PURPOSE_IDEATE",
    "PURPOSE_VALIDATE",
    "PURPOSE_DEVELOP",
    "PURPOSE_TEST",
    "PURPOSE_EVALUATE",
    "STAGE_PURPOSE",
    "STAGE_LABELS",
    "build_stage_messages",
]

#: 提示词版本；变更提示词结构时必须同步升级。
PROMPT_VERSION = "v5.0"

#: 各阶段 ``complete_json`` 的 purpose 值（预算与用量统计按此归集）。
PURPOSE_IDEATE = "loop.ideate"
PURPOSE_VALIDATE = "loop.validate"
PURPOSE_DEVELOP = "loop.develop"
PURPOSE_TEST = "loop.test"
PURPOSE_EVALUATE = "loop.evaluate"

STAGE_PURPOSE: dict[LoopStage, str] = {
    LoopStage.ideate: PURPOSE_IDEATE,
    LoopStage.validate: PURPOSE_VALIDATE,
    LoopStage.develop: PURPOSE_DEVELOP,
    LoopStage.test: PURPOSE_TEST,
    LoopStage.evaluate: PURPOSE_EVALUATE,
}

#: 阶段中文名（提示词与阶段摘要共用，避免出现两套叫法）。
STAGE_LABELS: dict[LoopStage, str] = {
    LoopStage.ideate: "方案构思",
    LoopStage.validate: "假设验证",
    LoopStage.develop: "原型开发",
    LoopStage.test: "测试执行",
    LoopStage.evaluate: "评分评审",
}

_BRIEF_LABELS: dict[str, str] = {
    "target_users": "目标用户",
    "usage_scenario": "使用场景",
    "core_problem": "核心问题",
    "current_alternatives": "现有替代方案",
    "product_form": "产品形态",
    "constraints": "约束条件",
    "success_criteria": "成功标准",
}

_STAGE_FOCUS: dict[LoopStage, str] = {
    LoopStage.ideate: (
        "把已确认的产品定义收敛成一个可交付的 MVP：给出功能清单、验收标准、"
        "关键假设与明确的「不做清单」。功能要能独立验收，禁止把多个目标揉进一条。"
    ),
    LoopStage.validate: (
        "逐条验证 MVP 的 must-have 功能与关键假设：设计可回答的检查项，"
        "能证伪的优先；没有证据的检查结果必须是 unknown，不得凭直觉写成 pass。"
    ),
    LoopStage.develop: (
        "产出可运行的最小原型：给出入口文件、运行命令与关键文件内容。"
        "原型只覆盖 must-have 功能，明确标注局限与需要人工构建的部分。"
    ),
    LoopStage.test: (
        "按 MVP 的验收标准设计并「模拟执行」测试用例：区分 passed / failed / skipped / untested，"
        "must-have 功能至少各一个用例。模拟执行必须在 notes 里写明「未实际执行」。"
    ),
    LoopStage.evaluate: (
        "基于前四阶段的实际产物给出评分卡：七个维度逐项打分并说明理由，"
        "列出阻塞项与下一步动作。评分必须可被确定性硬规则覆盖，不要为达标而虚高。"
    ),
}

_STAGE_PROTOCOL: dict[LoopStage, str] = {
    LoopStage.ideate: (
        '{"title": "MVP 标题", "summary": "一句话说明", '
        '"features": [{"id": "f1", "title": "功能名", "description": "做什么", '
        '"acceptance": "可验证的验收标准", "priority": "must|should|could", '
        '"effort": "S|M|L"}], '
        '"assumptions": [{"id": "a1", "statement": "假设内容", "risk": "风险", '
        '"verification": "如何验证"}], "not_in_scope": ["明确不做的范围"]}'
    ),
    LoopStage.validate: (
        '{"checks": [{"id": "c1", "feature_id": "f1", "question": "要回答的问题", '
        '"result": "pass|fail|unknown", "evidence_refs": ["证据 id"], "notes": "依据"}], '
        '"summary": "验证结论", "unknowns": ["仍未确认的问题"]}'
    ),
    LoopStage.develop: (
        '{"title": "原型标题", "summary": "原型说明", "entry_file": "入口文件路径", '
        '"run_command": "如何运行", '
        '"files": [{"path": "相对路径", "language": "python|javascript|...", '
        '"content": "完整文件内容", "purpose": "该文件作用"}], '
        '"limitations": "局限说明", "requires_human_build": true}'
    ),
    LoopStage.test: (
        '{"total": N, "passed": N, "failed": N, "skipped": N, "simulated": true, '
        '"cases": [{"id": "t1", "feature_id": "f1", "title": "用例名", '
        '"steps": ["步骤"], "expected": "预期结果", '
        '"status": "passed|failed|skipped|untested", "evidence": "执行依据"}], '
        '"notes": "说明；模拟执行时必须写「未实际执行」"}'
    ),
    LoopStage.evaluate: (
        '{"total": N, "dimensions": [{"key": "functionality", "score": 0, "max": 25, '
        '"reason": "打分理由"}], "verdict": "pending", "blockers": ["阻塞项"], '
        '"highlights": ["亮点"], "next_actions": ["下一步动作"], '
        '"generated_by": "model", "hard_capped": false}'
    ),
}

_SCORE_TABLE = (
    "评分维度（七个必须全部给出，max 固定，total 必须等于各维度 score 之和）："
    "functionality 0-25、feasibility 0-15、user_value 0-20、risk 0-15、"
    "delivery 0-10、testability 0-10、docs 0-5。"
)


def _system_prompt(stage: LoopStage) -> str:
    order = list(LoopStage).index(stage) + 1
    lines = [
        f"你是 PM Brainstorm Workbench 的 Loop Studio 交付工程师，"
        f"当前执行第 {order}/5 阶段「{STAGE_LABELS[stage]}」。",
        "",
        "阶段职责：",
        _STAGE_FOCUS[stage],
        "",
        "输出协议（只输出一个 JSON 对象，不要 Markdown、不要代码围栏、不要任何解释）：",
        _STAGE_PROTOCOL[stage],
    ]
    if stage == LoopStage.evaluate:
        lines.append(_SCORE_TABLE)
        lines.append("verdict 由系统用确定性规则复算，你可以先填 pending；generated_by 必须是 model。")
    lines.extend(
        [
            "",
            "硬性约束：",
            "1. 不得编造 evidence_id：证据只能来自 <evidence> 段给出的条目；"
            "该段为空时，任何结论都必须标 unknown（未验证），严禁写成 pass。",
            "2. 不确定即标注：无法验证的假设写 unknown / unverified，并在 notes 或 unknowns 中说明原因；"
            "禁止用推测代替证据。",
            "3. 所有 id 使用小写 ASCII（a-z、0-9 与连字符），同一产物内必须唯一。",
            f"4. 数量上限：features ≤ {MAX_LOOP_FEATURES}，assumptions ≤ {MAX_LOOP_ASSUMPTIONS}，"
            f"checks ≤ {MAX_LOOP_CHECKS}，files ≤ {MAX_LOOP_FILES}"
            f"（单个文件 ≤ {MAX_LOOP_FILE_CHARS} 字符），cases ≤ {MAX_LOOP_CASES}，"
            f"评分维度 ≤ {MAX_LOOP_DIMENSIONS}，blockers ≤ {MAX_LOOP_BLOCKERS}。",
            "5. 只输出结果对象本身；不要输出协议之外的字段，不要用省略号或占位内容代替真实内容。",
        ]
    )
    return "\n".join(lines)


def _budget_value(ctx: Any, key: str) -> Any:
    """从 ``ctx.budget``（dict 或对象）与 ``ctx.run.budget_json`` 读取 loop 参数。

    契约 §5.3 要求从 ``ctx.budget`` 读；:class:`workflows.runner.BudgetTracker`
    不保留未知键，因此余下的键从 ``Run.budget_json`` 原始 JSON 里取。
    """

    budget = getattr(ctx, "budget", None)
    if isinstance(budget, Mapping) and key in budget:
        return budget[key]
    if budget is not None and hasattr(budget, key):
        return getattr(budget, key)
    raw = getattr(getattr(ctx, "run", None), "budget_json", None)
    if raw:
        import json

        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = {}
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


def _brief_lines(state: Any) -> list[str]:
    brief = getattr(state, "brief", None)
    if brief is None:
        return ["（尚未提供产品定义字段）"]
    lines: list[str] = []
    for field_name in BRIEF_FIELDS:
        field = getattr(brief, field_name, None)
        value = getattr(field, "value", field)
        if value is None or not str(value).strip():
            continue
        status = getattr(getattr(field, "status", None), "value", None)
        mark = "" if status in (None, "confirmed") else f"（{status}）"
        lines.append(f"- {_BRIEF_LABELS.get(field_name, field_name)}{mark}：{_clip(str(value), 400)}")
    return lines or ["（尚未提供产品定义字段）"]


def _clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


_PRIOR_SUMMARY_KEYS = (
    "title",
    "summary",
    "entry_file",
    "total",
    "passed",
    "failed",
    "skipped",
    "verdict",
    "score",
    "highlights",
    "blockers",
    "unknowns",
    "limitations",
    "next_actions",
)


def _summarize_prior(value: Any) -> list[str]:
    """把上阶段产物压成摘要行；结构体只取关键标量，绝不 dump 原始 JSON。"""

    if value is None:
        return ["（空）"]
    if isinstance(value, str):
        return [_clip(value, 600)]
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key in _PRIOR_SUMMARY_KEYS:
            if key not in value:
                continue
            item = value[key]
            if isinstance(item, (list, tuple)):
                if not item:
                    continue
                joined = "；".join(_clip(str(part), 120) for part in list(item)[:4])
                lines.append(f"- {key}：{joined}")
            else:
                lines.append(f"- {key}：{_clip(str(item), 240)}")
        for key in ("features", "checks", "cases", "files", "dimensions"):
            item = value.get(key)
            if isinstance(item, (list, tuple)) and item:
                lines.append(f"- {key}：共 {len(item)} 项")
        return lines or ["（已产出结构化结果）"]
    if isinstance(value, (list, tuple)):
        return [f"- 共 {len(value)} 项"]
    return [_clip(str(value), 240)]


def _prior_lines(prior: Mapping[Any, Any] | None, stage: LoopStage) -> list[str]:
    lines: list[str] = []
    if not prior:
        return ["（无上阶段产物）"]
    for prior_stage in list(LoopStage):
        if prior_stage == stage:
            continue
        entry = prior.get(prior_stage) or prior.get(prior_stage.value)
        if entry is None:
            continue
        lines.append(f"[{STAGE_LABELS[prior_stage]}]")
        lines.extend(_summarize_prior(entry))
    return lines or ["（无上阶段产物）"]


def _evidence_lines(ctx: Any) -> list[str]:
    """证据段只收显式证据条目；当前没有证据登记通道时保持为空。"""

    evidence = getattr(ctx, "evidence", None)
    if evidence is None:
        return []
    if isinstance(evidence, Mapping):
        return [_clip(f"{key}：{value}", 300) for key, value in list(evidence.items())[:8]]
    if isinstance(evidence, (list, tuple)):
        return [_clip(str(item), 300) for item in list(evidence)[:8]]
    return [_clip(str(evidence), 300)]


def build_stage_messages(
    ctx: Any, *, stage: LoopStage, prior: dict
) -> list[LLMMessage]:
    """构造某阶段的 ``[system, user]`` 消息（契约 §5.2）。"""

    if not isinstance(stage, LoopStage):
        stage = LoopStage(stage)
    state = getattr(ctx, "product_state", None)
    focus = str(_budget_value(ctx, "loop_focus") or "").strip()
    decision_note = str(_budget_value(ctx, "loop_decision_note") or "").strip()

    scope = str(_budget_value(ctx, "loop_scope") or "").strip()
    scope_line = {
        "mvp": "最小可用产品（MVP）：先做一条能跑通的主路径，宁窄勿全",
        "prototype": "可交互原型：重点是可演示的关键交互与状态，不做真实后端",
        "optimize": "既有产品优化：在现有能力上做增量改进，明确前后对比",
    }.get(scope, "")
    user_lines = ["产品定义（来自已确认的产品状态）："]
    user_lines.extend(_brief_lines(state))
    user_lines.extend(
        [
            "",
            f"本轮 focus：{_clip(focus, 500) if focus else '（未指定，聚焦最小可交付闭环）'}",
            *([f"本轮范围：{scope_line}"] if scope_line else []),
        ]
    )
    if decision_note:
        user_lines.append(f"上一轮决策备注（用户要求，优先满足）：{_clip(decision_note, 500)}")
    user_lines.extend(["", "上阶段产物摘要："])
    user_lines.extend(_prior_lines(prior, stage))
    user_lines.extend(["", "<evidence>"])
    evidence = _evidence_lines(ctx)
    if evidence:
        user_lines.extend(evidence)
    else:
        user_lines.append("（本轮未提供任何证据条目；不得引用 evidence_id，结论一律标 unknown）")
    user_lines.extend(["</evidence>"])

    return [
        LLMMessage(
            role="system",
            content=_system_prompt(stage),
            source_label=f"loop.{stage.value}.system",
        ),
        LLMMessage(
            role="user",
            content="\n".join(user_lines),
            source_label=f"loop.{stage.value}.user",
        ),
    ]
