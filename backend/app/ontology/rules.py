"""Code-side rules for the TSG PM ontology.

Ontology structure lives in ``metadata/*.yaml``.  Anything that needs
behaviour is registered here under a stable name and referenced from
metadata: datasources materialize objects, resolvers traverse derived links,
criteria guard actions, handlers write back through ``ProjectRepository`` and
functions compute business judgements.  Keeping writes inside the repository
means the semantic layer and the legacy REST endpoints share the same rules.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from .derivations.economics import DepartmentAnalysis
from .derivations.project import (
    _budget_thresholds,
    derive_project,
    project_base,
    project_health_view,
    project_list_view,
)
from ..investment import InvestmentService, _phase
from ..plans import AnnualPlanRepository
from .. import maintenance_rules
from ..project_repository import (
    CAPPED_TYPES,
    POLICY_RANGES,
    _counts_as_effort,
    _project_number_from_subject,
    _signed_year,
)
from .runtime import OntologyContext


Datasource = Callable[[OntologyContext], list[dict[str, Any]]]
LinkResolver = Callable[[OntologyContext, dict[str, Any]], list[dict[str, Any]]]
Criterion = Callable[..., "str | None"]
Handler = Callable[[OntologyContext, dict[str, Any], "dict[str, Any] | None", str], dict[str, Any]]
Implementation = Callable[[OntologyContext, dict[str, Any]], Any]
Derivation = Callable[[OntologyContext, list[dict[str, Any]]], dict[Any, dict[str, Any]]]


class RuleRegistry:
    def __init__(self) -> None:
        self.datasources: dict[str, Datasource] = {}
        self.resolvers: dict[str, LinkResolver] = {}
        self.criteria: dict[str, Criterion] = {}
        self.handlers: dict[str, Handler] = {}
        self.functions: dict[str, Implementation] = {}
        self.derivations: dict[str, Derivation] = {}

    def _register(self, table: dict[str, Any], name: str):
        def decorator(fn):
            if name in table:
                raise ValueError(f"rule {name} registered twice")
            table[name] = fn
            return fn

        return decorator

    def datasource(self, name: str):
        return self._register(self.datasources, name)

    def derivation(self, name: str):
        """A derivation computes derived properties for every object a datasource materialized.

        It receives the objects and returns ``{primary key: {property: value}}``; keys starting with ``__``
        are internal facts for later derivations and functions and are never serialized.
        """
        return self._register(self.derivations, name)

    def resolver(self, name: str):
        return self._register(self.resolvers, name)

    def criterion(self, name: str):
        return self._register(self.criteria, name)

    def handler(self, name: str):
        return self._register(self.handlers, name)

    def function(self, name: str):
        return self._register(self.functions, name)

    def catalog(self) -> dict[str, list[str]]:
        return {
            "datasources": sorted(self.datasources),
            "resolvers": sorted(self.resolvers),
            "criteria": sorted(self.criteria),
            "handlers": sorted(self.handlers),
            "functions": sorted(self.functions),
            "derivations": sorted(self.derivations),
        }


RULES = RuleRegistry()


def _money(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("amount")
    return value


def _record_project_number(record: dict[str, Any]) -> str | None:
    return record.get("project_number") or _project_number_from_subject(record.get("subject") or "")


def _finance_visible(ctx: OntologyContext) -> bool:
    marking = ctx.ontology.marking("FINANCE")
    return marking is not None and ctx.principal.holds_any(marking.permissions)


def _quantize(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


# ------------------------------------------------------- investment helpers
def _investment(ctx: OntologyContext) -> InvestmentService:
    return ctx.dataset(
        "investment_service",
        lambda: InvestmentService(ctx.database, ctx.projects, ctx.records, ctx.projects.timezone),
    )


def _plans(ctx: OntologyContext) -> AnnualPlanRepository:
    return ctx.dataset("annual_plans", lambda: AnnualPlanRepository(ctx.database, ctx.projects.timezone))


def _department(ctx: OntologyContext) -> DepartmentAnalysis:
    return ctx.dataset(
        "department_analysis",
        lambda: DepartmentAnalysis(ctx.database, ctx.projects, ctx.records, _plans(ctx), ctx.projects.timezone,
                                   project_facts=ctx.project_facts),
    )


def _cost_objects(ctx: OntologyContext) -> dict[str, dict[str, Any]]:
    return ctx.dataset("investment:cost_objects", lambda: {item["code"]: item for item in _investment(ctx).cost_objects()})


def _cost_object_or_error(ctx: OntologyContext, code: str) -> dict[str, Any]:
    item = _cost_objects(ctx).get(code)
    if item is None:
        raise ValueError(f"项目代码 {code} 不存在")
    return item


# --------------------------------------------------------------- datasources
@RULES.datasource("projects")
def load_projects(ctx: OntologyContext) -> list[dict[str, Any]]:
    """Projects as maintained in the base programs; everything else is added by the derivations below."""
    result = []
    for bundle in sorted(ctx.projects.project_sources(), key=lambda b: b["project_number"]):
        base = project_base(bundle)
        setting = base["settings"]
        result.append({
            "projectNumber": base["project_number"],
            "subject": base["subject"],
            "customerName": base["customer"],
            "crmOwner": base["owner"],
            "projectManager": setting.get("project_manager"),
            "category": base["category"],
            "contractType": base["contract_type"],
            "lifecycleStatus": base["lifecycle_status"],
            "plannedStart": setting.get("planned_start"),
            "plannedEnd": setting.get("planned_end"),
            "hoursPerPersonDay": setting.get("hours_per_person_day") or "8.00",
            "notes": setting.get("notes"),
            "contractValue": base["contract_value"],
            "invoicedAmount": base["invoiced_amount"],
            "crmReceivedAmount": base["crm_received_amount"],
            "plannedPersonDays": base["planned_person_days"],
            "__sources": bundle,
        })
    return result


@RULES.derivation("maintenanceStatus")
def derive_maintenance_status(ctx: OntologyContext, objects: list[dict[str, Any]], object_type: str) -> dict[Any, dict[str, Any]]:
    """What still needs maintenance, from the requirements declared on the object type (metadata, not code)."""
    definition = ctx.ontology.object_type(object_type)
    counts: dict[str, Any] = {}
    for req in definition.maintenance_requirements:
        if req.link and req.link not in counts:
            link = next(l for l in ctx.ontology.link_types if l.source_type == object_type and l.api_name == req.link)
            counted: dict[str, int] = {}
            for target in ctx.objects(link.target_type):
                key = str(target.get(link.target_key))
                counted[key] = counted.get(key, 0) + 1
            counts[req.link] = (link.source_key, counted)
    result = {}
    for item in objects:
        checklist = []
        for req in definition.maintenance_requirements:
            if not req.applies_to(item):
                continue
            if req.property:
                value = item.get(req.property)
                met = value not in (None, "") and str(value) not in req.missing_values
            else:
                source_key, counted = counts[req.link]
                met = counted.get(str(item.get(source_key)), 0) >= req.min_count
            checklist.append({"requirement": req.api_name, "label": req.display_name, "met": met,
                              "action": req.action, "property": req.property, "link": req.link})
        gaps = [entry for entry in checklist if not entry["met"]]
        result[item[definition.primary_key]] = {
            "maintenanceChecklist": checklist,
            "maintenanceGapCount": len(gaps),
            "maintenanceComplete": not gaps,
        }
    return result


@RULES.derivation("projectFacts")
def derive_project_facts(ctx: OntologyContext, objects: list[dict[str, Any]], object_type: str) -> dict[Any, dict[str, Any]]:
    """Risk, budget, collection, milestone and maintenance facts of every project (derivations/project.py)."""
    policy = ctx.projects.get_worktime_baseline()
    thresholds = _budget_thresholds(policy)
    as_of = ctx.projects.today()
    result = {}
    for item in objects:
        bundle = item["__sources"]
        p = derive_project(
            bundle["project_number"], bundle["crm_records"], bundle["settings"], bundle["time_entries"],
            bundle["milestones"], bundle["payment_nodes"], bundle["payment_terms"], bundle["interventions"],
            True, thresholds, policy, as_of,
            maintenance_reasons=[entry["label"] for entry in item["maintenanceChecklist"] if not entry["met"]],
        )
        p["maintenance_checklist"] = item["maintenanceChecklist"]
        result[item["projectNumber"]] = {
            "masterCategory": p["master_category"],
            "lifecycleSource": p.get("lifecycle_source"),
            "contractCount": p["contract_count"],
            "signedYear": p["signed_year"],
            "opportunityCount": p["opportunity_count"],
            "revenueSettlementBasis": p.get("revenue_settlement_basis"),
            "outstandingReceivableAmount": p.get("outstanding_receivable_amount"),
            "receivedAmount": p["received_amount"],
            "reportedPersonDays": p["reported_person_days"],
            "customerConfirmedPersonDays": p["customer_confirmed_person_days"],
            "confirmationGapPersonDays": p["confirmation_gap_person_days"],
            "budgetUtilization": p.get("budget_utilization"),
            "milestoneTotal": p["milestone_total"],
            "milestoneCompleted": p["milestone_completed"],
            "overdueMilestones": p["overdue_milestones"],
            "paymentNodeTotal": p["payment_node_total"],
            "paymentTermCount": p["payment_term_count"],
            "unlinkedPaymentTermCount": p["unlinked_payment_term_count"],
            "overduePaymentNodes": p["overdue_payment_nodes"],
            "openInterventions": p["open_interventions"],
            "riskLevel": p["risk_level"],
            "riskReasons": p["risk_reasons"],
            "__facts": p,
        }
    return result


@RULES.derivation("projectInvestment")
def derive_project_investment(ctx: OntologyContext, objects: list[dict[str, Any]], object_type: str) -> dict[Any, dict[str, Any]]:
    """Cost-to-win, delivery, at-risk and sunk person-days of every project code (investment.py)."""
    investments = _cost_objects(ctx)
    result = {}
    for item in objects:
        investment = investments.get(item["projectNumber"], {})
        result[item["projectNumber"]] = {
            "conversionDate": investment.get("conversion_date"),
            "opportunityOutcome": investment.get("outcome"),
            "costToWinPersonDays": investment.get("presales_converted_person_days"),
            "deliveryPersonDays": investment.get("delivery_person_days"),
            "atRiskPersonDays": investment.get("at_risk_person_days"),
            "sunkPersonDays": investment.get("sunk_person_days"),
        }
    return result


@RULES.datasource("costObjects")
def load_cost_objects(ctx: OntologyContext) -> list[dict[str, Any]]:
    settings = _investment(ctx).get_settings()
    rate = Decimal(settings["standard_person_day_cost"]) if settings["standard_person_day_cost"] else None
    return [
        {
            "code": item["code"],
            "title": item["subject"] or item["code"],
            "customerName": item["customer"],
            "registration": item["registration"],
            "opportunityOutcome": item["outcome"],
            "outcomeSource": item["outcome_source"],
            "outcomeReason": item["outcome_reason"],
            "outcomeDate": item["outcome_date"],
            "winProbability": item["win_probability"],
            "conversionDate": item["conversion_date"],
            "firstWorkDate": item["first_work_date"],
            "lastWorkDate": item["last_work_date"],
            "employeeCount": item["employee_count"],
            "totalPersonDays": item["total_person_days"],
            "presalesPersonDays": item["presales_person_days"],
            "costToWinPersonDays": item["presales_converted_person_days"],
            "deliveryPersonDays": item["delivery_person_days"],
            "atRiskPersonDays": item["at_risk_person_days"],
            "sunkPersonDays": item["sunk_person_days"],
            "expectedSunkPersonDays": item["expected_sunk_person_days"],
            "unregisteredPersonDays": item["unregistered_person_days"],
            "internalPersonDays": item["internal_person_days"],
            "costToWinShare": item["cost_to_win_share"],
            "presalesAgeDays": item["presales_age_days"],
            "stalled": item["stalled"],
            "estimatedCost": _quantize(Decimal(item["total_person_days"]) * rate) if rate is not None else None,
            "dataFlags": item["data_flags"],
        }
        for item in _cost_objects(ctx).values()
    ]


def _load_records(ctx: OntologyContext, data_type: str) -> list[dict[str, Any]]:
    return [
        {
            "recordId": r["record_id"],
            "projectNumber": _record_project_number(r),
            "subject": r["subject"],
            "customerName": r.get("customer"),
            "category": r.get("category"),
            "signedAt": r.get("signed_at"),
            "totalAmount": _money(r.get("total_amount")),
            "receivedAmount": _money(r.get("received_amount")),
            "invoicedAmount": _money(r.get("invoiced_amount")),
            "crmOwner": r.get("owner"),
            "probability": r.get("probability"),
            "status": r.get("status"),
            "mergedFromOpportunity": bool(r.get("merged_from_opportunity")),
            "updatedAt": r.get("updated_at"),
        }
        for r in ctx.crm_records()
        if r["data_type"] == data_type
    ]


@RULES.datasource("contracts")
def load_contracts(ctx: OntologyContext) -> list[dict[str, Any]]:
    years = {r["record_id"]: _signed_year(r) for r in ctx.crm_records() if r["data_type"] == "contract_order"}
    return [{**item, "signedYear": years[item["recordId"]]} for item in _load_records(ctx, "contract_order")]


@RULES.datasource("opportunities")
def load_opportunities(ctx: OntologyContext) -> list[dict[str, Any]]:
    # Same conversion rule as the import (normalizer._mark_converted_opportunities): once a contract carries the
    # project number the opportunity is converted; its record stays for audit.
    contracted = {
        _record_project_number(r) for r in ctx.crm_records() if r["data_type"] == "contract_order"
    } - {None}
    stages = {r["record_id"]: r.get("sales_stage") for r in ctx.crm_records() if r["data_type"] == "sales_opportunity"}
    return [
        {**item, "convertedToContract": item["projectNumber"] in contracted, "salesStage": stages[item["recordId"]]}
        for item in _load_records(ctx, "sales_opportunity")
    ]


@RULES.datasource("customers")
def load_customers(ctx: OntologyContext) -> list[dict[str, Any]]:
    current_year = ctx.projects.today().year
    customers: dict[str, dict[str, Any]] = {}
    for record in ctx.crm_records():
        name = (record.get("customer") or "").strip()
        if not name:
            continue
        item = customers.setdefault(
            name,
            {
                "customerName": name,
                "contractCount": 0,
                "opportunityCount": 0,
                "projectNumbers": set(),
                "totalContractValue": Decimal("0"),
                "totalReceivedAmount": Decimal("0"),
                "totalInvoicedAmount": Decimal("0"),
                "currentYearContractValue": Decimal("0"),
                "currentYearReceivedAmount": Decimal("0"),
                "currentYearInvoicedAmount": Decimal("0"),
                "latestSignedAt": None,
                "crmOwners": set(),
            },
        )
        if record["data_type"] == "contract_order":
            item["contractCount"] += 1
            this_year = _signed_year(record) == current_year
            for field, total, current in (
                ("total_amount", "totalContractValue", "currentYearContractValue"),
                ("received_amount", "totalReceivedAmount", "currentYearReceivedAmount"),
                ("invoiced_amount", "totalInvoicedAmount", "currentYearInvoicedAmount"),
            ):
                amount = _money(record.get(field))
                if amount:
                    item[total] += Decimal(amount)
                    if this_year:
                        item[current] += Decimal(amount)
        else:
            item["opportunityCount"] += 1
        project_number = _record_project_number(record)
        if project_number:
            item["projectNumbers"].add(project_number)
        if record.get("owner"):
            item["crmOwners"].add(record["owner"])
        # An opportunity's signed_at is its expected signing date, not a signing.
        signed = record.get("signed_at") if record["data_type"] == "contract_order" else None
        if signed and (item["latestSignedAt"] is None or signed > item["latestSignedAt"]):
            item["latestSignedAt"] = signed
    return [
        {
            **item,
            "projectCount": len(item.pop("projectNumbers")),
            "totalContractValue": _quantize(item["totalContractValue"]),
            "totalReceivedAmount": _quantize(item["totalReceivedAmount"]),
            "totalInvoicedAmount": _quantize(item["totalInvoicedAmount"]),
            "currentYearContractValue": _quantize(item["currentYearContractValue"]),
            "currentYearReceivedAmount": _quantize(item["currentYearReceivedAmount"]),
            "currentYearInvoicedAmount": _quantize(item["currentYearInvoicedAmount"]),
            "crmOwners": sorted(item.pop("crmOwners")),
        }
        for item in sorted(customers.values(), key=lambda c: c["customerName"])
    ]


def _time_entry_rows(ctx: OntologyContext) -> list[dict[str, Any]]:
    return ctx.table("time_entries", "work_date DESC, external_id")


@RULES.datasource("employees")
def load_employees(ctx: OntologyContext) -> list[dict[str, Any]]:
    # Periodic properties use the selected month, by default the latest month with time entries.
    month = ctx.period or _department(ctx).latest_work_month()
    employees: dict[str, dict[str, Any]] = {}
    for row in _time_entry_rows(ctx):
        item = employees.setdefault(
            row["employee_id"],
            {
                "employeeId": row["employee_id"],
                "employeeName": row["employee_name"],
                "entryCount": 0,
                "reportedHours": Decimal("0"),
                "approvedHours": Decimal("0"),
                "projects": set(),
                "projectHours": {},
                "firstWorkDate": row["work_date"],
                "lastWorkDate": row["work_date"],
                "monthEntryCount": 0,
                "monthApprovedHours": Decimal("0"),
                "monthProjectHours": {},
            },
        )
        item["entryCount"] += 1
        in_month = row["work_date"].startswith(month)
        if in_month:
            item["monthEntryCount"] += 1
            if row["approval_status"] == "approved":
                item["monthApprovedHours"] += Decimal(row["approved_hours"])
        if _counts_as_effort(row):
            hours = Decimal(row["hours"]) + Decimal(row.get("overtime_hours") or "0")
            item["reportedHours"] += hours
            item["projectHours"][row["project_number"]] = item["projectHours"].get(row["project_number"], Decimal("0")) + hours
            if in_month:
                item["monthProjectHours"][row["project_number"]] = item["monthProjectHours"].get(row["project_number"], Decimal("0")) + hours
        if row["approval_status"] == "approved":
            item["approvedHours"] += Decimal(row["approved_hours"])
        item["projects"].add(row["project_number"])
        item["firstWorkDate"] = min(item["firstWorkDate"], row["work_date"])
        item["lastWorkDate"] = max(item["lastWorkDate"], row["work_date"])
    health = {
        str(row["employee_id"] or row["employee_name"]): row
        for row in ctx.dataset(f"department:resource_health:{month}", lambda: _department(ctx).resource_health(month))["data"]
    }

    def health_fields(employee_id: str) -> dict[str, Any]:
        row = health.get(str(employee_id))
        if row is None:
            return {"healthStatus": None, "healthReasons": None, "monthUtilizationPercent": None,
                    "overtimeSharePercent": None, "consecutiveOverloadMonths": None, "concurrentProjectCount": None}
        return {
            "healthStatus": row["status"],
            "healthReasons": {"month": row["month"], "statuses": row["statuses"], "reasons": row["reasons"]},
            "monthUtilizationPercent": row["utilization_percent"],
            "overtimeSharePercent": row["overtime_share_percent"],
            "consecutiveOverloadMonths": row["consecutive_overload_months"],
            "concurrentProjectCount": row["concurrent_project_count"],
        }

    cost_objects = _cost_objects(ctx)

    def effort_shares(project_hours: dict[str, Decimal], total: Decimal) -> list[dict[str, Any]]:
        rows = sorted(project_hours.items(), key=lambda pair: (-pair[1], pair[0]))
        return [{
            "projectNumber": code,
            "projectName": (cost_objects.get(code) or {}).get("subject"),
            "internal": (cost_objects.get(code) or {}).get("registration") == "internal",
            "hours": _quantize(hours),
            "sharePercent": format((hours / total * 100).quantize(Decimal("0.1")), "f") if total else None,
        } for code, hours in rows]

    def month_fields(item: dict[str, Any]) -> dict[str, Any]:
        hours = item.pop("monthProjectHours")
        total = sum(hours.values(), Decimal("0"))
        return {
            "statMonth": month,
            "monthReportedHours": _quantize(total),
            "monthApprovedHours": _quantize(item.pop("monthApprovedHours")),
            "monthProjectCount": len(hours),
            "monthProjectEffortShares": effort_shares(hours, total),
        }

    return [
        {
            **item,
            "reportedHours": _quantize(item["reportedHours"]),
            "approvedHours": _quantize(item["approvedHours"]),
            "projectEffortShares": effort_shares(item.pop("projectHours"), item["reportedHours"]),
            "projectCount": len(item.pop("projects")),
            **month_fields(item),
            **health_fields(item["employeeId"]),
        }
        for item in sorted(employees.values(), key=lambda e: e["employeeName"])
    ]


@RULES.datasource("timeEntries")
def load_time_entries(ctx: OntologyContext) -> list[dict[str, Any]]:
    investments = _cost_objects(ctx)

    def phase(row: dict[str, Any]) -> str:
        item = investments.get(row["project_number"])
        if item is None:
            return "unregistered"
        return _phase(item["registration"], item["outcome"], item["conversion_date"], row["work_date"])

    return [
        {
            "externalId": row["external_id"],
            "projectNumber": row["project_number"],
            "employeeId": row["employee_id"],
            "employeeName": row["employee_name"],
            "workDate": row["work_date"],
            "hours": row["hours"],
            "overtimeHours": row.get("overtime_hours") or "0.00",
            "approvedHours": row["approved_hours"],
            "approvalStatus": row["approval_status"],
            "countsAsEffort": _counts_as_effort(row),
            "costPhase": phase(row),
            "workContent": row["work_content"],
            "sourceUpdatedAt": row["source_updated_at"],
        }
        for row in _time_entry_rows(ctx)
    ]


def _milestone_rows(ctx: OntologyContext) -> list[dict[str, Any]]:
    return ctx.table("milestones", "project_number, sequence_no, planned_date")


@RULES.datasource("milestones")
def load_milestones(ctx: OntologyContext) -> list[dict[str, Any]]:
    return [
        {
            "milestoneId": row["milestone_id"],
            "projectNumber": row["project_number"],
            "sequenceNo": row.get("sequence_no"),
            "name": row["name"],
            "plannedStart": row.get("planned_start") or row["planned_date"],
            "plannedEnd": row.get("planned_end") or row["planned_date"],
            "completedDate": row.get("completed_date"),
            "status": row["status"],
            "owner": row.get("owner"),
            "deliverable": row.get("deliverable"),
            "isPaymentNode": bool(row.get("is_payment_node")),
            "paymentNodeId": row.get("payment_node_id"),
            "confirmedBy": row.get("confirmed_by"),
            "confirmedAt": row.get("confirmed_at"),
            "notes": row.get("notes"),
            "updatedAt": row["updated_at"],
        }
        for row in _milestone_rows(ctx)
    ]


@RULES.datasource("paymentNodes")
def load_payment_nodes(ctx: OntologyContext) -> list[dict[str, Any]]:
    milestone_by_payment = {
        row["payment_node_id"]: row["milestone_id"]
        for row in _milestone_rows(ctx)
        if row.get("payment_node_id")
    }
    return [
        {
            "paymentNodeId": row["payment_node_id"],
            "projectNumber": row["project_number"],
            "name": row["name"],
            "plannedDate": row["planned_date"],
            "plannedAmount": row["planned_amount"],
            "actualDate": row.get("actual_date"),
            "actualAmount": row.get("actual_amount") or "0.00",
            "status": row["status"],
            "linkedMilestoneId": milestone_by_payment.get(row["payment_node_id"]),
            "notes": row.get("notes"),
            "updatedAt": row["updated_at"],
        }
        for row in ctx.table("payment_nodes", "project_number, planned_date")
    ]


@RULES.datasource("paymentTerms")
def load_payment_terms(ctx: OntologyContext) -> list[dict[str, Any]]:
    """Payment terms as maintained; the node they produced and its schedule come from paymentTermFacts."""
    return [
        {
            "paymentTermId": row["payment_term_id"],
            "projectNumber": row["project_number"],
            "name": row["name"],
            "paymentRatio": row.get("payment_ratio"),
            "plannedAmount": row["planned_amount"],
            "linkedMilestoneId": row.get("linked_milestone_id"),
            "notes": row.get("notes"),
            "updatedAt": row["updated_at"],
            "__node_id": row.get("payment_node_id"),
        }
        for row in ctx.table("payment_terms", "project_number, created_at")
    ]


@RULES.derivation("paymentTermFacts")
def derive_payment_term_facts(ctx: OntologyContext, objects: list[dict[str, Any]], object_type: str) -> dict[Any, dict[str, Any]]:
    """The payment node a linked term produced, its planned date and status (unscheduled until linked)."""
    nodes = {row["payment_node_id"]: row for row in ctx.table("payment_nodes", "project_number, planned_date")}
    milestones = {row["milestone_id"]: row for row in _milestone_rows(ctx)}
    result = {}
    for item in objects:
        node = nodes.get(item["__node_id"] or "", {})
        milestone = milestones.get(item["linkedMilestoneId"] or "", {})
        result[item["paymentTermId"]] = {
            "paymentNodeId": item["__node_id"],
            "plannedDate": node.get("planned_date") or milestone.get("planned_end"),
            "nodeStatus": node.get("status") or "unscheduled",
        }
    return result


OPERATING_POLICY_FIELDS = {
    "standardHoursPerDay": "standard_hours_per_day",
    "standardWorkDaysPerYear": "standard_work_days_per_year",
    "attentionUtilizationPercent": "attention_utilization_percent",
    "minimumUtilizationPercent": "minimum_utilization_percent",
    "excellentUtilizationPercent": "excellent_utilization_percent",
    "overloadUtilizationPercent": "overload_utilization_percent",
    "budgetAttentionPercent": "budget_attention_percent",
    "budgetHighRiskPercent": "budget_high_risk_percent",
    "overtimeShareAttentionPercent": "overtime_share_attention_percent",
    "overloadConsecutiveMonths": "overload_consecutive_months",
    "maxConcurrentProjects": "max_concurrent_projects",
    "planLagTolerancePoints": "plan_lag_tolerance_points",
    "notes": "notes",
}


# Stored as text like every other parameter; the ontology declares them as integers.
INTEGER_POLICY_FIELDS = frozenset({"overloadConsecutiveMonths", "maxConcurrentProjects"})


@RULES.datasource("operatingPolicy")
def load_operating_policy(ctx: OntologyContext) -> list[dict[str, Any]]:
    baseline = ctx.projects.get_worktime_baseline()
    investment = _investment(ctx).get_settings()
    def value(api: str, column: str) -> Any:
        raw = baseline.get(column)
        return int(Decimal(raw)) if api in INTEGER_POLICY_FIELDS and raw not in (None, "") else raw

    return [{
        "configId": baseline["config_id"],
        **{api: value(api, column) for api, column in OPERATING_POLICY_FIELDS.items()},
        "stallDays": investment["stall_days"],
        "lostStatuses": investment["lost_statuses"],
        "internalCodes": investment["internal_codes"],
        "updatedAt": baseline["updated_at"],
    }]


ANNUAL_PLAN_FIELDS = {
    "department": "department",
    "newContractTarget": "new_contract_target",
    "collectionTarget": "collection_target",
    "capacityPersonDaysTarget": "capacity_person_days_target",
    "headcountTarget": "headcount_target",
    "utilizationTargetPercent": "utilization_target_percent",
    "notes": "notes",
}


@RULES.datasource("annualOperatingPlans")
def load_annual_operating_plans(ctx: OntologyContext) -> list[dict[str, Any]]:
    return [
        {
            "planId": row["plan_id"],
            "year": row["year"],
            **{api: row.get(column) for api, column in ANNUAL_PLAN_FIELDS.items()},
            "updatedAt": row["updated_at"],
        }
        for row in ctx.table("annual_operating_plans", "year DESC, department")
    ]


@RULES.datasource("interventions")
def load_interventions(ctx: OntologyContext) -> list[dict[str, Any]]:
    return [
        {
            "interventionId": row["intervention_id"],
            "projectNumber": row["project_number"],
            "title": row["title"],
            "interventionType": row["intervention_type"],
            "status": row["status"],
            "owner": row.get("owner"),
            "dueDate": row.get("due_date"),
            "notes": row.get("notes"),
            "updatedAt": row["updated_at"],
        }
        for row in ctx.table("interventions", "project_number, updated_at DESC")
    ]


# ------------------------------------------------------------ link resolvers
@RULES.resolver("projectTeam")
def project_team(ctx: OntologyContext, project: dict[str, Any]) -> list[dict[str, Any]]:
    ids = {
        row["employee_id"] for row in _time_entry_rows(ctx)
        if row["project_number"] == project["projectNumber"]
    }
    return [e for e in ctx.objects("Employee") if e["employeeId"] in ids]


@RULES.resolver("employeeProjects")
def employee_projects(ctx: OntologyContext, employee: dict[str, Any]) -> list[dict[str, Any]]:
    numbers = {
        row["project_number"] for row in _employee_period_rows(ctx, employee)
    }
    return [p for p in ctx.objects("Project") if p["projectNumber"] in numbers]


@RULES.resolver("costObjectTeam")
def cost_object_team(ctx: OntologyContext, cost_object: dict[str, Any]) -> list[dict[str, Any]]:
    ids = {row["employee_id"] for row in _time_entry_rows(ctx) if row["project_number"] == cost_object["code"]}
    return [e for e in ctx.objects("Employee") if e["employeeId"] in ids]


def _employee_period_rows(ctx: OntologyContext, employee: dict[str, Any]) -> list[dict[str, Any]]:
    """An employee's time entries; with a selected month only that month's effective entries (link periodProperty)."""
    rows = [row for row in _time_entry_rows(ctx) if row["employee_id"] == employee["employeeId"]]
    if ctx.period:
        rows = [row for row in rows if row["work_date"].startswith(ctx.period) and _counts_as_effort(row)]
    return rows


@RULES.resolver("employeeCostObjects")
def employee_cost_objects(ctx: OntologyContext, employee: dict[str, Any]) -> list[dict[str, Any]]:
    codes = {row["project_number"] for row in _employee_period_rows(ctx, employee)}
    return [c for c in ctx.objects("CostObject") if c["code"] in codes]


# ------------------------------------------------------- submission criteria
def _effective(values: dict[str, Any], target: dict[str, Any] | None, key: str) -> Any:
    if key in values:
        return values[key]
    return (target or {}).get(key)


@RULES.criterion("cappedBudget")
def capped_budget(ctx, values, target) -> str | None:
    return maintenance_rules.capped_budget(_effective(values, target, "contractType"), _effective(values, target, "plannedPersonDays"))


@RULES.criterion("dateOrder")
def date_order(ctx, values, target, *, start: str, end: str, label: str, ignore_target: bool = False) -> str | None:
    """``ignore_target`` for actions that create a new object: the target is its parent, whose dates do not apply."""
    source = None if ignore_target else target
    return maintenance_rules.date_order(_effective(values, source, start), _effective(values, source, end), label)


@RULES.criterion("nonNegative")
def non_negative(ctx, values, target, *, parameters: list[str]) -> str | None:
    return next((reason for key in parameters if (reason := maintenance_rules.non_negative(values.get(key)))), None)


@RULES.criterion("statusIsNot")
def status_is_not(ctx, values, target, *, status: str, reason: str) -> str | None:
    return reason if target and target.get("status") == status else None


@RULES.criterion("notLocked")
def not_locked(ctx, values, target, *, lock: str) -> str | None:
    """Status locks (completed milestone, received payment, closed intervention): one table for actions and base program."""
    return maintenance_rules.locked(lock, (target or {}).get("status"))


@RULES.criterion("linkedPaymentNotPaid")
def linked_payment_not_paid(ctx, values, target) -> str | None:
    payment = ctx.get("PaymentNode", (target or {}).get("paymentNodeId") or "")
    return maintenance_rules.linked_payment_unpaid(payment["status"] if payment else None)


@RULES.criterion("paymentRatioRange")
def payment_ratio_range(ctx, values, target) -> str | None:
    return maintenance_rules.payment_ratio(values.get("paymentRatio"))


@RULES.criterion("termAvailable")
def term_available(ctx, values, target) -> str | None:
    """Linking a payment term: it may not already belong to another milestone (create: target is the project)."""
    term_id = values.get("paymentTermId")
    term = ctx.get("PaymentTerm", term_id) if term_id else None
    milestone_id = (target or {}).get("milestoneId")
    return maintenance_rules.term_available(term.get("linkedMilestoneId"), milestone_id) if term else None


@RULES.criterion("notLinkedToMilestone")
def not_linked_to_milestone(ctx, values, target) -> str | None:
    return maintenance_rules.node_not_milestone_managed((target or {}).get("linkedMilestoneId"))


@RULES.criterion("termNotPaid")
def term_not_paid(ctx, values, target) -> str | None:
    return maintenance_rules.term_locked((target or {}).get("nodeStatus"))


@RULES.criterion("termNotLinked")
def term_not_linked(ctx, values, target) -> str | None:
    return maintenance_rules.term_unlinked((target or {}).get("linkedMilestoneId"))


def _text_list(value: Any) -> list[str] | None:
    """Accept a JSON list or text separated by 、 , ； or new lines (what the action form sends)."""
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    elif isinstance(value, str):
        items = [item.strip() for item in re.split(r"[、,，;；\n]", value)]
    else:
        return None
    return [item for item in items if item]


def _policy_number(values: dict[str, Any], target: dict[str, Any] | None, key: str) -> Decimal | None:
    value = _effective(values, target, key)
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except InvalidOperation:
        return None


POLICY_THRESHOLD_GROUPS = {
    "utilization": (
        ("attentionUtilizationPercent", "利用率关注线"),
        ("minimumUtilizationPercent", "最低线"),
        ("excellentUtilizationPercent", "优秀线"),
        ("overloadUtilizationPercent", "过载线"),
    ),
    "budget": (
        ("budgetAttentionPercent", "预算关注线"),
        ("budgetHighRiskPercent", "预算高风险线"),
    ),
}


@RULES.criterion("policyThresholdOrder")
def policy_threshold_order(ctx, values, target, *, group: str, upper: str, inclusive: bool = True) -> str | None:
    """Thresholds of one group must increase strictly and stay above 0 and within the upper bound."""
    fields = POLICY_THRESHOLD_GROUPS[group]
    numbers = [(label, _policy_number(values, target, key)) for key, label in fields]
    missing = [label for label, number in numbers if number is None]
    if missing:
        return f"{'、'.join(missing)}必须是数字"
    limit = Decimal(upper)
    chain = " < ".join(f"{label} {number.normalize():f}%" for label, number in numbers)
    above_limit = numbers[-1][1] > limit if inclusive else numbers[-1][1] >= limit
    if numbers[0][1] <= 0 or above_limit:
        bound = "不超过" if inclusive else "小于"
        return f"阈值必须大于 0 且{bound} {limit.normalize():f}%：{chain}"
    if any(left[1] >= right[1] for left, right in zip(numbers, numbers[1:])):
        return f"阈值必须逐级递增：{chain}"
    return None


@RULES.criterion("policyValuesValid")
def policy_values_valid(ctx, values, target) -> str | None:
    hours = _policy_number(values, target, "standardHoursPerDay")
    if hours is None or not Decimal("0") < hours <= Decimal("24"):
        return "标准每日小时数必须在 0 到 24 之间"
    days = _policy_number(values, target, "standardWorkDaysPerYear")
    if days is None or not Decimal("1") <= days <= Decimal("366"):
        return "年度参考工作日必须在 1 到 366 之间"
    stall_days = _effective(values, target, "stallDays")
    if stall_days is not None and not 7 <= int(stall_days) <= 730:
        return "商机停滞天数必须在 7 到 730 之间"
    for key, label in (("lostStatuses", "丢单状态"), ("internalCodes", "内部代码")):
        if key in values:
            items = _text_list(values[key])
            if not items:
                return f"{label}至少要有一项，多项用「、」分隔"
    return None


@RULES.criterion("policyRangesValid")
def policy_ranges_valid(ctx, values, target) -> str | None:
    """Same ranges as the settings page (POLICY_RANGES), so both write paths agree (lesson L10)."""
    for api, column in OPERATING_POLICY_FIELDS.items():
        if column not in POLICY_RANGES or api not in values:
            continue
        low, high, label, integer = POLICY_RANGES[column]
        number = _policy_number(values, target, api)
        if number is None or not low <= number <= high or (integer and number != number.to_integral_value()):
            kind = "整数" if integer else "数字"
            return f"{label}必须是 {low.normalize():f} 到 {high.normalize():f} 之间的{kind}"
    return None


@RULES.criterion("annualPlanValuesValid")
def annual_plan_values_valid(ctx, values, target) -> str | None:
    if not 2000 <= int(values["year"]) <= 2100:
        return "年度必须在 2000 到 2100 之间"
    for api, column in ANNUAL_PLAN_FIELDS.items():
        if column not in AnnualPlanRepository.PLAN_FIELDS or values.get(api) in (None, ""):
            continue
        try:
            number = Decimal(str(values[api]))
        except InvalidOperation:
            return f"{api} 必须是数字"
        if not number.is_finite() or number < 0:
            return f"{api} 不能为负数"
        if column.endswith("_percent") and number > 100:
            return f"{api} 不能超过 100%"
    return None


@RULES.criterion("outcomeApplicable")
def outcome_applicable(ctx, values, target) -> str | None:
    registration = (target or {}).get("registration")
    if registration == "contracted":
        return "该项目代码已签约，不能标记丢单"
    if registration == "internal":
        return "内部代码不是商机，不能标记丢单"
    if (target or {}).get("opportunityOutcome") == "won":
        return "CRM 中该商机已成功，不能标记丢单"
    return None


@RULES.criterion("outcomeDateValid")
def outcome_date_valid(ctx, values, target) -> str | None:
    outcome_date = values.get("outcomeDate") or _investment(ctx)._today().isoformat()
    if outcome_date > _investment(ctx)._today().isoformat():
        return "结论日期不能晚于今天"
    first = (target or {}).get("firstWorkDate")
    if first and outcome_date < first:
        return f"结论日期不能早于首次投入日 {first}"
    return None


@RULES.criterion("outcomeIsManual")
def outcome_is_manual(ctx, values, target) -> str | None:
    if (target or {}).get("outcomeSource") != "人工确认":
        return "该项目代码没有人工确认的丢单 / 放弃结论"
    return None


# ------------------------------------------------------------ action handlers
PROJECT_SETTING_FIELDS = {
    "contractType": "contract_type",
    "lifecycleStatus": "lifecycle_status",
    "plannedPersonDays": "planned_person_days",
    "hoursPerPersonDay": "hours_per_person_day",
    "plannedStart": "planned_start",
    "plannedEnd": "planned_end",
    "projectManager": "project_manager",
    "notes": "notes",
}
MILESTONE_FIELDS = {
    "name": "name",
    "plannedStart": "planned_start",
    "plannedEnd": "planned_end",
    "owner": "owner",
    "deliverable": "deliverable",
    "status": "status",
    "notes": "notes",
    "sequenceNo": "sequence_no",
    "paymentTermId": "payment_term_id",
}
PAYMENT_FIELDS = {
    "name": "name",
    "plannedDate": "planned_date",
    "plannedAmount": "planned_amount",
    "notes": "notes",
}
INTERVENTION_FIELDS = {
    "title": "title",
    "interventionType": "intervention_type",
    "owner": "owner",
    "dueDate": "due_date",
    "notes": "notes",
}


def _snake(values: dict[str, Any], mapping: dict[str, str], keep_none: frozenset[str] = frozenset()) -> dict[str, Any]:
    return {
        mapping[key]: value
        for key, value in values.items()
        if key in mapping and (value is not None or key in keep_none)
    }


def _edit(object_type: str, primary_key: str, operation: str) -> dict[str, str]:
    return {"objectType": object_type, "primaryKey": primary_key, "operation": operation}


@RULES.handler("configureProject")
def configure_project(ctx, values, target, actor):
    clearable = frozenset(PROJECT_SETTING_FIELDS) - {"contractType", "lifecycleStatus"}
    ctx.projects.upsert_settings(target["projectNumber"], _snake(values, PROJECT_SETTING_FIELDS, clearable))
    return {"edits": [_edit("Project", target["projectNumber"], "update")]}


@RULES.handler("createMilestone")
def create_milestone(ctx, values, target, actor):
    milestone = ctx.projects.create_milestone(target["projectNumber"], _snake(values, MILESTONE_FIELDS))
    edits = [_edit("Milestone", milestone["milestone_id"], "create")]
    if milestone.get("payment_node_id"):
        edits.append(_edit("PaymentNode", milestone["payment_node_id"], "create"))
    return {"edits": edits, "record": milestone}


@RULES.handler("editMilestone")
def edit_milestone(ctx, values, target, actor):
    payload = _snake(values, MILESTONE_FIELDS, frozenset({"owner", "deliverable", "notes", "paymentTermId"}))
    milestone = ctx.projects.update_milestone(target["projectNumber"], target["milestoneId"], payload, actor)
    edits = [_edit("Milestone", target["milestoneId"], "update")]
    if milestone.get("payment_node_id"):
        operation = "update" if target.get("paymentNodeId") else "create"
        edits.append(_edit("PaymentNode", milestone["payment_node_id"], operation))
    elif target.get("paymentNodeId"):
        edits.append(_edit("PaymentNode", target["paymentNodeId"], "delete"))
    return {"edits": edits, "record": milestone}


@RULES.handler("deleteMilestone")
def delete_milestone(ctx, values, target, actor):
    ctx.projects.delete_milestone(target["projectNumber"], target["milestoneId"], actor)
    edits = [_edit("Milestone", target["milestoneId"], "delete")]
    if target.get("paymentNodeId"):
        edits.append(_edit("PaymentNode", target["paymentNodeId"], "delete"))
    return {"edits": edits}


@RULES.handler("confirmMilestone")
def confirm_milestone(ctx, values, target, actor):
    milestone = ctx.projects.confirm_milestone(
        target["projectNumber"], target["milestoneId"], values.get("confirmedBy") or actor
    )
    return {"edits": [_edit("Milestone", target["milestoneId"], "update")], "record": milestone}


@RULES.handler("editPaymentNode")
def edit_payment_node(ctx, values, target, actor):
    payload = _snake(values, PAYMENT_FIELDS, frozenset({"notes"}))
    node = ctx.projects.update_payment_node(target["projectNumber"], target["paymentNodeId"], payload)
    return {"edits": [_edit("PaymentNode", target["paymentNodeId"], "update")], "record": node}


@RULES.handler("deletePaymentNode")
def delete_payment_node(ctx, values, target, actor):
    ctx.projects.delete_payment_node(target["projectNumber"], target["paymentNodeId"], actor)
    return {"edits": [_edit("PaymentNode", target["paymentNodeId"], "delete")]}


@RULES.handler("confirmPayment")
def confirm_payment(ctx, values, target, actor):
    node = ctx.projects.confirm_payment_node(
        target["projectNumber"],
        target["paymentNodeId"],
        values.get("confirmedBy") or actor,
        values.get("actualAmount"),
        values.get("actualDate"),
    )
    return {
        "edits": [
            _edit("PaymentNode", target["paymentNodeId"], "update"),
            _edit("Project", target["projectNumber"], "update"),
        ],
        "record": node,
    }


PAYMENT_TERM_FIELDS = {
    "name": "name",
    "paymentRatio": "payment_ratio",
    "plannedAmount": "planned_amount",
    "notes": "notes",
}


@RULES.handler("createPaymentTerm")
def create_payment_term(ctx, values, target, actor):
    term = ctx.projects.create_payment_term(target["projectNumber"], _snake(values, PAYMENT_TERM_FIELDS))
    return {"edits": [_edit("PaymentTerm", term["payment_term_id"], "create")], "record": term}


@RULES.handler("editPaymentTerm")
def edit_payment_term(ctx, values, target, actor):
    current = {"name": target["name"], "planned_amount": target["plannedAmount"],
               "payment_ratio": target.get("paymentRatio"), "notes": target.get("notes")}
    payload = {**current, **_snake(values, PAYMENT_TERM_FIELDS, frozenset({"notes", "paymentRatio"}))}
    term = ctx.projects.update_payment_term(target["projectNumber"], target["paymentTermId"], payload)
    return {"edits": [_edit("PaymentTerm", target["paymentTermId"], "modify")], "record": term}


@RULES.handler("deletePaymentTerm")
def delete_payment_term(ctx, values, target, actor):
    ctx.projects.delete_payment_term(target["projectNumber"], target["paymentTermId"], actor)
    return {"edits": [_edit("PaymentTerm", target["paymentTermId"], "delete")]}


@RULES.handler("updateOperatingPolicy")
def update_operating_policy(ctx, values, target, actor):
    baseline_changes = _snake(values, OPERATING_POLICY_FIELDS)
    if baseline_changes:
        ctx.projects.update_worktime_baseline(baseline_changes)
    investment_changes = {}
    if "stallDays" in values:
        investment_changes["stall_days"] = values["stallDays"]
    for api, key in (("lostStatuses", "lost_statuses"), ("internalCodes", "internal_codes")):
        if api in values:
            investment_changes[key] = _text_list(values[api])
    if investment_changes:
        _investment(ctx).update_settings(investment_changes)
    return {"edits": [_edit("OperatingPolicy", target["configId"], "modify")]}


@RULES.handler("setAnnualOperatingPlan")
def set_annual_operating_plan(ctx, values, target, actor):
    service = _plans(ctx)
    year = int(values["year"])
    department = (values.get("department") or "").strip() or None
    existed = service.get_plan(year, department or "实施交付部") is not None
    plan = service.upsert_plan(year, _snake(values, ANNUAL_PLAN_FIELDS, frozenset(ANNUAL_PLAN_FIELDS) - {"department"}), actor)
    return {"edits": [_edit("AnnualOperatingPlan", plan["plan_id"], "update" if existed else "create")], "record": plan}


@RULES.handler("openIntervention")
def open_intervention(ctx, values, target, actor):
    row = ctx.projects.create_intervention(target["projectNumber"], _snake(values, INTERVENTION_FIELDS))
    return {"edits": [_edit("Intervention", row["intervention_id"], "create")], "record": row}


@RULES.handler("updateInterventionStatus")
def update_intervention_status(ctx, values, target, actor):
    ctx.projects.update_intervention_status(
        target["projectNumber"], target["interventionId"], values["status"], values.get("notes")
    )
    return {"edits": [_edit("Intervention", target["interventionId"], "update")]}


@RULES.handler("recordOpportunityOutcome")
def record_opportunity_outcome(ctx, values, target, actor):
    service = _investment(ctx)
    service.record_outcome(
        target["code"], values["outcome"], values["reason"],
        values.get("outcomeDate") or service._today().isoformat(), values.get("notes"), actor,
    )
    edits = [_edit("CostObject", target["code"], "update")]
    if target.get("registration") == "opportunity":
        edits.append(_edit("Project", target["code"], "update"))
    return {"edits": edits}


@RULES.handler("reopenOpportunity")
def reopen_opportunity(ctx, values, target, actor):
    _investment(ctx).reopen(target["code"])
    edits = [_edit("CostObject", target["code"], "update")]
    if target.get("registration") == "opportunity":
        edits.append(_edit("Project", target["code"], "update"))
    return {"edits": edits}


# ------------------------------------------------------------------ functions
def _project_detail(ctx: OntologyContext, project_number: str) -> dict[str, Any]:
    project = ctx.project_fact(project_number)
    if project is None:
        raise ValueError(f"项目 {project_number} 不存在")
    return project


@RULES.function("projectRiskAssessment")
def fn_project_risk(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    project = _project_detail(ctx, params["project"])
    finance = _finance_visible(ctx)
    alerts = [
        alert if finance or alert["code"] != "PAYMENT_DUE" else {**alert, "detail": "回款尚未确认到账（金额已脱敏）"}
        for alert in project["anomaly_alerts"]
    ]
    return {
        "riskLevel": project["risk_level"],
        "riskReasons": project["risk_reasons"],
        "anomalyAlerts": alerts,
        "maintenanceComplete": project["maintenance_complete"],
        "maintenanceReasons": project["maintenance_reasons"],
        "overdueMilestones": project["overdue_milestones"],
        "overduePaymentNodes": project["overdue_payment_nodes"],
    }


@RULES.function("projectBudgetStatus")
def fn_project_budget(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    project = _project_detail(ctx, params["project"])
    planned = Decimal(project["planned_person_days"]) if project.get("planned_person_days") else None
    reported = Decimal(project["reported_person_days"])
    utilization = project.get("budget_utilization")
    if planned is None:
        status = "无上限（按确认工时结算）" if project["contract_type"] == "open_ended" else "未设置预算"
    elif utilization is not None and utilization > 100:
        status = "已超预算"
    elif utilization is not None and utilization >= 80:
        status = "接近上限"
    else:
        status = "正常"
    return {
        "contractType": project["contract_type"],
        "hoursPerPersonDay": (project.get("settings") or {}).get("hours_per_person_day") or "8.00",
        "plannedPersonDays": project.get("planned_person_days"),
        "budgetHours": project.get("budget_hours"),
        "reportedPersonDays": project["reported_person_days"],
        "remainingPersonDays": _quantize(planned - reported) if planned is not None else None,
        "budgetUtilization": utilization,
        "customerConfirmedPersonDays": project["customer_confirmed_person_days"],
        "confirmationGapPersonDays": project["confirmation_gap_person_days"],
        "billablePersonDays": project.get("billable_person_days"),
        "status": status,
    }


@RULES.function("projectPaymentStatus")
def fn_project_payment(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    project = _project_detail(ctx, params["project"])
    contract_value = Decimal(project["contract_value"])
    crm_received = Decimal(project["crm_received_amount"])
    return {
        "contractStatus": project.get("contract_status"),
        "contractValue": project["contract_value"],
        "invoicedAmount": project["invoiced_amount"],
        "crmReceivedAmount": project["crm_received_amount"],
        "outstandingReceivableAmount": project.get("outstanding_receivable_amount"),
        "collectionRate": round(float(crm_received / contract_value * 100), 1) if contract_value > 0 else None,
        "paymentPlan": project["payment_summary"],
        "overduePaymentNodes": project["overdue_payment_nodes"],
    }


@RULES.function("projectHealthForPeriod")
def fn_project_health(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    result = project_health_view(
        ctx.project_facts(), ctx.projects._crm_search_terms_by_project, ctx.projects.get_worktime_baseline(), ctx.projects._now(),
        start_month=params["startMonth"],
        end_month=params.get("endMonth") or params["startMonth"],
        project_number=params["project"],
    )
    return {"period": result["meta"], "health": result["data"][0] if result["data"] else None}


@RULES.function("employeeUtilization")
def fn_employee_utilization(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    employee = ctx.get("Employee", params["employee"])
    result = ctx.projects.employee_worktime_assessment(
        period="month", month=params["month"], employee_name=employee["employeeName"]
    )
    return {
        "employeeId": employee["employeeId"],
        "employeeName": employee["employeeName"],
        "period": result["meta"]["period_label"],
        "baselineHours": result["meta"]["baseline_hours"],
        "assessment": result["data"][0] if result["data"] else None,
    }


def _money_sum(values: Any) -> str:
    return f"{sum((Decimal(v) for v in values), Decimal('0')).quantize(Decimal('0.01'))}"


@RULES.function("portfolioSummary")
def fn_portfolio_summary(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    filters = {key: params.get(param) for key, param in (
        ("contract_type", "contractType"), ("lifecycle_status", "lifecycleStatus"), ("risk_level", "riskLevel"))}
    projects = [
        p for p in ctx.project_facts()
        if all(value is None or p[key] == value for key, value in filters.items())
    ]

    def count_by(key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for project in projects:
            counts[project[key]] = counts.get(project[key], 0) + 1
        return counts

    return {
        "total_projects": len(projects),
        "contract_value": _money_sum(p["contract_value"] for p in projects),
        "received_amount": _money_sum(p["received_amount"] for p in projects),
        "outstanding_receivable_amount": _money_sum(p["outstanding_receivable_amount"] or "0" for p in projects),
        "critical_projects": sum(p["risk_level"] == "critical" for p in projects),
        "warning_projects": sum(p["risk_level"] == "warning" for p in projects),
        "healthy_projects": sum(p["risk_level"] == "healthy" for p in projects),
        "by_contract_type": count_by("contract_type"),
        "by_lifecycle_status": count_by("lifecycle_status"),
        "incomplete_maintenance": sum(not p["maintenance_complete"] for p in projects),
    }


@RULES.function("customerAnnualSummary")
def fn_customer_annual_summary(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    wanted = int(params["year"]) if params.get("year") not in (None, "") else None
    years: dict[int, dict[str, dict[str, Any]]] = {}
    undated = 0
    for record in ctx.crm_records():
        if record["data_type"] != "contract_order":
            continue
        year = _signed_year(record)
        if year is None:
            undated += 1
            continue
        if wanted is not None and year != wanted:
            continue
        name = (record.get("customer") or "").strip() or "未填写客户"
        row = years.setdefault(year, {}).setdefault(name, {
            "customer_name": name, "contract_count": 0, "projects": set(),
            "signed_amount": Decimal("0"), "invoiced_amount": Decimal("0"), "received_amount": Decimal("0"),
        })
        row["contract_count"] += 1
        if project_number := _record_project_number(record):
            row["projects"].add(project_number)
        for field, key in (("total_amount", "signed_amount"), ("invoiced_amount", "invoiced_amount"),
                           ("received_amount", "received_amount")):
            if amount := _money(record.get(field)):
                row[key] += Decimal(amount)

    def totals(rows: list[dict[str, Any]], key: str) -> str:
        return _money_sum(row[key] for row in rows)

    result = []
    for year in sorted(years, reverse=True):
        rows = sorted(years[year].values(), key=lambda r: (-r["signed_amount"], r["customer_name"]))
        projects = set().union(*(row["projects"] for row in rows))
        customers = [
            {**{k: v for k, v in row.items() if k != "projects"}, "project_count": len(row["projects"]),
             **{k: _quantize(row[k]) for k in ("signed_amount", "invoiced_amount", "received_amount")}}
            for row in rows
        ]
        result.append({
            "year": year, "customer_count": len(rows), "project_count": len(projects),
            "contract_count": sum(row["contract_count"] for row in rows),
            "signed_amount": totals(rows, "signed_amount"), "invoiced_amount": totals(rows, "invoiced_amount"),
            "received_amount": totals(rows, "received_amount"), "customers": customers,
        })
    return _redact_payload(ctx, {
        "year": wanted, "current_year": ctx.projects.today().year,
        "years": result, "undated_contract_count": undated,
    })


@RULES.function("workingCalendar")
def fn_working_calendar(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    summary = ctx.projects.workday_summary(params["startDate"], params["endDate"])
    hours_per_day = Decimal(ctx.projects.get_worktime_baseline()["standard_hours_per_day"])
    return {**summary, "baseline_hours": f"{(Decimal(summary['working_days']) * hours_per_day).quantize(Decimal('0.01'))}"}


@RULES.function("costObjectInvestment")
def fn_cost_object_investment(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    item = _cost_object_or_error(ctx, params["costObject"])
    settings = _investment(ctx).get_settings()
    rate = Decimal(settings["standard_person_day_cost"]) if settings["standard_person_day_cost"] and _finance_visible(ctx) else None
    return {
        **{k: v for k, v in item.items() if k != "contract_value" or _finance_visible(ctx)},
        "estimated_cost": _quantize(Decimal(item["total_person_days"]) * rate) if rate is not None else None,
    }


@RULES.function("portfolioInvestmentAnalysis")
def fn_portfolio_investment(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    return _investment(ctx).analysis(
        start_month=params.get("startMonth"), end_month=params.get("endMonth"), include_cost=_finance_visible(ctx)
    )


@RULES.function("resourceHealth")
def fn_resource_health(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    return _department(ctx).resource_health(params.get("month"))


@RULES.function("departmentOperatingReview")
def fn_department_review(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    year = params.get("year")
    review = _department(ctx).department_review(int(year) if year else None, params.get("month"))
    if _finance_visible(ctx):
        return review
    money_kpis = {"new_contracts", "collections"}
    plan_money = {"new_contract_target", "collection_target"}
    return {
        **review,
        "plan": {k: (None if k in plan_money else v) for k, v in review["plan"].items()} if review["plan"] else None,
        "kpis": {k: ({**v, "actual": None, "target": None} if k in money_kpis else v) for k, v in review["kpis"].items()},
    }


# ------------------------------------------------------------- page functions (ontology-first, 3.1.0)
FINANCE_PAYLOAD_KEYS = frozenset({
    "contract_value", "received_amount", "crm_received_amount", "invoiced_amount", "outstanding_receivable_amount",
    "recognized_revenue_amount", "total_amount", "contract_person_day_rate",
})


def _can_read(ctx: OntologyContext, marking: str) -> bool:
    definition = ctx.ontology.marking(marking)
    return definition is not None and ctx.principal.holds_any(definition.permissions)


def _mask_finance(value: Any) -> Any:
    """Amounts in a project payload, at any depth: amounts become null, amount-bearing texts are reworded."""
    if isinstance(value, list):
        return [_mask_finance(item) for item in value]
    if not isinstance(value, dict):
        return value
    masked = {}
    for key, item in value.items():
        if key in FINANCE_PAYLOAD_KEYS or key.endswith("_amount") or key == "amount":
            masked[key] = None
        elif key == "anomaly_alerts" and isinstance(item, list):
            masked[key] = [
                {**alert, "detail": "回款尚未确认到账（金额已脱敏）"} if alert.get("code") == "PAYMENT_DUE" else alert
                for alert in item
            ]
        else:
            masked[key] = _mask_finance(item)
    return masked


def _mask_personnel(value: Any) -> Any:
    """Per-person hours inside a project payload; project totals stay visible."""
    if not isinstance(value, dict):
        return value
    masked = dict(value)
    if "monthly_time_entries" in masked:
        masked["monthly_time_entries"] = [
            {**month, "entries": [{**entry, **{k: None for k in entry if "hours" in k or "person_days" in k}} for entry in month["entries"]]}
            for month in masked["monthly_time_entries"]
        ]
    if "pmo_resource_rows" in masked:
        masked["pmo_resource_rows"] = [
            {key: (None if "hours" in key or "person_days" in key or key.endswith("_percent") else item) for key, item in row.items()}
            for row in masked["pmo_resource_rows"]
        ]
    return masked


def _redact_payload(ctx: OntologyContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the FINANCE and PERSONNEL markings to a page payload, the same way object properties are redacted."""
    redacted = []
    if not _can_read(ctx, "FINANCE"):
        payload, redacted = _mask_finance(payload), redacted + ["FINANCE"]
    if not _can_read(ctx, "PERSONNEL"):
        payload, redacted = _mask_personnel(payload), redacted + ["PERSONNEL"]
    return {**payload, "redacted_markings": redacted}


def _public_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in facts.items() if not key.startswith("__")}


@RULES.function("projectList")
def fn_project_list(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    result = project_list_view(
        [_public_facts(p) for p in ctx.project_facts()], ctx.projects._crm_search_terms_by_project,
        contract_type=params.get("contractType"), lifecycle_status=params.get("lifecycleStatus"),
        category=params.get("category"), risk_level=params.get("riskLevel"), query=params.get("query"),
        cursor=params.get("cursor"), page_size=int(params.get("pageSize") or 50),
    )
    return _redact_payload(ctx, result)


@RULES.function("portfolioHealth")
def fn_portfolio_health(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    facts = ctx.project_facts()
    maintenance = params.get("maintenance")
    if maintenance:
        facts = [p for p in facts if p["maintenance_complete"] == (maintenance == "complete")]
    result = project_health_view(
        facts, ctx.projects._crm_search_terms_by_project, ctx.projects.get_worktime_baseline(), ctx.projects._now(),
        month=params.get("month"), start_month=params.get("startMonth"), end_month=params.get("endMonth"),
        project_number=params.get("projectNumber"), lifecycle_status=params.get("lifecycleStatus"),
        contract_type=params.get("contractType"), risk_level=params.get("riskLevel"),
        master_category=params.get("masterCategory"), category=params.get("category"), query=params.get("query"),
        cursor=params.get("cursor"), page_size=int(params.get("pageSize") or 50),
    )
    result["summary"]["maintenance_pending_projects"] = sum(not p["maintenance_complete"] for p in ctx.project_facts())
    return _redact_payload(ctx, result)


@RULES.function("projectOverview")
def fn_project_overview(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    return _redact_payload(ctx, _public_facts(_project_detail(ctx, params["project"])))


@RULES.function("maintenanceWorklist")
def fn_maintenance_worklist(ctx: OntologyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Objects that still need maintenance, per declared requirement (every object type that declares any)."""
    requirement = params.get("requirement")
    only_pending = params.get("onlyPending", True)
    types = [t for t in ctx.ontology.object_types if t.maintenance_requirements
             and (not params.get("objectType") or t.api_name == params["objectType"])]
    summary, rows = [], []
    for definition in types:
        if not ctx.principal.holds_any(definition.read_permissions):
            continue
        objects = ctx.objects(definition.api_name)
        for req in definition.maintenance_requirements:
            applicable = [o for o in objects if any(e["requirement"] == req.api_name for e in o["maintenanceChecklist"])]
            missing = [o for o in applicable if any(e["requirement"] == req.api_name and not e["met"] for e in o["maintenanceChecklist"])]
            summary.append({"objectType": definition.api_name, "requirement": req.api_name, "label": req.display_name,
                            "action": req.action, "applicable": len(applicable), "missing": len(missing),
                            "maintained": len(applicable) - len(missing)})
        for obj in objects:
            gaps = [e for e in obj["maintenanceChecklist"] if not e["met"] and (not requirement or e["requirement"] == requirement)]
            if only_pending and not gaps:
                continue
            rows.append({"objectType": definition.api_name, "primaryKey": str(obj[definition.primary_key]),
                         "title": obj.get(definition.title_property), "checklist": obj["maintenanceChecklist"],
                         "pending": [e["label"] for e in gaps]})
    rows.sort(key=lambda r: (-len(r["pending"]), r["objectType"], r["primaryKey"]))
    return {"summary": summary, "data": rows}

