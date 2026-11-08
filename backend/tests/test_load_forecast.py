"""Employee load forecast that decides by itself whether it may be used (ontology 8.5.0, requirements baseline D46–D49,
build package docs/ontology-builds/2026-11-08-load-forecast.yaml).

The synthetic history alternates: every person works 10 hours a day one month (125%, over the overload line) and
4 hours the next (50%, under the attention line). 「照抄上月」 is always wrong by 75 points; a model that looks two
months back is right, so every output should pass its gate. Real-data quality is not judged here (D39).
"""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.ontology.derivations.load_forecast import build_samples, gate

from test_api import settings
from test_business_rules import entry, import_entries

MONTHS = [f"2030-{m:02d}" for m in range(1, 9)]
SEPTEMBER = date(2030, 9, 5)  # the cutoff is then 2030-08


def weekdays(month: str):
    day = date.fromisoformat(f"{month}-01")
    while day.strftime("%Y-%m") == month:
        if day.weekday() < 5:
            yield day.isoformat()
        day += timedelta(days=1)


def alternating(people=20, months=MONTHS):
    rows = []
    for p in range(people):
        for i, month in enumerate(months):
            hours = "10.00" if (i + p) % 2 == 0 else "4.00"
            rows += [entry(f"LF{p}-{day}", "TPL149", day, hours=hours, employee=f"E9{p:02d}", name=f"预测{p:02d}")
                     for day in weekdays(month)]
    return rows


def apply(client, action, parameters):
    return client.post(f"/api/v1/ontology/actions/{action}/apply", json={"parameters": parameters})


def lower_gates(client, months=6, person_months=100):
    response = apply(client, "updateOperatingPolicy", {"policy": "default", "forecastMinHistoryMonths": months,
                                                       "forecastMinPersonMonths": person_months})
    assert response.status_code == 200, response.text


def forecast(client, **params):
    response = client.post("/api/v1/ontology/functions/employeeLoadForecast/execute", json={"parameters": params})
    assert response.status_code == 200, response.text
    return response.json()["data"]["result"]


def test_too_little_history_trains_nothing_and_says_why(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, alternating(people=3, months=MONTHS[:4]))
        model = client.app.state.load_forecast.ensure_current(SEPTEMBER)
        assert model["status"] == "insufficient_data" and model["enabled_outputs"] == []
        assert "需 12" in model["notes"] and "需 600" in model["notes"], "the default gates (D47)"
        result = forecast(client)
        assert result["status"] == "insufficient_data" and result["data"] == [] and "需 12" in result["message"]


def test_a_learnable_history_passes_every_gate_and_predicts_next_month(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, alternating())
        lower_gates(client)
        model = client.app.state.load_forecast.ensure_current(SEPTEMBER)
        assert (model["status"], model["data_cutoff_month"], model["target_month"]) == ("active", "2030-08", "2030-09")
        assert set(model["enabled_outputs"]) == {"utilization", "overload", "idle"}, model["outputs"]
        assert model["backtest"]["baseline"]["mae_points"] > 70, "「照抄上月」 is wrong by 75 points every month"
        result = forecast(client)
        rows = {row["employee_name"]: row for row in result["data"]}
        assert len(rows) == 20 and result["target_month"] == "2030-09"
        # Person 00 worked 4 hours a day in August (index 7, odd): September should be a 10-hour month.
        busy = rows["预测00"]
        assert busy["current_utilization_percent"] < 60
        assert busy["predicted_utilization_percent"] > 100 and busy["overload_probability_percent"] > 50
        assert busy["interval_low_percent"] <= busy["predicted_utilization_percent"] <= busy["interval_high_percent"]
        quiet = rows["预测01"]
        assert quiet["predicted_utilization_percent"] < 70 and quiet["idle_probability_percent"] > 50
        assert busy["reasons"] and all(isinstance(r, str) for r in busy["reasons"]), "the top reasons are readable"
        assert [r["employee_name"] for r in forecast(client, employeeName="预测01")["data"]] == ["预测01"]


def test_training_runs_once_per_month_and_is_audited(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, alternating(months=MONTHS[:7]))
        lower_gates(client)
        service = client.app.state.load_forecast
        first = service.ensure_current(date(2030, 8, 3))
        assert first["data_cutoff_month"] == "2030-07"
        assert service.ensure_current(date(2030, 8, 20)) is None, "same month: no second training"
        import_entries(client, alternating(months=MONTHS))
        second = service.ensure_current(SEPTEMBER)
        assert second["version"] == first["version"] + 1 and second["data_cutoff_month"] == "2030-08"
        with client.app.state.database.connect() as connection:
            trainings = connection.execute("SELECT actor, channel, object_type FROM ontology_action_log "
                                           "WHERE action_type = 'trainLoadForecastModel'").fetchall()
        assert len(trainings) == 2 and {tuple(row) for row in trainings} == {("系统", "system", "LoadForecastModel")}
        versions = client.get("/api/v1/ontology/objects/LoadForecastModel/" + str(second["version"])).json()["data"]["properties"]
        assert versions["dataCutoffMonth"] == "2030-08" and versions["utilizationEnabled"] is True


def test_features_use_nothing_after_the_cutoff(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, alternating(people=4, months=MONTHS[:6]))
        service = client.app.state.load_forecast
        policy = client.app.state.project_repository.get_worktime_baseline()
        before = build_samples(service.metrics, policy, "2030-05")
        import_entries(client, [entry(f"LATE-{day}", "TPL149", day, hours="12.00", employee="E900", name="预测00")
                                for day in weekdays("2030-06")])
        after = build_samples(service.metrics, policy, "2030-05")
        strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]  # noqa: E731
        assert strip(before["current"]) == strip(after["current"]) and strip(before["samples"]) == strip(after["samples"])
        assert all(s["target_month"] <= "2030-05" for s in after["samples"]), "targets are only months already over"


def test_each_output_is_switched_on_by_its_own_gate():
    limits = {"forecast_min_history_months": 12, "forecast_min_person_months": 600, "forecast_mae_improvement_percent": 20,
              "forecast_min_auc": 0.75, "forecast_max_calibration_error": 0.05}
    report = {"baseline": {"mae_points": 10.0}, "positives": {"overload": 40, "idle": 3},
              "candidates": {
                  "linear": {"mae_points": 9.5, "auc_overload": 0.81, "calibration_overload": 0.04, "auc_idle": None, "calibration_idle": None},
                  "gbdt": {"mae_points": 7.9, "auc_overload": 0.78, "calibration_overload": 0.09, "auc_idle": None, "calibration_idle": None}}}
    outcome = gate(report, limits)
    assert outcome["utilization"] == {"enabled": True, "candidate": "gbdt", "reason": outcome["utilization"]["reason"]}
    assert "≤ 8.0" in outcome["utilization"]["reason"], "20% under 10 points"
    assert outcome["overload"]["enabled"] and outcome["overload"]["candidate"] == "linear", "the only one calibrated"
    assert not outcome["idle"]["enabled"] and "只有 3 个" in outcome["idle"]["reason"]
    stricter = gate(report, {**limits, "forecast_mae_improvement_percent": 25})
    assert not stricter["utilization"]["enabled"] and "未达到" in stricter["utilization"]["reason"]


def test_gates_are_operating_policy_parameters_with_ranges(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        policy = client.get("/api/v1/ontology/objects/OperatingPolicy/default").json()["data"]["properties"]
        assert (policy["forecastMinHistoryMonths"], policy["forecastMinPersonMonths"]) == (12, 600)
        assert (policy["forecastMaeImprovementPercent"], policy["forecastMinAuc"], policy["forecastMaxCalibrationError"]) == ("20.00", "0.75", "0.05")
        rejected = apply(client, "updateOperatingPolicy", {"policy": "default", "forecastMinAuc": "0.4"})
        assert rejected.status_code == 422
        assert apply(client, "updateOperatingPolicy", {"policy": "default", "forecastMinAuc": "0.8"}).status_code == 200
        assert client.app.state.project_repository.get_worktime_baseline()["forecast_min_auc"] == "0.80"


def test_the_forecast_needs_the_resource_menu(tmp_path):
    from dataclasses import replace
    with TestClient(create_app(replace(settings(tmp_path), auth_enabled=True))) as client:
        admin = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Tsnt@2026"}).json()["data"]["token"]}
        client.post("/api/v1/users", headers=admin, json={"username": "pmview", "password": "pmview-pass-1", "permissions": ["projects"]})
        viewer = {"Authorization": "Bearer " + client.post(
            "/api/v1/auth/login", json={"username": "pmview", "password": "pmview-pass-1"}).json()["data"]["token"]}
        response = client.post("/api/v1/ontology/functions/employeeLoadForecast/execute", json={"parameters": {}}, headers=viewer)
        assert response.status_code == 403
        assert client.post("/api/v1/ontology/functions/employeeLoadForecast/execute", json={"parameters": {}},
                           headers=admin).json()["data"]["result"]["status"] == "not_trained"


def test_a_changed_gate_or_a_late_import_retrains_the_same_month(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        import_entries(client, alternating(people=6, months=MONTHS))
        service = client.app.state.load_forecast
        first = service.ensure_current(SEPTEMBER)
        assert first["status"] == "insufficient_data" and service.ensure_current(SEPTEMBER) is None
        lower_gates(client)  # the admin lowers the data gate: the next hourly check retrains with it
        second = service.ensure_current(SEPTEMBER)
        assert second["version"] == first["version"] + 1 and second["thresholds"]["forecast_min_person_months"] == 100
        assert service.ensure_current(SEPTEMBER) is None
        import_entries(client, [entry("LATE-1", "TPL149", "2030-08-30", hours="3.00", employee="E950", name="补录")])
        third = service.ensure_current(SEPTEMBER)
        assert third and third["version"] == second["version"] + 1, "August hours imported late are trained on"
