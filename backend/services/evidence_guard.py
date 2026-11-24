"""引用归属校验（FR-04C / §5.3 不变量 5）。

模型产出的引用必须可解析到「本会话」或「公开知识域」：

* ``evidence_refs`` —— 证据行必须存在，且 ``session_id`` 等于本会话或为 ``NULL``
  （``NULL`` 表示公开知识域，例如人工录入的公开资料）。
* ``source_message_ids`` —— 消息必须属于本会话。

未知 ID、其它会话的 ID、以及模型臆造的 ID 一律视为校验失败：调用方应把
失败信息回喂给模型做一次修复（PRD 只允许一次），仍失败则该角色产物作废。
"""

from __future__ import annotations

import re

from domain import contracts as C

__all__ = ["check_role_result_refs", "assert_role_result_refs", "RefViolation"]


class RefViolation:
    """一条引用违规。"""

    __slots__ = ("path", "ref", "reason")

    def __init__(self, path: str, ref: str, reason: str) -> None:
        self.path = path
        self.ref = ref
        self.reason = reason

    def __str__(self) -> str:  # pragma: no cover - 仅用于错误文案
        return f"{self.path}: {self.ref}（{self.reason}）"

    def __repr__(self) -> str:  # pragma: no cover
        return f"RefViolation({self.path!r}, {self.ref!r}, {self.reason!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, RefViolation)
            and (self.path, self.ref, self.reason) == (other.path, other.ref, other.reason)
        )


_ORDINAL_REF_RE = re.compile(r"^#?([0-9]{1,9})$")


def normalize_message_refs(
    result: C.RoleResult, *, message_seq_to_id: dict[str, str]
) -> tuple[C.RoleResult, list[str]]:
    """把模型常用的 ``#3`` 序号引用映射为本会话真实消息 ID。

    上下文里消息渲染为 ``[#seq]``，模型会把序号当引用写进
    ``claims[i].source_message_ids``。序号只在本会话内有意义，映射是确定性的；
    映射不到的引用原样保留，交给归属校验拒绝。返回 (规范化结果, 警告列表)。
    """

    if not message_seq_to_id:
        return result, []
    notes: list[str] = []
    changed = False
    claims = []
    for index, claim in enumerate(result.claims):
        refs = []
        for ref in claim.source_message_ids:
            match = _ORDINAL_REF_RE.match(str(ref).strip()) if ref is not None else None
            mapped = message_seq_to_id.get(match.group(1)) if match else None
            if mapped is not None and mapped != ref:
                refs.append(mapped)
                changed = True
                notes.append(f"claims[{index}].source_message_ids: {ref} → {mapped}（按会话序号解析）")
            else:
                refs.append(ref)
        if list(claim.source_message_ids) != refs:
            claims.append(claim.model_copy(update={"source_message_ids": refs}))
        else:
            claims.append(claim)
    if not changed:
        return result, []
    updated = result.model_copy(update={"claims": claims})
    return updated, notes


def check_role_result_refs(
    result: C.RoleResult,
    *,
    allowed_evidence_ids: set[str],
    allowed_message_ids: set[str],
) -> list[RefViolation]:
    """返回角色产物里所有不可解析的引用；空列表表示全部合法。"""

    violations: list[RefViolation] = []
    for index, claim in enumerate(result.claims):
        for ref in claim.evidence_refs:
            if ref not in allowed_evidence_ids:
                violations.append(
                    RefViolation(f"claims[{index}].evidence_refs", ref, "不属于本会话或公开知识域")
                )
        for ref in claim.source_message_ids:
            if ref not in allowed_message_ids:
                violations.append(
                    RefViolation(f"claims[{index}].source_message_ids", ref, "不属于本会话")
                )
    return violations


def assert_role_result_refs(
    result: C.RoleResult,
    *,
    allowed_evidence_ids: set[str],
    allowed_message_ids: set[str],
) -> None:
    """校验引用归属；违规时抛 ``ValueError``（交由上层回喂模型修复一次）。"""

    violations = check_role_result_refs(
        result,
        allowed_evidence_ids=allowed_evidence_ids,
        allowed_message_ids=allowed_message_ids,
    )
    if not violations:
        return
    detail = "；".join(str(item) for item in violations[:5])
    more = "" if len(violations) <= 5 else f"（共 {len(violations)} 条）"
    raise ValueError(f"引用校验失败：{detail}{more}。请只引用本会话已有消息或已登记证据，不要臆造 ID。")
