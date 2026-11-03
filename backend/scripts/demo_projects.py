"""Demo projects and opportunities: a year of operations up to the seed date, one business scenario each.

Loaded by scripts/seed_demo.py on top of the example snapshot (TPL149, ZMJ113 contracts and the WDR101 opportunity,
which stay as imported and unmaintained). History is fixed in 2026 (contracts signed, payments received, costs entered,
milestones completed); near-term dates are relative to the seed date ``T`` (written "T+5"), so a milestone due in five
days stays due in five days whenever the data is rebuilt. The staff in demo_people.py work on these codes.
The table is also published for testers in docs/test-data/project-scenarios.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


def day(value: str, today: date) -> str:
    """'2026-03-31' as is; 'T', 'T+5', 'T-12' relative to the seed date."""
    if not value.startswith("T"):
        return value
    offset = int(value[1:] or 0)
    return (today + timedelta(days=offset)).isoformat()


@dataclass
class Contract:
    crm_id: str
    code: str
    subject: str
    customer: str
    owner: str
    signed: str
    category: str
    status: str
    total: str
    received: str
    invoiced: str


@dataclass
class Opportunity:
    crm_id: str
    code: str
    subject: str
    customer: str
    owner: str
    expected_sign: str
    stage: str
    status: str
    amount: str
    probability: str
    scenario: str
    outcome: str  # expected investment outcome: open, won, lost, abandoned
    action: tuple[str, str] | None = None  # ("abandon", reason) or ("reopen", reason) after the import


@dataclass
class Milestone:
    name: str
    start: str
    end: str
    completed: str | None = None  # completion date (backdated after the confirm action)
    term: tuple[str, str, str | None] | None = None  # (payment term, amount, ratio %)
    received: str | None = None  # date the term's payment node was received


@dataclass
class Intervention:
    title: str
    kind: str
    owner: str
    due: str
    status: str = "open"  # open, in_progress, closed
    notes: str = ""


@dataclass
class Project:
    code: str
    scenario: str
    contract_type: str | None  # None: no project settings, everything comes from the CRM
    lifecycle: str | None = None
    manager: str | None = None
    start: str | None = None
    end: str | None = None
    budget_share: float | None = None  # budget = reported person-days ÷ share, set after the staff are loaded
    budget_days: str | None = None  # fixed budget for projects without effort
    milestones: list[Milestone] = field(default_factory=list)
    interventions: list[Intervention] = field(default_factory=list)
    # Expected on the seed date: risk level, settlement basis and anomaly-alert codes that must be present.
    risk: str | None = None
    settlement: str | None = None
    alerts: tuple[str, ...] = ()
    lifecycle_source: str | None = None


CONTRACTS = [
    Contract("demo-c-sey101", "SEY101", "SEY101-数字化工厂系统实施", "SEY", "Sales A", "2025-12-20", "系统实施", "执行中",
             "1254086.00", "376226.00", "376226.00"),
    Contract("demo-c-sey103", "SEY103", "SEY103-质量管理系统实施", "SEY", "Sales A", "2026-01-15", "系统实施", "执行中",
             "700000.00", "210000.00", "350000.00"),
    Contract("demo-c-sey104", "SEY104", "SEY104-仓储接口实施", "SEY", "Sales A", "2026-03-10", "系统实施", "执行中",
             "172250.00", "51675.00", "51675.00"),
    Contract("demo-c-sey105", "SEY105", "SEY105-MES二期", "SEY", "Sales A", "2026-10-22", "系统实施", "待启动",
             "860000.00", "0.00", "0.00"),
    Contract("demo-c-hxj201", "HXJ201", "HXJ201-华兴机械MES实施", "HXJ", "Sales B", "2026-02-10", "系统实施", "执行中",
             "980000.00", "490000.00", "490000.00"),
    Contract("demo-c-hxj201-add", "HXJ201", "HXJ201-追加-报表开发", "HXJ", "Sales B", "2026-08-18", "系统实施", "执行中",
             "160000.00", "0.00", "0.00"),
    Contract("demo-c-lny202", "LNY202", "LNY202-澜宁医药TSnT365运维", "LNY", "Sales C", "2026-01-08", "运维服务", "执行中",
             "240000.00", "180000.00", "180000.00"),
    Contract("demo-c-lny207", "LNY207", "LNY207-澜宁医药数据治理咨询", "LNY", "Sales C", "2025-09-10", "咨询服务", "终止",
             "120000.00", "36000.00", "36000.00"),
    Contract("demo-c-qyd203", "QYD203", "QYD203-启元电子开口服务", "QYD", "Sales B", "2026-04-01", "人力服务", "执行中",
             "500000.00", "200000.00", "320000.00"),
    Contract("demo-c-mks204", "MKS204", "MKS204-明凯食品追溯系统", "MKS", "Sales A", "2025-06-20", "系统实施", "已完成",
             "380000.00", "380000.00", "380000.00"),
    Contract("demo-c-jtk205", "JTK205", "JTK205-江拓汽配WMS实施", "JTK", "Sales C", "2025-11-05", "系统实施", "暂停",
             "450000.00", "135000.00", "135000.00"),
]

OPPORTUNITIES = [
    Opportunity("demo-o-hxj201", "HXJ201", "HXJ201-华兴机械MES", "HXJ", "Sales B", "2026-02-10", "赢单", "成功", "980000.00", "100",
                "赢单转合同：1 月的售前投入在签约日前计为获客成本", "won"),
    Opportunity("demo-o-stl302", "STL302", "STL302-仓储升级咨询", "STL", "Sales B", "2026-12-31", "初步接洽", "跟踪", "300000.00", "30",
                "停滞：3–4 月有售前投入，之后 90 天以上没有投入", "open"),
    Opportunity("demo-o-lso301", "LSO301", "LSO301-MES选型投标", "LSO", "Sales B", "2026-07-31", "方案", "丢单", "880000.00", "0",
                "丢单（CRM 状态在丢单名单中）：5–7 月的投入计入沉没成本", "lost"),
    Opportunity("demo-o-pzx305", "PZX305", "PZX305-排产优化", "PZX", "Sales C", "2026-04-30", "需求分析", "失效", "260000.00", "0",
                "失效（10.7）：与失败一样计为丢单，1–3 月的投入计入沉没成本", "lost"),
    Opportunity("demo-o-mrl304", "MRL304", "MRL304-质量追溯平台", "MRL", "Sales A", "2026-05-31", "方案", "跟踪", "420000.00", "40",
                "人工放弃：CRM 仍为跟踪，经「记录商机结论」判为放弃（主动放弃）", "abandoned", ("abandon", "主动放弃")),
    Opportunity("demo-o-ryf306", "RYF306", "RYF306-能耗管理", "RYF", "Sales C", "2026-12-31", "方案", "跟踪", "350000.00", "20",
                "重新激活：人工判为丢单（竞争失败）后，经「重新激活商机」恢复为在途（只能撤销人工结论）", "open", ("reopen", "客户重新发起招标")),
    Opportunity("demo-o-xny307", "XNY307", "XNY307-园区数字孪生", "XNY", "Sales A", "2026-12-15", "商务谈判", "跟踪", "1500000.00", "80",
                "纯管道：高可能性、尚无投入", "open"),
    Opportunity("demo-o-gxt308", "GXT308", "GXT308-供应链协同", "GXT", "Sales B", "2026-09-30", "商务谈判", "跟踪", "600000.00", "60",
                "预计签单日期已过仍在跟踪：10 月有售前投入", "open"),
]

PROJECTS = [
    Project("SEY101", "执行中、关注：上线里程碑与上线款 5 天内到期，客户核减形成确认缺口；上年签约、今年回款", "construction", "active",
            "李经理", "2026-01-05", "2026-12-31", budget_share=0.70, milestones=[
                Milestone("项目启动", "2026-01-05", "2026-01-31", "2026-01-30", ("首付款", "376226.00", "30"), "2026-02-15"),
                Milestone("蓝图确认", "2026-02-01", "2026-04-30", "2026-05-20"),
                Milestone("系统上线", "2026-05-01", "T+5", None, ("上线款", "501634.00", "40")),
                Milestone("项目验收", "T+6", "2026-12-31", None, ("验收款", "376226.00", "30")),
            ], interventions=[Intervention("跟进上线款开票", "payment", "李经理", "T+20")],
            risk="warning", settlement="contract", alerts=("TIME_CONFIRMATION_GAP", "MILESTONE_DUE", "PAYMENT_DUE")),
    Project("SEY103", "执行中、严重：方案评审逾期且中期款未到账，预算使用 85% 达到关注线；干预处理中", "construction", "active",
            "王经理", "2026-02-02", "2026-11-30", budget_share=0.85, milestones=[
                Milestone("项目启动", "2026-02-02", "2026-02-27", "2026-02-27", ("首付款", "210000.00", "30"), "2026-03-20"),
                Milestone("方案评审", "2026-03-01", "2026-06-30", None, ("中期款", "280000.00", "40")),
                Milestone("项目验收", "2026-07-01", "2026-11-30", None, ("尾款", "210000.00", "30")),
            ], interventions=[Intervention("推动客户完成方案评审", "schedule", "王经理", "T+10", "in_progress", "已约客户评审会")],
            risk="critical", settlement="contract", alerts=("BUDGET_NEAR_LIMIT", "MILESTONE_DUE", "PAYMENT_DUE")),
    Project("SEY104", "执行中、严重：工时超出预算 50%，上线里程碑与尾款已逾期；干预逾期未关闭", "construction", "active",
            "李经理", "2026-03-16", "2026-10-31", budget_share=1.50, milestones=[
                Milestone("项目启动", "2026-03-16", "2026-03-31", "2026-03-31", ("首付款", "51675.00", "30"), "2026-04-10"),
                Milestone("系统上线", "2026-04-01", "2026-10-31", None, ("尾款", "120575.00", "70")),
            ], interventions=[Intervention("补充测试资源", "resource", "李经理", "2026-10-20")],
            risk="critical", settlement="contract", alerts=("BUDGET_OVERRUN", "INTERVENTION_OVERDUE", "MILESTONE_DUE")),
    Project("SEY105", "本月新签、计划中：尚未指定项目经理、没有里程碑（待维护）", "construction", "planned",
            None, "2026-11-16", "2027-06-30", budget_days="400",
            risk="warning", settlement="contract", alerts=("MANUAL_MAINTENANCE_REQUIRED",)),
    Project("HXJ201", "执行中、健康：里程碑按期完成、回款按期到账、预算使用 60%；赢单商机转来，含追加合同", "construction", "active",
            "赵经理", "2026-02-16", "2027-01-31", budget_share=0.60, milestones=[
                Milestone("项目启动", "2026-02-16", "2026-02-28", "2026-02-27", ("首付款", "294000.00", "30"), "2026-03-15"),
                Milestone("蓝图确认", "2026-03-01", "2026-05-29", "2026-05-28", ("二期款", "196000.00", "20"), "2026-06-30"),
                Milestone("报表开发验收", "2026-08-24", "2026-11-30", None, ("追加款", "160000.00", None)),
                Milestone("系统上线", "2026-06-01", "2026-12-15", None, ("上线款", "294000.00", "30")),
            ], risk="healthy", settlement="contract"),
    Project("LNY202", "TSnT365 运维、健康：按季度服务评审与回款，前三季度已完成", "maintenance", "active",
            "孙经理", "2026-01-01", "2026-12-31", budget_share=0.75, milestones=[
                Milestone("一季度服务评审", "2026-01-01", "2026-03-31", "2026-03-31", ("一季度服务费", "60000.00", "25"), "2026-04-08"),
                Milestone("二季度服务评审", "2026-04-01", "2026-06-30", "2026-06-30", ("二季度服务费", "60000.00", "25"), "2026-07-06"),
                Milestone("三季度服务评审", "2026-07-01", "2026-09-30", "2026-09-30", ("三季度服务费", "60000.00", "25"), "2026-10-12"),
                Milestone("四季度服务评审", "2026-10-01", "2026-12-31", None, ("四季度服务费", "60000.00", "25")),
            ], risk="healthy", settlement="contract"),
    Project("QYD203", "开口合同（工作量结算）：不做预算判断，开票 32 万、回款 20 万；10 月工时已提交未审批形成确认缺口", "open_ended", "active",
            "钱经理", "2026-04-01", "2027-03-31", milestones=[Milestone("年度框架评审", "2026-04-01", "2026-12-20")],
            risk="warning", settlement="workload", alerts=("TIME_CONFIRMATION_GAP",)),
    Project("MKS204", "已结束仍有投入：合同已完成、里程碑与回款全部完成，9–10 月仍有工时（额外投入）", "construction", "completed",
            "周经理", "2025-07-01", "2026-06-30", budget_share=0.98, milestones=[
                Milestone("系统上线", "2025-07-01", "2026-03-31", "2026-03-30", ("上线款", "152000.00", "40"), "2026-04-15"),
                Milestone("项目验收", "2026-04-01", "2026-06-30", "2026-06-29", ("验收款", "228000.00", "60"), "2026-07-15"),
            ], settlement="contract"),
    Project("JTK205", "已暂停：客户暂停（CRM 暂停），6 月后无投入，上线顺延至明年；暂停干预已关闭", "construction", "on_hold",
            "吴经理", "2025-11-10", "2027-03-31", budget_share=0.40, milestones=[
                Milestone("项目启动", "2025-11-10", "2025-11-30", "2025-11-28", ("首付款", "135000.00", "30"), "2025-12-10"),
                Milestone("系统上线", "2026-01-01", "2027-03-31", None, ("上线款", "315000.00", "70")),
            ], interventions=[Intervention("客户暂停，协商重启时间", "customer", "吴经理", "2026-07-31", "closed", "客户确认明年一季度重启")],
            settlement="contract"),
    Project("LNY207", "已终止：CRM 状态终止，项目状态由 CRM 推导为已关闭；未做任何人工维护", None,
            lifecycle_source="contract_status"),
]

# Operating costs per month (finance figures, D24). July is deliberately missing: gross profit is cut at September and
# July is listed as a missing month (D25).
COST_MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-08", "2026-09"]
MONTHLY_COST = {"laborCost": "100000", "bonusCost": "15000", "travelCost": "12000", "outsourcingCost": "15000",
                "trainingCost": "3000", "otherCost": "5000"}
# Annual targets as a share of the actual figure at October (time progress 10/12 ≈ 83%, tolerance 5 points):
# revenue 70% → 落后, contract value 85% → 正常, operating cost 85% of the target by September (75%) → 超支.
TARGET_SHARES = {"collectionTarget": 0.70, "newContractTarget": 0.85, "operatingCostTarget": 0.85}
