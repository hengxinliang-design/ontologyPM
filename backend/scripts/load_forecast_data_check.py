"""Step 0 of the small-model pilot: is there enough work-hour history to train a next-month load forecast?

Read-only. Opens the SQLite file in read-only mode and writes nothing to it. The report holds aggregates only, no
employee names or IDs, so it can be shared without the PERSONNEL marking. Run from backend/ against a copy of the
real database (see docs/ontology-design/2026-11-01-small-model-pilot.md, section 8 and decision D39):

    .venv/bin/python scripts/load_forecast_data_check.py --database /path/to/tsg.sqlite3
    .venv/bin/python scripts/load_forecast_data_check.py --database ... --output ../docs/test-reports/load-forecast-data-check.md
    .venv/bin/python scripts/load_forecast_data_check.py --database ... --weekday-calendar   # no network

Utilization uses the ontology's one implementation (app/ontology/derivations/resources.py): project hours of
submitted or approved entries, excluding the internal codes of the investment settings, over working days × standard
hours per day. A training sample is a person-month with effort whose following month is complete; its answer is the
following month's utilization. The gate is at least 12 months and 600 person-months (D39).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.china_holiday_calendar import ChinaHolidayCalendarService  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.ontology.derivations.resources import (  # noqa: E402
    ResourceMetrics,
    _month_end,
    _shift,
    monthly_buckets,
    utilization_percent,
)
from app.project_repository import ProjectRepository  # noqa: E402

MIN_MONTHS = 12
MIN_PERSON_MONTHS = 600
UTILIZATION_BANDS = ((Decimal("60"), "≤60%"), (Decimal("75"), "60–75%"), (Decimal("90"), "75–90%"),
                     (Decimal("100"), "90–100%"), (None, ">100%"))


class ReadOnlyDatabase(Database):
    """The application's Database with the file opened read-only: the check can never change real data."""

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


def months_between(first: str, last: str) -> list[str]:
    months, current = [], first
    while current <= last:
        months.append(current)
        current = _shift(current, 1)
    return months


def band(utilization: Decimal) -> str:
    for upper, label in UTILIZATION_BANDS:
        if upper is None or utilization <= upper:
            return label
    return UTILIZATION_BANDS[-1][1]


def percent(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


def check(database_path: Path, today: date, weekday_calendar: bool) -> str:
    database = ReadOnlyDatabase(database_path)
    settings = Settings.from_env()
    calendar = None if weekday_calendar else ChinaHolidayCalendarService(
        settings.china_holiday_rest_ics_url, settings.china_holiday_work_ics_url,
        cache_hours=settings.china_holiday_cache_hours)
    projects = ProjectRepository(database, settings.timezone, holiday_calendar=calendar)
    metrics = ResourceMetrics(projects)

    with database.connect() as connection:
        policy = dict(connection.execute("SELECT * FROM worktime_baseline_config WHERE config_id='default'").fetchone())
        span = connection.execute("SELECT MIN(work_date) AS first, MAX(work_date) AS last, COUNT(*) AS n "
                                  "FROM time_entries").fetchone()
        statuses = dict(connection.execute("SELECT approval_status, COUNT(*) FROM time_entries "
                                           "GROUP BY approval_status").fetchall())
    if not span["n"]:
        return "# 负荷预测数据核对\n\n工时表为空，无法核对。\n"

    first_month, last_month = span["first"][:7], span["last"][:7]
    # The latest month counts as complete only once it is over; an open month cannot be a training answer.
    last_complete = last_month if _month_end(last_month) < today.isoformat() else _shift(last_month, -1)
    months = months_between(first_month, last_month)
    internal = metrics.internal_codes()
    people = monthly_buckets(metrics.entries(f"{first_month}-01", _month_end(last_month)), internal)
    capacity, sources = {}, set()
    for month in months:
        hours, calendar_meta = metrics.capacity(f"{month}-01", _month_end(month), policy)
        capacity[month] = hours
        sources.add(str(calendar_meta.get("source") or ("节假日日历" if calendar_meta.get("available") else "未知")))

    overload, attention = Decimal(policy["overload_utilization_percent"]), Decimal(policy["attention_utilization_percent"])
    active_by_month: Counter[str] = Counter()
    bands: Counter[str] = Counter()
    tenure, gaps_people, internal_only = [], 0, 0
    samples = overload_answers = idle_answers = no_next_month = 0
    for person in people.values():
        worked = sorted(m for m, bucket in person["months"].items() if bucket["project_hours"] > 0)
        if not worked:
            internal_only += 1
            continue
        tenure.append(len(worked))
        if len(worked) < len(months_between(worked[0], worked[-1])):
            gaps_people += 1
        for month in worked:
            active_by_month[month] += 1
            util = utilization_percent(person["months"][month]["project_hours"], capacity[month])
            if month <= last_complete:
                bands[band(util)] += 1
            following = _shift(month, 1)
            if following > last_complete:
                continue
            samples += 1
            nxt = person["months"].get(following)
            next_util = utilization_percent(nxt["project_hours"], capacity[following]) if nxt else Decimal("0")
            no_next_month += nxt is None or nxt["project_hours"] == 0
            overload_answers += next_util > overload
            idle_answers += next_util <= attention

    complete_months = [m for m in months if m <= last_complete]
    empty_months = [m for m in complete_months if not active_by_month[m]]
    months_ok, samples_ok = len(complete_months) >= MIN_MONTHS, samples >= MIN_PERSON_MONTHS
    verdict = "通过，可以进入第 1 步离线回测" if months_ok and samples_ok else "未通过，先暂停试点、继续积累数据"

    lines = [
        "# 负荷预测数据核对（小模型试点第 0 步）",
        "",
        f"核对日期 {today.isoformat()}；数据库 `{database_path.name}`（只读打开）；工作日历：{'、'.join(sorted(sources))}。",
        "报告只含汇总数字，不含员工姓名或编号。",
        "",
        "## 结论",
        "",
        f"**{verdict}。** 门槛是至少 {MIN_MONTHS} 个完整月、{MIN_PERSON_MONTHS} 个训练样本（D39）。",
        "",
        "| 门槛 | 要求 | 实际 | 结果 |",
        "| --- | --- | --- | --- |",
        f"| 完整月份数 | ≥ {MIN_MONTHS} | {len(complete_months)} | {'✓' if months_ok else '✗'} |",
        f"| 训练样本（「人 × 月」，且下月已完整） | ≥ {MIN_PERSON_MONTHS} | {samples} | {'✓' if samples_ok else '✗'} |",
        "",
        "## 数据范围",
        "",
        "| 项目 | 数值 |",
        "| --- | --- |",
        f"| 工时日期 | {span['first']} 至 {span['last']} |",
        f"| 最后一个完整月 | {last_complete} |",
        f"| 工时记录 | {span['n']} 条（" + "、".join(f"{k} {v}" for k, v in sorted(statuses.items())) + "；只计 submitted 和 approved） |",
        f"| 内部代码 | {'、'.join(sorted(internal))}（不计入利用率） |",
        f"| 有项目工时的员工 | {len(tenure)} 人；另有 {internal_only} 人只有内部工时 |",
        f"| 每人有项目工时的月数 | 中位数 {median(tenure) if tenure else 0}，最少 {min(tenure, default=0)}，最多 {max(tenure, default=0)} |",
        f"| 中途断档的员工 | {gaps_people} 人（首末月之间有整月没有项目工时） |",
        f"| 没有任何工时的完整月 | {'、'.join(empty_months) if empty_months else '无'} |",
        "",
        "## 每月有项目工时的人数",
        "",
        "| 月份 | 人数 | 产能工时（每人） |",
        "| --- | --- | --- |",
        *(f"| {m}{'（未完整）' if m > last_complete else ''} | {active_by_month[m]} | {capacity[m]} |" for m in months),
        "",
        "## 训练答案的分布",
        "",
        f"按当前运营口径：过载线 {overload}%，关注线 {attention}%。正样本太少时，过载概率的模型学不出来，"
        "所以这里同时看比例。",
        "",
        "| 项目 | 数量 | 占训练样本 |",
        "| --- | --- | --- |",
        f"| 下月利用率高于过载线 | {overload_answers} | {percent(overload_answers, samples)} |",
        f"| 下月利用率不高于关注线 | {idle_answers} | {percent(idle_answers, samples)} |",
        f"| 下月没有项目工时（离职、长假或调岗，数据里分不出来） | {no_next_month} | {percent(no_next_month, samples)} |",
        "",
        "完整月的利用率分布：",
        "",
        "| 区间 | 「人 × 月」 |",
        "| --- | --- |",
        *(f"| {label} | {bands[label]} |" for _, label in UTILIZATION_BANDS),
        "",
        "## 读这份报告时注意",
        "",
        "- 「下月没有项目工时」的样本在第 1 步需要决定怎么处理：当作闲置，还是作为离职、长假剔除。占比越高，这个决定越重要。",
        "- 过载样本占比低于 5% 时，过载概率的 AUC 和校准都不稳定，第 1 步应先看利用率回归的误差。",
        "- 演示数据按固定规律生成，只能验证本脚本能跑通，结论不代表真实情况。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True, help="SQLite file to check (opened read-only)")
    parser.add_argument("--output", type=Path, help="write the Markdown report here instead of printing it")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today(),
                        help="check date, decides whether the latest month is complete (default: today)")
    parser.add_argument("--weekday-calendar", action="store_true",
                        help="count Monday–Friday as working days instead of fetching the holiday calendar")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f"database not found: {args.database}")
    report = check(args.database.resolve(), args.today, args.weekday_calendar)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
