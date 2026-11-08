"""Three-layer evaluation: ontology rules, small model, large model, on the same data.

Each layer is measured on what it is responsible for (docs/ontology-design/2026-11-01-small-model-pilot.md, section 1):

1. Ontology rules (``rules``): definitions and judgements. Are the designed demo scenarios judged as designed
   (project risk and alerts, people's load, department KPIs, strategy signals, opportunity outcomes), and how fast
   are the ontology functions?
2. Small model (``small``): prediction. A rolling backtest of the next-month load forecast: the two naive baselines
   against a linear regression and logistic regressions written in plain Python (no new dependencies).
3. Large model (``llm``): understanding questions and explaining conclusions. PM Assistant answers the questions in
   scripts/eval_cases.py through the real model service; each answer is scored against the ontology's own answer.

The source database is never written: the script copies it into a temporary folder first (PM Assistant writes its
audit log there). Run from backend/:

    .venv/bin/python scripts/eval_three_layers.py --database data/preview.sqlite3 --layers rules,small
    .venv/bin/python scripts/eval_three_layers.py --database data/preview.sqlite3 \\
        --models cloudflare:@cf/moonshotai/kimi-k2.6,cloudflare:@cf/zai-org/glm-5.3 --output ../docs/test-reports

Large-model runs call the real service named by each ``provider:model`` and use the credentials in .env.local. The
API token is never printed. There is no fallback: a model that fails is reported as failed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import dataclasses
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent))
sys.path.insert(0, str(SCRIPTS))

import demo_people  # noqa: E402
import demo_projects as dp  # noqa: E402
from eval_cases import ADMIN, LLM_CASES, Facts, score_answer  # noqa: E402

CURRENT = contextvars.ContextVar("eval_record", default=None)


# ---------------------------------------------------------------------------------------------- setup
def open_ontology(database: Path, workdir: Path):
    """The application's services on a private copy of ``database``, with authentication off."""
    copy = workdir / "eval.sqlite3"
    shutil.copy2(database, copy)
    os.environ.update({"XTCRM_DATABASE_PATH": str(copy), "TSG_PM_AUTH_ENABLED": "false",
                       "XTCRM_AUTO_IMPORT_ON_EMPTY": "false"})
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    client.__enter__()  # runs the lifespan: schema migration, version registration, index warm-up
    return app, client


class Timer:
    def __init__(self):
        self.samples: dict[str, list[float]] = defaultdict(list)

    def wrap(self, ontology) -> Callable[[dict[str, Any], str, dict[str, Any]], Any]:
        def call(user: dict[str, Any], name: str, params: dict[str, Any]) -> Any:
            start = time.perf_counter()
            result = ontology.execute_function(user, name, params)["result"]
            self.samples[name].append((time.perf_counter() - start) * 1000)
            return result
        return call


def ms(values: list[float]) -> str:
    return f"{statistics.median(values):.0f}" if values else "—"


# ---------------------------------------------------------------------------------------------- layer 1
def check(results: list[dict[str, Any]], group: str, item: str, expected: Any, actual: Any, ok: bool | None = None) -> None:
    results.append({"group": group, "item": item, "expected": expected, "actual": actual,
                    "passed": (expected == actual) if ok is None else ok})


def run_rules(ontology) -> dict[str, Any]:
    timer = Timer()
    call = timer.wrap(ontology)
    facts = Facts(call)
    results: list[dict[str, Any]] = []
    projects = {r["project_number"]: r for r in facts.projects}
    alerts = {r["project_number"]: {a["code"] for a in r["anomaly_alerts"]} for r in facts.health}

    # Risk alerts: every designed project scenario (scripts/demo_projects.py).
    for project in dp.PROJECTS:
        row = projects.get(project.code) or {}
        for label, expected, actual in (("风险等级", project.risk, row.get("risk_level")),
                                        ("生命周期", project.lifecycle, row.get("lifecycle_status")),
                                        ("生命周期来源", project.lifecycle_source, row.get("lifecycle_source")),
                                        ("收入确认口径", project.settlement, row.get("revenue_settlement_basis"))):
            if expected:
                check(results, "风险预警", f"{project.code} {label}", expected, actual)
        if project.alerts:
            have = alerts.get(project.code, set())
            check(results, "风险预警", f"{project.code} 预警", sorted(project.alerts), sorted(have & set(project.alerts)),
                  set(project.alerts) <= have)

    # Load assessment: every designed person (scripts/demo_people.py), for the check month.
    month = demo_people.CHECK_MONTH
    health = {r["employee_id"]: r["statuses"] for r in call(ADMIN, "resourceHealth", {"month": month})["data"]}
    assessment = {r["employee_id"]: r["status"] for r in call(ADMIN, "employeeWorktimeAssessment", {"period": "month", "month": month})["data"]}
    operations = {r["employee_id"]: r["allocation_status"]
                  for r in call(ADMIN, "resourceOperations", {"month": month, "pageSize": 200})["data"]}
    for person in demo_people.PEOPLE:
        check(results, "负荷评估", f"{person.employee_id} 资源健康", person.health, health.get(person.employee_id))
        if person.assessment:
            check(results, "负荷评估", f"{person.employee_id} 工时评价", person.assessment, assessment.get(person.employee_id))
        if person.staffing:
            check(results, "负荷评估", f"{person.employee_id} 调剂建议", person.staffing, operations.get(person.employee_id))

    # Operating analysis: the department targets as designed (docs/test-data/project-scenarios.md) and opportunities.
    for name, expected in (("new_contracts", "on_track"), ("collections", "behind"), ("operating_cost", "over_budget")):
        check(results, "经营分析", f"部门指标 {facts.kpi(name)['label']}", expected, facts.kpi(name)["status"])
    check(results, "经营分析", "经营成本缺失月份", ["2026-07"], facts.review["operating"]["missing_cost_months"])
    analysis = call(ADMIN, "portfolioInvestmentAnalysis", {})
    outcomes = {row["code"]: row["outcome"] for key in ("open_opportunities", "sunk_items") for row in analysis[key]}
    for opportunity in dp.OPPORTUNITIES:
        if opportunity.outcome == "won" or (opportunity.code == "XNY307"):
            continue  # won: acquisition cost of the project; XNY307 has no effort, so it is not an investment
        check(results, "经营分析", f"{opportunity.code} 商机结论", opportunity.outcome, outcomes.get(opportunity.code))

    # Strategy signals: consistent with the facts they cite, and in priority order.
    signals = facts.review["signals"]
    check(results, "策略预警", "信号按优先级排列", sorted(s["priority"] for s in signals), [s["priority"] for s in signals])
    check(results, "策略预警", "有调整信号时不出现「保持」", False,
          any(s["signal"] == "hold" for s in signals) and len(signals) > 1)
    push = facts.signal("push_acceptance")
    overdue_payments = facts.alerts("PAYMENT_DUE", overdue_only=True)
    from eval_cases import _codes
    check(results, "策略预警", "「推动验收回款」= 回款逾期的项目", sorted(overdue_payments),
          sorted(_codes(push["evidence"]) if push else set()))
    exceeded = {r["project_number"] for r in facts.projects if float(r.get("budget_utilization") or 0) > 100}
    control = facts.signal("control_cost")
    need_control = len(exceeded) >= int(Decimal(ontology.projects.get_worktime_baseline()["control_cost_exceeded_projects"]))
    check(results, "策略预警", f"「控制成本」触发与超预算项目数一致（{len(exceeded)} 个超预算）", need_control,
          bool(control and any("超标" in e for e in control["evidence"])))

    # Speed: each function once more, five times, after the warm-up above.
    for name, params in (("projectList", {"pageSize": 100}), ("portfolioHealth", {}), ("departmentOperatingReview", {}),
                         ("resourceHealth", {}), ("resourceForecast", {}), ("projectOverview", {"project": "SEY101"}),
                         ("projectRiskAssessment", {"project": "SEY101"})):
        for _ in range(5):
            call(ADMIN, name, params)
    by_group: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_group[r["group"]]["total"] += 1
        by_group[r["group"]]["passed"] += r["passed"]
    return {"checks": results, "groups": {g: dict(c) for g, c in by_group.items()},
            "latency_ms": {name: {"first": round(v[0], 1), "median": round(statistics.median(v), 1), "calls": len(v)}
                           for name, v in timer.samples.items()},
            "facts": facts}


# ---------------------------------------------------------------------------------------------- layer 2
# Features and samples come from the system's own implementation (app/ontology/derivations/load_forecast.py, D46), so
# this evaluation and the in-system gate judge the same thing; only the extra comparisons below live here.
from app.ontology.derivations.load_forecast import FEATURES  # noqa: E402


def load_samples(ontology, facts: Facts, today: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One sample per person-month with project hours whose following month is complete (see build_samples)."""
    from app.ontology.derivations.load_forecast import build_samples
    from app.ontology.derivations.resources import ResourceMetrics, _month_end, _shift

    metrics = ResourceMetrics(ontology.projects)
    policy = ontology.projects.get_worktime_baseline()
    last = metrics.latest_work_month()
    last_complete = last if _month_end(last) < today.isoformat() else _shift(last, -1)
    data = build_samples(metrics, policy, last_complete)
    return data["samples"], {"months": data["months"], "overload": data["overload"], "attention": data["attention"],
                             "last_complete": last_complete}


def _standardize(train: list[list[float]]) -> tuple[list[float], list[float]]:
    columns = list(zip(*train))
    means = [sum(c) / len(c) for c in columns]
    stds = [math.sqrt(sum((x - m) ** 2 for x in c) / len(c)) or 1.0 for c, m in zip(columns, means)]
    return means, stds


def _scale(rows: list[list[float]], means: list[float], stds: list[float]) -> list[list[float]]:
    return [[1.0] + [(x - m) / s for x, m, s in zip(row, means, stds)] for row in rows]


def ridge_fit(x: list[list[float]], y: list[float], alpha: float = 1.0) -> list[float]:
    """Solve (XᵀX + αI)w = Xᵀy by Gaussian elimination; the intercept is not penalized."""
    k = len(x[0])
    a = [[sum(r[i] * r[j] for r in x) + (alpha if i == j and i else 0.0) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * t for r, t in zip(x, y)) for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(a[r][col]))
        a[col], a[pivot], b[col], b[pivot] = a[pivot], a[col], b[pivot], b[col]
        for row in range(k):
            if row != col and a[col][col]:
                factor = a[row][col] / a[col][col]
                a[row] = [v - factor * p for v, p in zip(a[row], a[col])]
                b[row] -= factor * b[col]
    return [b[i] / a[i][i] if a[i][i] else 0.0 for i in range(k)]


def logistic_fit(x: list[list[float]], y: list[bool], steps: int = 400, rate: float = 0.3, l2: float = 0.05) -> list[float]:
    w = [0.0] * len(x[0])
    for _ in range(steps):
        grad = [0.0] * len(w)
        for row, target in zip(x, y):
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, sum(a * b for a, b in zip(w, row))))))
            for i, value in enumerate(row):
                grad[i] += (p - target) * value
        w = [wi - rate * (g / len(x) + (l2 * wi if i else 0.0)) for i, (wi, g) in enumerate(zip(w, grad))]
    return w


def predict(w: list[float], row: list[float]) -> float:
    return sum(a * b for a, b in zip(w, row))


def auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def calibration_error(probabilities: list[float], labels: list[bool], bins: int = 10) -> float | None:
    if not probabilities:
        return None
    total, error = len(probabilities), 0.0
    for b in range(bins):
        members = [(p, l) for p, l in zip(probabilities, labels) if b / bins <= p < (b + 1) / bins or (b == bins - 1 and p == 1)]
        if members:
            error += len(members) / total * abs(sum(p for p, _ in members) / len(members) - sum(l for _, l in members) / len(members))
    return error


LINEAR = "线性回归 + 逻辑回归"
GBDT = "梯度提升树"
LEARNED = (LINEAR, GBDT)


def _gbdt_fit(train: list[dict[str, Any]]):
    """Gradient-boosted trees (scikit-learn, optional: requirements-ml.txt): a regressor for next month's
    utilization and two classifiers for overload and idle. Trees need no scaling and capture rules such as
    "no project continues next month, so utilization drops to zero" that a linear model cannot."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

    x = [[s[f] for f in FEATURES] for s in train]
    settings = {"max_iter": 200, "learning_rate": 0.05, "max_depth": 3, "min_samples_leaf": 10, "random_state": 0}
    regressor = HistGradientBoostingRegressor(loss="absolute_error", **settings).fit(x, [s["y"] for s in train])

    def classifier(labels: list[bool]):
        if len(set(labels)) < 2:  # a fold without positives: the base rate is all there is to say
            rate = float(sum(labels)) / len(labels)
            return lambda rows: [rate] * len(rows)
        model = HistGradientBoostingClassifier(**settings).fit(x, labels)
        return lambda rows: [float(p) for p in model.predict_proba(rows)[:, 1]]

    over, idle = classifier([s["y_over"] for s in train]), classifier([s["y_idle"] for s in train])

    def predict_rows(test: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
        rows = [[s[f] for f in FEATURES] for s in test]
        return list(zip((float(v) for v in regressor.predict(rows)), over(rows), idle(rows)))
    return predict_rows


def _sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def run_small(ontology, facts: Facts, today: date, min_train: int = 40) -> dict[str, Any]:
    start = time.perf_counter()
    samples, meta = load_samples(ontology, facts, today)
    prepared = time.perf_counter()
    overload, attention = meta["overload"], meta["attention"]
    test_months = sorted({s["month"] for s in samples})
    predictions: dict[str, list[tuple[float, float, dict[str, Any]]]] = defaultdict(list)  # model → (value, prob, sample)
    fit_ms: dict[str, list[float]] = defaultdict(list)
    use_gbdt = _sklearn_available()
    folds = 0
    for month in test_months:
        train = [s for s in samples if s["target_month"] <= month]
        test = [s for s in samples if s["month"] == month]
        if len(train) < min_train or not test:
            continue
        folds += 1
        for s in test:
            predictions["照抄上月"].append((s["util_m"], float(s["util_m"] > overload), s))
            predictions["最近三个月平均"].append((s["mean3"], float(s["mean3"] > overload), s))
        t0 = time.perf_counter()
        raw = [[s[f] for f in FEATURES] for s in train]
        means, stds = _standardize(raw)
        x = _scale(raw, means, stds)
        w_util = ridge_fit(x, [s["y"] for s in train])
        w_over = logistic_fit(x, [s["y_over"] for s in train])
        w_idle = logistic_fit(x, [s["y_idle"] for s in train])
        xt = _scale([[s[f] for f in FEATURES] for s in test], means, stds)
        for row, s in zip(xt, test):
            p_over = 1 / (1 + math.exp(-max(-30.0, min(30.0, predict(w_over, row)))))
            p_idle = 1 / (1 + math.exp(-max(-30.0, min(30.0, predict(w_idle, row)))))
            predictions[LINEAR].append((predict(w_util, row), p_over, {**s, "p_idle": p_idle}))
        fit_ms[LINEAR].append((time.perf_counter() - t0) * 1000)
        if use_gbdt:
            t0 = time.perf_counter()
            for (value, p_over, p_idle), s in zip(_gbdt_fit(train)(test), test):
                predictions[GBDT].append((value, p_over, {**s, "p_idle": p_idle}))
            fit_ms[GBDT].append((time.perf_counter() - t0) * 1000)
    models = {}
    by_year: dict[str, dict[str, Any]] = defaultdict(dict)
    for name, rows in predictions.items():
        probabilistic = name in LEARNED
        for year in sorted({s["month"][:4] for _, _, s in rows}):
            part = [(v, p, s) for v, p, s in rows if s["month"].startswith(year)]
            by_year[year][name] = {
                "samples": len(part), "mae_points": round(statistics.mean(abs(v - s["y"]) for v, _, s in part), 2),
                "auc_overload": _round(auc([p if probabilistic else v for v, p, _ in part], [s["y_over"] for _, _, s in part])),
                "positives_overload": sum(s["y_over"] for _, _, s in part)}
        errors = [abs(v - s["y"]) for v, _, s in rows]
        labels_over = [s["y_over"] for _, _, s in rows]
        labels_idle = [s["y_idle"] for _, _, s in rows]
        over_scores = [p for _, p, _ in rows] if probabilistic else [v for v, _, _ in rows]
        idle_scores = [s["p_idle"] for _, _, s in rows] if probabilistic else [-v for v, _, _ in rows]
        models[name] = {
            "samples": len(rows), "mae_points": round(statistics.mean(errors), 2) if errors else None,
            "auc_overload": _round(auc(over_scores, labels_over)), "auc_idle": _round(auc(idle_scores, labels_idle)),
            "calibration_overload": _round(calibration_error([p for _, p, _ in rows], labels_over)) if probabilistic else None,
            "positives_overload": sum(labels_over), "positives_idle": sum(labels_idle),
            "fit_ms_per_fold": round(statistics.mean(fit_ms[name]), 1) if fit_ms.get(name) else None,
        }
    baseline = (models.get("照抄上月") or {}).get("mae_points")
    gates = {}
    for name in LEARNED:
        model = models.get(name) or {}
        if baseline and model.get("mae_points") is not None:
            gates[name] = {
                "误差比「照抄上月」低 20% 以上": model["mae_points"] <= baseline * 0.8,
                "过载判别 AUC ≥ 0.75": (model["auc_overload"] or 0) >= 0.75,
                "校准误差 ≤ 0.05": model["calibration_overload"] is not None and model["calibration_overload"] <= 0.05,
            }
    return {"samples": len(samples), "folds": folds, "months": meta["months"], "models": models, "gates": gates,
            "by_year": by_year, "gbdt": use_gbdt,
            "prepare_ms": round((prepared - start) * 1000),
            "fit_ms_per_fold": (models.get(LINEAR) or {}).get("fit_ms_per_fold"),
            "overload_line": overload, "attention_line": attention}


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


# ---------------------------------------------------------------------------------------------- layer 3
def install_recorders():
    from app import pm_assistant

    class RecordingSession(pm_assistant.AssistantSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.outputs: list[tuple[str, str]] = []
            record = CURRENT.get()
            if record is not None:
                record["session"] = self

        def run(self, name: str, raw_arguments: str) -> str:
            output = super().run(name, raw_arguments)
            self.outputs.append((name, output))
            return output

    pm_assistant.AssistantSession = RecordingSession
    return pm_assistant


def recording_provider(base: type) -> type:
    class Recording(base):
        async def _post(self, url, token, payload):
            data = await super()._post(url, token, payload)
            record = CURRENT.get()
            if record is not None:
                usage = data.get("usage") or (data.get("result") or {}).get("usage") or {}
                record["requests"] += 1
                record["input_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                record["output_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            return data
    return Recording


def cloudflare_prices(settings) -> dict[str, tuple[float, float]]:
    """Live per-million-token prices from the account's model catalog (input, output)."""
    import httpx

    if not (settings.cloudflare_account_id and settings.cloudflare_api_token):
        return {}
    try:
        response = httpx.get(
            f"https://api.cloudflare.com/client/v4/accounts/{settings.cloudflare_account_id}/ai/models/search",
            params={"task": "Text Generation", "per_page": 200},
            headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"}, timeout=20)
        models = response.json().get("result") or []
    except Exception:  # noqa: BLE001 - prices are informative only
        return {}
    prices = {}
    for model in models:
        values = {p["property_id"]: p["value"] for p in model.get("properties", [])}
        units = {item["unit"]: float(item["price"]) for item in values.get("price") or [] if isinstance(item, dict)}
        if "per M input tokens" in units and "per M output tokens" in units:
            prices[model["name"]] = (units["per M input tokens"], units["per M output tokens"])
    return prices


async def run_llm(app, facts: Facts, specs: list[str], concurrency: int, timeout: float, repeat: int) -> dict[str, Any]:
    pm_assistant = install_recorders()
    base_settings = app.state.settings
    prices = cloudflare_prices(base_settings)
    truths = {case.case_id: case.truth(facts) for case in LLM_CASES}
    runs: dict[str, Any] = {}
    for spec in specs:
        provider_name, _, model = spec.partition(":")
        settings = dataclasses.replace(base_settings, pm_assistant_provider=provider_name, pm_assistant_model=model,
                                       pm_assistant_timeout_seconds=timeout)
        service = pm_assistant.PmAssistantService(settings, app.state.ontology_service, app.state.database)
        base = pm_assistant.PROVIDERS.get(provider_name)
        if base is None:
            runs[spec] = {"error": f"未知的模型服务 {provider_name}"}
            continue
        service.provider = recording_provider(base)(settings)
        if not service.provider.configured():
            runs[spec] = {"error": f"{provider_name} 未配置凭据"}
            continue
        gate = asyncio.Semaphore(concurrency)

        async def one(case, attempt: int) -> dict[str, Any]:
            async with gate:
                record = {"session": None, "requests": 0, "input_tokens": 0, "output_tokens": 0}
                CURRENT.set(record)
                start = time.perf_counter()
                answer, error = "", None
                try:
                    answer = (await service.answer(case.user, case.question))["answer"]
                except Exception as exc:  # noqa: BLE001 - a failed answer is a result, not a crash
                    error = str(exc) or exc.__class__.__name__
                seconds = time.perf_counter() - start
                session = record["session"]
                tool_errors = 0
                for _, output in (session.outputs if session else []):
                    try:
                        tool_errors += isinstance(json.loads(output), dict) and "error" in json.loads(output)
                    except ValueError:
                        tool_errors += output.startswith("{\"error\"")
                score = score_answer(answer, truths[case.case_id], case.question, facts.employee_names) if not error else \
                    {"score": 0.0, "passed": False, "recall": 0.0, "missing": [], "extra": [], "keywords_missing": [], "forbidden": []}
                return {"case": case.case_id, "group": case.group, "attempt": attempt, "seconds": round(seconds, 2),
                        "error": error, "answer": answer, "tools": [c["tool"] for c in (session.calls if session else [])],
                        "tool_calls": [c for c in (session.calls if session else [])], "tool_errors": tool_errors,
                        "requests": record["requests"], "input_tokens": record["input_tokens"],
                        "output_tokens": record["output_tokens"], **score}

        started = time.perf_counter()
        results = await asyncio.gather(*(one(case, attempt) for attempt in range(repeat) for case in LLM_CASES))
        price = prices.get(model)
        tokens_in = sum(r["input_tokens"] for r in results)
        tokens_out = sum(r["output_tokens"] for r in results)
        runs[spec] = {
            "provider": provider_name, "model": model, "results": results, "wall_seconds": round(time.perf_counter() - started, 1),
            "price_per_million": price,
            "cost_usd": round(tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1], 4) if price else None,
        }
        passed = sum(r["passed"] for r in results)
        print(f"  {spec}: {passed}/{len(results)} passed, {runs[spec]['wall_seconds']}s", flush=True)
    return {"runs": runs, "truths": {k: {"note": v.note, "entities": sorted(v.entities)} for k, v in truths.items()}}


# ---------------------------------------------------------------------------------------------- report
def pct(part: float, whole: float) -> str:
    return f"{part / whole * 100:.0f}%" if whole else "—"


def report(rules: dict | None, small: dict | None, llm: dict | None, database: Path, today: date) -> str:
    lines = ["# 三层评测结果：本体规则、小模型、大模型", "",
             f"评测日期 {today.isoformat()}；数据库 `{database.name}`（复制后评测，原文件不写入）。", ""]
    if rules:
        checks = rules["checks"]
        lines += ["## 1. 本体规则层", "",
                  f"对照演示数据的设计场景逐项核对，共 {len(checks)} 项，通过 {sum(c['passed'] for c in checks)} 项。", "",
                  "| 场景 | 核对项 | 通过 | 通过率 |", "| --- | --- | --- | --- |"]
        for group, counts in rules["groups"].items():
            lines.append(f"| {group} | {counts['total']} | {counts['passed']} | {pct(counts['passed'], counts['total'])} |")
        failed = [c for c in checks if not c["passed"]]
        if failed:
            lines += ["", "未通过的核对项：", "", "| 场景 | 核对项 | 设计结果 | 实际结果 |", "| --- | --- | --- | --- |"]
            lines += [f"| {c['group']} | {c['item']} | {c['expected']} | {c['actual']} |" for c in failed]
        lines += ["", "函数耗时（毫秒，进程内调用，对象索引已预热）：", "", "| 函数 | 首次 | 中位数 | 调用次数 |", "| --- | --- | --- | --- |"]
        lines += [f"| {name} | {v['first']} | {v['median']} | {v['calls']} |" for name, v in sorted(rules["latency_ms"].items())]
        lines.append("")
    if small:
        lines += ["## 2. 小模型层：员工下月负荷预测（滚动回测）", "",
                  f"样本 {small['samples']} 个「人 × 月」，回测 {small['folds']} 个月份；过载线 {small['overload_line']}%，关注线 {small['attention_line']}%。"
                  f"准备数据 {small['prepare_ms']} 毫秒；每个回测月份的训练与预测耗时见表。", "",
                  "| 模型 | 预测样本 | 利用率平均误差（百分点） | 过载判别 AUC | 闲置判别 AUC | 过载概率校准误差 | 过载 / 闲置正样本 | 训练与预测（毫秒） |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, m in small["models"].items():
            lines.append(f"| {name} | {m['samples']} | {m['mae_points']} | {_fmt(m['auc_overload'])} | {_fmt(m['auc_idle'])} | "
                         f"{_fmt(m['calibration_overload'])} | {m['positives_overload']} / {m['positives_idle']} | {_fmt(m.get('fit_ms_per_fold'))} |")
        gates = small["gates"]
        if gates and not isinstance(next(iter(gates.values())), dict):  # results saved before per-model gates
            gates = {LINEAR: gates}
        for name, checks in gates.items():
            lines += ["", f"接入门槛（设计稿第 5 节），{name}：" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()) + "。"]
        if small.get("gbdt") is False:
            lines += ["", "梯度提升树未评测：没有安装 scikit-learn（`pip install -r requirements-ml.txt`）。"]
        if small.get("by_year"):
            lines += ["", "按预测月份所在年度拆开（误差，百分点 / 过载判别 AUC / 过载正样本）：", "",
                      "| 年度 | " + " | ".join(small["models"]) + " |", "| --- | " + " | ".join("---" for _ in small["models"]) + " |"]
            for year, row in small["by_year"].items():
                lines.append(f"| {year} | " + " | ".join(
                    f"{row[n]['mae_points']} / {_fmt(row[n]['auc_overload'])} / {row[n]['positives_overload']}" if n in row else "—"
                    for n in small["models"]) + " |")
        lines += ["", "演示数据由模拟生成：2026 年是按规则边界设计的固定工时，2025 年历史按里程碑赶工、项目间空档、休假和随机波动生成"
                  "（scripts/demo_history.py）。成绩说明管道和特征能否学到这些规律，不代表模型在真实数据上的效果。", ""]
    if llm:
        lines += ["## 3. 大模型层：PM Assistant 问答", "",
                  "每题的标准答案由本体函数现场算出；「通过」要求答出全部应答对象、不多报、命中全部关键数字或结论、不出现禁止内容。"
                  "「作答」不含因模型服务额度用完而没有送达模型的题；通过率按作答题数计算。", "",
                  "| 模型 | 作答 | 通过 | 通过率 | 平均得分 | 问答查询 | 经营分析 | 风险预警 | 策略预警 | 权限与范围 | 中位耗时（秒） | 工具调用 / 出错 | 多报对象 | 输入 / 输出 token | 费用（美元） |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        groups = ["问答查询", "经营分析", "风险预警", "策略预警", "权限与范围"]
        for spec, run in llm["runs"].items():
            if "error" in run:
                lines.append(f"| {spec} | {run['error']} |" + " |" * 14)
                continue
            rs = [r for r in run["results"] if not _unanswered(r)]
            if not rs:  # every request failed (e.g. the daily quota ran out): report why instead of crashing
                reasons = sorted({r["error"] for r in run["results"] if r.get("error")})
                lines.append(f"| `{run['model']}` | 0/{len(run['results'])} 作答：{'；'.join(reasons)[:200]} |" + " |" * 14)
                continue
            by ={g: [r for r in rs if r["group"] == g] for g in groups}
            passed = sum(r["passed"] for r in rs)
            lines.append(
                f"| `{run['model']}` | {len(rs)}/{len(run['results'])} | {passed} | {pct(passed, len(rs))} | "
                f"{statistics.mean(r['score'] for r in rs):.2f} | "
                + " | ".join(f"{sum(r['passed'] for r in by[g])}/{len(by[g])}" for g in groups)
                + f" | {statistics.median(r['seconds'] for r in rs):.1f} | {sum(len(r['tools']) for r in rs)} / {sum(r['tool_errors'] for r in rs)}"
                f" | {sum(len(r['extra']) for r in rs)} | {sum(r['input_tokens'] for r in rs)} / {sum(r['output_tokens'] for r in rs)}"
                f" | {_fmt(run['cost_usd'])} |")
        lines += ["", "逐题结果（✓ 通过；✗ 后为缺少的内容）：", "",
                  "| 题号 | 问题 | 本体答案 | " + " | ".join(f"`{r['model'].split('/')[-1]}`" for r in llm["runs"].values() if "results" in r) + " |",
                  "| --- | --- | --- | " + " | ".join("---" for r in llm["runs"].values() if "results" in r) + " |"]
        for case in LLM_CASES:
            cells = []
            for run in llm["runs"].values():
                if "results" not in run:
                    continue
                r = next(x for x in run["results"] if x["case"] == case.case_id)
                cells.append("✓" if r["passed"] else "✗ " + _why(r))
            lines.append(f"| {case.case_id} | {case.question} | {llm['truths'][case.case_id]['note']} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "—" if value is None else str(value)


def _unanswered(result: dict[str, Any]) -> bool:
    """The question never reached the model: the service's quota was used up (Cloudflare Workers Free plan)."""
    return bool(result["error"]) and "额度" in result["error"]


def _why(result: dict[str, Any]) -> str:
    if _unanswered(result):
        return "未作答（额度用完）"
    if result["error"]:
        return "失败：" + result["error"][:40]
    parts = []
    if result["missing"]:
        parts.append("漏 " + "、".join(result["missing"]))
    if result["extra"]:
        parts.append("多 " + "、".join(result["extra"]))
    if result["keywords_missing"]:
        parts.append("缺 " + "；".join("/".join(g) for g in result["keywords_missing"]))
    if result["forbidden"]:
        parts.append("出现禁止内容")
    return "，".join(parts)


def combine(args: argparse.Namespace) -> None:
    """One report from several runs, e.g. one process per model so a stopped run loses only its own model."""
    parts = [json.loads(path.read_text(encoding="utf-8")) for path in args.combine]
    rules = next((p["rules"] for p in parts if p.get("rules")), None)
    small = next((p["small"] for p in parts if p.get("small")), None)
    runs, truths = {}, {}
    for part in parts:
        if part.get("llm"):
            runs.update(part["llm"]["runs"])
            truths.update(part["llm"]["truths"])
    if runs and args.database.is_file():
        # Re-score the saved answers with the current scoring rules against the ontology's answers of today.
        workdir = Path(tempfile.mkdtemp(prefix="tsg-eval-"))
        app, client = open_ontology(args.database.resolve(), workdir)
        try:
            calendar = app.state.ontology_service.projects.holiday_calendar
            if calendar is not None and not calendar.list_days()["meta"].get("available"):
                # Without the holiday calendar the ontology counts Monday–Friday, and utilization-based answers
                # (overload, idle, forecast) differ from the ones the models were measured against.
                raise SystemExit("holiday calendar unavailable: not re-scoring, try again when it can be fetched")
            facts = Facts(Timer().wrap(app.state.ontology_service))
            expects = {case.case_id: case for case in LLM_CASES}
            for run in runs.values():
                for result in run.get("results", []):
                    if not result["error"]:
                        case = expects[result["case"]]
                        result.update(score_answer(result["answer"], case.truth(facts), case.question, facts.employee_names))
            truths = {k: {"note": c.truth(facts).note, "entities": sorted(c.truth(facts).entities)} for k, c in expects.items()}
        finally:
            client.__exit__(None, None, None)
            shutil.rmtree(workdir, ignore_errors=True)
    llm = {"runs": runs, "truths": truths} if runs else None
    raw = {"rules": rules, "small": small, "llm": llm}
    text = report(rules, small, llm, args.database, args.today)
    output = args.output or args.combine[0].parent
    output.mkdir(parents=True, exist_ok=True)
    stem = output / f"{args.today.isoformat()}-three-layer-eval"
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    stem.with_suffix(".json").write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"report written to {stem.with_suffix('.md')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True, help="SQLite file (copied, never written)")
    parser.add_argument("--layers", default="rules,small,llm", help="comma-separated: rules, small, llm")
    parser.add_argument("--models", default="", help="provider:model list for the llm layer; default the configured model")
    parser.add_argument("--concurrency", type=int, default=3, help="questions in flight per model")
    parser.add_argument("--timeout", type=float, default=90, help="seconds per model request")
    parser.add_argument("--repeat", type=int, default=1, help="ask every question this many times (stability)")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path, help="folder for <date>-three-layer-eval.md and .json")
    parser.add_argument("--combine", type=Path, nargs="+", help="build one report from earlier .json results "
                        "(rules and small model from the first that has them, every model's answers merged)")
    args = parser.parse_args()
    if args.combine:
        combine(args)
        return
    if not args.database.is_file():
        parser.error(f"database not found: {args.database}")
    layers = {layer.strip() for layer in args.layers.split(",") if layer.strip()}
    workdir = Path(tempfile.mkdtemp(prefix="tsg-eval-"))
    app, client = open_ontology(args.database.resolve(), workdir)
    try:
        ontology = app.state.ontology_service
        print("layer 1: ontology rules", flush=True)
        rules = run_rules(ontology)
        facts = rules["facts"]
        small = None
        if "small" in layers:
            print("layer 2: small model backtest", flush=True)
            small = run_small(ontology, facts, args.today)
        llm = None
        if "llm" in layers:
            settings = app.state.settings
            specs = [s.strip() for s in args.models.split(",") if s.strip()] or [f"{settings.pm_assistant_provider}:{settings.pm_assistant_model}"]
            print(f"layer 3: large model, {len(specs)} model(s) × {len(LLM_CASES)} questions", flush=True)
            llm = asyncio.run(run_llm(app, facts, specs, args.concurrency, args.timeout, args.repeat))
        text = report(rules if "rules" in layers else None, small, llm, args.database, args.today)
        raw = {"rules": {k: v for k, v in rules.items() if k != "facts"} if "rules" in layers else None, "small": small, "llm": llm}
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            stem = args.output / f"{args.today.isoformat()}-three-layer-eval"
            stem.with_suffix(".md").write_text(text, encoding="utf-8")
            stem.with_suffix(".json").write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            print(f"report written to {stem.with_suffix('.md')}")
        else:
            print(text)
    finally:
        client.__exit__(None, None, None)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
