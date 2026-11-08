"""Employee next-month load forecast (requirements baseline D35–D39, D46–D49; ontology 8.5.0).

The system decides by itself whether the forecast may be used (D46): at the start of each month it checks how much
history there is, runs a rolling backtest on the system's own time entries, compares each output with the gates in
the operating policy (D47) and registers a model version. Each output is switched on only when it passes its own gate
(D48): the utilization estimate on the error gate, the overload and idle probabilities on AUC and calibration.

Utilization, the overload line and the attention line come from the same resource implementation the pages use
(derivations/resources.py), so the model and the rules never disagree about what "overloaded" means.

Features use time entries up to the cutoff month only. Planned end dates of projects and milestones are their
current values: at prediction time that is what is known; in the backtest it is a small look-ahead (reported in the
build package's exception table).
"""
from __future__ import annotations

import io
import json
import pickle
import statistics
import threading
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.db import active_filter
from app.ontology.derivations.resources import ResourceMetrics, _month_end, _shift, monthly_buckets, utilization_percent

FEATURES = ("util_m", "util_m1", "util_m2", "mean3", "overtime_share", "projects", "tenure", "overload_streak",
            "internal_share", "ending_projects", "continuing_projects", "milestones_due_next", "next_workdays")
FEATURE_LABELS = {
    "util_m": ("本月利用率", "%"), "util_m1": ("上月利用率", "%"), "util_m2": ("前月利用率", "%"),
    "mean3": ("近三个月平均利用率", "%"), "overtime_share": ("加班占比", "%"), "projects": ("同期项目数", " 个"),
    "tenure": ("有工时的月数", " 个月"), "overload_streak": ("连续超过过载线", " 个月"), "internal_share": ("内部工时占比", "%"),
    "ending_projects": ("下月计划结束的项目", " 个"), "continuing_projects": ("下月仍在进行的项目", " 个"),
    "milestones_due_next": ("下月到期的里程碑", " 个"), "next_workdays": ("下月工作日", " 天"),
}
OUTPUTS = ("utilization", "overload", "idle")
OUTPUT_LABELS = {"utilization": "利用率估计", "overload": "过载概率", "idle": "闲置概率"}
BASELINE = "照抄上月"
CANDIDATES = ("linear", "gbdt")
CANDIDATE_LABELS = {"linear": "线性回归 + 逻辑回归", "gbdt": "梯度提升树"}
MIN_TRAIN_SAMPLES = 40
MIN_POSITIVES = 5  # fewer overload (or idle) cases than this in the backtest: AUC says nothing
GBDT_SETTINGS = {"max_iter": 200, "learning_rate": 0.05, "max_depth": 3, "min_samples_leaf": 10, "random_state": 0}
POLICY_GATES = {  # operating-policy column → meaning (D47)
    "forecast_min_history_months": "最少历史月数",
    "forecast_min_person_months": "最少「人 × 月」",
    "forecast_mae_improvement_percent": "误差比「照抄上月」至少低（%）",
    "forecast_min_auc": "判别 AUC 不低于",
    "forecast_max_calibration_error": "概率校准误差不超过",
}


# ------------------------------------------------------------------------------------------------ samples
def project_calendar(database: Any) -> tuple[dict[str, str], dict[str, Counter]]:
    """Planned end month per project and milestones due per project and month (live rows only)."""
    with database.connect() as connection:
        ends = {row["project_number"]: (row["planned_end"] or "")[:7]
                for row in connection.execute("SELECT project_number, planned_end FROM project_settings")}
        due: dict[str, Counter] = {}
        for row in connection.execute(
                f"SELECT project_number, planned_end, planned_date FROM milestones WHERE {active_filter('milestones')}"):
            due.setdefault(row["project_number"], Counter())[(row["planned_end"] or row["planned_date"] or "")[:7]] += 1
    return ends, due


def build_samples(metrics: ResourceMetrics, policy: dict[str, Any], cutoff: str) -> dict[str, Any]:
    """Training samples (person-months before ``cutoff`` whose next month is known) and the rows to predict
    (people with project hours in ``cutoff``, for the month after). Only time entries up to ``cutoff`` are read."""
    with metrics.database.connect() as connection:
        first = connection.execute("SELECT MIN(work_date) AS first FROM time_entries WHERE work_date <= ?",
                                   (_month_end(cutoff),)).fetchone()["first"]
    if not first:
        return {"samples": [], "current": [], "months": [], "person_months": 0}
    months = [first[:7]]
    while months[-1] < cutoff:
        months.append(_shift(months[-1], 1))
    target = _shift(cutoff, 1)
    capacity = metrics.monthly_capacity(months + [target], policy)
    hours_per_day = Decimal(policy["standard_hours_per_day"])
    people = monthly_buckets(metrics.entries(f"{months[0]}-01", _month_end(cutoff)), metrics.internal_codes())
    ends, due = project_calendar(metrics.database)
    overload = float(policy["overload_utilization_percent"])
    attention = float(policy["attention_utilization_percent"])

    def util(person: dict[str, Any], month: str) -> float:
        bucket = person["months"].get(month)
        return float(utilization_percent(bucket["project_hours"], capacity[month])) if bucket and capacity.get(month) else 0.0

    samples, current, person_months = [], [], 0
    for key, person in people.items():
        streak = tenure = 0
        for month in months:
            bucket = person["months"].get(month)
            if not bucket or bucket["project_hours"] <= 0:
                streak = 0
                continue
            person_months += 1
            tenure += 1
            u = util(person, month)
            streak = streak + 1 if u > overload else 0
            following = _shift(month, 1)
            total = bucket["project_hours"] + bucket["internal_hours"]
            recent = [util(person, _shift(month, -i)) for i in range(3)]
            row = {
                "person": key, "employee_id": person["employee_id"], "employee_name": person["employee_name"],
                "month": month, "target_month": following,
                "util_m": recent[0], "util_m1": recent[1], "util_m2": recent[2], "mean3": sum(recent) / 3,
                "overtime_share": float(bucket["overtime_hours"] / total * 100) if total else 0.0,
                "projects": len(bucket["projects"]), "tenure": tenure, "overload_streak": streak,
                "internal_share": float(bucket["internal_hours"] / total * 100) if total else 0.0,
                "ending_projects": sum(ends.get(p) == following for p in bucket["projects"]),
                # No planned end counts as still running: none left means the person is likely between projects.
                "continuing_projects": sum(not ends.get(p) or ends[p] >= following for p in bucket["projects"]),
                "milestones_due_next": sum(due.get(p, Counter())[following] for p in bucket["projects"]),
                "next_workdays": float(capacity[following] / hours_per_day) if capacity.get(following) else 0.0,
            }
            if month == cutoff:
                current.append(row)
            else:
                y = util(person, following)
                samples.append({**row, "y": y, "y_over": y > overload, "y_idle": y <= attention})
    return {"samples": samples, "current": current, "months": months, "person_months": person_months,
            "overload": overload, "attention": attention, "target_month": target}


# ------------------------------------------------------------------------------------------------ metrics
def auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if len(pos) < MIN_POSITIVES or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def calibration_error(probabilities: list[float], labels: list[bool], bins: int = 10) -> float | None:
    if not probabilities:
        return None
    total, error = len(probabilities), 0.0
    for b in range(bins):
        members = [(p, l) for p, l in zip(probabilities, labels) if b / bins <= p < (b + 1) / bins or (b == bins - 1 and p >= 1)]
        if members:
            error += len(members) / total * abs(sum(p for p, _ in members) / len(members) - sum(l for _, l in members) / len(members))
    return error


def _matrix(rows: list[dict[str, Any]]) -> list[list[float]]:
    return [[float(r[f]) for f in FEATURES] for r in rows]


def _fit(candidate: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One regressor (utilization) and two classifiers (overload, idle) of one candidate kind."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x = _matrix(rows)
    if candidate == "gbdt":
        regressor = HistGradientBoostingRegressor(loss="absolute_error", **GBDT_SETTINGS)
        make_classifier = lambda: HistGradientBoostingClassifier(**GBDT_SETTINGS)  # noqa: E731
    else:
        regressor = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        make_classifier = lambda: make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=1000))  # noqa: E731
    models: dict[str, Any] = {"utilization": regressor.fit(x, [r["y"] for r in rows])}
    for output, key in (("overload", "y_over"), ("idle", "y_idle")):
        labels = [bool(r[key]) for r in rows]
        # A training window without positives (or without negatives): the base rate is all there is to say.
        models[output] = make_classifier().fit(x, labels) if len(set(labels)) == 2 else float(sum(labels)) / len(labels)
    return models


def _predict(models: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    x = _matrix(rows)
    result = {"utilization": [max(0.0, float(v)) for v in models["utilization"].predict(x)]}
    for output in ("overload", "idle"):
        model = models[output]
        result[output] = [model] * len(rows) if isinstance(model, float) else [float(p) for p in model.predict_proba(x)[:, 1]]
    return result


def backtest(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Rolling backtest: for each month, train on what was known at that month's end, predict the next month."""
    predictions: dict[str, dict[str, list[float]]] = {c: {o: [] for o in OUTPUTS} for c in CANDIDATES}
    tested: list[dict[str, Any]] = []
    folds = 0
    for month in sorted({s["month"] for s in samples}):
        train = [s for s in samples if s["target_month"] <= month]
        test = [s for s in samples if s["month"] == month]
        if len(train) < MIN_TRAIN_SAMPLES or not test or len({s["month"] for s in train}) < 2:
            continue
        folds += 1
        tested += test
        for candidate in CANDIDATES:
            for output, values in _predict(_fit(candidate, train), test).items():
                predictions[candidate][output] += values
    labels_over = [s["y_over"] for s in tested]
    labels_idle = [s["y_idle"] for s in tested]
    actual = [s["y"] for s in tested]
    result: dict[str, Any] = {"folds": folds, "samples": len(tested),
                              "positives": {"overload": sum(labels_over), "idle": sum(labels_idle)}}
    if not tested:
        return {**result, "baseline": None, "candidates": {}}
    result["baseline"] = {"mae_points": round(statistics.mean(abs(s["util_m"] - s["y"]) for s in tested), 2)}
    result["candidates"] = {}
    for candidate in CANDIDATES:
        p = predictions[candidate]
        residuals = sorted(a - v for a, v in zip(actual, p["utilization"]))
        result["candidates"][candidate] = {
            "mae_points": round(statistics.mean(abs(a - v) for a, v in zip(actual, p["utilization"])), 2),
            "auc_overload": _round(auc(p["overload"], labels_over)),
            "calibration_overload": _round(calibration_error(p["overload"], labels_over)),
            "auc_idle": _round(auc(p["idle"], labels_idle)),
            "calibration_idle": _round(calibration_error(p["idle"], labels_idle)),
            # 10th / 90th percentile of actual − predicted: the interval shown next to the estimate.
            "residual_low": round(residuals[int(0.1 * (len(residuals) - 1))], 2),
            "residual_high": round(residuals[int(0.9 * (len(residuals) - 1))], 2),
        }
    return result


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


# ------------------------------------------------------------------------------------------------ gates
def thresholds(policy: dict[str, Any]) -> dict[str, float]:
    return {name: float(Decimal(policy[name])) for name in POLICY_GATES}


def gate(report: dict[str, Any], limits: dict[str, float]) -> dict[str, dict[str, Any]]:
    """D48: each output separately. Returns, per output, whether it is enabled, the chosen candidate and why."""
    baseline = (report.get("baseline") or {}).get("mae_points")
    candidates = report.get("candidates") or {}
    outcome: dict[str, dict[str, Any]] = {}
    # Utilization estimate: the candidate with the lowest error, if it beats 「照抄上月」 by the required margin.
    needed = None if baseline is None else round(baseline * (1 - limits["forecast_mae_improvement_percent"] / 100), 2)
    best = min(candidates, key=lambda c: candidates[c]["mae_points"], default=None)
    if best is None or needed is None:
        outcome["utilization"] = {"enabled": False, "candidate": None, "reason": "回测样本不足，无法比较"}
    else:
        error = candidates[best]["mae_points"]
        enabled = error <= needed
        outcome["utilization"] = {
            "enabled": enabled, "candidate": best if enabled else None,
            "reason": (f"{CANDIDATE_LABELS[best]}平均误差 {error} 个百分点，「照抄上月」{baseline}，"
                       f"{'达到' if enabled else '未达到'}门槛（≤ {needed}）")}
    for output in ("overload", "idle"):
        positives = report.get("positives", {}).get(output, 0)
        scored = [c for c in candidates if candidates[c][f"auc_{output}"] is not None]
        if not scored:
            outcome[output] = {"enabled": False, "candidate": None,
                               "reason": f"回测期内{'过载' if output == 'overload' else '闲置'}样本只有 {positives} 个（至少 {MIN_POSITIVES} 个），无法评估"}
            continue
        passing = [c for c in scored if candidates[c][f"auc_{output}"] >= limits["forecast_min_auc"]
                   and candidates[c][f"calibration_{output}"] is not None
                   and candidates[c][f"calibration_{output}"] <= limits["forecast_max_calibration_error"]]
        pick = max(passing or scored, key=lambda c: candidates[c][f"auc_{output}"])
        stats = candidates[pick]
        outcome[output] = {
            "enabled": bool(passing), "candidate": pick if passing else None,
            "reason": (f"{CANDIDATE_LABELS[pick]} AUC {stats[f'auc_{output}']}（需 ≥ {limits['forecast_min_auc']}），"
                       f"校准误差 {stats[f'calibration_{output}']}（需 ≤ {limits['forecast_max_calibration_error']}）")}
    return outcome


def importance(models: dict[str, Any], rows: list[dict[str, Any]], output: str) -> list[str]:
    """Features that move this output most (permutation importance on the training rows), most important first."""
    from sklearn.inspection import permutation_importance

    model = models[output]
    if isinstance(model, float):
        return []
    x = _matrix(rows)
    y = [r["y"] for r in rows] if output == "utilization" else [bool(r["y_over" if output == "overload" else "y_idle"]) for r in rows]
    scores = permutation_importance(model, x, y, n_repeats=3, random_state=0).importances_mean
    return [FEATURES[i] for i in sorted(range(len(FEATURES)), key=lambda i: -scores[i]) if scores[i] > 0]


def reasons(row: dict[str, Any], order: list[str], limit: int = 3) -> list[str]:
    def fmt(feature: str) -> str:
        label, unit = FEATURE_LABELS[feature]
        value = row[feature]
        number = f"{value:.1f}" if unit == "%" else f"{value:g}"
        return f"{label} {number}{unit}"
    # A count of zero ("下月到期的里程碑 0 个") explains nothing to a reader; percentages and day counts always do.
    shown = [f for f in order if not (FEATURE_LABELS[f][1] in (" 个",) and row[f] == 0)]
    return [fmt(f) for f in shown[:limit]]


# ------------------------------------------------------------------------------------------------ service
class LoadForecastService:
    """Trains, registers and serves the forecast. Training runs at most once per cutoff month (D46)."""

    _lock = threading.Lock()

    def __init__(self, database: Any, projects: Any, log_action: Any | None = None):
        self.database = database
        self.projects = projects
        self.metrics = ResourceMetrics(projects)
        self.log_action = log_action

    # The cutoff is the last complete calendar month that has time entries.
    def cutoff(self, today: date | None = None) -> str | None:
        today = today or self.projects.today()
        previous = _shift(today.strftime("%Y-%m"), -1)
        latest = self.metrics.latest_work_month()
        return None if latest is None else min(previous, latest)

    def versions(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM load_forecast_models ORDER BY version DESC").fetchall()
        return [self._public(dict(row)) for row in rows]

    def latest(self) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM load_forecast_models ORDER BY version DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def fingerprint(self, cutoff: str) -> str:
        """What the training saw: time entries up to the cutoff (a late import of last month changes it)."""
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n, COALESCE(SUM(CAST(hours AS REAL)), 0) AS h, MAX(source_updated_at) AS u "
                                     "FROM time_entries WHERE work_date <= ?", (_month_end(cutoff),)).fetchone()
        return f"{row['n']}|{row['h']:.2f}|{row['u'] or ''}"

    def ensure_current(self, today: date | None = None) -> dict[str, Any] | None:
        """Called at start-up and hourly. Trains when a new month's cutoff is reached, and again for the same cutoff
        when the gates in the operating policy or the time entries up to the cutoff have changed since."""
        cutoff = self.cutoff(today)
        if cutoff is None:
            return None
        latest = self.latest()
        if latest and latest["data_cutoff_month"] > cutoff:
            return None
        if latest and latest["data_cutoff_month"] == cutoff:
            same_gates = json.loads(latest["thresholds_json"] or "{}") == thresholds(self.projects.get_worktime_baseline())
            if same_gates and latest["data_fingerprint"] == self.fingerprint(cutoff):
                return None
        return self.train(cutoff, force=True)

    def train(self, cutoff: str, actor: str = "系统", force: bool = False) -> dict[str, Any]:
        with self._lock:
            latest = self.latest()
            if latest and latest["data_cutoff_month"] == cutoff and not force and latest["data_fingerprint"] == self.fingerprint(cutoff):
                return self._public(latest)  # another thread trained it meanwhile
            policy = self.projects.get_worktime_baseline()
            limits = thresholds(policy)
            data = build_samples(self.metrics, policy, cutoff)
            history_months = len({s["month"] for s in data["samples"]} | {r["month"] for r in data["current"]})
            record: dict[str, Any] = {
                "trained_at": datetime.now(timezone.utc).isoformat(), "data_cutoff_month": cutoff,
                "target_month": _shift(cutoff, 1), "history_months": history_months,
                "person_months": data["person_months"], "sample_count": len(data["samples"]),
                "thresholds_json": json.dumps(limits), "features_json": json.dumps(FEATURES),
                "library_json": json.dumps(_libraries()), "actor": actor, "data_fingerprint": self.fingerprint(cutoff),
            }
            if history_months < limits["forecast_min_history_months"] or data["person_months"] < limits["forecast_min_person_months"]:
                outcome = {o: {"enabled": False, "candidate": None, "reason": "历史数据不足"} for o in OUTPUTS}
                record.update(status="insufficient_data", backtest_json=json.dumps({}), outputs_json=json.dumps(outcome),
                              model_blob=None, notes=(f"有工时的月份 {history_months} 个（需 {limits['forecast_min_history_months']:g}），"
                                                      f"「人 × 月」{data['person_months']} 个（需 {limits['forecast_min_person_months']:g}）"))
            else:
                report = backtest(data["samples"])
                outcome = gate(report, limits)
                bundle = self._final_models(data["samples"], report, outcome)
                enabled = [o for o in OUTPUTS if outcome[o]["enabled"]]
                record.update(status="active" if enabled else "not_enabled", backtest_json=json.dumps(report),
                              outputs_json=json.dumps(outcome), model_blob=_dump(bundle) if enabled else None,
                              notes="已启用：" + "、".join(OUTPUT_LABELS[o] for o in enabled) if enabled else "回测未达到任何一项门槛")
            with self.database.connect() as connection:
                columns = list(record)
                cursor = connection.execute(
                    f"INSERT INTO load_forecast_models({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    tuple(record[c] for c in columns))
                version = cursor.lastrowid
                connection.commit()
            if self.log_action:
                self.log_action("trainLoadForecastModel", "LoadForecastModel", str(version),
                                {"dataCutoffMonth": cutoff}, actor=actor, status="succeeded", message=record["notes"],
                                edits=[{"objectType": "LoadForecastModel", "primaryKey": str(version), "operation": "create"}],
                                channel="system")
            return self._public({**record, "version": version})

    def _final_models(self, samples: list[dict[str, Any]], report: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
        """Each enabled output trained on all samples with its chosen candidate, plus its interval and reasons."""
        fitted: dict[str, dict[str, Any]] = {}
        bundle: dict[str, Any] = {"outputs": {}}
        for output in OUTPUTS:
            candidate = outcome[output]["candidate"]
            if not outcome[output]["enabled"]:
                continue
            if candidate not in fitted:
                fitted[candidate] = _fit(candidate, samples)
            entry = {"candidate": candidate, "model": fitted[candidate][output],
                     "reasons": importance(fitted[candidate], samples, output)}
            if output == "utilization":
                stats = report["candidates"][candidate]
                entry["interval"] = (stats["residual_low"], stats["residual_high"])
            bundle["outputs"][output] = entry
        return bundle

    def forecast(self, month: str | None = None) -> dict[str, Any]:
        """Predictions for the month after ``month`` (default: the latest model's cutoff), enabled outputs only."""
        latest = self.latest()
        policy = self.projects.get_worktime_baseline()
        if latest is None:
            return {"status": "not_trained", "message": "还没有训练过负荷预测模型：每月初自动训练，需要上月完整的工时。",
                    "model": None, "outputs": {}, "data": [], "thresholds": thresholds(policy)}
        cutoff = month or latest["data_cutoff_month"]
        if cutoff > latest["data_cutoff_month"]:
            raise ValueError(f"月份 {cutoff} 晚于模型的数据截止月 {latest['data_cutoff_month']}")
        model = self._public(latest)
        outputs = model["outputs"]
        result = {"status": latest["status"], "model": model, "outputs": outputs, "statistics_month": cutoff,
                  "target_month": _shift(cutoff, 1), "thresholds": model["thresholds"], "data": []}
        if not latest["model_blob"]:
            result["message"] = model["notes"]
            return result
        bundle = _load(latest["model_blob"])
        rows = build_samples(self.metrics, policy, cutoff)["current"]
        x = _matrix(rows)
        values = {output: [max(0.0, float(v)) for v in entry["model"].predict(x)] if output == "utilization"
                  else _probabilities(entry["model"], rows) for output, entry in bundle["outputs"].items()}
        order = next((entry["reasons"] for entry in bundle["outputs"].values() if entry["reasons"]), [])
        for index, row in enumerate(rows):
            item: dict[str, Any] = {"employee_id": row["employee_id"], "employee_name": row["employee_name"],
                                    "current_utilization_percent": round(row["util_m"], 1), "reasons": reasons(row, order)}
            if "utilization" in values:
                estimate = values["utilization"][index]
                low, high = bundle["outputs"]["utilization"]["interval"]
                item.update(predicted_utilization_percent=round(estimate, 1),
                            interval_low_percent=round(max(0.0, estimate + low), 1), interval_high_percent=round(estimate + high, 1))
            if "overload" in values:
                item["overload_probability_percent"] = round(values["overload"][index] * 100, 1)
            if "idle" in values:
                item["idle_probability_percent"] = round(values["idle"][index] * 100, 1)
            result["data"].append(item)
        result["data"].sort(key=lambda r: (-(r.get("overload_probability_percent") or 0), -(r.get("predicted_utilization_percent") or 0),
                                           r["employee_name"] or ""))
        return result

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        outputs = json.loads(row.get("outputs_json") or "{}")
        return {
            "version": row.get("version"), "trained_at": row.get("trained_at"), "status": row.get("status"),
            "data_cutoff_month": row.get("data_cutoff_month"), "target_month": row.get("target_month"),
            "history_months": row.get("history_months"), "person_months": row.get("person_months"),
            "sample_count": row.get("sample_count"), "outputs": outputs,
            "enabled_outputs": [o for o in OUTPUTS if outputs.get(o, {}).get("enabled")],
            "backtest": json.loads(row.get("backtest_json") or "{}"), "thresholds": json.loads(row.get("thresholds_json") or "{}"),
            "features": json.loads(row.get("features_json") or "[]"), "libraries": json.loads(row.get("library_json") or "{}"),
            "notes": row.get("notes"), "actor": row.get("actor"),
        }


def _probabilities(model: Any, rows: list[dict[str, Any]]) -> list[float]:
    return [model] * len(rows) if isinstance(model, float) else [float(p) for p in model.predict_proba(_matrix(rows))[:, 1]]


def _libraries() -> dict[str, str]:
    import sklearn
    return {"scikit-learn": sklearn.__version__}


def _dump(bundle: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    pickle.dump(bundle, buffer)
    return buffer.getvalue()


def _load(blob: bytes) -> dict[str, Any]:
    # Only this service writes the blob, into the system's own database (build package exception R2).
    return pickle.loads(blob)  # noqa: S301
