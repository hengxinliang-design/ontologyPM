"""Effective worktime (ontology 5.1.0, build package docs/ontology-builds/2026-10-28-effective-worktime.yaml).

Submitted and approved time is effort; rejected and draft entries stay stored but count nowhere. Excel re-imports may
move a submitted entry to approved or rejected, never back.
"""
import base64
import shutil
from dataclasses import replace
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.compiler import DEFAULT_METADATA_DIR, OntologyMetadataError, load_ontology

from test_api import _minimal_timesheet_xlsx, settings

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "test-data" / "wdr101-time-entries.json"
HEADER = ["姓名", "部门", "日期", "项目代码", "case", "天数", "工作内容", "流程状态"]


def upload(client, *rows):
    workbook = _minimal_timesheet_xlsx([HEADER, *rows])
    response = client.post("/api/v1/time-entries/import-xlsx", json={
        "filename": "timesheet.xlsx", "content_base64": base64.b64encode(workbook).decode("ascii")})
    assert response.status_code == 200, response.text
    return {key: response.json()[key] for key in ("imported", "updated", "unchanged")}


def test_rejected_and_draft_entries_do_not_count_as_effort(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        imported = client.post("/api/v1/time-entries/import", json=json.loads(SAMPLES.read_text(encoding="utf-8")))
        assert imported.json() == {"imported": 46, "updated": 0, "unchanged": 0}

        # R1: the time entry says whether it counts, and the excluded ones can be queried.
        excluded = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": {
            "type": "filter", "objectSet": {"type": "base", "objectType": "TimeEntry"},
            "where": {"type": "eq", "field": "countsAsEffort", "value": False}}}).json()["data"]
        assert sorted(o["primaryKey"] for o in excluded) == ["TEST-WDR101-028", "TEST-WDR101-029"]
        assert {o["properties"]["approvalStatus"] for o in excluded} == {"draft", "rejected"}

        # R2: project totals leave out the draft 4 h and the rejected 8 h; the gap keeps only submitted time.
        project = client.get("/api/v1/projects/WDR101").json()["data"]
        assert project["reported_person_days"] == "41.50" and project["actual_person_days"] == "41.50"
        assert project["customer_confirmed_person_days"] == "38.50"
        assert project["confirmation_gap_person_days"] == "3.00"
        assert project["pmo_resource_summary"]["reported_person_days"] == "41.50"
        september = next(m for m in project["monthly_time_entries"] if m["month"] == "2026-09")
        assert september["entry_count"] == 34 and len(september["entries"]) == 34, "every entry stays listed"
        assert september["excluded_entry_count"] == 2 and september["excluded_person_days"] == "1.50"
        assert september["reported_person_days"] == "29.50" and september["confirmation_gap_person_days"] == "3.00"

        # R3: the same rule on the employee side and in the investment analysis.
        worktime = client.get("/api/v1/employee-worktime?period=month&month=2026-09&employee_name=李婷").json()["data"]
        assert [(row["employee_id"], row["reported_hours"]) for row in worktime] == [("E201", "136.00")]
        employee = client.get("/api/v1/ontology/objects/Employee/E201").json()["data"]["properties"]
        assert employee["reportedHours"] == "232.00" and employee["entryCount"] == 32, "the count lists every record"
        cost_object = client.get("/api/v1/investment/cost-objects/WDR101").json()["data"]
        assert cost_object["total_person_days"] == "41.50"


def test_excel_reimport_moves_approval_status_forward_only(tmp_path):
    pending = ["王殿忠", "TSG", "2026-01-13", "MKT800", "", "0.5", "返回保定", "进行中"]
    approved = pending[:7] + ["流转完成"]
    rejected = ["王殿忠", "TSG", "2026-01-14", "MKT800", "", "0.5", "现场培训", "驳回"]
    with TestClient(create_app(settings(tmp_path))) as client:
        assert upload(client, pending, rejected) == {"imported": 2, "updated": 0, "unchanged": 0}
        project = client.get("/api/v1/projects/MKT800").json()["data"]
        assert (project["reported_hours"], project["customer_confirmed_hours"]) == ("4.00", "0.00"), "rejected is not effort"

        # R4: the approval arrives with the next export.
        assert upload(client, approved) == {"imported": 0, "updated": 1, "unchanged": 0}
        project = client.get("/api/v1/projects/MKT800").json()["data"]
        assert (project["reported_hours"], project["customer_confirmed_hours"]) == ("4.00", "4.00")
        assert project["confirmation_gap_hours"] == "0.00"

        # An older export cannot undo the approval, and a rejected entry is not overwritten either.
        assert upload(client, pending) == {"imported": 0, "updated": 0, "unchanged": 1}
        assert upload(client, rejected[:7] + ["流转完成"]) == {"imported": 0, "updated": 0, "unchanged": 1}
        project = client.get("/api/v1/projects/MKT800").json()["data"]
        assert (project["reported_hours"], project["customer_confirmed_hours"]) == ("4.00", "4.00")
        statuses = sorted(e["approval_status"] for m in project["monthly_time_entries"] for e in m["entries"])
        assert statuses == ["approved", "rejected"]


def test_employee_project_effort_shares(tmp_path):
    """Ontology 7.1.0, build package docs/ontology-builds/2026-10-29-employee-project-shares.yaml."""
    def entry(key, project, day, hours, status="approved"):
        return {"external_id": f"SHARE-{key}", "project_number": project, "employee_id": "E900", "employee_name": "占比测试",
                "work_date": day, "hours": hours, "approved_hours": hours if status == "approved" else "0",
                "approval_status": status, "work_content": "占比", "source_updated_at": "2026-10-01T00:00:00+08:00"}

    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=[
            entry(1, "TPL149", "2026-09-01", "8"), entry(2, "TPL149", "2026-09-02", "8"), entry(3, "TPL149", "2026-09-03", "8", "submitted"),
            entry(4, "WDR101", "2026-09-04", "8"), entry(5, "TSG999", "2026-09-07", "8"),
            entry(6, "WDR101", "2026-09-08", "8", "rejected"),
        ]).status_code == 200
        employee = client.get("/api/v1/ontology/objects/Employee/E900").json()["data"]["properties"]
        assert employee["reportedHours"] == "40.00"
        shares = [(r["projectNumber"], r["hours"], r["sharePercent"], r["internal"]) for r in employee["projectEffortShares"]]
        assert shares == [("TPL149", "24.00", "60.0", False), ("TSG999", "8.00", "20.0", True), ("WDR101", "8.00", "20.0", False)]
        assert employee["projectEffortShares"][0]["projectName"].startswith("TPL149-")

    secure = replace(settings(tmp_path / "secure"), auth_enabled=True)
    (tmp_path / "secure").mkdir()
    with TestClient(create_app(secure)) as client:
        admin = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        assert client.post("/api/v1/time-entries/import", headers=admin, json=[entry(1, "TPL149", "2026-09-01", "8")]).status_code == 200
        client.post("/api/v1/users", headers=admin, json={"username": "pmviewer", "password": "pmviewer-pass-1", "permissions": ["projects"]})
        token = client.post("/api/v1/auth/login", json={"username": "pmviewer", "password": "pmviewer-pass-1"}).json()["data"]["token"]
        hidden = client.get("/api/v1/ontology/objects/Employee/E900", headers={"Authorization": f"Bearer {token}"}).json()["data"]
        assert hidden["properties"]["projectEffortShares"] is None and "projectEffortShares" in hidden["redactedProperties"]


def test_employee_month_period_filters_list_and_detail(tmp_path):
    """Ontology 7.2.0, build package docs/ontology-builds/2026-10-29-employee-month-period.yaml."""
    def entry(key, project, day, hours, status="approved", employee="E901"):
        return {"external_id": f"PERIOD-{key}", "project_number": project, "employee_id": employee, "employee_name": "月度测试",
                "work_date": day, "hours": hours, "approved_hours": hours if status == "approved" else "0",
                "approval_status": status, "work_content": "月度", "source_updated_at": "2026-10-01T00:00:00+08:00"}

    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=[
            entry(1, "TPL149", "2026-08-03", "8"), entry(2, "TPL149", "2026-08-04", "8"),
            entry(3, "TSG999", "2026-09-01", "8"), entry(4, "WDR101", "2026-09-02", "8"), entry(5, "WDR101", "2026-09-03", "8", "submitted"),
            entry(6, "WDR101", "2026-09-04", "8", "rejected"),
            entry(7, "TPL149", "2026-08-05", "8", employee="E902"),
        ]).status_code == 200

        # R1: the list for a month only has people with time in it, and says which months exist.
        listed = client.get("/api/v1/ontology/objects/Employee?month=2026-09&pageSize=200").json()
        keys = {o["primaryKey"] for o in listed["data"]}
        assert "E901" in keys and "E902" not in keys
        assert listed["meta"]["period"]["month"] == "2026-09" and {"2026-09", "2026-08"} <= set(listed["meta"]["period"]["options"])
        assert client.get("/api/v1/ontology/objects/Employee?month=2026-9").status_code == 422
        assert client.get("/api/v1/ontology/objects/Project?month=2026-09").status_code == 422

        # R2: month figures follow the month; cumulative ones stay.
        def employee(month):
            return client.get(f"/api/v1/ontology/objects/Employee/E901?month={month}").json()["data"]
        september, august = employee("2026-09"), employee("2026-08")
        p = september["properties"]
        assert (p["statMonth"], p["monthEntryCount"], p["monthReportedHours"], p["monthApprovedHours"], p["monthProjectCount"]) == (
            "2026-09", 4, "24.00", "16.00", 2)
        assert [(r["projectNumber"], r["sharePercent"]) for r in p["monthProjectEffortShares"]] == [("WDR101", "66.7"), ("TSG999", "33.3")]
        assert (august["properties"]["monthReportedHours"], august["properties"]["reportedHours"], p["reportedHours"]) == ("16.00", "40.00", "40.00")
        assert [(r["projectNumber"], r["sharePercent"]) for r in august["properties"]["monthProjectEffortShares"]] == [("TPL149", "100.0")]
        assert august["properties"]["monthUtilizationPercent"] is not None
        assert august["properties"]["healthReasons"]["month"] == "2026-08"

        # R3: relations follow the month.
        entries = client.get("/api/v1/ontology/objects/Employee/E901/links/timeEntries?month=2026-09&pageSize=200").json()["data"]
        assert {o["properties"]["workDate"][:7] for o in entries} == {"2026-09"} and len(entries) == 4
        cost_objects = {o["primaryKey"] for o in client.get("/api/v1/ontology/objects/Employee/E901/links/costObjects?month=2026-09").json()["data"]}
        assert cost_objects == {"TSG999", "WDR101"}, "the rejected entry does not count"
        counts = {link["apiName"]: link["count"] for link in september["links"]}
        assert counts["timeEntries"] == 4 and counts["costObjects"] == 2
        all_time = client.get("/api/v1/ontology/objects/Employee/E901").json()["data"]
        assert {link["apiName"]: link["count"] for link in all_time["links"]}["timeEntries"] == 6
        assert all_time["period"]["defaultMonth"] == all_time["properties"]["statMonth"]

        # R4: the schema tells the browser which type has a period and which properties follow it.
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        employee_type = next(t for t in schema["objectTypes"] if t["apiName"] == "Employee")
        assert employee_type["period"]["activeProperty"] == "monthEntryCount"
        assert {p["apiName"] for p in employee_type["properties"] if p["periodic"]} >= {"monthReportedHours", "healthStatus"}
        assert not any(p["periodic"] for p in employee_type["properties"] if p["apiName"] in {"reportedHours", "projectEffortShares"})


@pytest.mark.parametrize(
    ("file", "old", "new", "message"),
    [
        ("object-types/people.yaml", "activeProperty: monthEntryCount", "activeProperty: entryCount", "必须是本类型标记了 periodic 的数值属性"),
        ("object-types/people.yaml", "options: {objectType: TimeEntry, property: workDate}", "options: {objectType: TimeEntry, property: hours}", "必须指向已定义对象类型的日期属性"),
        ("object-types/crm.yaml", "source: records.status}", "source: records.status, periodic: true}", "没有声明 period"),
        ("link-types.yaml", "employeeId}, periodProperty: workDate}", "employeeId}, periodProperty: hours}", "periodProperty 'hours' 必须是 TimeEntry 的日期属性"),
        ("object-types/people.yaml", "dateProperty: workDate, defaultView", "dateProperty: workDate, activeProperty: hours, defaultView", "必须且只能写 activeProperty、dateProperty、startProperty"),
        ("object-types/people.yaml", "dateProperty: workDate, defaultView", "dateProperty: hours, defaultView", "dateProperty 'hours' 必须是本类型的日期属性"),
        ("object-types/delivery.yaml", "startProperty: plannedStart, endProperty: plannedEnd", "startProperty: plannedStart", "startProperty 与 endProperty 都必须是本类型的日期属性"),
        ("object-types/delivery.yaml", "startProperty: plannedStart, endProperty: plannedEnd", "startProperty: plannedStart, endProperty: plannedEnd, dateProperty: plannedEnd", "必须且只能写 activeProperty、dateProperty、startProperty"),
        ("object-types/people.yaml", "activeProperty: monthEntryCount", "activeProperty: monthEntryCount, range: true", "有按月计算（periodic）属性的类型不能声明 range"),
        ("object-types/delivery.yaml", "listProperties: [projectNumber, status,", "listProperties: [projectNumber, phase,", "listProperties 引用了未知属性 \\['phase'\\]"),
    ],
)
def test_invalid_period_metadata_is_rejected(tmp_path, file, old, new, message):
    metadata = tmp_path / "metadata"
    shutil.copytree(DEFAULT_METADATA_DIR, metadata)
    path = metadata / file
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(OntologyMetadataError, match=message):
        load_ontology(metadata)


def test_time_entries_are_viewed_by_month(tmp_path):
    """Ontology 7.3.0, build package docs/ontology-builds/2026-10-29-time-entry-month-period.yaml."""
    with TestClient(create_app(settings(tmp_path))) as client:
        client.post("/api/v1/time-entries/import", json=json.loads(SAMPLES.read_text(encoding="utf-8")))
        everything = client.get("/api/v1/ontology/objects/TimeEntry?pageSize=200").json()
        months = {}
        for page in range(1, everything["meta"]["totalPages"] + 1):
            for item in client.get(f"/api/v1/ontology/objects/TimeEntry?pageSize=200&page={page}").json()["data"]:
                months[item["properties"]["workDate"][:7]] = months.get(item["properties"]["workDate"][:7], 0) + 1
        period = everything["meta"]["period"]
        assert period["defaultView"] == "latest" and period["defaultMonth"] == max(months) and period["options"] == sorted(months, reverse=True)
        assert everything["meta"]["totalObjects"] == sum(months.values()), "without a month the API returns everything"

        september = client.get("/api/v1/ontology/objects/TimeEntry?month=2026-09&pageSize=200").json()
        assert september["meta"]["totalObjects"] == months["2026-09"]
        assert {o["properties"]["workDate"][:7] for o in september["data"]} == {"2026-09"}

        schema = client.get("/api/v1/ontology/schema").json()["data"]
        periods = {t["apiName"]: t["period"] for t in schema["objectTypes"] if t["period"]}
        assert periods["TimeEntry"]["dateProperty"] == "workDate" and periods["Employee"]["defaultView"] == "all"


def test_milestones_are_viewed_by_month_span(tmp_path):
    """Ontology 7.4.0, build package docs/ontology-builds/2026-10-29-milestone-span-period.yaml."""
    def milestone(name, start, end):
        response = client.post("/api/v1/ontology/actions/createMilestone/apply", json={"parameters": {
            "project": "WDR101", "name": name, "plannedStart": start, "plannedEnd": end}})
        assert response.status_code == 200, response.text

    def names(query):
        return sorted(o["properties"]["name"] for o in client.get(f"/api/v1/ontology/objects/Milestone?pageSize=200&{query}").json()["data"]
                      if o["properties"]["projectNumber"] == "WDR101")

    with TestClient(create_app(settings(tmp_path))) as client:
        milestone("项目启动", "2026-09-27", "2026-09-30")
        milestone("签约", "2026-10-30", "2026-10-31")
        milestone("验收", "2026-11-30", "2026-12-31")
        assert names("month=2026-09") == ["项目启动"]
        assert names("month=2026-10") == ["签约"]
        assert set(names("month=2026-10&monthTo=2026-11")) == {"签约", "验收"}, "a span that starts in the range counts"
        assert names("month=2026-12") == ["验收"]
        assert set(names("month=2026-09&monthTo=2026-12")) == {"签约", "验收", "项目启动"}
        assert set(names("")) == {"签约", "验收", "项目启动"}

        period = client.get("/api/v1/ontology/objects/Milestone?month=2026-09").json()["meta"]["period"]
        assert period["range"] is True and {"2026-09", "2026-10", "2026-11", "2026-12"} <= set(period["options"])
        assert client.get("/api/v1/ontology/objects/Milestone?month=2026-12&monthTo=2026-10").status_code == 422
        assert client.get("/api/v1/ontology/objects/Milestone?monthTo=2026-10").status_code == 422
        assert client.get("/api/v1/ontology/objects/Employee?month=2026-09&monthTo=2026-10").status_code == 422
        assert client.get("/api/v1/ontology/objects/Project?month=2026-09").status_code == 422, "types without a period refuse a month"

        # R4: the list shows the milestone status.
        schema = client.get("/api/v1/ontology/schema").json()["data"]
        milestone_type = next(t for t in schema["objectTypes"] if t["apiName"] == "Milestone")
        assert milestone_type["listProperties"] == ["projectNumber", "status", "sequenceNo", "plannedStart", "plannedEnd"]
