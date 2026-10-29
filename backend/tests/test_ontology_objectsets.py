from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app

from test_api import settings
from test_ontology import TIME_ENTRIES


PROJECTS = {"type": "base", "objectType": "Project"}


def where(clause, object_set=PROJECTS):
    return {"type": "filter", "objectSet": object_set, "where": clause}


def keys(client, object_set, **extra):
    response = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": object_set, **extra})
    assert response.status_code == 200, response.text
    return [o["primaryKey"] for o in response.json()["data"]]


def aggregate(client, object_set, aggregation, group_by=None, headers=None):
    return client.post(
        "/api/v1/ontology/objectSets/aggregate",
        headers=headers,
        json={"objectSet": object_set, "aggregation": aggregation, "groupBy": group_by or []},
    )


def test_where_clauses_follow_osdk_semantics(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert keys(client, PROJECTS) == ["TPL149", "WDR101", "ZMJ113"]
        assert keys(client, where({"type": "eq", "field": "contractType", "value": "construction"})) == ["TPL149"]
        assert keys(client, where({"type": "in", "field": "contractType", "value": ["sales", "open_ended"]})) == ["WDR101", "ZMJ113"]
        assert keys(client, where({"type": "gt", "field": "contractValue", "value": 20000})) == ["TPL149"]
        assert keys(client, where({"type": "gte", "field": "contractValue", "value": "20000"})) == ["TPL149", "ZMJ113"]
        assert keys(client, where({"type": "isNull", "field": "plannedPersonDays", "value": True})) == ["WDR101", "ZMJ113"]
        assert keys(client, where({"type": "startsWith", "field": "projectNumber", "value": "zmj"})) == ["ZMJ113"]
        assert keys(client, where({"type": "containsAnyTerm", "field": "subject", "value": "运维 鹏翎"})) == ["TPL149", "WDR101"]
        assert keys(client, where({"type": "containsAllTerms", "field": "subject", "value": "SRM 接口 鹏翎"})) == ["TPL149"]
        assert keys(client, where({"type": "contains", "field": "riskReasons", "value": "尚未设置里程碑"})) == ["WDR101", "ZMJ113"]
        assert keys(client, where({
            "type": "and",
            "value": [
                {"type": "eq", "field": "riskLevel", "value": "warning"},
                {"type": "not", "value": {"type": "eq", "field": "contractType", "value": "sales"}},
            ],
        })) == ["ZMJ113"]
        assert keys(client, where({
            "type": "or",
            "value": [
                {"type": "eq", "field": "riskLevel", "value": "critical"},
                {"type": "lt", "field": "contractValue", "value": 1},
            ],
        })) == ["TPL149", "WDR101"]

        ordered = keys(client, PROJECTS, orderBy={"fields": [{"field": "contractValue", "direction": "desc"}]})
        assert ordered == ["TPL149", "ZMJ113", "WDR101"]

        bad_enum = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": where({"type": "eq", "field": "riskLevel", "value": "purple"})})
        assert bad_enum.status_code == 422 and "取值必须是" in bad_enum.json()["detail"]
        bad_field = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": where({"type": "eq", "field": "nope", "value": 1})})
        assert bad_field.status_code == 422
        bad_type = client.post("/api/v1/ontology/objectSets/loadObjects", json={"objectSet": {"type": "magic"}})
        assert bad_type.status_code == 422 and "searchAround" in bad_type.json()["detail"]


def test_set_algebra_static_sets_and_search_around(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=TIME_ENTRIES).status_code == 200
        critical = where({"type": "eq", "field": "riskLevel", "value": "critical"})
        construction = where({"type": "eq", "field": "contractType", "value": "construction"})
        static = {"type": "static", "objectType": "Project", "objects": ["ZMJ113", "TPL149", "MISSING"]}
        assert keys(client, static) == ["ZMJ113", "TPL149"]
        assert keys(client, {"type": "union", "objectSets": [critical, static]}) == ["TPL149", "ZMJ113"]
        assert keys(client, {"type": "intersect", "objectSets": [static, construction]}) == ["TPL149"]
        assert keys(client, {"type": "subtract", "objectSets": [PROJECTS, critical]}) == ["WDR101", "ZMJ113"]

        team = {"type": "searchAround", "objectSet": critical, "link": "team"}
        assert sorted(keys(client, team)) == ["E001", "E002"]
        overdue_projects = {
            "type": "searchAround",
            "link": "project",
            "objectSet": where(
                {"type": "and", "value": [
                    {"type": "lt", "field": "plannedEnd", "value": "2026-09-01"},
                    {"type": "not", "value": {"type": "eq", "field": "status", "value": "completed"}},
                ]},
                {"type": "base", "objectType": "Milestone"},
            ),
        }
        assert keys(client, overdue_projects) == ["TPL149"]
        round_trip = {"type": "searchAround", "objectSet": team, "link": "workedProjects"}
        assert keys(client, round_trip) == ["TPL149"]

        mixed = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": {"type": "union", "objectSets": [PROJECTS, team]}})
        assert mixed.status_code == 422 and "同一对象类型" in mixed.json()["detail"]
        bad_link = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": {"type": "searchAround", "objectSet": PROJECTS, "link": "friends"}})
        assert bad_link.status_code == 422 and "team" in bad_link.json()["detail"]


def test_load_pagination_and_select(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        first = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": PROJECTS, "pageSize": 2, "select": ["contractType"]}).json()
        assert first["totalCount"] == 3 and len(first["data"]) == 2
        assert set(first["data"][0]["properties"]) == {"projectNumber", "subject", "contractType"}
        second = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": PROJECTS, "pageSize": 2, "pageToken": first["nextPageToken"]}).json()
        assert [o["primaryKey"] for o in second["data"]] == ["ZMJ113"]
        assert second["nextPageToken"] is None
        assert client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": PROJECTS, "pageToken": "garbage"}).status_code == 422


def test_aggregations_match_portfolio_totals(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.post("/api/v1/time-entries/import", json=TIME_ENTRIES).status_code == 200
        portfolio = client.get("/api/v1/projects").json()["summary"]

        total = aggregate(client, PROJECTS, [
            {"type": "count"},
            {"type": "sum", "field": "contractValue", "name": "contractTotal"},
            {"type": "avg", "field": "contractValue"},
            {"type": "max", "field": "contractValue"},
            {"type": "exactDistinct", "field": "riskLevel"},
        ]).json()
        metrics = {m["name"]: m["value"] for m in total["data"][0]["metrics"]}
        assert metrics == {
            "count": 3,
            "contractTotal": portfolio["contract_value"],
            "avg(contractValue)": "56633.33",
            "max(contractValue)": "149900.00",
            "exactDistinct(riskLevel)": 2,
        }

        by_type = aggregate(client, PROJECTS, [{"type": "count"}], [{"type": "exact", "field": "riskLevel"}]).json()
        assert [(row["group"]["riskLevel"], row["metrics"][0]["value"]) for row in by_type["data"]] == [
            ("warning", 2), ("critical", 1),
        ]

        reasons = aggregate(client, PROJECTS, [{"type": "count"}], [{"type": "exact", "field": "riskReasons"}]).json()
        counts = {row["group"]["riskReasons"]: row["metrics"][0]["value"] for row in reasons["data"]}
        assert counts["尚未设置里程碑"] == 2

        limited = aggregate(client, PROJECTS, [{"type": "count"}], [
            {"type": "exact", "field": "contractType", "maxGroupCount": 1}]).json()
        assert len(limited["data"]) == 1 and limited["excludedItems"] == 2

        ranges = aggregate(client, PROJECTS, [{"type": "count"}], [{"type": "ranges", "field": "contractValue", "ranges": [
            {"startValue": 0, "endValue": 50000}, {"startValue": 50000, "endValue": None}]}]).json()
        buckets = {row["group"]["contractValue"]["startValue"]: row["metrics"][0]["value"] for row in ranges["data"]}
        assert buckets == {0: 2, 50000: 1}

        widths = aggregate(client, PROJECTS, [{"type": "count"}], [
            {"type": "fixedWidth", "field": "contractValue", "fixedWidth": 100000}]).json()
        assert {row["group"]["contractValue"]["startValue"] for row in widths["data"]} == {"0.00", "100000.00"}

        hours = aggregate(client, {"type": "base", "objectType": "TimeEntry"}, [
            {"type": "sum", "field": "hours"}], [{"type": "exact", "field": "employeeName"}]).json()
        assert {row["group"]["employeeName"]: row["metrics"][0]["value"] for row in hours["data"]} == {
            "Zhang San": "16.00", "Li Si": "8.00",
        }

        assert aggregate(client, PROJECTS, [{"type": "sum", "field": "subject"}]).status_code == 422
        assert aggregate(client, PROJECTS, []).status_code == 422


def test_object_set_queries_respect_safe_policies(tmp_path):
    secure = replace(settings(tmp_path), auth_enabled=True)
    with TestClient(create_app(secure)) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        client.post("/api/v1/users", headers=admin, json={
            "username": "healthonly", "password": "health-pass-1", "permissions": ["health"]})
        health = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "healthonly", "password": "health-pass-1"}).json()["data"]["token"]}

        visible = client.post("/api/v1/ontology/objectSets/loadObjects", headers=health, json={"objectSet": PROJECTS})
        assert visible.status_code == 200
        assert all(o["properties"]["contractValue"] is None for o in visible.json()["data"])
        assert client.post("/api/v1/ontology/objectSets/loadObjects", headers=health, json={
            "objectSet": where({"type": "gt", "field": "contractValue", "value": 0})}).status_code == 403
        assert aggregate(client, PROJECTS, [{"type": "sum", "field": "contractValue"}], headers=health).status_code == 403
        assert aggregate(client, PROJECTS, [{"type": "count"}], [
            {"type": "exact", "field": "contractValue"}], headers=health).status_code == 403
        assert client.post("/api/v1/ontology/objectSets/loadObjects", headers=health, json={
            "objectSet": {"type": "searchAround", "objectSet": PROJECTS, "link": "milestones"}}).status_code == 403
        assert aggregate(client, PROJECTS, [{"type": "count"}], [
            {"type": "exact", "field": "riskLevel"}], headers=health).status_code == 200
        assert client.get("/api/v1/ontology/index", headers=health).status_code == 403


def test_object_index_caches_until_backing_data_changes(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        status = {t["objectType"]: t for t in client.get("/api/v1/ontology/index").json()["data"]["objectTypes"]}
        assert all(t["indexed"] and t["fresh"] for t in status.values()), "startup warms every object type"
        project_builds = status["Project"]["builds"]
        employee_builds = status["Employee"]["builds"]

        keys(client, PROJECTS)
        keys(client, PROJECTS)
        status = {t["objectType"]: t for t in client.get("/api/v1/ontology/index").json()["data"]["objectTypes"]}
        assert status["Project"]["builds"] == project_builds
        assert status["Project"]["hits"] >= 2

        applied = client.post("/api/v1/ontology/actions/configureProject/apply", json={
            "parameters": {"project": "ZMJ113", "projectManager": "缓存验证"}})
        assert applied.status_code == 200
        manager = client.post("/api/v1/ontology/objectSets/loadObjects", json={
            "objectSet": where({"type": "eq", "field": "projectManager", "value": "缓存验证"})}).json()
        assert [o["primaryKey"] for o in manager["data"]] == ["ZMJ113"]
        status = {t["objectType"]: t for t in client.get("/api/v1/ontology/index").json()["data"]["objectTypes"]}
        assert status["Project"]["builds"] > project_builds
        assert status["Employee"]["builds"] == employee_builds, "unrelated object types stay cached"

        client.patch("/api/v1/projects/WDR101/settings", json={"project_manager": "旧接口写入"})
        assert keys(client, where({"type": "eq", "field": "projectManager", "value": "旧接口写入"})) == ["WDR101"]

        assert client.post("/api/v1/time-entries/import", json=TIME_ENTRIES).status_code == 200
        status = {t["objectType"]: t for t in client.get("/api/v1/ontology/index").json()["data"]["objectTypes"]}
        assert status["Employee"]["fresh"] and status["Employee"]["objectCount"] == 2, "import warms the index"
        assert status["Employee"]["builds"] > employee_builds
        assert status["Contract"]["builds"] == 1

        refreshed = client.post("/api/v1/ontology/index/refresh").json()["data"]
        assert set(refreshed["rebuilt"]) == {t["objectType"] for t in refreshed["objectTypes"]}
