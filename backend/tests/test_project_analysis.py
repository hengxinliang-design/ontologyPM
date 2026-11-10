"""AI project review (项目详情「AI 分析」): the ontology states the facts and the alerts; the model adds what the rules
cannot say (findings that link facts) and one action per alert; everything it writes is checked against the facts.
"""
from fastapi.testclient import TestClient

from app import project_analysis

from test_assistant_cloudflare import cloudflare, scripted

LABELS = {"RiskLevel": {"warning": "关注", "critical": "严重"}, "ContractType": {"open_ended": "开口合同"},
          "LifecycleStatus": {"active": "执行中"}, "MilestoneStatus": {"planned": "计划中"}}
GAP = "已填报 145.00 人天，客户确认 127.00 人天，缺口 18.00 人天"
QYD203 = {
    "project_number": "QYD203", "subject": "启元电子开口服务", "customer": "QYD", "contract_type": "open_ended",
    "lifecycle_status": "active", "project_manager": "钱经理", "planned_start": "2026-04-01", "planned_end": "2027-03-31",
    "risk_level": "warning", "risk_reasons": ["已填报工时与客户确认工时相差 18.00 人天"],
    "planned_person_days": None, "actual_person_days": "145.00", "customer_confirmed_person_days": "127.00",
    "confirmation_gap_person_days": "18.00", "revenue_settlement_basis": "workload", "contract_value": "500000.00",
    "invoiced_amount": "320000.00", "crm_received_amount": "200000.00", "outstanding_receivable_amount": "120000.00",
    "milestone_total": 1, "milestone_completed": 0,
    "milestones": [{"name": "年度框架评审", "planned_start": "2026-04-01", "planned_end": "2026-12-20", "status": "planned",
                    "owner": "钱经理", "deadline": {"label": "还有 44 天"}}],
    "pmo_resource_rows": [{"employee_name": "蒋涛", "reported_person_days": "145.00"}],
    "anomaly_alerts": [{"severity": "warning", "title": "存在客户未确认工时", "detail": GAP}],
    "maintenance_complete": True, "redacted_markings": [],
}


def review(text, project=QYD203, today=None):
    facts = project_analysis.fact_sheet(project, LABELS)
    return project_analysis.verify(project_analysis.parse(text), facts, project, LABELS, project_analysis.issues(project), today)


def test_facts_use_ontology_labels_and_name_roles_plainly():
    text = "\n".join(project_analysis.fact_sheet(QYD203, LABELS))
    assert text.startswith("F1. 项目：QYD203")
    assert "项目类型 开口合同" in text and "风险等级：关注" in text and "warning" not in text, "codes become ontology labels"
    assert "项目经理 钱经理" in text and "团队成员：共 1 人；投入最多的：蒋涛 145.00 人天" in text
    assert "未设置预算人天（开口合同按工作量结算，不设上限）" in text, "no budget is said, so 127 cannot pass for a plan"
    assert "计划完成 2026-12-20（还有 44 天）" in text and "2026-04-01" not in text, "the start date is not offered as a deadline"


def test_issues_are_the_drawer_alerts_in_order_not_the_models_choice():
    project = {**QYD203, "anomaly_alerts": [
        {"severity": "warning", "title": "存在客户未确认工时", "detail": GAP},
        {"severity": "critical", "title": "系统上线 · 已延期 5 天", "detail": "里程碑尚未确认完成"}]}
    assert [(i["id"], i["level"], i["title"]) for i in project_analysis.issues(project)] == [
        ("I1", "高", "系统上线 · 已延期 5 天"), ("I2", "中", "存在客户未确认工时")]
    quiet = {**QYD203, "anomaly_alerts": [], "maintenance_complete": False, "maintenance_reasons": ["请指定项目经理"]}
    assert [i["title"] for i in project_analysis.issues(quiet)] == ["同步后待人工维护", "项目风险提示"]


def test_ai_alerts_are_not_issues_for_the_model():
    """8.6.0 functional test F-B12: the AI's own flags and pending suggestions stay out of the analysis issues."""
    ai = [{"code": "AI_RISK_FLAG", "severity": "warning", "title": f"AI 风险标记 · {n}", "detail": "d"} for n in range(7)]
    project = {**QYD203, "anomaly_alerts": ai + [{"code": "MILESTONE_DUE", "severity": "warning", "title": "系统上线 · 已延期 5 天", "detail": "x"}]}
    assert [i["title"] for i in project_analysis.issues(project)] == ["系统上线 · 已延期 5 天"]
    only_ai = {**QYD203, "anomaly_alerts": ai[:1], "maintenance_complete": False, "maintenance_reasons": ["请指定项目经理"]}
    assert [i["title"] for i in project_analysis.issues(only_ai)][0] == "同步后待人工维护", "maintenance gaps still show"


def test_actions_are_kept_only_when_the_facts_support_them():
    result = review("结论：客户未确认工时缺口 18.00 人天待处理\nI1：钱经理本周与客户确认 145.00 人天中的差异")
    assert result["conclusion"] == "客户未确认工时缺口 18.00 人天待处理"
    assert result["items"] == [{"level": "中", "title": "存在客户未确认工时", "evidence": GAP,
                                "action": "钱经理本周与客户确认 145.00 人天中的差异", "code": None}]
    # QYD203's mistakes: the start date as a deadline, a made-up figure; and generic advice.
    for action in ("钱经理在 2026-04-01 前完成评审", "钱经理复核超出的 30 人天", "加强沟通，密切关注进度"):
        dropped = review(f"结论：工时待确认\nI1：{action}")
        assert dropped["items"][0]["action"] is None and dropped["dropped"] == 1, action
        assert dropped["items"][0]["title"] == "存在客户未确认工时", "the issue stays; only the action is withheld"


def test_unsupported_claims_in_words_replace_the_conclusion():
    # granite-4.0-h-micro wrote 「项目已逾期」 for QYD203, where nothing is overdue.
    assert review("结论：项目已逾期，需紧急跟进")["conclusion"] == "风险等级关注：已填报工时与客户确认工时相差 18.00 人天"
    assert review("结论：预算超支，需处理")["conclusion"].startswith("风险等级关注")


def test_past_dates_and_issue_numbers_do_not_reach_the_user():
    from datetime import date
    # SEY104 got 「截止 2026-10-20 前完成」 on 2026-11-06; another conclusion said 「优先解决 I1 问题」.
    late = review("结论：工时待确认\nI1：钱经理在 2026-12-20 前与客户确认", today=date(2027, 1, 5))
    assert late["items"][0]["action"] is None and late["dropped"] == 1
    on_time = review("结论：工时待确认\nI1：钱经理在 2026-12-20 前与客户确认", today=date(2026, 11, 6))
    assert on_time["items"][0]["action"] == "钱经理在 2026-12-20 前与客户确认"
    assert review("结论：优先解决 I1 问题")["conclusion"] == "优先解决 「存在客户未确认工时」"


def test_a_plain_team_member_is_not_made_the_owner():
    assert review("结论：工时待确认\nI1：蒋涛本周整理工作清单")["items"][0]["action"] == "钱经理本周整理工作清单"


def test_broken_or_free_text_replies_fall_back_to_the_ontology():
    for text in ('{"conclusion": "x", "items": [{"level": "高"}], {"level": "高"}]}', "1. 风险判断：……"):
        result = review(text)
        assert result["formatted"] is False and result["conclusion"].startswith("风险等级关注")
        assert [(i["title"], i["action"]) for i in result["items"]] == [("存在客户未确认工时", None)], "issues still show"
    one_line = project_analysis.parse("结论：缺口待确认；I1：钱经理本周确认；I2：钱经理本月复核")
    assert one_line == {"conclusion": "缺口待确认", "actions": {"I1": "钱经理本周确认", "I2": "钱经理本月复核"}, "findings": []}


def test_endpoint_sends_facts_and_issues_and_returns_a_checked_review(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, ["结论：里程碑需处理\nI1：项目经理本周更新计划"])
    with TestClient(cloudflare(tmp_path)) as client:
        response = client.post("/api/v1/projects/TPL149/ai-analysis")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    system, user = sent[0]["body"]["messages"]
    assert "规则提醒已经逐条显示给用户，不要复述" in system["content"] and "发现：" in system["content"]
    assert user["content"].startswith("事实清单") and "F1. 项目：TPL149" in user["content"] and "规则提醒（已显示给用户）：" in user["content"]
    assert '"risk_level"' not in user["content"], "the model no longer reads raw field names"
    assert data["review"]["conclusion"] == "里程碑需处理" and data["review"]["items"][0]["action"] == "项目经理本周更新计划"
    assert data["facts"][0].startswith("F1.") and data["answer"].startswith("**结论**：里程碑需处理")


def test_findings_link_two_real_facts_and_do_not_guess():
    facts = project_analysis.fact_sheet(QYD203, LABELS)
    ids = {fact.split(". ", 1)[1].split("：", 1)[0]: fact.split(".", 1)[0] for fact in facts}
    work, milestone = ids["工时"], next(v for k, v in ids.items() if k.startswith("未完成里程碑"))
    result = review(f"""结论：工时缺口待确认
发现：缺口 18.00 人天待客户确认，唯一的里程碑「年度框架评审」在 2026-12-20 到期（{work}、{milestone}）
发现：缺口可能导致回款延迟（{work}、{milestone}）
发现：已填报工时与里程碑进度不符（{work}、{milestone}）
发现：已填报 145.00 人天（{work}）
发现：投入超出 30 人天（{work}、{milestone}）""")
    # Kept: two real facts, nothing guessed. Dropped: a guessed consequence, a single fact, a made-up number.
    assert [f["text"] for f in result["findings"]] == ["缺口 18.00 人天待客户确认，唯一的里程碑「年度框架评审」在 2026-12-20 到期"]
    assert result["findings"][0]["evidence"][0].startswith(f"{work} 工时：已填报 145.00 人天")
    assert project_analysis.parse("发现：x（F99、F98）") and review("结论：a\n发现：上线与回款同日（F98、F99）")["findings"] == []


def test_the_review_runs_on_the_analysis_model(tmp_path, monkeypatch):
    sent = scripted(monkeypatch, ["结论：里程碑需处理"])
    with TestClient(cloudflare(tmp_path, pm_analysis_model="@cf/meta/llama-3.3-70b-instruct-fp8-fast")) as client:
        response = client.post("/api/v1/projects/TPL149/ai-analysis")
    assert response.status_code == 200, response.text
    assert sent[0]["url"].endswith("/ai/v1/chat/completions") and sent[0]["body"]["model"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert response.json()["data"]["model"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def test_findings_drop_restatements_and_citation_prefixes():
    facts = project_analysis.fact_sheet(QYD203, LABELS)
    ids = {fact.split(". ", 1)[1].split("：", 1)[0]: fact.split(".", 1)[0] for fact in facts}
    risk, work, milestone = ids["风险等级"], ids["工时"], next(v for k, v in ids.items() if k.startswith("未完成里程碑"))
    result = review(f"""结论：项目整体进度滞后
发现：{risk} 与 {work} 相关，风险等级为关注，缺口 18.00 人天（{risk}、{work}）
发现：{work} 与 {milestone} 相关，缺口 18.00 人天待确认时里程碑在 2026-12-20 到期（{work}、{milestone}）""")
    assert [f["text"] for f in result["findings"]] == ["缺口 18.00 人天待确认时里程碑在 2026-12-20 到期"], \
        "a finding resting on the risk-level fact only restates the alert; the 「F5 与 F9 相关，」 prefix is removed"
    assert result["conclusion"].startswith("风险等级关注"), "「滞后」 needs an overdue fact"

