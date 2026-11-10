"""The facts an AI suggestion rests on, and whether they still hold (requirements baseline D54, D55; ontology 8.6.0).

An AI suggestion (intervention, risk flag, strategy note) is only a candidate (principle 1: the model drafts, the
ontology decides). It must cite facts the ontology can check without a model:

- ``fact``: an object property compared with a value, e.g. Project SEY103 budgetUtilization >= 80;
- ``alert``: a project's rule alert by code, e.g. SEY104 BUDGET_OVERRUN;
- ``signal``: a department strategy signal, optionally naming a project in its evidence.

The facts are checked when the suggestion is submitted and approved, as the acting user (their object types,
markings and project scope apply), and every hour afterwards as the system. This module is the only implementation
of that check; alerts and signals come in through ``Lookups`` so the module does not depend on the rule registry.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from ..security import Principal, SecurityPolicy

MAX_FACTS = 5
OPERATORS = ("=", "!=", ">", ">=", "<", "<=")
OPERATOR_TEXT = {"=": "等于", "!=": "不等于", ">": ">", ">=": "≥", "<": "<", "<=": "≤"}


class BasisError(ValueError):
    """The cited facts are malformed (not whether they hold)."""


@dataclass(frozen=True)
class Lookups:
    """Rule outputs the facts may cite: a project's alerts and the department's current strategy signals."""
    alerts: Callable[[str], list[dict[str, Any]] | None]   # None = project unknown or out of the caller's scope
    signals: Callable[[], list[dict[str, Any]]]
    signal_labels: dict[str, str]


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def normalize(raw: Any) -> list[dict[str, Any]]:
    """The cited facts in canonical form; raises BasisError when they are missing or malformed."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BasisError("依据必须是事实列表（JSON）") from exc
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise BasisError("至少引用一条可核对的系统事实作为依据")
    if len(raw) > MAX_FACTS:
        raise BasisError(f"依据最多 {MAX_FACTS} 条")
    facts = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise BasisError(f"第 {index} 条依据格式不对")
        kind = item.get("type")
        if kind == "fact":
            op = _text(item.get("op"), 2)
            if op not in OPERATORS:
                raise BasisError(f"第 {index} 条依据的比较符只能是 {' '.join(OPERATORS)}")
            value = item.get("value")
            if isinstance(value, (dict, list)):
                raise BasisError(f"第 {index} 条依据的值只能是数字、文字、是否或空")
            fact = {"type": "fact", "objectType": _text(item.get("objectType"), 60), "primaryKey": _text(item.get("primaryKey"), 80),
                    "property": _text(item.get("property"), 60), "op": op, "value": value}
            if not fact["objectType"] or not fact["primaryKey"] or not fact["property"]:
                raise BasisError(f"第 {index} 条依据缺少对象类型、主键或属性")
        elif kind == "alert":
            fact = {"type": "alert", "project": _text(item.get("project"), 40), "code": _text(item.get("code"), 60).upper()}
            if not fact["project"] or not fact["code"]:
                raise BasisError(f"第 {index} 条依据缺少项目或预警代码")
            if fact["code"].startswith("AI_"):
                raise BasisError(f"第 {index} 条依据引用的是 AI 自己的标记，不能作为依据")
        elif kind == "signal":
            fact = {"type": "signal", "signal": _text(item.get("signal"), 40), "project": _text(item.get("project"), 40) or None}
            if not fact["signal"]:
                raise BasisError(f"第 {index} 条依据缺少信号")
        else:
            raise BasisError(f"第 {index} 条依据的类型只能是 fact、alert、signal")
        facts.append(fact)
    return facts


def identity(fact: dict[str, Any]) -> str:
    """What a fact is about, without its value: two suggestions citing the same thing count as one (D54)."""
    if fact["type"] == "fact":
        return f"fact:{fact['objectType']}:{fact['primaryKey']}:{fact['property']}:{fact['op']}"
    if fact["type"] == "alert":
        return f"alert:{fact['project']}:{fact['code']}"
    return f"signal:{fact['signal']}:{fact.get('project') or ''}"


def dedupe_key(kind: str, subject: str | None, facts: list[dict[str, Any]]) -> str:
    """One item per subject (project, or category for strategy notes) + kind + cited facts (D54)."""
    return "|".join([kind, subject or "", *sorted(identity(f) for f in facts)])


def projects_cited(ctx: Any, facts: list[dict[str, Any]]) -> set[str]:
    """Project numbers the facts are about: alerts, signals with a project, and facts on project-scoped objects."""
    found: set[str] = set()
    for fact in facts:
        if fact["type"] in ("alert", "signal") and fact.get("project"):
            found.add(fact["project"])
        elif fact["type"] == "fact":
            definition = ctx.ontology.object_type(fact["objectType"])
            if definition is not None and fact["objectType"] == "Project":
                found.add(fact["primaryKey"])
            elif definition is not None and definition.project_scope:
                item = ctx.get(fact["objectType"], fact["primaryKey"])
                if item and item.get(definition.project_scope):
                    found.add(str(item[definition.project_scope]))
    return found


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).strip().rstrip("%"))
    except (InvalidOperation, ValueError):
        return None


def compare(current: Any, op: str, expected: Any) -> bool:
    """Numbers compare as numbers (80 = 80.00); anything else only with = and !=, as text."""
    left, right = _number(current), _number(expected)
    if left is not None and right is not None:
        return {"=": left == right, "!=": left != right, ">": left > right, ">=": left >= right,
                "<": left < right, "<=": left <= right}[op]
    if op not in ("=", "!="):
        return False
    norm = lambda v: "" if v is None else (str(v).lower() if isinstance(v, bool) else str(v))  # noqa: E731
    same = norm(current) == norm(expected)
    return same if op == "=" else not same


def _show(value: Any) -> str:
    if value is None or value == "":
        return "空"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def check(ctx: Any, facts: list[dict[str, Any]], lookups: Lookups) -> list[dict[str, Any]]:
    """Each fact with ``holds`` (True / False) and a readable sentence of what the system shows now.

    A fact the caller cannot see (type, marking or project scope) or that names an unknown object type or
    property does not hold: it cannot be checked, so nothing may rest on it (principle 14: not knowing is not yes).
    """
    principal: Principal = ctx.principal
    security = SecurityPolicy(ctx.ontology)
    signals: list[dict[str, Any]] | None = None
    results = []
    for fact in facts:
        if fact["type"] == "fact":
            definition = ctx.ontology.object_type(fact["objectType"])
            prop = next((p for p in definition.properties if p.api_name == fact["property"]), None) if definition else None
            if definition is None or prop is None:
                results.append({**fact, "holds": False, "text": f"无法核对：没有对象类型或属性 {fact['objectType']}.{fact['property']}"})
                continue
            label = f"{definition.display_name} {fact['primaryKey']} 的{prop.display_name}"
            if not security.can_read_type(principal, definition) or not security.can_read_marking(principal, prop.marking):
                results.append({**fact, "holds": False, "text": f"无法核对：当前账号无权查看{label}"})
                continue
            item = ctx.get(fact["objectType"], fact["primaryKey"])
            if item is None:
                results.append({**fact, "holds": False, "text": f"无法核对：{definition.display_name} {fact['primaryKey']} 不存在或不在你的范围内"})
                continue
            current = item.get(fact["property"])
            holds = compare(current, fact["op"], fact["value"])
            results.append({**fact, "holds": holds,
                            "text": f"{label} {OPERATOR_TEXT[fact['op']]} {_show(fact['value'])}（当前 {_show(current)}）"})
        elif fact["type"] == "alert":
            alerts = lookups.alerts(fact["project"])
            if alerts is None:
                results.append({**fact, "holds": False, "text": f"无法核对：项目 {fact['project']} 不存在或不在你的范围内"})
                continue
            match = next((a for a in alerts if str(a.get("code")) == fact["code"]), None)
            results.append({**fact, "holds": match is not None,
                            "text": f"{fact['project']} 预警「{match.get('title') or fact['code']}」：{match.get('detail') or ''}".rstrip("：")
                            if match else f"{fact['project']} 已没有预警 {fact['code']}"})
        else:
            if signals is None:
                signals = lookups.signals()
            label = lookups.signal_labels.get(fact["signal"], fact["signal"])
            signal = next((s for s in signals if s.get("signal") == fact["signal"]), None)
            evidence = [str(e) for e in (signal or {}).get("evidence") or []]
            if fact.get("project"):
                hit = [e for e in evidence if fact["project"] in e]
                results.append({**fact, "holds": bool(hit), "text": f"策略信号「{label}」涉及 {fact['project']}：{hit[0]}" if hit
                                else f"策略信号「{label}」当前不涉及 {fact['project']}"})
            else:
                results.append({**fact, "holds": signal is not None,
                                "text": f"策略信号「{label}」：{'；'.join(evidence[:2])}" if signal else f"当前没有策略信号「{label}」"})
    return results


def summary(results: list[dict[str, Any]]) -> tuple[bool, str]:
    """Whether every fact holds, and the sentence to show: the failing facts first."""
    failing = [r["text"] for r in results if not r["holds"]]
    if failing:
        return False, "依据不再成立：" + "；".join(failing)
    return True, "；".join(r["text"] for r in results)
