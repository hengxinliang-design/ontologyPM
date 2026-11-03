"""Load the demo case into a running local server (demo data only, never a production database).

Run from backend/ against a server started with authentication disabled and the example CRM snapshot:

    .venv/bin/python scripts/seed_demo.py                    # empty database: projects, opportunities, staff
    .venv/bin/python scripts/seed_demo.py --reset-people     # replace all time entries with the demo staff

Projects and opportunities come from scripts/demo_projects.py: CRM contracts and opportunities are imported, then
every project is maintained through the business endpoints (settings, payment terms linked to milestones, milestone
and payment confirmations, interventions), opportunities are abandoned or reopened through their actions, and the
monthly operating costs are entered. Milestone completion dates are backdated in the local SQLite file after the
confirm action, because the action always records today (demo history only). Staff: 40 people
(scripts/demo_people.py plus docs/test-data/wdr101-time-entries.json), January–October 2026. Budgets and annual
targets are then set from the loaded figures so each project and target lands in its intended band, and every
project, opportunity and person is checked against its expected result.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_people  # noqa: E402
import demo_projects as dp  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
TEST_DATA = BACKEND.parent / "docs" / "test-data" / "wdr101-time-entries.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--reset-people", action="store_true", help="delete all time entries and load the demo staff")
    parser.add_argument("--database", default=str(BACKEND / "data" / "preview.sqlite3"))
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base, timeout=120)

    def call(method: str, path: str, body=None, *, params=None) -> dict:
        response = client.request(method, path, json=body, params=params)
        if response.status_code >= 400:
            raise SystemExit(f"{method} {path} → {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    if client.get("/api/v1/auth/me").status_code == 401:
        raise SystemExit("The server requires login; start it with TSG_PM_AUTH_ENABLED=false to load demo data.")
    fresh = client.get("/api/v1/projects/SEY101").status_code != 200
    today = date.today()
    if fresh:
        seed_projects(call, args.database, today)
    else:
        print("Projects: SEY101 exists, project and opportunity data kept.")

    with sqlite3.connect(args.database) as connection:
        existing = connection.execute("SELECT COUNT(*) FROM time_entries").fetchone()[0]
    if existing and not args.reset_people:
        print(f"Staff: {existing} time entries exist; pass --reset-people to replace them.")
    else:
        if existing:
            if "preview" not in Path(args.database).name:
                raise SystemExit(f"Refusing to clear {args.database}: only a preview database is reset.")
            with sqlite3.connect(args.database) as connection:
                connection.execute("DELETE FROM time_entries")
            print(f"Staff: deleted {existing} time entries.")
        seed_people(call)
    if fresh:
        record_outcomes(call)
        set_annual_targets(call)
    failures = verify_people(call) + verify_projects(call)
    if failures:
        raise SystemExit(f"{failures} mismatches.")


# ------------------------------------------------------------------ projects and opportunities
def _csv(header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return base64.b64encode(buffer.getvalue().encode()).decode()


def seed_projects(call, database: str, today: date) -> None:
    call("POST", "/api/v1/crm-imports", {"data_type": "contract_order", "filename": "contracts-demo.csv", "content_base64": _csv(
        ["内部ID", "项目编号", "主题", "对应客户", "我方签约人", "签约时间", "分类", "状态", "总金额", "回款金额", "开票金额"],
        [[c.crm_id, c.code, c.subject, c.customer, c.owner, c.signed, c.category, c.status, c.total, c.received, c.invoiced]
         for c in dp.CONTRACTS])})
    call("POST", "/api/v1/crm-imports", {"data_type": "sales_opportunity", "filename": "opportunities-demo.csv", "content_base64": _csv(
        ["内部ID", "项目编号", "主题", "对应客户", "所有者", "预计签单日期", "阶段", "状态", "总金额", "可能性"],
        [[o.crm_id, o.code, o.subject, o.customer, o.owner, o.expected_sign, o.stage, o.status, o.amount, o.probability]
         for o in dp.OPPORTUNITIES])})
    print(f"CRM: imported {len(dp.CONTRACTS)} contracts and {len(dp.OPPORTUNITIES)} opportunities.")

    backdated = []
    for project in dp.PROJECTS:
        if project.contract_type is None:
            continue
        settings = {"contract_type": project.contract_type, "lifecycle_status": project.lifecycle,
                    "planned_start": project.start, "planned_end": project.end}
        if project.manager:
            settings["project_manager"] = project.manager
        if project.contract_type in ("construction", "maintenance"):
            settings["planned_person_days"] = project.budget_days or "100"  # replaced once the staff are loaded
        call("PATCH", f"/api/v1/projects/{project.code}/settings", settings)
        for milestone in project.milestones:
            body = {"name": milestone.name, "planned_start": dp.day(milestone.start, today),
                    "planned_end": dp.day(milestone.end, today), "owner": project.manager}
            if milestone.term:
                name, amount, ratio = milestone.term
                term = call("POST", f"/api/v1/projects/{project.code}/payment-terms",
                            {"name": name, "planned_amount": amount, "payment_ratio": ratio})["data"]
                body["payment_term_id"] = term["payment_term_id"]
            created = call("POST", f"/api/v1/projects/{project.code}/milestones", body)["data"]
            if milestone.completed:
                call("POST", f"/api/v1/projects/{project.code}/milestones/{created['milestone_id']}/confirm",
                     {"confirmed_by": project.manager})
                backdated.append((created["milestone_id"], milestone.completed))
        nodes = {n["name"]: n for n in call("GET", f"/api/v1/projects/{project.code}")["data"]["payment_nodes"]}
        for milestone in project.milestones:
            if milestone.term and milestone.received:
                name, amount, _ = milestone.term
                node = nodes[name]
                call("POST", f"/api/v1/projects/{project.code}/payment-nodes/{node['payment_node_id']}/confirm",
                     {"confirmed_by": "财务", "actual_amount": amount, "actual_date": milestone.received})
        for item in project.interventions:
            created = call("POST", f"/api/v1/projects/{project.code}/interventions", {
                "title": item.title, "intervention_type": item.kind, "owner": item.owner, "due_date": dp.day(item.due, today)})["data"]
            path = f"/api/v1/projects/{project.code}/interventions/{created['intervention_id']}/status"
            if item.status in ("in_progress", "closed"):
                call("POST", path, {"status": "in_progress", "notes": item.notes or None})
            if item.status == "closed":
                call("POST", path, {"status": "closed", "notes": item.notes or None})
    # The confirm action records today; demo history needs the real completion dates.
    with sqlite3.connect(database) as connection:
        connection.executemany("UPDATE milestones SET completed_date = ?, confirmed_at = ? || 'T18:00:00+08:00' WHERE milestone_id = ?",
                               [(done, done, milestone_id) for milestone_id, done in backdated])
    for month in dp.COST_MONTHS:
        call("POST", "/api/v1/ontology/actions/recordOperatingCost/apply",
             {"parameters": {"policy": "default", "month": month, **dp.MONTHLY_COST, "notes": "演示数据"}})
    print(f"Projects: maintained {sum(p.contract_type is not None for p in dp.PROJECTS)} projects, "
          f"backdated {len(backdated)} completed milestones, entered costs for {len(dp.COST_MONTHS)} months.")


def record_outcomes(call) -> None:
    for opportunity in dp.OPPORTUNITIES:
        if not opportunity.action:
            continue
        kind, reason = opportunity.action
        if kind == "abandon":
            call("POST", "/api/v1/ontology/actions/recordOpportunityOutcome/apply", {"parameters": {
                "costObject": opportunity.code, "outcome": "abandoned", "reason": reason, "outcomeDate": "2026-06-15"}})
        else:  # reopening undoes a manual conclusion, so record one first
            call("POST", "/api/v1/ontology/actions/recordOpportunityOutcome/apply", {"parameters": {
                "costObject": opportunity.code, "outcome": "lost", "reason": "竞争失败", "outcomeDate": "2026-03-20"}})
            call("POST", "/api/v1/ontology/actions/reopenOpportunity/apply",
                 {"parameters": {"costObject": opportunity.code, "reason": reason}})
    print("Opportunities: recorded the manual outcomes (abandoned, reopened).")


def set_annual_targets(call) -> None:
    def review() -> dict:
        return call("GET", "/api/v1/department/review", params={"year": 2026, "month": "2026-10"})["data"]

    plan = {"policy": "default", "year": 2026, "collectionTarget": "1", "newContractTarget": "1", "operatingCostTarget": "1",
            "capacityPersonDaysTarget": "8000", "headcountTarget": "40", "utilizationTargetPercent": "85", "notes": "演示数据"}
    call("POST", "/api/v1/ontology/actions/setAnnualOperatingPlan/apply", {"parameters": plan})
    kpis = review()["kpis"]
    actual = {"collectionTarget": kpis["collections"]["actual"], "newContractTarget": kpis["new_contracts"]["actual"],
              "operatingCostTarget": kpis["operating_cost"]["actual"]}
    for key, share in dp.TARGET_SHARES.items():
        plan[key] = str((Decimal(actual[key]) / Decimal(str(share))).quantize(Decimal("1000")))
    call("POST", "/api/v1/ontology/actions/setAnnualOperatingPlan/apply", {"parameters": plan})
    kpis = review()["kpis"]
    print("Annual plan: " + "；".join(f"{kpis[k]['label']} {kpis[k]['actual']} / {kpis[k]['target']} → {kpis[k]['status']}"
                                      for k in ("collections", "new_contracts", "operating_cost", "gross_margin")))


# ------------------------------------------------------------------ staff
def seed_people(call) -> None:
    cache: dict[str, bool] = {}

    def working(day: str) -> bool:
        if day not in cache:
            result = call("POST", "/api/v1/ontology/functions/workingCalendar/execute",
                          {"parameters": {"startDate": day, "endDate": day}})["data"]["result"]
            cache[day] = result["working_days"] == 1
        return cache[day]

    entries = demo_people.generate(working) + json.loads(TEST_DATA.read_text(encoding="utf-8"))
    for index in range(0, len(entries), 500):
        call("POST", "/api/v1/time-entries/import", entries[index:index + 500])
    print(f"Staff: loaded {len(entries)} time entries for {len({e['employee_id'] for e in entries})} people.")

    effort: dict[str, Decimal] = defaultdict(Decimal)
    for entry in entries:
        if entry["approval_status"] in ("submitted", "approved"):
            effort[entry["project_number"]] += Decimal(entry["hours"]) + Decimal(entry["overtime_hours"])
    for project in dp.PROJECTS:
        if project.budget_share is None:
            continue
        reported = effort[project.code] / 8
        person_days = (reported / Decimal(str(project.budget_share))).quantize(Decimal("1"))
        call("PATCH", f"/api/v1/projects/{project.code}/settings", {"planned_person_days": str(person_days)})
        print(f"Budget: {project.code} {person_days} person-days for {reported:.1f} reported ({project.budget_share:.0%}).")


# ------------------------------------------------------------------ checks
def verify_people(call) -> int:
    month = demo_people.CHECK_MONTH
    health = {r["employee_id"]: r["statuses"] for r in call("GET", f"/api/v1/department/resource-health?month={month}")["data"]["data"]}
    operations = {r["employee_id"]: r for r in call("GET", f"/api/v1/resource-operations?month={month}&page_size=200")["data"]}
    worktime = {r["employee_id"]: r for r in call("GET", f"/api/v1/employee-worktime?period=month&month={month}")["data"]}
    failures = 0
    for person in demo_people.PEOPLE:
        actual = (health.get(person.employee_id), (worktime.get(person.employee_id) or {}).get("status"),
                  (operations.get(person.employee_id) or {}).get("allocation_status"))
        ok = actual[0] == person.health and all(
            e is None or e == a for e, a in zip((person.assessment, person.staffing), actual[1:]))
        failures += not ok
        if not ok:
            print(f"✗ {person.employee_id} {person.name}: health={actual[0]} assessment={actual[1]} staffing={actual[2]}, "
                  f"expected {(person.health, person.assessment, person.staffing)}")
    print(f"People: checked {len(demo_people.PEOPLE)} for {month}, {failures} mismatches.")
    return failures


def verify_projects(call) -> int:
    failures = 0
    for project in dp.PROJECTS:
        data = call("GET", f"/api/v1/projects/{project.code}")["data"]
        codes = {a["code"] for a in data["anomaly_alerts"]}
        problems = []
        if project.risk and data["risk_level"] != project.risk:
            problems.append(f"risk {data['risk_level']} ≠ {project.risk}")
        if project.settlement and data["revenue_settlement_basis"] != project.settlement:
            problems.append(f"settlement {data['revenue_settlement_basis']} ≠ {project.settlement}")
        if project.lifecycle and data["lifecycle_status"] != project.lifecycle:
            problems.append(f"lifecycle {data['lifecycle_status']} ≠ {project.lifecycle}")
        if project.lifecycle_source and data["lifecycle_source"] != project.lifecycle_source:
            problems.append(f"lifecycle source {data['lifecycle_source']} ≠ {project.lifecycle_source}")
        if missing := set(project.alerts) - codes:
            problems.append(f"missing alerts {sorted(missing)}")
        failures += bool(problems)
        print(f"{'✓' if not problems else '✗'} {project.code} risk={data['risk_level']} lifecycle={data['lifecycle_status']} "
              f"budget={data['budget_utilization']}% alerts={sorted(codes)}" + (f"  ← {'; '.join(problems)}" if problems else ""))
    analysis = call("GET", "/api/v1/investment/analysis")["data"]
    outcomes = {row["code"]: row["outcome"] for key in ("open_opportunities", "sunk_items") for row in analysis[key]}
    for opportunity in dp.OPPORTUNITIES:
        if opportunity.outcome == "won":
            continue  # converted: its presales effort is acquisition cost of the project
        actual = outcomes.get(opportunity.code)
        expected = opportunity.outcome if opportunity.outcome != "abandoned" else "abandoned"
        ok = actual == expected or (actual is None and opportunity.code == "XNY307")  # no effort, not an investment
        failures += not ok
        print(f"{'✓' if ok else '✗'} {opportunity.code} outcome={actual} (expected {expected})")
    print("Investment: " + ", ".join(f"{k} {v}" for k, v in analysis["totals"].items() if v is not None))
    return failures


if __name__ == "__main__":
    main()
