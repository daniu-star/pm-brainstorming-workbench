"""数字可追溯性守卫（PRD AC-20 / FR-05）：模型给出的数字必须能回到来源。

规则（确定性、不调模型）：
- 论断文本里的每个数字，必须在它自己引用的消息/证据原文里出现过，
  才算「可追溯」；
- 找不到的记为「未验证数字」，进入 ``Claim.unverified_numbers``，
  画布上标「数字未验证」并给出验证建议（补充来源与统计日期）；
- 没有引用任何来源的论断，其数字自然也全部记未验证（与「模型推测」并列展示）。
"""

from __future__ import annotations

import re

from domain import contracts as C

# 数字边界：前面不能是字母/数字/小数点（避免把 "v2"、"Q3" 里的数字当成数据），
# 后面的同类字符同理；中文量词（家/小时/%）不影响匹配。
_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z.])([0-9]+(?:\.[0-9]+)?)(?![0-9A-Za-z])")
_DATE_LIKE_RE = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}")

VERIFICATION_HINT = (
    "请补充这些数字的来源（对应消息/证据里的实测值）与统计日期；"
    "补不齐就按「未验证数字」展示，不得当作事实引用。"
)


def _normalize(token: str) -> str:
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    if token.startswith("0") and token != "0":
        token = token.lstrip("0") or "0"
    return token


def extract_numbers(text: str) -> list[str]:
    """抽取文本中的数字（去重、按出现顺序；归一化 12.50 → 12.5、012 → 12）。"""

    if not text:
        return []
    seen: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        value = _normalize(match.group(1))
        if not value or value in seen:
            continue
        seen.append(value)
    return seen


def numbers_in_text(text: str | None) -> set[str]:
    return set(extract_numbers(text or ""))


def number_is_traceable(value: str, source_text: str | None) -> bool:
    """来源原文里是否出现过同一个数字（按归一化值比较，不看单位）。"""

    if not value or not source_text:
        return False
    return _normalize(value) in numbers_in_text(source_text)


def look_like_date(text: str) -> bool:
    return bool(_DATE_LIKE_RE.search(text or ""))


def verification_suggestion(numbers: list[str]) -> str | None:
    if not numbers:
        return None
    return "未验证数字：" + "、".join(numbers) + "。" + VERIFICATION_HINT


def audit_claim(claim: C.Claim, *, source_text: str) -> C.Claim:
    """按该论断自己的引用文本，标注其中的未验证数字。"""

    numbers = extract_numbers(claim.text)
    unverified = [value for value in numbers if not number_is_traceable(value, source_text)]
    if unverified == list(claim.unverified_numbers):
        return claim
    return claim.model_copy(update={"unverified_numbers": unverified})


def audit_role_result(
    result: C.RoleResult,
    *,
    message_texts: dict[str, str],
    evidence_texts: dict[str, str],
) -> tuple[C.RoleResult, list[str]]:
    """逐个论断做数字归属校验，返回（更新后的结果，警告列表）。

    只做「标注」，不否决角色产物：AC-20 要求「数字标未验证 + 输出验证建议」，
    而不是让整轮失败。
    """

    warnings: list[str] = []
    claims: list[C.Claim] = []
    for claim in result.claims:
        parts = [message_texts.get(ref) for ref in claim.source_message_ids]
        parts.extend(evidence_texts.get(ref) for ref in claim.evidence_refs)
        source_text = "\n".join(part for part in parts if part)
        updated = audit_claim(claim, source_text=source_text)
        if updated.unverified_numbers:
            warnings.append(
                f"{result.role_code.value} 的主张含未验证数字："
                + "、".join(updated.unverified_numbers)
                + "（来源原文中找不到，已标注为未验证）"
            )
        claims.append(updated)
    if claims == result.claims:
        return result, warnings
    return result.model_copy(update={"claims": claims}), warnings
