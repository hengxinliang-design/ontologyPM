"""The PM Assistant evaluation scorer (scripts/eval_cases.py): names an answer mentions only to set them apart.

The answers below follow what doubao-seed-evolving actually wrote in the 2026-11-09 evaluation (R3, R4, Q1, S3),
shortened. Before this rule the scorer counted every name in them as a wrong extra mention.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_cases import Expect, score_answer  # noqa: E402

PEOPLE = {"王磊", "陈静", "何飞", "刘洋", "冯雪", "郑凯"}


def employees(*names):
    return Expect(set(names), "employee", True)


def projects(*codes):
    return Expect(set(codes), "project", True)


def test_a_sub_list_under_an_excluding_lead_line_is_not_counted():
    answer = """## 结论
共有 2 名员工处于持续过载：

| 员工 | 利用率 |
|---|---:|
| 王磊 | 137.5% |
| 陈静 | 118.8% |

## 说明
- 以下人员当月利用率超过过载线，但**连续月数不足，未判定为持续过载**：
  - 何飞：106.2%，连续 1 个月
  - 刘洋：118.8%，连续 1 个月
- 本月尚无工时数据。
"""
    result = score_answer(answer, employees("王磊", "陈静"), "哪些员工处于持续过载状态？", PEOPLE)
    assert result["passed"] and result["extra"] == [] and result["set_apart"] == ["何飞", "刘洋"]


def test_a_name_set_apart_in_its_own_sentence_is_not_counted():
    answer = "闲置风险员工：郑凯（0%）。\n\n补充：另有 **冯雪（37.5%）** 属于首个有工时月份，按口径**不评价闲置**。"
    result = score_answer(answer, employees("郑凯"), "哪些员工有闲置风险？", PEOPLE)
    assert result["passed"] and result["set_apart"] == ["冯雪"]


def test_projects_said_to_be_not_over_budget_are_not_counted():
    answer = """**当前工时已超预算的项目有 1 个：SEY104。**
- 另有 SEY103 预算使用率 85%，**尚未超支**。
- QYD203、TPL149 未维护预算人天，无法判断是否超预算，不属于已确认超支。
"""
    result = score_answer(answer, projects("SEY104"), "工时超预算的项目有哪些？", set())
    assert result["passed"] and result["set_apart"] == ["QYD203", "SEY103", "TPL149"]


def test_items_under_an_excluding_heading_are_not_counted_but_a_positive_mention_elsewhere_is():
    answer = """### 一、可释放项目
- SEY104：里程碑全部完成。
- LNY202：12 月底到期。

### 二、暂不能释放、但要重点盯的项目
- SEY101：仍有 555 人天剩余。
- HXJ201：剩余 483 人天。

### 三、建议
1. 释放出的人力可补位 HXJ201。
"""
    result = score_answer(answer, projects("SEY104"), "哪些项目快结束了，人员可以释放？", set())
    assert not result["passed"]
    assert result["extra"] == ["HXJ201", "LNY202"], "HXJ201 is also named in a positive line; LNY202 is a real extra"
    assert result["set_apart"] == ["SEY101"]


def test_a_lead_list_item_covers_only_the_items_indented_below_it():
    answer = """- 以下人员不计入：
  - 何飞
- 刘洋 持续过载
"""
    result = score_answer(answer, employees(), "哪些员工处于持续过载状态？", PEOPLE)
    assert result["extra"] == ["刘洋"] and result["set_apart"] == ["何飞"]


def test_exclusion_wording_never_takes_recall_away():
    answer = "持续过载：王磊。陈静不是首月，同样持续过载。"
    result = score_answer(answer, employees("王磊", "陈静"), "哪些员工处于持续过载状态？", PEOPLE)
    assert result["recall"] == 1.0 and result["passed"]


def test_a_plain_extra_mention_is_still_wrong():
    answer = "持续过载的员工有王磊、陈静和何飞。"
    result = score_answer(answer, employees("王磊", "陈静"), "哪些员工处于持续过载状态？", PEOPLE)
    assert not result["passed"] and result["extra"] == ["何飞"] and result["set_apart"] == []
