"""资源运营监控 over a month range (ontology 8.3.0, requirements baseline D42, build package
docs/ontology-builds/2026-11-06-resource-range.yaml): the staffing advice comes from the range's last month.

Before D42 a range was averaged over every month, months without hours counted as idle and past gaps were added up:
someone who joined in the last month showed as 「可优先调剂」 with weeks to spare.
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app

from test_api import settings
from test_business_rules import entry, import_entries


def weekdays(month: str):
    day = date.fromisoformat(f"{month}-01")
    while day.strftime("%Y-%m") == month:
        if day.weekday() < 5:
            yield day.isoformat()
        day += timedelta(days=1)


def worked(name, employee, months, hours="8.00"):
    return [entry(f"{employee}-{day}", "TPL149", day, hours=hours, employee=employee, name=name)
            for month in months for day in weekdays(month)]


def operations(client, **params):
    response = client.post("/api/v1/ontology/functions/resourceOperations/execute", json={"parameters": {**params, "pageSize": 200}})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]


def by_name(result):
    return {row["employee_name"]: row for row in result["data"]}


def setup(client):
    import_entries(client, worked("新人", "E801", ["2030-03"]) + worked("超负荷", "E802", ["2030-01", "2030-02", "2030-03"], "10.00")
                   + worked("已离开", "E803", ["2030-02"]))


def test_a_range_is_judged_on_its_last_month(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)
        rows = by_name(operations(client, startMonth="2030-01", endMonth="2030-03"))
        newcomer = rows["新人"]
        # Worked only in March, every working day: not idle, and the range does not count January and February.
        assert newcomer["allocation_status"] != "可优先调剂" and newcomer["available_person_days"] == "0.00"
        assert (newcomer["basis_month"], newcomer["present_months"], newcomer["total_months"]) == ("2030-03", 1, 3)
        assert newcomer["utilization_percent"] == newcomer["basis_utilization_percent"], "the average counts worked months only"
        overloaded = rows["超负荷"]
        assert overloaded["allocation_status"] == "本月负荷过高" and overloaded["overload_months"] == 3
        assert rows["已离开"]["allocation_status"] == "期末无工时" and rows["已离开"]["available_person_days"] == "0.00"


def test_range_totals_are_the_last_months_not_the_sum_of_past_gaps(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)
        ranged = operations(client, startMonth="2030-01", endMonth="2030-03")
        march = operations(client, month="2030-03")
        assert ranged["summary"]["available_person_days"] == march["summary"]["available_person_days"]
        assert ranged["summary"]["high_load_count"] == march["summary"]["high_load_count"]
        assert ranged["summary"]["total_employees"] == march["summary"]["total_employees"] + 1, "the leaver is still listed"
        assert ranged["data"][-1]["allocation_status"] == "期末无工时", "people without advice are listed last"


def test_a_single_month_is_unchanged(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        setup(client)
        row = by_name(operations(client, month="2030-03"))["超负荷"]
        assert (row["basis_month"], row["present_months"], row["total_months"], row["overload_months"]) == ("2030-03", 1, 1, 1)
        assert row["utilization_percent"] == row["basis_utilization_percent"] and row["allocation_status"] == "本月负荷过高"


def test_the_internal_part_of_the_advice_is_from_the_advice_month(tmp_path):
    """吴迪 showed 「可调剂 11.00 人天（含 TSG999 33.00 人天）」: the bracket was the range total, not the advice month."""
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, [entry(f"E804-{day}", "TSG999", day, employee="E804", name="内部")
                                for month in ("2030-01", "2030-02", "2030-03") for day in weekdays(month)])
        row = by_name(operations(client, startMonth="2030-01", endMonth="2030-03"))["内部"]
        march_days = len(list(weekdays("2030-03")))
        assert float(row["basis_internal_person_days"]) <= march_days < float(row["internal_nonproject_person_days"])
        assert float(row["available_person_days"]) >= float(row["basis_internal_person_days"])

