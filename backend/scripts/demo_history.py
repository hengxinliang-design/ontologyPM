"""2025 work history for the demo staff: a year of varied, explainable workload for the load-forecast pilot.

The 2026 staff in demo_people.py work fixed hours every day so that each lands exactly on a rule boundary; on such
data "same as last month" is almost always right and no forecast can do better. This module adds 2025, where the
hours move for reasons a forecast could learn from the ontology (docs/ontology-design/2026-11-01-small-model-pilot.md):

- overtime in the three weeks before a milestone of the person's project is due (crunch);
- internal hours (TSG999) between two projects, so utilization drops when a project ends (bench);
- a slower first two weeks on a new project (ramp-up, the rest of the day is internal onboarding);
- leave: a few days next to the Spring Festival, a summer week, the odd sick day;
- half-hour noise on every day.

Who gets history: the 2026 staff already working by March 2026 (their 2026 data is untouched; a 2026 start after
January leaves a gap, read as leave or a transfer), plus 15 people who worked only in 2025 and left in the year.
No one gets an hour in 2026 from here, so every October 2026 expectation in demo_people.py still holds: resource
health looks back 12 months, and only the October joiners (no history) depend on a first month.

Everything is random but seeded per person, so a rebuild gives the same entries. These numbers come from a simulator:
a forecast that learns them shows that the pipeline and features work, not that real staff behave this way.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

import demo_people
import demo_projects as dp

YEAR_START, YEAR_END = date(2025, 1, 1), date(2025, 12, 31)
INTERNAL = "TSG999"
CRUNCH_DAYS = 21  # calendar days before a milestone's planned end
RAMP_DAYS = 10  # working days at a slower pace on a new assignment
SPRING_FESTIVAL = (date(2025, 1, 23), date(2025, 2, 7))  # leave is taken next to the statutory holiday


@dataclass(frozen=True)
class Stint:
    code: str
    first: str
    last: str


# Assignment patterns over 2025. Gaps between stints are internal time (bench). MKS204, JTK205 and LNY207 are
# contracts of demo_projects.py that already ran in 2025; the others are the 2025 history projects.
PATTERNS: list[list[Stint]] = [
    [Stint("HDL190", "2025-01-06", "2025-06-30"), Stint("MKS204", "2025-08-01", "2025-12-31")],
    [Stint("HDL190", "2025-01-06", "2025-06-30"), Stint("QYD190", "2025-07-07", "2025-12-19")],
    [Stint("LNY190", "2025-01-01", "2025-12-31")],
    [Stint("SEY190", "2025-03-03", "2025-09-30"), Stint("LNY207", "2025-10-13", "2025-12-31")],
    [Stint("SEY190", "2025-03-03", "2025-09-30"), Stint("JTK205", "2025-11-10", "2025-12-31")],
    [Stint("HDL190", "2025-01-06", "2025-03-31"), Stint("QYD190", "2025-04-07", "2025-12-19")],
    [Stint("LNY190", "2025-01-01", "2025-06-30"), Stint("MKS204", "2025-07-01", "2025-12-31")],
    [Stint("QYD190", "2025-04-07", "2025-12-19")],
]

# 2025-only staff: (id, name, joined, left). Most leave during the year; two join in spring.
LEAVERS = [
    ("E301", "方圆", "2025-01-01", "2025-04-30"), ("E302", "顾晴", "2025-01-01", "2025-05-31"),
    ("E303", "邵杰", "2025-01-01", "2025-06-30"), ("E304", "柳青", "2025-01-01", "2025-07-31"),
    ("E305", "钟磊", "2025-01-01", "2025-08-31"), ("E306", "严冬", "2025-01-01", "2025-09-30"),
    ("E307", "谭欣", "2025-01-01", "2025-10-31"), ("E308", "陆川", "2025-01-01", "2025-11-30"),
    ("E309", "苏禾", "2025-01-01", "2025-12-31"), ("E310", "孔亮", "2025-01-01", "2025-12-31"),
    ("E311", "叶帆", "2025-01-01", "2025-12-31"), ("E312", "鲁宁", "2025-01-01", "2025-12-31"),
    ("E313", "温蕾", "2025-01-01", "2025-12-31"), ("E314", "邱实", "2025-03-03", "2025-12-31"),
    ("E315", "左岩", "2025-04-01", "2025-11-30"),
]


def milestone_ends() -> dict[str, list[date]]:
    """Planned ends of the milestones a 2025 stint can run into, by project code."""
    ends: dict[str, list[date]] = {}
    for project in dp.PROJECTS:
        for milestone in project.milestones:
            if not milestone.end.startswith("T"):
                ends.setdefault(project.code, []).append(date.fromisoformat(milestone.end))
    return ends


def people() -> list[tuple[str, str, str, str]]:
    """(id, name, first day, last day) of everyone with 2025 history."""
    continuing = [(p.employee_id, p.name, "2025-01-01", "2025-12-31") for p in demo_people.PEOPLE
                  if min(s.first for s in p.segments) <= "2026-03"]
    return continuing + LEAVERS


def generate(working: Callable[[str], bool]) -> list[dict]:
    ends = milestone_ends()
    entries: list[dict] = []
    for index, (employee_id, name, joined, left) in enumerate(people()):
        rng = random.Random(f"tsg-demo-history-{employee_id}")
        pattern = PATTERNS[index % len(PATTERNS)]
        base = rng.choice([Decimal("7"), Decimal("7.5"), Decimal("8"), Decimal("8")])
        crunch_hours = rng.choice([1, 1.5, 2, 2.5, 3])
        crunch_chance = rng.uniform(0.5, 0.85)
        leave = _leave_days(rng, working)
        first, last = date.fromisoformat(joined), date.fromisoformat(left)
        stint_days: dict[str, int] = {}
        day = first
        while day <= last:
            iso = day.isoformat()
            if working(iso) and day not in leave and rng.random() > 0.012:  # the odd sick day
                stint = next((s for s in pattern if s.first <= iso <= s.last), None)
                for code, hours, overtime in _day(rng, stint, day, base, crunch_hours, crunch_chance, ends, stint_days):
                    entries.append(_entry(employee_id, name, day, code, hours, overtime))
            day += timedelta(days=1)
    return entries


def _day(rng: random.Random, stint: Stint | None, day: date, base: Decimal, crunch_hours: float, crunch_chance: float,
         ends: dict[str, list[date]], stint_days: dict[str, int]) -> list[tuple[str, Decimal, Decimal]]:
    if stint is None:
        return [(INTERNAL, Decimal("8"), Decimal("0"))]  # bench between projects
    count = stint_days[stint.code] = stint_days.get(stint.code, 0) + 1
    hours = _half(float(base) + rng.gauss(0, 0.4), 4, 8)
    if count <= RAMP_DAYS:  # ramp-up: the rest of the day is internal onboarding
        project = _half(float(hours) * 0.7, 3, 8)
        return [(stint.code, project, Decimal("0")), (INTERNAL, Decimal("8") - project, Decimal("0"))]
    crunch = any(timedelta(0) <= end - day <= timedelta(days=CRUNCH_DAYS) for end in ends.get(stint.code, []))
    maintenance = stint.code == "LNY190"
    if crunch and rng.random() < (crunch_chance * (0.5 if maintenance else 1)):
        overtime = Decimal(str(crunch_hours if not maintenance else 1)) + _half(rng.gauss(0, 0.5), -1, 1)
        return [(stint.code, Decimal("8"), max(Decimal("0.5"), overtime))]
    return [(stint.code, hours, Decimal("0"))]


def _leave_days(rng: random.Random, working: Callable[[str], bool]) -> set[date]:
    """2–3 days next to the Spring Festival, a summer week, as working days."""
    leave: set[date] = set()
    if rng.random() < 0.6:
        near = [d for d in _days(*SPRING_FESTIVAL) if working(d.isoformat())]
        leave.update(rng.sample(near, min(len(near), rng.choice([2, 3]))))
    if rng.random() < 0.7:
        start = date(2025, rng.choice([7, 8]), rng.randint(1, 20))
        week = [d for d in _days(start, start + timedelta(days=12)) if working(d.isoformat())][:5]
        leave.update(week)
    return leave


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _half(value: float, low: float, high: float) -> Decimal:
    return Decimal(str(min(high, max(low, round(value * 2) / 2))))


def _entry(employee_id: str, name: str, day: date, code: str, hours: Decimal, overtime: Decimal) -> dict:
    total = hours + overtime
    return {
        "external_id": f"DEMO-H-{employee_id}-{day.isoformat()}-{code}",
        "project_number": code, "employee_id": employee_id, "employee_name": name,
        "work_date": day.isoformat(), "hours": f"{hours:.2f}", "overtime_hours": f"{overtime:.2f}",
        "approved_hours": f"{total:.2f}", "approval_status": "approved",
        "work_content": "2025 年历史工时（负荷预测试点）", "source_updated_at": "2025-12-31T18:00:00+08:00",
    }
