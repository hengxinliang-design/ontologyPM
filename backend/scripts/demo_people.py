"""The demo staff: 38 generated employees plus the two in docs/test-data/wdr101-time-entries.json (40 people).

Each employee exists to show one scenario of the worktime rules (ontology derivations/resources.py and the
operating policy defaults: attention 60%, minimum 75%, excellent 90%, overload 100% for 2 consecutive months,
overtime share 20%, at most 4 concurrent projects; internal code TSG999). Daily hours are a fixed share of an
8-hour day on every working day of the Chinese calendar, so utilization lands exactly on the boundaries.
``expect`` is what October 2026 should show; ``scripts/seed_demo.py`` checks it after loading.
Project codes are those of scripts/demo_projects.py and the example snapshot. The table is also published
for testers in docs/test-data/people-scenarios.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]
CHECK_MONTH = "2026-10"
# One day's work: (project code, hours, overtime hours).
Day = list[tuple[str, Decimal, Decimal]]


def const(code: str, hours: str, overtime: str = "0") -> Callable[[date, int], Day]:
    return lambda day, index: [(code, Decimal(hours), Decimal(overtime))]


def split(*parts: tuple[str, str]) -> Callable[[date, int], Day]:
    return lambda day, index: [(code, Decimal(hours), Decimal("0")) for code, hours in parts]


def rotate(codes: list[str], hours: str = "8") -> Callable[[date, int], Day]:
    return lambda day, index: [(codes[index % len(codes)], Decimal(hours), Decimal("0"))]


@dataclass
class Segment:
    first: str
    last: str
    work: Callable[[date, int], Day]
    status: str = "approved"
    approved_share: Decimal | None = None  # approved hours as a share of the day's hours (customer cuts)
    saturdays: bool = False  # also work on Saturdays that are not make-up workdays
    only_dates: tuple[str, ...] = ()  # work only on these dates (e.g. on public holidays)


@dataclass
class Person:
    employee_id: str
    name: str
    scenario: str
    segments: list[Segment]
    # October 2026: resource-health statuses (None = not listed), worktime-assessment status, staffing status.
    health: list[str] | None = field(default_factory=lambda: ["healthy"])
    assessment: str | None = None
    staffing: str | None = None


S = Segment
PEOPLE: list[Person] = [
    # ---- resource health statuses
    Person("E101", "王磊", "持续过载 + 疲劳：8–10 月每天 8 小时加 3 小时加班（137.5%，加班占比 27.3%）",
           [S("2026-01", "2026-07", const("SEY101", "8")), S("2026-08", "2026-10", const("SEY101", "8", "3"))],
           ["overloaded", "fatigue"], "工时充足", "本月负荷过高"),
    Person("E102", "陈静", "持续过载（恰好连续 2 个月）：9–10 月 8 + 1.5 小时（118.75%，加班 15.8%）",
           [S("2026-01", "2026-08", const("LNY202", "8")), S("2026-09", "2026-10", const("LNY202", "8", "1.5"))],
           ["overloaded"], "工时充足", "本月负荷过高"),
    Person("E103", "刘洋", "单月负荷过高（D13）：只有 10 月超过过载线，健康状态仍为健康",
           [S("2026-01", "2026-06", const("MKS204", "8")), S("2026-07", "2026-09", const("SEY101", "8")),
            S("2026-10", "2026-10", const("SEY101", "8", "1.5"))],
           ["healthy"], "工时充足", "本月负荷过高"),
    Person("E104", "赵敏", "疲劳风险：每天 6 小时加 2 小时加班，利用率 100% 不过载，加班占比 25%",
           [S("2026-06", "2026-10", const("SEY103", "6", "2"))], ["fatigue"], "工时充足", "负荷较高"),
    Person("E105", "孙悦", "闲置：9–10 月每天 4 小时（50%）",
           [S("2026-03", "2026-08", const("SEY103", "8")), S("2026-09", "2026-10", const("SEY103", "4"))],
           ["idle"], "严重不足", "可优先调剂"),
    Person("E106", "周航", "碎片化：10 月轮流在 6 个项目和商机上",
           [S("2026-04", "2026-09", const("SEY104", "8")),
            S("2026-10", "2026-10", rotate(["SEY101", "SEY103", "SEY104", "TPL149", "ZMJ113", "WDR101"]))],
           ["fragmented"], "工时充足", "负荷较高"),
    Person("E107", "吴迪", "闲置但有内部工时可调剂：每天 4 小时项目 + 4 小时 TSG999",
           [S("2026-07", "2026-10", split(("TPL149", "4"), ("TSG999", "4")))], ["idle"], "严重不足", "可优先调剂"),
    Person("E108", "郑凯", "10 月全部是内部工时 TSG999：项目利用率 0%；1–3 月在已终止的 LNY207",
           [S("2026-01", "2026-03", const("LNY207", "8")), S("2026-04", "2026-09", const("SEY101", "8")),
            S("2026-10", "2026-10", const("TSG999", "8"))],
           ["idle"], "严重不足", "可优先调剂"),
    Person("E109", "冯雪", "首月（不评价闲置，D14）：10 月入职，每天 3 小时（37.5%）",
           [S("2026-10", "2026-10", const("SEY104", "3"))], ["first_month"], "严重不足", "可优先调剂"),
    Person("E110", "何飞", "首月但疲劳：首月不掩盖其他发现，6 + 2.5 小时（加班 29.4%）",
           [S("2026-10", "2026-10", const("ZMJ113", "6", "2.5"))], ["fatigue"], "工时充足", "本月负荷过高"),
    Person("E111", "罗娜", "过载中断：8 月、10 月都超载但 9 月没有工时，不算连续；加班占比恰好 20% 不算疲劳",
           [S("2026-08", "2026-08", const("SEY101", "8", "2")), S("2026-10", "2026-10", const("SEY101", "8", "2"))],
           ["healthy"], "工时充足", "本月负荷过高"),
    Person("E112", "高峰", "历史过载已恢复：6–8 月持续过载（查看 2026-08 可见）；9–10 月在已结束的 MKS204 上（额外投入）",
           [S("2026-06", "2026-08", const("SEY101", "8", "2.5")), S("2026-09", "2026-10", const("MKS204", "8"))],
           ["healthy"], "工时充足", "负荷较高"),
    # ---- utilization bands and boundaries (assessment: >90 充足, >75 达标, >60 关注; staffing bands likewise)
    Person("E113", "梁晨", "利用率恰好 100%：工时充足、负荷较高，不算过载",
           [S("2026-01", "2026-01", const("SEY101", "8")), S("2026-02", "2026-10", const("HXJ201", "8"))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E114", "宋佳", "利用率 95%", [S("2026-02", "2026-10", const("SEY103", "7.6"))], ["healthy"], "工时充足", "负荷较高"),
    Person("E115", "唐磊", "利用率恰好 90%：基本达标（需高于 90% 才算充足）", [S("2026-01", "2026-10", const("TPL149", "7.2"))],
           ["healthy"], "基本达标", "安排基本均衡"),
    Person("E116", "韩梅", "利用率 85%", [S("2026-01", "2026-10", const("ZMJ113", "6.8"))], ["healthy"], "基本达标", "安排基本均衡"),
    Person("E117", "曹阳", "利用率恰好 75%（最低利用率默认值）：需要关注、可适度支持（需高于 75% 才达标）；1–2 月在商机 RYF306",
           [S("2026-01", "2026-02", const("RYF306", "6")), S("2026-03", "2026-10", const("SEY101", "6"))],
           ["healthy"], "需要关注", "可适度支持"),
    Person("E118", "许琳", "利用率 70%", [S("2026-02", "2026-10", const("SEY103", "5.6"))], ["healthy"], "需要关注", "可适度支持"),
    Person("E119", "邓超", "利用率恰好 60%：严重不足、可优先调剂、闲置（需高于 60%）",
           [S("2026-01", "2026-10", const("TPL149", "4.8"))], ["idle"], "严重不足", "可优先调剂"),
    Person("E120", "彭丽", "利用率 61%：刚过关注线，不算闲置；1–3 月在后来放弃的商机 MRL304",
           [S("2026-01", "2026-03", const("MRL304", "4.88")), S("2026-04", "2026-10", const("ZMJ113", "4.88"))],
           ["healthy"], "需要关注", "可适度支持"),
    # ---- approval states and confirmation (submitted counts as effort; draft and rejected do not, L29)
    Person("E121", "蒋涛", "10 月工时已提交未审批：计入利用率，待确认缺口为全部工时（开口合同 QYD203）",
           [S("2026-02", "2026-03", const("SEY103", "8")), S("2026-04", "2026-09", const("QYD203", "8")),
            S("2026-10", "2026-10", const("QYD203", "8"), status="submitted")],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E122", "沈琪", "客户核减：每天填报 8 小时、审批确认 6 小时，形成确认缺口",
           [S("2026-01", "2026-10", const("SEY101", "8"), approved_share=Decimal("0.75"))], ["healthy"], "工时充足", "负荷较高"),
    Person("E123", "韦东", "10 月工时全部被驳回：不计入利用率，健康页不出现，工时评价为 0%",
           [S("2026-01", "2026-09", const("LNY202", "8")), S("2026-10", "2026-10", const("LNY202", "8"), status="rejected")],
           None, "严重不足", None),
    Person("E124", "姚雯", "10 月工时全部是草稿：同驳回，不计入",
           [S("2026-02", "2026-09", const("SEY103", "8")), S("2026-10", "2026-10", const("SEY103", "8"), status="draft")],
           None, "严重不足", None),
    # ---- project and investment scenarios
    Person("E125", "史航", "售前：1 月赢单商机 HXJ201（签约前为获客成本），3–4 月停滞商机 STL302，5–7 月丢单商机 LSO301（沉没），8–10 月在途商机 WDR101",
           [S("2026-01", "2026-01", const("HXJ201", "8")), S("2026-03", "2026-04", const("STL302", "8")),
            S("2026-05", "2026-07", const("LSO301", "8")), S("2026-08", "2026-10", const("WDR101", "8"))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E126", "金鑫", "未建档项目代码 NEW201：计入利用率，在投入成本中显示为未建档代码",
           [S("2026-09", "2026-10", const("NEW201", "8"))], ["healthy"], "工时充足", "负荷较高"),
    Person("E127", "魏然", "长期在 ZMJ113（已导入未维护的项目）", [S("2026-01", "2026-10", const("ZMJ113", "8"))], ["healthy"], "工时充足", "负荷较高"),
    Person("E128", "秦岚", "在未设预算的 TPL149 上（预算监控之外的投入）", [S("2026-07", "2026-10", const("TPL149", "8"))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E129", "范博", "在已超预算的 SEY104 上", [S("2026-03", "2026-10", const("SEY104", "8"))], ["healthy"], "工时充足", "负荷较高"),
    Person("E130", "汤宁", "每天拆成两个项目各 4 小时", [S("2026-02", "2026-10", split(("SEY101", "4"), ("SEY103", "4")))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E131", "尹浩", "周末加班记作工时：10 月非补班的周六也上班，单月超过过载线",
           [S("2026-02", "2026-09", const("SEY103", "8")), S("2026-10", "2026-10", const("SEY103", "8"), saturdays=True)],
           ["healthy"], "工时充足", "本月负荷过高"),
    Person("E132", "黎明", "8 月后离开：9–10 月没有工时，不出现在 10 月", [S("2026-01", "2026-08", const("LNY202", "8"))],
           None, None, None),
    Person("E133", "易航", "9 月入职：9 月为首月，10 月正常评价（87.5%）", [S("2026-09", "2026-10", const("SEY104", "7"))],
           ["healthy"], "基本达标", "安排基本均衡"),
    Person("E134", "常欢", "年度视图：一季度半天在后来失效的商机 PZX305（50%），之后满勤",
           [S("2026-01", "2026-03", const("PZX305", "4")), S("2026-04", "2026-10", const("SEY101", "8"))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E135", "江涛", "加班占比恰好 20%（6.4 + 1.6 小时）：不算疲劳",
           [S("2026-01", "2026-01", const("SEY101", "6.4", "1.6")), S("2026-02", "2026-10", const("HXJ201", "6.4", "1.6"))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E136", "牛犇", "10 月恰好 4 个项目：不算碎片化",
           [S("2026-01", "2026-01", const("SEY101", "8")), S("2026-02", "2026-09", const("HXJ201", "8")),
            S("2026-10", "2026-10", rotate(["HXJ201", "SEY103", "TPL149", "ZMJ113"]))],
           ["healthy"], "工时充足", "负荷较高"),
    Person("E137", "范冰", "10 月 5 个项目（含过了预计签单日期的商机 GXT308）：刚超过上限，碎片化；1–6 月在已暂停的 JTK205",
           [S("2026-01", "2026-06", const("JTK205", "8")), S("2026-07", "2026-09", const("SEY101", "8")),
            S("2026-10", "2026-10", rotate(["SEY101", "SEY103", "SEY104", "TPL149", "GXT308"]))],
           ["fragmented"], "工时充足", "负荷较高"),
    Person("E138", "白洁", "国庆值班：10 月只在 10 月 1–3 日（法定节假日）上班，利用率 16.7%",
           [S("2026-01", "2026-01", const("SEY101", "8")), S("2026-02", "2026-09", const("HXJ201", "8")),
            S("2026-10", "2026-10", const("HXJ201", "8"), only_dates=("2026-10-01", "2026-10-02", "2026-10-03"))],
           ["idle"], "严重不足", "可优先调剂"),
]

# The two people of docs/test-data/wdr101-time-entries.json (loaded as is): approval-state edge cases and a second 王磊.
TEST_FILE_PEOPLE = [
    ("E201", "李婷", "WDR101 运维（工作量结算）：7–9 月，9 月含草稿、驳回与已提交未审批的记录（L29）"),
    ("E202", "王磊", "与 E101 同名不同人：按员工编号区分（L37）"),
]


def generate(working: Callable[[str], bool]) -> list[dict]:
    """Time entries for every person; ``working(day)`` says whether a date is a working day in China."""
    entries: list[dict] = []
    for person in PEOPLE:
        for segment in person.segments:
            days = _days(segment, working)
            for index, day in enumerate(days):
                for code, hours, overtime in segment.work(day, index):
                    total = hours + overtime
                    approved = Decimal("0")
                    if segment.status == "approved":
                        approved = total * segment.approved_share if segment.approved_share is not None else total
                    entries.append({
                        "external_id": f"DEMO-{person.employee_id}-{day.isoformat()}-{code}",
                        "project_number": code, "employee_id": person.employee_id, "employee_name": person.name,
                        "work_date": day.isoformat(), "hours": f"{hours:.2f}", "overtime_hours": f"{overtime:.2f}",
                        "approved_hours": f"{approved:.2f}", "approval_status": segment.status,
                        "work_content": person.scenario[:60], "source_updated_at": "2026-10-31T18:00:00+08:00",
                    })
    return entries


def _days(segment: Segment, working: Callable[[str], bool]) -> list[date]:
    if segment.only_dates:
        return [date.fromisoformat(d) for d in segment.only_dates]
    result = []
    for month in MONTHS:
        if not segment.first <= month <= segment.last:
            continue
        year, number = (int(part) for part in month.split("-"))
        day = date(year, number, 1)
        while day.month == number:
            iso = day.isoformat()
            if working(iso) or (segment.saturdays and day.weekday() == 5):
                result.append(day)
            day = date.fromordinal(day.toordinal() + 1)
    return result
