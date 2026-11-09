"""Cases for the three-layer evaluation (scripts/eval_three_layers.py): ontology rules, small model, large model.

The large-model cases are PM Assistant questions in five groups: everyday questions, operating analysis, risk
alerts, strategy signals, and permission and scope. Their right answers are not written down here. Each case has a
``truth`` function that reads them from the ontology at run time (``Facts``), so the large model is measured on
whether it relays the ontology's conclusion correctly, and the cases stay valid when the demo data is rebuilt.

The rule-layer expectations are the designed demo scenarios (scripts/demo_projects.py, scripts/demo_people.py and
docs/test-data/project-scenarios.md): there the ontology itself is the thing under test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

PROJECT_CODE = re.compile(r"(?<![A-Z0-9])[A-Z]{3}\d{3}(?![0-9])")
PERCENT_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*%")
HYPHENS = re.compile(r"[‐‑‒–—−]")

ADMIN = {"username": "eval-admin", "is_admin": True, "role": "admin"}
# A project manager sees only projects whose project manager is 王经理 and has the project menus only (D2, D11).
PROJECT_MANAGER = {"username": "eval-pm", "is_admin": False, "role": "project_manager", "person_name": "王经理",
                   "permissions": ["projects"]}

STATUS_WORDS = {
    "on_track": ["正常", "符合", "领先", "超过时间进度", "高于时间进度", "未落后", "不落后", "超前", "快于"],
    "behind": ["落后", "滞后", "低于时间进度", "慢于", "未达"],
    "over_budget": ["超支", "超出", "高于时间进度", "快于时间进度", "偏快", "超过时间进度"],
}
ALERT_WORDS = {
    "BUDGET_OVERRUN": ["超出", "超预算", "超支", "预算"],
    "BUDGET_ATTENTION": ["预算"],
    "MILESTONE_DUE": ["里程碑", "上线", "延期"],
    "PAYMENT_DUE": ["回款", "尾款", "到账", "付款"],
    "INTERVENTION_OVERDUE": ["干预"],
}
NOT_FOUND_WORDS = ["不存在", "未找到", "没有找到", "查不到", "找不到", "没有该项目", "无此项目", "未查到", "没有查到", "无法找到"]
NO_PERMISSION_WORDS = ["无权", "没有权限", "权限", "无法查看", "不能查看", "看不到"]


def money_forms(value: Any) -> list[str]:
    """700000 → 700000, 700000.00, 70万, 70.0万, 70.00万 (the answer is compared with commas and spaces removed)."""
    amount = float(value)
    wan = amount / 10000
    forms = {f"{amount:.0f}", f"{amount:.2f}", f"{wan:.2f}万", f"{wan:.1f}万"}
    if wan == int(wan):
        forms.add(f"{int(wan)}万")
    return sorted(forms)


def percent_forms(value: Any) -> list[str]:
    number = float(value)
    forms = {f"{number:.1f}%", f"{number:.2f}%"}
    if number == round(number):
        forms.add(f"{round(number)}%")
    return sorted(forms)


def month_forms(month: str) -> list[Any]:
    """2026-07, 7月, 07月, 2026年7月, and the month inside a list such as 「07、10、11 月」."""
    number = int(month[5:7])
    return [month, f"{number}月", f"{number:02d}月", f"{month[:4]}年{number}月",
            re.compile(rf"(?<![\d.\-]){'0?' if number < 10 else ''}{number}(?=(?:月|、|，|,|和|与|及))")]


@dataclass
class Expect:
    """What a right answer contains. Every group in ``keywords`` needs one of its alternatives."""

    entities: set[str] = field(default_factory=set)
    kind: str | None = None  # "project" or "employee": which mentions are counted
    exclusive: bool = False  # mentioning an entity outside ``entities`` (and the question's own) is an error
    keywords: list[list[str]] = field(default_factory=list)
    forbid: list[re.Pattern[str]] = field(default_factory=list)
    note: str = ""


@dataclass
class LlmCase:
    case_id: str
    group: str
    question: str
    truth: Callable[["Facts"], Expect]
    user: dict[str, Any] = field(default_factory=lambda: ADMIN)


class Facts:
    """The ontology's own answers, read once per run as an administrator."""

    def __init__(self, call: Callable[[dict[str, Any], str, dict[str, Any]], Any]):
        self.call = call
        self.projects = self._all_projects(ADMIN)
        self.health = call(ADMIN, "portfolioHealth", {})["data"]
        self.review = call(ADMIN, "departmentOperatingReview", {})
        self.resource_health = call(ADMIN, "resourceHealth", {})["data"]
        self.customers = call(ADMIN, "customerAnnualSummary", {"year": int(self.review["year"])})
        self.forecast = call(ADMIN, "resourceForecast", {})
        self.employee_names = {row["employee_name"] for row in self.resource_health}
        self.pm_projects = {row["project_number"] for row in self._all_projects(PROJECT_MANAGER)}

    def _all_projects(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        rows, cursor = [], None
        while True:
            page = self.call(user, "projectList", {"pageSize": 100, **({"cursor": cursor} if cursor else {})})
            rows += page["data"]
            cursor = (page.get("meta") or {}).get("next_cursor")
            if not cursor:
                return rows

    def alerts(self, code: str, overdue_only: bool = False) -> set[str]:
        return {row["project_number"] for row in self.health for alert in row["anomaly_alerts"]
                if alert["code"] == code and (not overdue_only or "延期" in alert["title"])}

    def signal(self, name: str) -> dict[str, Any] | None:
        return next((s for s in self.review["signals"] if s["signal"] == name), None)

    def kpi(self, name: str) -> dict[str, Any]:
        return self.review["kpis"][name]


def _codes(evidence: list[str]) -> set[str]:
    return {code for line in evidence for code in PROJECT_CODE.findall(line)}


def _kpi_case(name: str) -> Callable[[Facts], Expect]:
    def truth(f: Facts) -> Expect:
        kpi = f.kpi(name)
        return Expect(keywords=[percent_forms(kpi["completion_percent"]), STATUS_WORDS[kpi["status"]]],
                      note=f"{kpi['label']} 完成 {kpi['completion_percent']}%，时间进度 {kpi['time_progress_percent']}%，{kpi['status']}")
    return truth


def _project_set(select: Callable[[Facts], set[str]], label: str) -> Callable[[Facts], Expect]:
    def truth(f: Facts) -> Expect:
        codes = select(f)
        return Expect(codes, "project", True, [] if codes else [["没有", "无", "不存在"]], note=f"{label}：{sorted(codes) or '无'}")
    return truth


def _employee_set(status: str, label: str) -> Callable[[Facts], Expect]:
    def truth(f: Facts) -> Expect:
        names = {row["employee_name"] for row in f.resource_health if status in row["statuses"]}
        return Expect(names, "employee", True, [] if names else [["没有", "无"]], note=f"{label}：{sorted(names) or '无'}")
    return truth


def _project_alerts(code: str) -> Callable[[Facts], Expect]:
    def truth(f: Facts) -> Expect:
        row = next(r for r in f.health if r["project_number"] == code)
        codes = sorted({a["code"] for a in row["anomaly_alerts"]})
        return Expect(keywords=[ALERT_WORDS.get(c, [c]) for c in codes], note=f"{code} 预警：{codes}")
    return truth


def _project_facts(f: Facts) -> Expect:
    row = next(r for r in f.projects if r["project_number"] == "SEY103")
    return Expect(keywords=[[row["project_manager"]], money_forms(row["contract_value"])],
                  note=f"SEY103 项目经理 {row['project_manager']}，合同额 {row['contract_value']}")


def _top_customer(f: Facts) -> Expect:
    year = next(y for y in f.customers["years"] if y["year"] == f.customers["year"])
    top = max(year["customers"], key=lambda c: float(c["signed_amount"]))
    return Expect(keywords=[[top["customer_name"]], money_forms(top["signed_amount"])],
                  note=f"签约额最高：{top['customer_name']} {top['signed_amount']}")


def _missing_cost(f: Facts) -> Expect:
    months = f.review["operating"]["missing_cost_months"]
    return Expect(keywords=[month_forms(m) for m in months] or [["没有", "都已录入", "无缺失"]], note=f"缺失月份：{months}")


def _cost_status(f: Facts) -> Expect:
    kpi = f.kpi("operating_cost")
    return Expect(keywords=[STATUS_WORDS[kpi["status"]], percent_forms(kpi["completion_percent"])],
                  note=f"经营成本截至 {kpi['as_of']} 完成 {kpi['completion_percent']}%，时间进度 {kpi['time_progress_percent']}%，{kpi['status']}")


def _strategy(f: Facts) -> Expect:
    labels = [[s["label"]] for s in f.review["signals"]]
    return Expect(_codes([e for s in f.review["signals"] for e in s["evidence"]]), "project", False, labels,
                  note="信号：" + "、".join(s["label"] for s in f.review["signals"]))


def _signal_projects(name: str) -> Callable[[Facts], Expect]:
    def truth(f: Facts) -> Expect:
        signal = f.signal(name)
        codes = _codes(signal["evidence"]) if signal else set()
        return Expect(codes, "project", True, [] if codes else [["没有", "无"]], note=f"{name}：{sorted(codes) or '未触发'}")
    return truth


def _forecast_gap(f: Facts) -> Expect:
    short = [m["month"] for m in f.forecast["months"] if m["status"] == "shortage"]
    groups = [month_forms(m) for m in short] or [["没有缺口", "充足", "足够", "不存在缺口"]]
    if short:
        groups.append(["缺口", "不足", "短缺", "不够"])
    return Expect(keywords=groups, note=f"缺口月份：{short or '无'}")


def _not_found(f: Facts) -> Expect:
    return Expect(set(), "project", True, [NOT_FOUND_WORDS], [PERCENT_NUMBER], note="项目不存在，不能给出百分比")


def _pm_projects(f: Facts) -> Expect:
    return Expect(f.pm_projects, "project", True, note=f"王经理的项目：{sorted(f.pm_projects)}")


def _pm_no_personnel(f: Facts) -> Expect:
    return Expect(keywords=[NO_PERMISSION_WORDS], forbid=[PERCENT_NUMBER], note="项目经理没有个人工时权限（D11）")


LLM_CASES: list[LlmCase] = [
    # Everyday questions
    LlmCase("Q1", "问答查询", "工时超预算的项目有哪些？",
            _project_set(lambda f: {r["project_number"] for r in f.projects if float(r.get("budget_utilization") or 0) > 100}, "预算使用率高于 100%")),
    LlmCase("Q2", "问答查询", "目前风险等级为「严重」的项目有哪些？",
            _project_set(lambda f: {r["project_number"] for r in f.projects if r["risk_level"] == "critical"}, "风险等级严重")),
    LlmCase("Q3", "问答查询", "SEY103 的项目经理是谁？合同额是多少？", _project_facts),
    LlmCase("Q4", "问答查询", "有哪些项目存在已延期的里程碑？",
            _project_set(lambda f: {r["project_number"] for r in f.projects if (r.get("overdue_milestones") or 0) > 0}, "有延期里程碑")),
    LlmCase("Q5", "问答查询", "项目 ABC999 的预算使用率是多少？", _not_found),
    # Operating analysis
    LlmCase("B1", "经营分析", "今年合同额目标完成了多少？和时间进度相比怎么样？", _kpi_case("new_contracts")),
    LlmCase("B2", "经营分析", "今年经营收入（税前）的目标完成情况如何？", _kpi_case("collections")),
    LlmCase("B3", "经营分析", "今年的经营成本有没有超支？", _cost_status),
    LlmCase("B4", "经营分析", "今年签约额最高的客户是哪个？签了多少？", _top_customer),
    LlmCase("B5", "经营分析", "今年哪个月的经营成本还没有录入？", _missing_cost),
    # Risk alerts
    LlmCase("R1", "风险预警", "哪些项目的回款节点已经逾期？", _project_set(lambda f: f.alerts("PAYMENT_DUE", overdue_only=True), "回款已延期")),
    LlmCase("R2", "风险预警", "SEY104 目前有哪些预警？", _project_alerts("SEY104")),
    LlmCase("R3", "风险预警", "哪些员工处于持续过载状态？", _employee_set("overloaded", "持续过载")),
    LlmCase("R4", "风险预警", "哪些员工有闲置风险？", _employee_set("idle", "闲置")),
    LlmCase("R5", "风险预警", "有哪些项目的管理干预已经逾期还没关闭？", _project_set(lambda f: f.alerts("INTERVENTION_OVERDUE"), "干预逾期")),
    # Strategy signals
    LlmCase("S1", "策略预警", "部门现在应该优先采取哪些经营策略？依据是什么？", _strategy),
    LlmCase("S2", "策略预警", "哪些项目需要推动验收回款？", _signal_projects("push_acceptance")),
    LlmCase("S3", "策略预警", "哪些项目快结束了，人员可以释放？", _signal_projects("release_resources")),
    LlmCase("S4", "策略预警", "未来三个月部门人手够不够？哪个月有缺口？", _forecast_gap),
    # Permission and scope (asked as the project manager 王经理)
    LlmCase("P1", "权限与范围", "我负责的项目有哪些？", _pm_projects, PROJECT_MANAGER),
    LlmCase("P2", "权限与范围", "孙悦十月份的工时利用率是多少？", _pm_no_personnel, PROJECT_MANAGER),
]


# Wording that sets a name apart from the answer: 「冯雪是首月，不评价闲置」, 「以下人员未判定为持续过载：」,
# 「SEY103 尚未超支」. Kept to phrases that do not occur in a positive finding (no bare 未 / 不 / 无).
EXCLUSION_WORDS = re.compile(
    "不计入|未计入|不算|不属于|不在此列|不在名单|不在范围|未判定|未被判定|不判定|未达|不评价|未纳入|不纳入|"
    "尚未超|没有超|未超过|未超出|不满足|不符合|排除|除外|暂不|不能释放|无法判断|无法判定|并非|不是")
LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]\s|\d+[.、)]\s?|\|)")
LEAD_END = re.compile(r"[：:]\s*$")


def excluded_lines(answer: str) -> list[tuple[str, bool]]:
    """Each non-empty line and whether it sets its names apart (see EXCLUSION_WORDS).

    A line is set apart when it carries such wording itself, or when it is a list item or table row under a heading
    with it (「### 暂不能释放的项目」) or under a lead line ending in a colon with it (「以下人员未判定为持续过载：」).
    A lead list item covers only the items indented below it.
    """
    lines: list[tuple[str, bool]] = []
    heading = False
    lead: tuple[int, bool] | None = None  # indent of the lead line (-1 for a paragraph) and whether it sets apart
    for raw in answer.splitlines():
        line = raw.replace("**", "").rstrip()
        if not line.strip():
            continue
        own = bool(EXCLUSION_WORDS.search(line))
        item = LIST_ITEM.match(line)
        if line.lstrip().startswith("#"):
            heading, lead = own, None
            lines.append((line, own))
            continue
        if item is None:
            lead = (-1, own) if LEAD_END.search(line) else None
            lines.append((line, own))
            continue
        indent = len(item.group(1))
        if lead is not None and lead[0] >= 0 and indent <= lead[0]:
            lead = None
        lines.append((line, own or heading or bool(lead and lead[1])))
        if LEAD_END.search(line):
            lead = (indent, own or bool(lead and lead[1]))
    return lines


def set_apart(names: set[str], answer: str) -> set[str]:
    """The names whose every mention is in a line that sets them apart."""
    lines = excluded_lines(answer)
    return {n for n in names if all(apart for line, apart in lines if n in line) and any(n in line for line, _ in lines)}


def score_answer(answer: str, expect: Expect, question: str, employee_names: set[str]) -> dict[str, Any]:
    """Recall of the expected entities, wrong extra mentions, keyword groups hit and forbidden content.

    A name the answer only mentions to set it apart (「冯雪是首月，不评价闲置」) is not a wrong extra mention; it
    still counts towards recall when it is expected, so the exclusion wording can only forgive, never take away.
    """
    answer = HYPHENS.sub("-", answer)  # models write 2026‑11 with a non-breaking hyphen
    text = re.sub(r"[,，\s¥￥]", "", answer).replace("％", "%")
    asked = set(PROJECT_CODE.findall(question)) | {n for n in employee_names if n in question}
    if expect.kind == "project":
        mentioned = set(PROJECT_CODE.findall(answer))
    elif expect.kind == "employee":
        mentioned = {n for n in employee_names if n in answer}
    else:
        mentioned = set()
    hit = mentioned & expect.entities
    extra = (mentioned - expect.entities - asked) if expect.exclusive else set()
    apart = set_apart(extra, answer)
    extra -= apart
    keyword_hits = [any(alt.search(text) if isinstance(alt, re.Pattern) else alt.replace(",", "") in text for alt in group)
                    for group in expect.keywords]
    forbidden = [p.pattern for p in expect.forbid if p.search(answer)]
    recall = len(hit) / len(expect.entities) if expect.entities else 1.0
    parts = [recall, *(1.0 if ok else 0.0 for ok in keyword_hits)]
    if expect.exclusive:
        parts.append(1.0 if not extra else max(0.0, 1 - len(extra) / max(1, len(mentioned))))
    if expect.forbid:
        parts.append(0.0 if forbidden else 1.0)
    score = sum(parts) / len(parts)
    passed = recall == 1.0 and not extra and all(keyword_hits) and not forbidden
    return {"score": round(score, 3), "passed": passed, "recall": round(recall, 3), "missing": sorted(expect.entities - hit),
            "extra": sorted(extra), "set_apart": sorted(apart),
            "keywords_missing": [[a for a in g if isinstance(a, str)][:3] for g, ok in zip(expect.keywords, keyword_hits) if not ok],
            "forbidden": forbidden}
