"""Project derivations: every project fact that is computed rather than maintained.

Base programs (``ProjectRepository``) keep master data and maintained fields and hand over raw per-project
bundles (``project_sources``).  Everything here is derived from those bundles and the operating policy, and is
registered with the ontology (``rules.py``: derivation ``projectFacts`` and the project functions).  The code
moved here unchanged from the repository in ontology 3.1.0, so results are identical to the former REST output.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Any

from ...project_repository import (
    CAPPED_TYPES,
    LIFECYCLE_STATUS_ORDER,
    _contracts_are_source_closed,
    _counts_as_effort,
    _signed_year,
    _cursor_page,
    _cursor_scope,
    _format_decimal,
    _format_percent,
    _month_range,
    _overtime_entry_hours,
    _plan_months,
    _previous_month,
    _shift_month,
    _total_entry_hours,
    _validate_month,
)


def project_base(bundle: dict[str, Any]) -> dict[str, Any]:
    """Master data of one project as imported and maintained: CRM identity and sums, PM-maintained settings.

    The same values appear in the facts of ``derive_project``; ``test_ontology_first`` keeps the two in step.
    """
    crm_records = bundle["crm_records"]
    settings = bundle["settings"]
    contracts = [r for r in crm_records if r["data_type"] == "contract_order"]
    opportunities = [r for r in crm_records if r["data_type"] == "sales_opportunity"]
    primary = (contracts or opportunities or [{}])[0]
    source_categories = [
        str(record["category"]).strip()
        for record in (contracts or opportunities)
        if record.get("category") and str(record["category"]).strip()
    ]
    planned = settings.get("planned_person_days")
    return {
        "project_number": bundle["project_number"],
        "subject": primary.get("subject") or bundle["project_number"],
        "customer": primary.get("customer") or ("未匹配 CRM 主数据" if not primary else None),
        "owner": primary.get("owner"),
        "category": "、".join(dict.fromkeys(source_categories)) or None,
        "contract_value": _format_decimal(_sum_money(contracts, "total_amount")),
        "invoiced_amount": _format_decimal(_sum_money(contracts, "invoiced_amount")),
        "crm_received_amount": _format_decimal(_sum_money(contracts, "received_amount")),
        "contract_type": settings.get("contract_type", "unknown"),
        "lifecycle_status": settings.get("lifecycle_status") or _source_lifecycle_status(contracts) or "planned",
        "planned_person_days": _format_decimal(Decimal(planned)) if planned else None,
        "settings": settings,
    }


def derive_project(
    project_number: str,
    crm_records: list[dict[str, Any]],
    settings: dict[str, Any],
    time_entries: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    payment_terms: list[dict[str, Any]],
    interventions: list[dict[str, Any]],
    include_details: bool,
    thresholds: dict[str, Decimal],
    policy: dict[str, Any],
    as_of: date,
    maintenance_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """``maintenance_reasons`` are the labels of the unmet maintenance requirements declared in the
    metadata (derivation ``maintenanceStatus``); the facts only report them."""
    maintenance_reasons = list(maintenance_reasons or [])
    contracts = [r for r in crm_records if r["data_type"] == "contract_order"]
    opportunities = [r for r in crm_records if r["data_type"] == "sales_opportunity"]
    primary = (contracts or opportunities or [{}])[0]
    hours_per_day = Decimal(settings.get("hours_per_person_day") or "8")
    reported_hours = sum(
        (_total_entry_hours(row) for row in time_entries if _counts_as_effort(row)),
        Decimal("0"),
    )
    customer_confirmed_hours = sum(
        (Decimal(row["approved_hours"]) for row in time_entries if row["approval_status"] == "approved"),
        Decimal("0"),
    )
    reported_days = reported_hours / hours_per_day if hours_per_day else Decimal("0")
    customer_confirmed_days = (
        customer_confirmed_hours / hours_per_day if hours_per_day else Decimal("0")
    )
    confirmation_gap_hours = max(
        reported_hours - customer_confirmed_hours, Decimal("0")
    )
    confirmation_gap_days = (
        confirmation_gap_hours / hours_per_day if hours_per_day else Decimal("0")
    )
    planned_days = (
        Decimal(settings["planned_person_days"])
        if settings.get("planned_person_days")
        else None
    )
    budget_hours = (
        planned_days * hours_per_day if planned_days is not None else None
    )
    utilization = (
        reported_days / planned_days * 100 if planned_days and planned_days > 0 else None
    )
    contract_value = _sum_money(contracts, "total_amount")
    contract_received_amount = _sum_money(contracts, "received_amount")
    invoiced_amount = _sum_money(contracts, "invoiced_amount")
    today_date = date.today()
    today = today_date.isoformat()
    overdue_milestones = sum(
        row["planned_date"] < today and row["status"] != "completed"
        for row in milestones
    )
    overdue_payments = sum(
        row["planned_date"] < today and row["status"] != "paid" for row in payments
    )
    # D32/D33: an open intervention past its due date is a to-do reminder, not a risk reason.
    overdue_interventions = [
        row for row in interventions
        if row["status"] != "closed" and row.get("due_date") and row["due_date"] < today
    ]
    contract_type = settings.get("contract_type", "unknown")
    # 开口合同按已确认工作量结算，CRM 的“总金额”只是框架额，不能作为
    # 回款完成率的分母。历史数据未被人工标为开口合同，但开票或回款已
    # 超过框架额时，同样按工作量结算处理，避免把金额钳制成 100%。
    workload_settlement = (
        contract_type == "open_ended"
        or invoiced_amount > contract_value
        or contract_received_amount > contract_value
    )
    recognized_revenue_amount = invoiced_amount
    receivable_base = (
        recognized_revenue_amount if workload_settlement else contract_value
    )
    outstanding_receivable_amount = max(
        receivable_base - contract_received_amount, Decimal("0")
    )
    # Project lifecycle is an optional PM-maintained override.  For a
    # newly imported project there is no settings row yet, so using the
    # database default (``planned``) would hide an explicit terminal CRM
    # status such as "结束".  In that case, derive the initial lifecycle
    # from the contract source instead.
    source_lifecycle = _source_lifecycle_status(contracts)
    lifecycle = settings.get("lifecycle_status") or source_lifecycle or "planned"
    lifecycle_source = (
        "manual" if settings.get("lifecycle_status")
        else "contract_status" if source_lifecycle else "default"
    )
    risk_reasons: list[str] = []
    critical = False
    budget_attention = thresholds["budget_attention_percent"]
    budget_monitored = utilization is not None and not workload_settlement
    if budget_monitored and utilization > 100:
        critical = True
        risk_reasons.append("实际人天已超过预算")
    elif budget_monitored and utilization >= budget_attention and lifecycle not in {"completed", "closed"}:
        risk_reasons.append(f"人天预算使用率已达到 {_format_percent(budget_attention)}")
    if overdue_milestones:
        critical = True
        risk_reasons.append(f"{overdue_milestones} 个里程碑逾期")
    if overdue_payments:
        critical = True
        risk_reasons.append(f"{overdue_payments} 个回款节点逾期")
    if contract_type == "unknown":
        risk_reasons.append("合同类型尚未设置")
    if contract_type in CAPPED_TYPES and planned_days is None:
        risk_reasons.append("预算人天尚未设置")
    if not milestones and lifecycle in {"planned", "active"}:
        risk_reasons.append("尚未设置里程碑")

    payment_by_id = {row["payment_node_id"]: row for row in payments}
    enriched_milestones: list[dict[str, Any]] = []
    todo_events: list[dict[str, Any]] = []
    for row in milestones:
        item = dict(row)
        item["planned_start"] = item.get("planned_start") or item["planned_date"]
        item["planned_end"] = item.get("planned_end") or item["planned_date"]
        linked_payment = payment_by_id.get(item.get("payment_node_id"))
        item["payment_term_id"] = (
            linked_payment.get("payment_term_id") if linked_payment else None
        )
        item["payment_date"] = linked_payment["planned_date"] if linked_payment else None
        item["payment_amount"] = linked_payment["planned_amount"] if linked_payment else None
        item["payment_status"] = linked_payment["status"] if linked_payment else None
        item["deadline"] = _deadline(item["planned_end"], item["status"] == "completed", today_date)
        enriched_milestones.append(item)
        todo_events.append(
            _todo_event(
                "milestone", item["milestone_id"], item["name"],
                item["planned_end"], item["status"] == "completed", today_date,
                deliverable=item.get("deliverable"),
                completed_at=item.get("completed_date"),
                confirmed_by=item.get("confirmed_by"),
            )
        )

    for row in payments:
        todo_events.append(
            _todo_event(
                "payment", row["payment_node_id"], row["name"],
                row["planned_date"], row["status"] == "paid", today_date,
                amount=row["planned_amount"],
                completed_at=row.get("actual_date"),
            )
        )
    todo_events.sort(
        key=lambda event: (event["completed"], event["due_date"], event["event_type"])
    )
    current_milestone = _current_milestone(enriched_milestones, today_date)
    payment_summary = _payment_summary(payments, today_date)
    # The overview must lead with the payment term attached to the next open
    # milestone.  Maintenance time is only a fallback for terms that are not
    # part of the upcoming delivery plan; otherwise a later-edited future term
    # can incorrectly replace the next payment commitment in the list.
    milestone_by_id = {
        row["milestone_id"]: row for row in enriched_milestones
    }
    upcoming_payment_terms: list[tuple[str, dict[str, Any]]] = []
    remaining_payment_terms: list[dict[str, Any]] = []
    for term in payment_terms:
        linked_milestone = milestone_by_id.get(term.get("linked_milestone_id"))
        linked_payment = payment_by_id.get(term.get("payment_node_id"))
        planned_date = (
            linked_payment.get("planned_date") if linked_payment
            else linked_milestone.get("planned_end") if linked_milestone
            else None
        )
        is_open = (
            linked_milestone is not None
            and linked_milestone["status"] != "completed"
            and (linked_payment is None or linked_payment["status"] != "paid")
            and planned_date is not None
        )
        if is_open:
            upcoming_payment_terms.append((str(planned_date), term))
        else:
            remaining_payment_terms.append(term)

    ordered_payment_terms = [
        term for _, term in sorted(
            upcoming_payment_terms,
            key=lambda item: (item[0], str(item[1].get("payment_term_id") or "")),
        )
    ] + sorted(
        remaining_payment_terms,
        key=lambda term: (
            str(term.get("updated_at") or ""),
            str(term.get("created_at") or ""),
            str(term.get("payment_term_id") or ""),
        ),
        reverse=True,
    )
    payment_term_summary = [
        {
            "name": term["name"],
            "payment_ratio": term.get("payment_ratio"),
            "planned_amount": term["planned_amount"],
            "linked_milestone_id": term.get("linked_milestone_id"),
            "payment_node_id": term.get("payment_node_id"),
            "node_status": payment_by_id.get(term.get("payment_node_id"), {}).get("status"),
            "planned_date": (
                payment_by_id.get(term.get("payment_node_id"), {}).get("planned_date")
                or milestone_by_id.get(term.get("linked_milestone_id"), {}).get("planned_end")
            ),
        }
        for term in ordered_payment_terms
    ]
    received_amount = Decimal(payment_summary["actual_amount"])
    successful_opportunities = [
        record for record in opportunities if record.get("status") == "成功"
    ]
    # “阶段”是销售过程的描述，不作为项目转换依据；CRM 明确状态为
    # “成功”即表示商机已签约并进入项目管理范围。
    master_category = (
        "项目"
        if contracts or successful_opportunities
        else "商机" if opportunities else "项目"
    )
    # “类别” is a CRM business field, not the internal project/opportunity
    # classification above.  A project can aggregate several contract rows;
    # retain every distinct imported category in a predictable order so the
    # overview remains traceable to the source CSV's “分类” column.
    source_categories = [
        str(record["category"]).strip()
        for record in (contracts or opportunities)
        if record.get("category") and str(record["category"]).strip()
    ]
    category = "、".join(dict.fromkeys(source_categories)) or None
    monthly_time_entries = _monthly_time_entries(time_entries, hours_per_day)
    pmo_resource_summary, pmo_resource_rows = _pmo_resource_rows(
        [row for row in time_entries if _counts_as_effort(row)], hours_per_day
    )
    if confirmation_gap_hours > 0:
        risk_reasons.append(
            f"已填报工时与客户确认工时相差 {_format_decimal(confirmation_gap_days)} 人天"
        )
    anomaly_alerts: list[dict[str, Any]] = []
    if budget_monitored and utilization > 100:
        anomaly_alerts.append(
            {
                "code": "BUDGET_OVERRUN",
                "severity": "critical",
                "title": "工时已超出初始预算",
                "detail": f"已填报 {_format_decimal(reported_days)} 人天，预算 {_format_decimal(planned_days)} 人天",
            }
        )
    elif budget_monitored and utilization >= budget_attention:
        anomaly_alerts.append(
            {
                "code": "BUDGET_NEAR_LIMIT",
                "severity": "warning",
                "title": "工时预算接近上限",
                "detail": f"当前已使用 {round(float(utilization), 1)}%",
            }
        )
    if confirmation_gap_hours > 0:
        anomaly_alerts.append(
            {
                "code": "TIME_CONFIRMATION_GAP",
                "severity": "warning",
                "title": "存在客户未确认工时",
                "detail": (
                    f"已填报 {_format_decimal(reported_days)} 人天，客户确认 "
                    f"{_format_decimal(customer_confirmed_days)} 人天，缺口 "
                    f"{_format_decimal(confirmation_gap_days)} 人天"
                ),
            }
        )
    for event in todo_events:
        if event["completed"] or event["urgency"] not in {
            "overdue", "due_today", "due_soon"
        }:
            continue
        anomaly_alerts.append(
            {
                "code": "MILESTONE_DUE" if event["event_type"] == "milestone" else "PAYMENT_DUE",
                "severity": "critical" if event["urgency"] == "overdue" else "warning",
                "title": f"{event['name']} · {event['label']}",
                "detail": (
                    "里程碑尚未确认完成"
                    if event["event_type"] == "milestone"
                    else f"回款尚未确认到账，计划金额 {_format_decimal(Decimal(event['amount'] or '0'))} 元"
                ),
            }
        )
    for row in overdue_interventions:
        anomaly_alerts.append(
            {
                "code": "INTERVENTION_OVERDUE",
                "severity": "warning",
                "title": f"管理干预逾期 · {row['title']}",
                "detail": f"截止 {row['due_date']}，负责人 {row.get('owner') or '未指定'}，尚未关闭",
            }
        )
    if maintenance_reasons:
        anomaly_alerts.append(
            {
                "code": "MANUAL_MAINTENANCE_REQUIRED",
                "severity": "warning",
                "title": "同步后人工维护尚未完成",
                "detail": "、".join(maintenance_reasons),
            }
        )
    risk_level = "critical" if critical else "warning" if risk_reasons else "healthy"

    project = {
        "project_number": project_number,
        "subject": primary.get("subject") or project_number,
        "customer": primary.get("customer") or (
            "未匹配 CRM 主数据" if not primary else None
        ),
        # CRM “所有者 / 我方签约人” is the salesperson, not the delivery PM.
        # Keep both responsibilities independently available to every view.
        "sales_owner": primary.get("owner"),
        "owner": primary.get("owner"),
        "project_manager": settings.get("project_manager"),
        "master_category": master_category,
        "category": category,
        "contract_type": contract_type,
        "lifecycle_status": lifecycle,
        "lifecycle_source": lifecycle_source,
        "status": lifecycle,
        "planned_start": settings.get("planned_start"),
        "planned_end": settings.get("planned_end"),
        "contract_count": len(contracts),
        "signed_year": min((year for year in map(_signed_year, contracts) if year), default=None),
        "opportunity_count": len(opportunities),
        "contract_value": _format_decimal(contract_value),
        "crm_received_amount": _format_decimal(contract_received_amount),
        "revenue_settlement_basis": "workload" if workload_settlement else "contract",
        "recognized_revenue_amount": _format_decimal(recognized_revenue_amount),
        "outstanding_receivable_amount": _format_decimal(outstanding_receivable_amount),
        "received_amount": _format_decimal(received_amount),
        "invoiced_amount": _format_decimal(invoiced_amount),
        "planned_person_days": _format_decimal(planned_days) if planned_days is not None else None,
        "budget_hours": _format_decimal(budget_hours) if budget_hours is not None else None,
        "reported_hours": _format_decimal(reported_hours),
        "reported_person_days": _format_decimal(reported_days),
        "customer_confirmed_hours": _format_decimal(customer_confirmed_hours),
        "customer_confirmed_person_days": _format_decimal(customer_confirmed_days),
        "confirmation_gap_hours": _format_decimal(confirmation_gap_hours),
        "confirmation_gap_person_days": _format_decimal(confirmation_gap_days),
        "actual_hours": _format_decimal(reported_hours),
        "actual_person_days": _format_decimal(reported_days),
        "budget_utilization": round(float(utilization), 1) if utilization is not None else None,
        "billable_person_days": _format_decimal(customer_confirmed_days) if contract_type == "open_ended" else None,
        "milestone_total": len(milestones),
        "milestone_completed": sum(row["status"] == "completed" for row in milestones),
        "overdue_milestones": overdue_milestones,
        "current_milestone": current_milestone,
        "payment_node_total": len(payments),
        "payment_term_count": len(payment_terms),
        "unlinked_payment_term_count": sum(not term.get("linked_milestone_id") for term in payment_terms),
        "overdue_payment_nodes": overdue_payments,
        "payment_summary": payment_summary,
        "payment_term_summary": payment_term_summary,
        "open_interventions": sum(row["status"] != "closed" for row in interventions),
        "overdue_interventions": len(overdue_interventions),
        "maintenance_complete": not maintenance_reasons,
        "maintenance_reasons": maintenance_reasons,
        "pending_todos": sum(not event["completed"] for event in todo_events),
        "overdue_todos": sum(
            event["urgency"] == "overdue" for event in todo_events
        ),
        "next_todo": next(
            (event for event in todo_events if not event["completed"]), None
        ),
        "anomaly_count": len(anomaly_alerts),
        "anomaly_alerts": anomaly_alerts,
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
    }
    if include_details:
        project.update(
            {
                "crm_records": crm_records,
                "milestones": enriched_milestones,
                "payment_nodes": payments,
                "payment_terms": payment_terms,
                "interventions": [
                    {**row, "overdue": any(row is late for late in overdue_interventions)} for row in interventions
                ],
                "settings": settings,
                "todo_events": todo_events,
                "monthly_time_entries": monthly_time_entries,
                "pmo_resource_summary": pmo_resource_summary,
                "pmo_resource_rows": pmo_resource_rows,
            }
        )
    return project


def _sum_money(records: list[dict[str, Any]], field: str) -> Decimal:
    return sum(
        (Decimal(record[field]["amount"]) for record in records if record.get(field)),
        Decimal("0"),
    )


def _available_lifecycle_statuses(projects: list[dict[str, Any]]) -> list[str]:
    """Return only lifecycle values currently represented by project data."""
    present_statuses = {project.get("lifecycle_status") for project in projects}
    return [status for status in LIFECYCLE_STATUS_ORDER if status in present_statuses]


def _project_category_values(category: Any) -> list[str]:
    """Split a project's CRM categories while preserving the import values."""
    return [
        value.strip()
        for value in str(category or "").split("、")
        if value.strip()
    ]


def _available_project_categories(projects: list[dict[str, Any]]) -> list[str]:
    """Return distinct CRM ``分类`` values for the overview filter."""
    return sorted(
        {
            category
            for project in projects
            for category in _project_category_values(project.get("category"))
        },
        key=str.casefold,
    )


def _source_lifecycle_status(contracts: list[dict[str, Any]]) -> str | None:
    """Map a consistent imported contract state to the project lifecycle.

    The imported ``状态`` remains the source-of-truth audit field on the CRM
    record.  This mapping only supplies the initial project lifecycle when PM
    has not created a manual project-settings record.  Mixed contract states
    deliberately resolve to an active lifecycle whenever one linked contract
    is still active, so a completed historical order cannot close an ongoing
    renewal project.
    """
    statuses = {
        str(contract.get("status") or "").strip()
        for contract in contracts
        if str(contract.get("status") or "").strip()
    }
    if not statuses:
        return None

    closed_statuses = {"关闭", "已关闭", "终止", "已终止"}
    completed_statuses = {"结束", "已结束", "完成", "已完成"}
    active_statuses = {"执行中", "进行中", "实施中", "履约中", "在执行"}
    paused_statuses = {"暂停", "已暂停", "挂起"}
    planned_statuses = {"计划中", "待执行", "未开始", "待启动"}

    if statuses <= closed_statuses:
        return "closed"
    if statuses <= completed_statuses | closed_statuses:
        return "completed"
    if statuses & active_statuses:
        return "active"
    if statuses <= paused_statuses:
        return "on_hold"
    if statuses <= planned_statuses:
        return "planned"
    return None


def _decimal_sum(values: Any) -> str:
    return _format_decimal(sum((Decimal(value) for value in values), Decimal("0")))


def _risk_rank(value: str) -> int:
    return {"critical": 0, "warning": 1, "healthy": 2}.get(value, 3)


def _deadline(due_date: str, completed: bool, today: date) -> dict[str, Any]:
    days = (date.fromisoformat(due_date) - today).days
    if completed:
        urgency = "completed"
        label = "已确认完成"
    elif days < 0:
        urgency = "overdue"
        label = f"已延期 {abs(days)} 天"
    elif days == 0:
        urgency = "due_today"
        label = "今天到期"
    elif days <= 7:
        urgency = "due_soon"
        label = f"还有 {days} 天"
    else:
        urgency = "upcoming"
        label = f"还有 {days} 天"
    return {"days_remaining": days, "urgency": urgency, "label": label}


def _todo_event(
    event_type: str,
    event_id: str,
    name: str,
    due_date: str,
    completed: bool,
    today: date,
    *,
    amount: str | None = None,
    deliverable: str | None = None,
    completed_at: str | None = None,
    confirmed_by: str | None = None,
) -> dict[str, Any]:
    deadline = _deadline(due_date, completed, today)
    completion_state = None
    completion_label = None
    delay_days = 0
    if completed:
        if completed_at:
            delay_days = max((date.fromisoformat(completed_at) - date.fromisoformat(due_date)).days, 0)
            completion_state = "delayed" if delay_days else "on_time"
            completion_label = (
                f"延期已完成 · 晚 {delay_days} 天"
                if delay_days
                else "按期完成"
            )
        else:
            completion_state = "unknown"
            completion_label = "已完成（未记录完成日期）"
        deadline["label"] = completion_label
    return {
        "event_type": event_type,
        "event_id": event_id,
        "name": name,
        "due_date": due_date,
        "amount": amount,
        "deliverable": deliverable,
        "completed": completed,
        "completed_at": completed_at,
        "confirmed_by": confirmed_by,
        "completion_state": completion_state,
        "completion_label": completion_label,
        "delay_days": delay_days,
        **deadline,
    }


def _current_milestone(
    milestones: list[dict[str, Any]], today: date
) -> dict[str, Any] | None:
    if not milestones:
        return None
    open_items = [item for item in milestones if item["status"] != "completed"]
    if open_items:
        item = min(open_items, key=lambda row: row["planned_end"])
        return {
            "milestone_id": item["milestone_id"],
            "name": item["name"],
            "status": item["status"],
            "planned_start": item.get("planned_start"),
            "planned_end": item.get("planned_end"),
            "deadline": _deadline(item["planned_end"], False, today),
        }
    item = max(milestones, key=lambda row: row["planned_end"])
    return {
        "milestone_id": item["milestone_id"],
        "name": item["name"],
        "status": "completed",
        "planned_start": item.get("planned_start"),
        "planned_end": item.get("planned_end"),
        "deadline": _deadline(item["planned_end"], True, today),
    }


def _payment_summary(
    payments: list[dict[str, Any]], today: date
) -> dict[str, Any]:
    paid = [row for row in payments if row["status"] == "paid"]
    open_items = [row for row in payments if row["status"] != "paid"]
    next_payment = min(open_items, key=lambda row: row["planned_date"]) if open_items else None
    planned_amount = sum(
        (Decimal(row["planned_amount"]) for row in payments),
        Decimal("0"),
    )
    actual_amount = sum(
        (Decimal(row["actual_amount"] or "0") for row in payments),
        Decimal("0"),
    )
    return {
        "total_nodes": len(payments),
        "paid_nodes": len(paid),
        "planned_amount": _format_decimal(planned_amount),
        "actual_amount": _format_decimal(actual_amount),
        "next_node": (
            {
                "payment_node_id": next_payment["payment_node_id"],
                "name": next_payment["name"],
                "planned_date": next_payment["planned_date"],
                "planned_amount": next_payment["planned_amount"],
                "status": next_payment["status"],
                "deadline": _deadline(next_payment["planned_date"], False, today),
            }
            if next_payment else None
        ),
    }


def _monthly_time_entries(
    time_entries: list[dict[str, Any]], hours_per_day: Decimal
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in time_entries:
        month = row["work_date"][:7]
        item = grouped.setdefault(
            month,
            {
                "month": month,
                "reported_hours": Decimal("0"),
                "overtime_hours": Decimal("0"),
                "approved_hours": Decimal("0"),
                "excluded_hours": Decimal("0"),
                "excluded_entry_count": 0,
                "entry_count": 0,
                "entries": [],
            },
        )
        item["entry_count"] += 1
        item["entries"].append(row)
        if not _counts_as_effort(row):
            item["excluded_hours"] += _total_entry_hours(row)
            item["excluded_entry_count"] += 1
            continue
        overtime = _overtime_entry_hours(row)
        hours = Decimal(row["hours"]) + overtime
        approved = (
            Decimal(row["approved_hours"])
            if row["approval_status"] == "approved"
            else Decimal("0")
        )
        item["reported_hours"] += hours
        item["overtime_hours"] += overtime
        item["approved_hours"] += approved

    months: list[dict[str, Any]] = []
    for item in grouped.values():
        gap = max(item["reported_hours"] - item["approved_hours"], Decimal("0"))
        reported_days = item["reported_hours"] / hours_per_day if hours_per_day else Decimal("0")
        approved_days = item["approved_hours"] / hours_per_day if hours_per_day else Decimal("0")
        gap_days = gap / hours_per_day if hours_per_day else Decimal("0")
        months.append(
            {
                "month": item["month"],
                "entry_count": item["entry_count"],
                "reported_hours": _format_decimal(item["reported_hours"]),
                "overtime_hours": _format_decimal(item["overtime_hours"]),
                "approved_hours": _format_decimal(item["approved_hours"]),
                "confirmation_gap_hours": _format_decimal(gap),
                "reported_person_days": _format_decimal(reported_days),
                "overtime_person_days": _format_decimal(
                    item["overtime_hours"] / hours_per_day
                    if hours_per_day else Decimal("0")
                ),
                "approved_person_days": _format_decimal(approved_days),
                "confirmation_gap_person_days": _format_decimal(gap_days),
                "excluded_entry_count": item["excluded_entry_count"],
                "excluded_person_days": _format_decimal(
                    item["excluded_hours"] / hours_per_day if hours_per_day else Decimal("0")
                ),
                "entries": sorted(
                    item["entries"],
                    key=lambda row: (row["work_date"], row["external_id"]),
                    reverse=True,
                ),
            }
        )
    return sorted(months, key=lambda item: item["month"], reverse=True)


def _pmo_resource_rows(
    time_entries: list[dict[str, Any]], hours_per_day: Decimal
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build an auditable people-and-effort view for the project detail page.

    Currency cost is intentionally not inferred: the system has no employee rate
    source.  The usable, source-backed cost unit is person-days, with overtime
    disclosed separately rather than folded into an assumed money amount.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    total_normal_hours = Decimal("0")
    total_overtime_hours = Decimal("0")
    total_approved_hours = Decimal("0")

    for row in time_entries:
        employee_name = (row.get("employee_name") or "未命名员工").strip()
        employee_id = (row.get("employee_id") or "").strip()
        item = grouped.setdefault(
            (employee_id, employee_name),
            {
                "employee_name": employee_name,
                "employee_id": employee_id or None,
                "entry_count": 0,
                "normal_hours": Decimal("0"),
                "overtime_hours": Decimal("0"),
                "approved_hours": Decimal("0"),
                "work_dates": set(),
                "months": set(),
            },
        )
        normal_hours = Decimal(row["hours"])
        overtime_hours = _overtime_entry_hours(row)
        approved_hours = (
            Decimal(row["approved_hours"])
            if row["approval_status"] == "approved"
            else Decimal("0")
        )
        item["entry_count"] += 1
        item["normal_hours"] += normal_hours
        item["overtime_hours"] += overtime_hours
        item["approved_hours"] += approved_hours
        item["work_dates"].add(row["work_date"])
        item["months"].add(row["work_date"][:7])
        total_normal_hours += normal_hours
        total_overtime_hours += overtime_hours
        total_approved_hours += approved_hours

    total_reported_hours = total_normal_hours + total_overtime_hours
    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        reported_hours = item["normal_hours"] + item["overtime_hours"]
        confirmation_gap_hours = max(reported_hours - item["approved_hours"], Decimal("0"))
        reported_days = reported_hours / hours_per_day if hours_per_day else Decimal("0")
        month_count = len(item["months"])
        rows.append(
            {
                "employee_name": item["employee_name"],
                "employee_id": item["employee_id"],
                "entry_count": item["entry_count"],
                "active_day_count": len(item["work_dates"]),
                "active_month_count": month_count,
                "normal_person_days": _format_decimal(
                    item["normal_hours"] / hours_per_day if hours_per_day else Decimal("0")
                ),
                "overtime_person_days": _format_decimal(
                    item["overtime_hours"] / hours_per_day if hours_per_day else Decimal("0")
                ),
                "reported_person_days": _format_decimal(reported_days),
                "approved_person_days": _format_decimal(
                    item["approved_hours"] / hours_per_day if hours_per_day else Decimal("0")
                ),
                "confirmation_gap_person_days": _format_decimal(
                    confirmation_gap_hours / hours_per_day if hours_per_day else Decimal("0")
                ),
                "project_effort_share_percent": round(
                    float(reported_hours / total_reported_hours * 100), 1
                ) if total_reported_hours else 0.0,
                "overtime_ratio_percent": round(
                    float(item["overtime_hours"] / reported_hours * 100), 1
                ) if reported_hours else 0.0,
                "monthly_average_person_days": _format_decimal(
                    reported_days / Decimal(month_count) if month_count else Decimal("0")
                ),
            }
        )
    rows.sort(
        key=lambda row: (-Decimal(row["reported_person_days"]), row["employee_name"])
    )

    confirmed_gap_hours = max(total_reported_hours - total_approved_hours, Decimal("0"))
    summary = {
        "cost_basis": "人天",
        "monetary_cost_available": False,
        "participant_count": len(rows),
        "normal_person_days": _format_decimal(
            total_normal_hours / hours_per_day if hours_per_day else Decimal("0")
        ),
        "overtime_person_days": _format_decimal(
            total_overtime_hours / hours_per_day if hours_per_day else Decimal("0")
        ),
        "reported_person_days": _format_decimal(
            total_reported_hours / hours_per_day if hours_per_day else Decimal("0")
        ),
        "approved_person_days": _format_decimal(
            total_approved_hours / hours_per_day if hours_per_day else Decimal("0")
        ),
        "confirmation_gap_person_days": _format_decimal(
            confirmed_gap_hours / hours_per_day if hours_per_day else Decimal("0")
        ),
    }
    return summary, rows


def _health_period(
    month: str | None, start_month: str | None, end_month: str | None, now: str
) -> tuple[str, str]:
    if month:
        _validate_month(month)
        return month, month
    if start_month or end_month:
        start = start_month or end_month
        end = end_month or start_month
        assert start is not None and end is not None
        _validate_month(start)
        _validate_month(end)
        if start > end:
            raise ValueError("start_month must be earlier than or equal to end_month")
        return start, end
    current = now[:7]
    _validate_month(current)
    return current, current


def _project_health_row(
    project: dict[str, Any],
    start_month: str,
    end_month: str,
    thresholds: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or _budget_thresholds({})
    monthly = {item["month"]: item for item in project.get("monthly_time_entries", [])}
    period_months = _month_range(start_month, end_month)
    period_hours = sum(
        (Decimal(monthly[m]["reported_hours"]) for m in period_months if m in monthly),
        Decimal("0"),
    )
    period_days = sum(
        (Decimal(monthly[m]["reported_person_days"]) for m in period_months if m in monthly),
        Decimal("0"),
    )
    cumulative_days = sum(
        (
            Decimal(item["reported_person_days"])
            for item in monthly.values()
            if item["month"] <= end_month
        ),
        Decimal("0"),
    )
    previous_month = _previous_month(start_month)
    previous_days = (
        Decimal(monthly[previous_month]["reported_person_days"])
        if previous_month in monthly else Decimal("0")
    )
    # The trend compares against the immediately preceding period of the same
    # length, so a three-month range is not judged against a single month.
    previous_period_months = [
        _shift_month(month, -len(period_months)) for month in period_months
    ]
    previous_period_days = sum(
        (Decimal(monthly[m]["reported_person_days"]) for m in previous_period_months if m in monthly),
        Decimal("0"),
    )
    period_participants = {
        str(entry.get("employee_id") or entry.get("employee_name") or "").strip()
        for m in period_months
        for entry in monthly.get(m, {}).get("entries", [])
    } - {""}
    cumulative_participants = {
        str(entry.get("employee_id") or entry.get("employee_name") or "").strip()
        for item in monthly.values()
        if item["month"] <= end_month
        for entry in item.get("entries", [])
    } - {""}
    planned_days = (
        Decimal(project["planned_person_days"])
        if project.get("planned_person_days") is not None else None
    )
    remaining_days = (
        planned_days - cumulative_days if planned_days is not None else None
    )
    burn_rate = (
        cumulative_days / planned_days * Decimal("100")
        if planned_days and planned_days > 0 else None
    )
    plan_months = _plan_months(project.get("planned_start"), project.get("planned_end"))
    monthly_budget = (
        planned_days / Decimal(plan_months)
        if planned_days is not None and plan_months else None
    )
    if project.get("revenue_settlement_basis") == "workload":
        warning = "工作量结算"
    else:
        warning = _overrun_warning(burn_rate, planned_days, remaining_days, thresholds)
    trend = _monthly_trend(period_days, previous_period_days)
    contracts = [
        record
        for record in project.get("crm_records", [])
        if record.get("data_type") == "contract_order"
    ]
    has_extra_investment = _contracts_are_source_closed(contracts) and period_days > 0
    health = _health_status(warning, project, has_extra_investment)
    risk_reasons = list(project.get("risk_reasons", []))
    if has_extra_investment:
        risk_reasons.insert(
            0,
            f"来源状态已结束，统计期仍填报 {_format_decimal(period_days)} 人天（额外投入）",
        )
    return {
        "project_number": project["project_number"],
        "project_name": project["subject"],
        "customer": project.get("customer"),
        "master_category": project.get("master_category"),
        "category": project.get("category"),
        "contract_type": project.get("contract_type"),
        "risk_level": project.get("risk_level"),
        "contract_value": project.get("contract_value"),
        "crm_received_amount": project.get("crm_received_amount"),
        "revenue_settlement_basis": project.get("revenue_settlement_basis"),
        "recognized_revenue_amount": project.get("recognized_revenue_amount"),
        "outstanding_receivable_amount": project.get("outstanding_receivable_amount"),
        "payment_term_summary": project.get("payment_term_summary", []),
        "anomaly_count": project.get("anomaly_count", 0),
        "anomaly_alerts": project.get("anomaly_alerts", []),
        "budget_utilization": project.get("budget_utilization"),
        "billable_person_days": project.get("billable_person_days"),
        "actual_person_days": project.get("actual_person_days"),
        "pm_owner": project.get("project_manager"),
        "sales_owner": project.get("sales_owner"),
        "project_status": project["status"],
        "planned_start": project.get("planned_start"),
        "planned_end": project.get("planned_end"),
        "budget_person_days": (
            _format_decimal(planned_days) if planned_days is not None else None
        ),
        "plan_months": plan_months,
        "monthly_budget_person_days": (
            _format_decimal(monthly_budget) if monthly_budget is not None else None
        ),
        "cumulative_actual_person_days": _format_decimal(cumulative_days),
        "period_actual_hours": _format_decimal(period_hours),
        "period_actual_person_days": _format_decimal(period_days),
        "previous_month_person_days": _format_decimal(previous_days),
        "previous_period": {"start_month": previous_period_months[0], "end_month": previous_period_months[-1]},
        "previous_period_person_days": _format_decimal(previous_period_days),
        "participant_count": len(period_participants),
        "cumulative_participant_count": len(cumulative_participants),
        "burn_rate_percent": (
            f"{burn_rate.quantize(Decimal('0.1'))}" if burn_rate is not None else None
        ),
        "overrun_warning": warning,
        "monthly_trend": trend,
        "health_status": health,
        "budget_exhaustion_forecast": _budget_exhaustion_forecast(
            remaining_days, period_days, len(period_months), end_month
        ),
        "remaining_budget_person_days": (
            _format_decimal(remaining_days) if remaining_days is not None else None
        ),
        "current_milestone": project.get("current_milestone"),
        "payment_summary": project.get("payment_summary"),
        "risk_reasons": risk_reasons,
    }


def _budget_thresholds(baseline: dict[str, Any]) -> dict[str, Decimal]:
    """Budget lines shared by project risk and operations monitoring."""
    return {
        "budget_attention_percent": Decimal(baseline.get("budget_attention_percent") or "80"),
        "budget_high_risk_percent": Decimal(baseline.get("budget_high_risk_percent") or "90"),
    }


def _overrun_warning(
    burn_rate: Decimal | None,
    planned_days: Decimal | None,
    remaining_days: Decimal | None,
    thresholds: dict[str, Decimal],
) -> str:
    if planned_days is None:
        return "待维护预算"
    if remaining_days is not None and remaining_days < 0:
        return "已超预算"
    if burn_rate is not None and burn_rate >= thresholds["budget_high_risk_percent"]:
        return "高风险"
    if burn_rate is not None and burn_rate >= thresholds["budget_attention_percent"]:
        return "需关注"
    return "正常"


def _monthly_trend(period_days: Decimal, previous_days: Decimal) -> str:
    if period_days == 0 and previous_days == 0:
        return "暂无记录"
    if previous_days == 0:
        return "新增投入"
    change = (period_days - previous_days) / previous_days * Decimal("100")
    if change >= Decimal("10"):
        return "上升"
    if change <= Decimal("-10"):
        return "下降"
    return "持平"


def _health_status(
    warning: str, project: dict[str, Any], has_extra_investment: bool = False
) -> str:
    if has_extra_investment:
        return "额外投入"
    if warning == "已超预算":
        return "已超预算"
    if warning == "待维护预算":
        return "无基线"
    if warning == "工作量结算":
        return "需关注" if project.get("risk_level") in {"critical", "warning"} else "健康"
    if warning in {"高风险", "需关注"} or project.get("risk_level") in {"critical", "warning"}:
        return "需关注"
    return "健康"


def _budget_exhaustion_forecast(
    remaining_days: Decimal | None,
    period_days: Decimal,
    period_month_count: int,
    end_month: str,
) -> dict[str, str]:
    """Forecast when the remaining person-day budget will be used up.

    This is intentionally a budget-consumption forecast, not a delivery-date
    forecast: delivery completion remains governed by the project schedule and
    milestone confirmations.
    """
    if remaining_days is None:
        return {"label": "暂无预算预测", "basis": "请先维护预算人天"}
    if remaining_days < 0:
        return {
            "label": "预算已超支",
            "basis": f"已超出 {_format_decimal(-remaining_days)} 人天",
        }
    if remaining_days == 0:
        return {"label": "预算已耗尽", "basis": "剩余预算为 0 人天"}
    if period_days == 0:
        return {"label": "暂无预算预测", "basis": "统计期无投入，无法估算"}

    monthly_rate = period_days / Decimal(max(period_month_count, 1))
    months_until_exhaustion = remaining_days / monthly_rate
    month_offset = max(
        1,
        int(months_until_exhaustion.to_integral_value(rounding=ROUND_CEILING)),
    )
    forecast_month = _shift_month(end_month, month_offset)
    return {
        "label": f"预计 {forecast_month.replace('-', '年', 1)}月耗尽",
        "basis": (
            f"按统计期月均 {_format_decimal(monthly_rate)} 人天"
            f" · 约 {months_until_exhaustion.quantize(Decimal('0.1'))} 个月"
        ),
    }


def _health_rank(value: str) -> int:
    return {"额外投入": 0, "已超预算": 1, "需关注": 2, "无基线": 3, "健康": 4}.get(value, 5)


def matches_project_query(
    project: dict[str, Any], query: str, crm_terms_by_project: dict[str, list[str]]
) -> bool:
    needle = query.casefold()
    return needle in " ".join(
        [
            project["project_number"],
            project["subject"],
            project.get("customer") or "",
            project.get("project_manager") or "",
            *crm_terms_by_project.get(project["project_number"], []),
        ]
    ).casefold()


# ------------------------------------------------------------------ list views (former REST payloads)
def project_list_view(
    projects: list[dict[str, Any]],
    crm_terms: Any,
    *,
    contract_type: str | None = None,
    lifecycle_status: str | None = None,
    category: str | None = None,
    risk_level: str | None = None,
    query: str | None = None,
    cursor: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    all_projects = list(projects)
    lifecycle_statuses = _available_lifecycle_statuses(all_projects)
    categories = _available_project_categories(all_projects)
    projects = all_projects
    if contract_type:
        projects = [p for p in projects if p["contract_type"] == contract_type]
    if lifecycle_status:
        projects = [p for p in projects if p["lifecycle_status"] == lifecycle_status]
    if category:
        projects = [
            project
            for project in projects
            if category in _project_category_values(project.get("category"))
        ]
    if risk_level:
        projects = [p for p in projects if p["risk_level"] == risk_level]
    if query:
        crm_terms_by_project = crm_terms()
        projects = [
            project
            for project in projects
            if matches_project_query(
                project, query, crm_terms_by_project
            )
        ]
    projects.sort(key=lambda p: (_risk_rank(p["risk_level"]), p["project_number"]))
    page, next_cursor = _cursor_page(
        projects,
        cursor=cursor,
        page_size=page_size,
        kind="projects",
        key=lambda project: (_risk_rank(project["risk_level"]), project["project_number"]),
        scope=_cursor_scope(
            contract_type=contract_type,
            lifecycle_status=lifecycle_status,
            category=category,
            risk_level=risk_level,
            query=query,
        ),
    )
    return {
        "data": page,
        "summary": {
            "total_projects": len(projects),
            "contract_value": _decimal_sum(p["contract_value"] for p in projects),
            "received_amount": _decimal_sum(p["received_amount"] for p in projects),
            "outstanding_receivable_amount": _decimal_sum(
                p["outstanding_receivable_amount"] or "0" for p in projects
            ),
            "critical_projects": sum(p["risk_level"] == "critical" for p in projects),
            "warning_projects": sum(p["risk_level"] == "warning" for p in projects),
        },
        "meta": {
            "page_size": page_size,
            "next_cursor": next_cursor,
            "lifecycle_statuses": lifecycle_statuses,
            "categories": categories,
        },
    }


def project_health_view(
    projects: list[dict[str, Any]],
    crm_terms: Any,
    policy: dict[str, Any],
    now: str,
    *,
    month: str | None = None,
    start_month: str | None = None,
    end_month: str | None = None,
    project_number: str | None = None,
    lifecycle_status: str | None = None,
    contract_type: str | None = None,
    risk_level: str | None = None,
    master_category: str | None = None,
    category: str | None = None,
    query: str | None = None,
    cursor: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    start, end = _health_period(month, start_month, end_month, now)
    rows = []
    included: list[dict[str, Any]] = []
    thresholds = _budget_thresholds(policy)
    lifecycle_statuses = _available_lifecycle_statuses(projects)
    categories = _available_project_categories(projects)
    crm_terms_by_project = crm_terms() if query else {}
    for project in projects:
        if project_number and project["project_number"] != project_number:
            continue
        if lifecycle_status and project["lifecycle_status"] != lifecycle_status:
            continue
        if contract_type and project["contract_type"] != contract_type:
            continue
        if risk_level and project["risk_level"] != risk_level:
            continue
        if master_category and project["master_category"] != master_category:
            continue
        if category and category not in _project_category_values(project.get("category")):
            continue
        if query and not matches_project_query(
            project, query, crm_terms_by_project
        ):
            continue
        included.append(project)
        rows.append(_project_health_row(project, start, end, thresholds))
    # The project workspace lists projects needing attention first: risk
    # level, then budget health, then project code.
    def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
        return (_risk_rank(row["risk_level"]), _health_rank(row["health_status"]), row["project_number"])

    rows.sort(key=sort_key)
    page, next_cursor = _cursor_page(
        rows,
        cursor=cursor,
        page_size=page_size,
        kind="project-health",
        key=sort_key,
        scope=_cursor_scope(
            month=month,
            start_month=start_month,
            end_month=end_month,
            project_number=project_number,
            lifecycle_status=lifecycle_status,
            contract_type=contract_type,
            risk_level=risk_level,
            master_category=master_category,
            category=category,
            query=query,
        ),
    )
    return {
        "data": page,
        "summary": {
            "total_projects": len(rows),
            "contract_value": _decimal_sum(p["contract_value"] for p in included),
            "received_amount": _decimal_sum(p["received_amount"] for p in included),
            "outstanding_receivable_amount": _decimal_sum(
                p["outstanding_receivable_amount"] or "0" for p in included
            ),
            "risk_critical_projects": sum(p["risk_level"] == "critical" for p in included),
            "risk_warning_projects": sum(p["risk_level"] == "warning" for p in included),
            "critical_projects": sum(row["health_status"] == "已超预算" for row in rows),
            "warning_projects": sum(
                row["health_status"] in {"需关注", "额外投入"} for row in rows
            ),
            "healthy_projects": sum(row["health_status"] == "健康" for row in rows),
            "no_baseline_projects": sum(row["health_status"] == "无基线" for row in rows),
        },
        "meta": {
            "start_month": start,
            "end_month": end,
            "mode": "month" if start == end else "range",
            "page_size": page_size,
            "next_cursor": next_cursor,
            "lifecycle_statuses": lifecycle_statuses,
            "categories": categories,
        },
    }
