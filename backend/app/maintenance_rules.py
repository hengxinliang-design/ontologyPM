"""Manual-maintenance rules, implemented once (requirements baseline §5, tsg-ontology principle 15).

Ontology action criteria and the base program's write guards both call these checks, so a rule cannot say one
thing in the action and another in the repository (lesson L32). Each check returns the reason in Chinese when the
rule is broken and ``None`` when it holds. This module sits in the base layer: the repository must not import the
ontology, and the ontology may import the base program.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

CAPPED_TYPES = {"construction", "maintenance"}

# Objects locked in a status: maintenance actions are refused with the reason.
LOCKS: dict[str, tuple[str, str]] = {
    "milestone": ("completed", "里程碑已确认完成，已锁定"),
    "paymentNode": ("paid", "回款已确认到账，已锁定"),
    "intervention": ("closed", "管理干预已关闭"),
}


def locked(kind: str, status: Any) -> str | None:
    locked_status, reason = LOCKS[kind]
    return reason if status == locked_status else None


def date_order(start: Any, end: Any, label: str) -> str | None:
    if start and end and str(end) < str(start):
        return f"{label}不能早于开始日期"
    return None


def capped_budget(contract_type: Any, planned_person_days: Any) -> str | None:
    if contract_type in CAPPED_TYPES and not planned_person_days:
        return "系统实施、TSnT365 项目必须填写预算人天"
    return None


def non_negative(amount: Any) -> str | None:
    if amount in (None, ""):
        return None
    try:
        return "金额不能为负数" if Decimal(str(amount)) < 0 else None
    except InvalidOperation:
        return "金额必须是数字"


def payment_ratio(ratio: Any) -> str | None:
    if ratio in (None, ""):
        return None
    try:
        value = Decimal(str(ratio))
    except InvalidOperation:
        return "回款比例必须是数字"
    return None if Decimal("0") <= value <= Decimal("100") else "回款比例必须在 0 到 100 之间"


def term_available(linked_milestone_id: Any, milestone_id: Any = None) -> str | None:
    """A payment term belongs to at most one milestone; relinking it to its own milestone is fine."""
    if linked_milestone_id and linked_milestone_id != milestone_id:
        return "该回款约定已关联其他里程碑，请先在原里程碑上解除关联"
    return None


def linked_payment_unpaid(payment_status: Any) -> str | None:
    return "关联回款已确认到账，不能删除" if payment_status == "paid" else None


def term_locked(payment_status: Any) -> str | None:
    """A payment term whose payment node is received is locked, like the node itself."""
    return "回款约定已确认到账，已锁定" if payment_status == "paid" else None


def term_unlinked(linked_milestone_id: Any) -> str | None:
    return "回款约定已关联里程碑，请先在里程碑上解除关联" if linked_milestone_id else None


def node_not_milestone_managed(linked_milestone_id: Any) -> str | None:
    return "该回款节点由里程碑管理，请通过编辑里程碑维护" if linked_milestone_id else None


def cost_month(month: Any, current_month: str) -> str | None:
    """Operating costs (D24) are entered per past or current month: YYYY-MM, not after the current month."""
    text = str(month or "")
    if len(text) != 7 or text[4] != "-" or not (text[:4] + text[5:]).isdigit() or not 1 <= int(text[5:]) <= 12:
        return "月份必须是 YYYY-MM 格式"
    if text > current_month:
        return f"不能录入 {current_month} 之后的月份"
    return None


def ensure(reason: str | None) -> None:
    """Base-program guard: raise the rule's reason (the REST layer turns ValueError into a 422)."""
    if reason:
        raise ValueError(reason)
