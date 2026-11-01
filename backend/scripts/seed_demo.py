"""Load the demo case into a running local server (demo data only, never a production database).

Run from backend/ against a server started with authentication disabled and the example CRM snapshot:

    .venv/bin/python scripts/seed_demo.py                    # empty database: projects, then staff
    .venv/bin/python scripts/seed_demo.py --reset-people     # replace all time entries with the demo staff

Projects: three SEY contracts through the CRM import, their settings, payment terms linked to milestones with the
first payment received, and the 2026 annual plan (skipped when SEY101 exists); two more opportunities for the
investment view — LSO301 lost (sunk cost) and STL302 still open but without effort for over 90 days (stalled). Staff: 40 people
(scripts/demo_people.py plus docs/test-data/wdr101-time-entries.json), January–October 2026, one scenario each;
SEY budgets are then set so SEY101 is normal (70%), SEY103 near its limit (85%) and SEY104 over budget (150%).
Finally every person's October 2026 health, worktime assessment and staffing status are checked against
demo_people.PEOPLE. --reset-people deletes the time entries directly in the local SQLite file (the dataset-version
triggers make the server refresh its index).
"""
from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_people  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
TEST_DATA = BACKEND.parent / "docs" / "test-data" / "wdr101-time-entries.json"
BUDGET_TARGETS = {"SEY101": Decimal("0.70"), "SEY103": Decimal("0.85"), "SEY104": Decimal("1.50")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--reset-people", action="store_true", help="delete all time entries and load the demo staff")
    parser.add_argument("--database", default=str(BACKEND / "data" / "preview.sqlite3"))
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base, timeout=120)

    def call(method: str, path: str, body=None) -> dict:
        response = client.request(method, path, json=body)
        if response.status_code >= 400:
            raise SystemExit(f"{method} {path} → {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    if client.get("/api/v1/auth/me").status_code == 401:
        raise SystemExit("The server requires login; start it with TSG_PM_AUTH_ENABLED=false to load demo data.")
    if client.get("/api/v1/projects/SEY101").status_code == 200:
        print("Projects: SEY101 exists, project data kept.")
    else:
        seed_projects(call)
    if client.get("/api/v1/projects/LSO301").status_code != 200:
        seed_opportunities(call)

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
    verify(call)


def seed_projects(call) -> None:
    csv = ("项目编号,主题,对应客户,我方签约人,签约时间,分类,状态,总金额,回款金额,开票金额\n"
           "SEY101,SEY101-数字化工厂系统实施,SEY,Sales A,2025-12-20,系统实施,执行中,1254086.00,627043.00,627043.00\n"
           "SEY103,SEY103-质量管理系统实施,SEY,Sales A,2026-01-15,系统实施,执行中,700000.00,350000.00,350000.00\n"
           "SEY104,SEY104-仓储接口实施,SEY,Sales A,2026-03-10,系统实施,执行中,172250.00,51675.00,51675.00\n")
    call("POST", "/api/v1/crm-imports", {"data_type": "contract_order", "filename": "sey-demo.csv",
                                         "content_base64": base64.b64encode(csv.encode()).decode()})
    # code: start, end, first payment (date, amount), acceptance payment (date, amount); budgets follow the staff.
    plans = {"SEY101": ("2026-01-05", "2026-12-31", ("2026-03-31", "627043.00"), ("2026-12-31", "627043.00")),
             "SEY103": ("2026-02-02", "2026-11-30", ("2026-04-30", "350000.00"), ("2026-11-30", "350000.00")),
             "SEY104": ("2026-03-16", "2026-10-31", ("2026-05-29", "51675.00"), ("2026-10-31", "120575.00"))}
    for code, (start, end, first, last) in plans.items():
        call("PATCH", f"/api/v1/projects/{code}/settings", {"contract_type": "construction", "lifecycle_status": "active",
             "planned_person_days": "100", "planned_start": start, "planned_end": end, "project_manager": "李经理"})
        for name, milestone, (due, amount) in (("首付款", "项目启动", first), ("验收款", "项目验收", last)):
            term = call("POST", f"/api/v1/projects/{code}/payment-terms", {"name": name, "planned_amount": amount})["data"]
            call("POST", f"/api/v1/projects/{code}/milestones", {"name": milestone, "planned_start": start, "planned_end": due,
                                                                 "payment_term_id": term["payment_term_id"]})
        nodes = {n["name"]: n for n in call("GET", f"/api/v1/projects/{code}")["data"]["payment_nodes"]}
        first_node = nodes.get("首付款") or next(iter(nodes.values()))
        call("POST", f"/api/v1/projects/{code}/payment-nodes/{first_node['payment_node_id']}/confirm",
             {"confirmed_by": "财务", "actual_amount": first[1], "actual_date": first[0]})
    call("PUT", "/api/v1/department/annual-plans/2026", {"new_contract_target": "6000000", "collection_target": "3500000",
         "capacity_person_days_target": "8000", "headcount_target": "40", "utilization_target_percent": "85",
         "notes": "演示数据"})
    print("Projects: loaded SEY101, SEY103, SEY104 and the 2026 annual plan.")


def seed_opportunities(call) -> None:
    csv = ("项目编号,主题,对应客户,我方签约人,预计签单日期,阶段,状态,总金额\n"
           "LSO301,LSO301-MES 选型投标,LSO,Sales B,2026-07-31,方案,丢单,880000.00\n"
           "STL302,STL302-仓储升级咨询,STL,Sales B,2026-12-31,初步接洽,跟进中,300000.00\n")
    call("POST", "/api/v1/crm-imports", {"data_type": "sales_opportunity", "filename": "opportunities-demo.csv",
                                         "content_base64": base64.b64encode(csv.encode()).decode()})
    print("Opportunities: loaded LSO301 (lost) and STL302 (stalled).")


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
    people = {entry["employee_id"] for entry in entries}
    print(f"Staff: loaded {len(entries)} time entries for {len(people)} people.")

    effort: dict[str, Decimal] = defaultdict(Decimal)
    for entry in entries:
        if entry["approval_status"] in ("submitted", "approved"):
            effort[entry["project_number"]] += Decimal(entry["hours"]) + Decimal(entry["overtime_hours"])
    for code, share in BUDGET_TARGETS.items():
        person_days = (effort[code] / 8 / share).quantize(Decimal("1"))
        call("PATCH", f"/api/v1/projects/{code}/settings", {"planned_person_days": str(person_days)})
        print(f"Budget: {code} {person_days} person-days for {effort[code] / 8:.1f} reported ({share:.0%}).")


def verify(call) -> None:
    month = demo_people.CHECK_MONTH
    health = {r["employee_id"]: r["statuses"] for r in call("GET", f"/api/v1/department/resource-health?month={month}")["data"]["data"]}
    operations = {r["employee_id"]: r for r in call("GET", f"/api/v1/resource-operations?month={month}&page_size=200")["data"]}
    worktime = {r["employee_id"]: r for r in call("GET", f"/api/v1/employee-worktime?period=month&month={month}")["data"]}
    failures = 0
    for person in demo_people.PEOPLE:
        actual = (health.get(person.employee_id), (worktime.get(person.employee_id) or {}).get("status"),
                  (operations.get(person.employee_id) or {}).get("allocation_status"))
        expected = (person.health, person.assessment, person.staffing)
        ok = all(e is None and a is None or e == a for e, a in zip(expected[:1], actual[:1])) and all(
            e is None or e == a for e, a in zip(expected[1:], actual[1:]))
        failures += not ok
        print(f"{'✓' if ok else '✗'} {person.employee_id} {person.name}  health={actual[0]}  assessment={actual[1]}  "
              f"staffing={actual[2]}" + ("" if ok else f"   expected {expected}"))
    print(f"Checked {len(demo_people.PEOPLE)} generated people for {month}: {failures} mismatches.")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
