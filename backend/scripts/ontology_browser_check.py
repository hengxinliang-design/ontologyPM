"""Boundary and exception checks for the ontology browser's API (functional test campaign, 2026-11-01).

Phase A runs against the local demo server (http://127.0.0.1:8000, authentication disabled, demo data from
seed_demo.py) as the administrator: schema and versions, object lists, details, links, available actions, actions,
object sets, aggregation, functions, the action log and the index. Writes use validateOnly; the only real executions
target objects that are already locked, so they must be rejected and change nothing.
Phase B starts the app in-process with authentication on the test fixtures and checks the roles: browser actions
are admin-only, a project manager's data scope holds in every endpoint, and properties under a marking the user
lacks can be neither read, filtered, sorted, aggregated nor grouped.

Every case states the expected status (and optionally a check on the body); any 500 is a failure. Results are written
to docs/test-reports/ontology-browser-cases.md.

    .venv/bin/python scripts/ontology_browser_check.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

BACKEND = Path(__file__).resolve().parents[1]
REPORT = BACKEND.parent / "docs" / "test-reports" / "ontology-browser-cases.md"
BASE = os.environ.get("ONTOLOGY_CHECK_BASE", "http://127.0.0.1:8000")
O = "/api/v1/ontology"


@dataclass
class Result:
    phase: str
    case_id: str
    area: str
    title: str
    expected: str
    actual: str
    ok: bool
    note: str = ""


RESULTS: list[Result] = []


def run(client: Any, phase: str, case_id: str, area: str, title: str, method: str, path: str, *, body: Any = None,
        params: dict | None = None, headers: dict | None = None, expect: int | set[int] = 200,
        check: Callable[[Any], str | bool | None] | None = None) -> Any:
    response = None
    for attempt in range(2):  # a 500 can leave the keep-alive connection reset; retry once on a fresh one
        try:
            response = client.request(method, path, json=body, params=params, headers=headers or {})
            break
        except Exception as exc:
            if attempt == 1:  # transport errors are failures too
                RESULTS.append(Result(phase, case_id, area, title, str(expect), f"异常 {exc.__class__.__name__}", False, str(exc)[:160]))
                return None
    expected = expect if isinstance(expect, set) else {expect}
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    ok = response.status_code in expected and response.status_code < 500
    note = ""
    if ok and check is not None:
        try:
            verdict = check(payload)
        except Exception as exc:  # a check that cannot read the body fails the case
            verdict = f"检查失败：{exc.__class__.__name__} {exc}"
        if verdict is False or isinstance(verdict, str):
            ok = False
            note = verdict if isinstance(verdict, str) else "响应内容不符合预期"
    if not ok and not note:
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        note = json.dumps(detail, ensure_ascii=False)[:200] if detail is not None else ""
    RESULTS.append(Result(phase, case_id, area, title, "/".join(map(str, sorted(expected))), str(response.status_code), ok, note))
    return payload


def data(payload: Any) -> Any:
    return payload.get("data") if isinstance(payload, dict) else None


# ====================================================================== phase A
def phase_a() -> None:
    c = httpx.Client(base_url=BASE, timeout=60)
    A = lambda *args, **kwargs: run(c, "A", *args, **kwargs)  # noqa: E731
    schema = data(c.get(f"{O}/schema").json())
    types = {t["apiName"]: t for t in schema["objectTypes"]}

    # ---- schema, versions, index
    A("S01", "元数据", "读取 schema", "GET", f"{O}/schema", check=lambda p: len(data(p)["objectTypes"]) == 14)
    A("S02", "元数据", "版本列表", "GET", f"{O}/versions", check=lambda p: any(v["active"] for v in data(p)))
    A("S03", "元数据", "同一版本自比较：无差异", "GET", f"{O}/versions/diff", params={"from": "8.0.0", "to": "8.0.0"},
      check=lambda p: data(p)["summary"].get("breaking", 0) == 0)
    A("S04", "元数据", "差异：未登记的版本", "GET", f"{O}/versions/diff", params={"from": "1.0.0", "to": "8.0.0"}, expect=404)
    A("S05", "元数据", "差异：版本号格式错误（8.0）", "GET", f"{O}/versions/diff", params={"from": "8.0", "to": "8.0.0"}, expect=422)
    A("S06", "元数据", "差异：缺少 to 参数", "GET", f"{O}/versions/diff", params={"from": "8.0.0"}, expect=422)
    A("S07", "元数据", "版本文档：不存在的版本", "GET", f"{O}/versions/9.9.9", expect=404)
    A("S08", "元数据", "版本文档：路径穿越样式的版本号", "GET", f"{O}/versions/..%2F..%2Fetc", expect={404, 422})
    A("S09", "元数据", "版本文档：当前版本", "GET", f"{O}/versions/8.0.0", check=lambda p: len(data(p)) > 0)
    A("S10", "索引", "索引状态", "GET", f"{O}/index", check=lambda p: len(data(p)["objectTypes"]) == 14)
    A("S11", "索引", "刷新索引", "POST", f"{O}/index/refresh", check=lambda p: "rebuilt" in data(p))

    # ---- object lists
    for name in types:
        A(f"L-{name}", "对象列表", f"{name} 首页", "GET", f"{O}/objects/{name}", params={"pageSize": 5},
          check=lambda p: isinstance(data(p), list) and "totalObjects" in p["meta"])
    A("L01", "对象列表", "不存在的对象类型", "GET", f"{O}/objects/Nope", expect=404)
    A("L02", "对象列表", "对象类型大小写不符（project）", "GET", f"{O}/objects/project", expect=404)
    A("L03", "对象列表", "pageSize=0", "GET", f"{O}/objects/Project", params={"pageSize": 0}, expect=422)
    A("L04", "对象列表", "pageSize=201（上限 200）", "GET", f"{O}/objects/Project", params={"pageSize": 201}, expect=422)
    A("L05", "对象列表", "pageSize=200（上限）", "GET", f"{O}/objects/TimeEntry", params={"pageSize": 200},
      check=lambda p: len(data(p)) == 200)
    A("L06", "对象列表", "page=0", "GET", f"{O}/objects/Project", params={"page": 0}, expect=422)
    A("L07", "对象列表", "page 远超总页数：空页不报错", "GET", f"{O}/objects/Project", params={"page": 9999},
      check=lambda p: data(p) == [] and p["meta"]["totalPages"] >= 1)
    A("L08", "对象列表", "page 为非数字", "GET", f"{O}/objects/Project", params={"page": "abc"}, expect=422)
    A("L09", "对象列表", "搜索 201 个字符（上限 200）", "GET", f"{O}/objects/Project", params={"q": "x" * 201}, expect=422)
    for i, q in enumerate(["%", "_", "'", '"', "<script>alert(1)</script>", "😀", "   ", "SEY", "sey", "仓储"]):
        A(f"L1{i}", "对象列表", f"搜索特殊输入 {q!r}", "GET", f"{O}/objects/Project", params={"q": q},
          check=lambda p: isinstance(data(p), list))
    A("L20", "对象列表", "搜索不区分大小写（sey 与 SEY 结果相同）", "GET", f"{O}/objects/Project", params={"q": "sey"},
      check=lambda p: p["meta"]["totalObjects"] == c.get(f"{O}/objects/Project", params={"q": "SEY"}).json()["meta"]["totalObjects"] > 0)
    A("L21", "对象列表", "排序：降序更新时间", "GET", f"{O}/objects/Intervention", params={"orderBy": "-updatedAt"})
    A("L22", "对象列表", "排序：不存在的属性", "GET", f"{O}/objects/Project", params={"orderBy": "nope"}, expect={404, 422})
    A("L23", "对象列表", "排序：81 个字符（上限 80）", "GET", f"{O}/objects/Project", params={"orderBy": "a" * 81}, expect=422)
    A("L24", "对象列表", "排序：只有减号", "GET", f"{O}/objects/Project", params={"orderBy": "-"}, expect={404, 422})
    A("L25", "对象列表", "筛选：干预状态=open", "GET", f"{O}/objects/Intervention", params={"where.status": "open"},
      check=lambda p: all(o["properties"]["status"] == "open" for o in data(p)) and data(p))
    A("L26", "对象列表", "筛选：不存在的属性", "GET", f"{O}/objects/Project", params={"where.nope": "1"}, expect={404, 422})
    A("L27", "对象列表", "筛选：枚举中没有的值（返回空，不报错）", "GET", f"{O}/objects/Intervention", params={"where.status": "bogus"},
      check=lambda p: data(p) == [])
    A("L28", "对象列表", "筛选：布尔值 true", "GET", f"{O}/objects/Intervention", params={"where.overdue": "true"},
      check=lambda p: data(p) and all(o["properties"]["overdue"] is True for o in data(p)))
    A("L29", "对象列表", "月份：工时记录 2026-10", "GET", f"{O}/objects/TimeEntry", params={"month": "2026-10", "pageSize": 5},
      check=lambda p: all(o["properties"]["workDate"].startswith("2026-10") for o in data(p)))
    A("L30", "对象列表", "月份：2026-13", "GET", f"{O}/objects/TimeEntry", params={"month": "2026-13"}, expect=422)
    A("L31", "对象列表", "月份：abcd", "GET", f"{O}/objects/TimeEntry", params={"month": "abcd"}, expect=422)
    A("L32", "对象列表", "月份：8 个字符（上限 7）", "GET", f"{O}/objects/TimeEntry", params={"month": "2026-100"}, expect=422)
    A("L33", "对象列表", "月份：对没有统计期间的对象类型（项目）", "GET", f"{O}/objects/Project", params={"month": "2026-10"}, expect={200, 422})
    A("L34", "对象列表", "月份区间：结束早于开始", "GET", f"{O}/objects/Milestone", params={"month": "2026-10", "monthTo": "2026-01"}, expect=422)
    A("L35", "对象列表", "月份区间：只有结束月份", "GET", f"{O}/objects/Milestone", params={"monthTo": "2026-10"}, expect={200, 422})
    A("L36", "对象列表", "月份区间：里程碑 2026-01 至 2026-03", "GET", f"{O}/objects/Milestone", params={"month": "2026-01", "monthTo": "2026-03"},
      check=lambda p: isinstance(data(p), list) and data(p))
    A("L37", "对象列表", "未来月份：工时记录 2099-01（空）", "GET", f"{O}/objects/TimeEntry", params={"month": "2099-01"}, check=lambda p: data(p) == [])

    # ---- object details and links
    sample = {name: (c.get(f"{O}/objects/{name}", params={"pageSize": 1}).json()["data"] or [None])[0] for name in types}
    for name, obj in sample.items():
        if obj is None:
            continue
        A(f"D-{name}", "对象详情", f"{name} 详情", "GET", f"{O}/objects/{name}/{obj['primaryKey']}",
          check=lambda p: "links" in data(p) and "properties" in data(p))
        A(f"AC-{name}", "可用动作", f"{name} 可用动作", "GET", f"{O}/objects/{name}/{obj['primaryKey']}/actions")
        for link in types[name].get("links", []) or []:
            pass
    A("D01", "对象详情", "不存在的主键", "GET", f"{O}/objects/Project/NOPE999", expect=404)
    A("D02", "对象详情", "超长主键（1000 字符）", "GET", f"{O}/objects/Project/{'X' * 1000}", expect=404)
    A("D03", "对象详情", "主键含空格与中文", "GET", f"{O}/objects/Customer/不存在 客户", expect=404)
    A("D04", "对象详情", "主键含编码斜杠", "GET", f"{O}/objects/Project/SEY101%2F..", expect=404)
    A("D05", "对象详情", "主键大小写不符（sey101）", "GET", f"{O}/objects/Project/sey101", expect=404)
    A("D06", "对象详情", "员工按月查看 2026-10", "GET", f"{O}/objects/Employee/E101", params={"month": "2026-10"},
      check=lambda p: data(p)["period"] is not None)
    A("D07", "对象详情", "员工按月：非法月份", "GET", f"{O}/objects/Employee/E101", params={"month": "10-2026"}, expect=422)
    A("D08", "对象详情", "员工按月：没有工时的月份（2026-12）", "GET", f"{O}/objects/Employee/E101", params={"month": "2026-12"})
    A("D09", "对象详情", "同名员工按编号区分（E101 与 E202 都叫王磊）", "GET", f"{O}/objects/Employee/E202",
      check=lambda p: data(p)["primaryKey"] == "E202" and data(p)["properties"]["employeeName"] == "王磊")
    A("D10", "对象详情", "离职员工（E132 只有 1–8 月工时）", "GET", f"{O}/objects/Employee/E132")
    A("D11", "对象详情", "未建档代码的成本对象 NEW201", "GET", f"{O}/objects/CostObject/NEW201")
    A("D12", "对象详情", "内部代码 TSG999 不是项目", "GET", f"{O}/objects/Project/TSG999", expect=404)

    # every link of every sample object (also exercises the reverse names)
    for link in schema["linkTypes"]:
        for forward, from_type, name in ((True, link["sourceObjectType"], link["apiName"]),
                                          (False, link["targetObjectType"], link.get("reverseApiName"))):
            obj = sample.get(from_type)
            if not name or obj is None:
                continue
            A(f"K-{from_type}.{name}", "关系", f"{from_type} → {name}", "GET", f"{O}/objects/{from_type}/{obj['primaryKey']}/links/{name}",
              params={"pageSize": 5}, check=lambda p: isinstance(data(p), list))
    A("K01", "关系", "不存在的关系名", "GET", f"{O}/objects/Project/SEY101/links/nope", expect=404)
    A("K02", "关系", "关系名属于其他对象类型（Project 上用 paymentNode）", "GET", f"{O}/objects/Project/SEY101/links/paymentNode", expect=404)
    A("K03", "关系", "pageSize=0", "GET", f"{O}/objects/Project/SEY101/links/milestones", params={"pageSize": 0}, expect=422)
    A("K04", "关系", "pageSize=201", "GET", f"{O}/objects/Project/SEY101/links/milestones", params={"pageSize": 201}, expect=422)
    A("K05", "关系", "page 远超总页数", "GET", f"{O}/objects/Project/SEY101/links/milestones", params={"page": 500}, check=lambda p: data(p) == [])
    A("K06", "关系", "从不存在的对象出发", "GET", f"{O}/objects/Project/NOPE/links/milestones", expect=404)
    A("K07", "关系", "关系包含已逻辑删除记录？（SEY101 里程碑只计有效记录）", "GET", f"{O}/objects/Project/SEY101/links/milestones",
      check=lambda p: len(data(p)) == 4)
    A("K08", "关系", "年度计划到本年签约合同", "GET", f"{O}/objects/AnnualOperatingPlan/2026-实施交付部/links/signedContracts",
      check=lambda p: len(data(p)) >= 8)
    A("K09", "关系", "悬空关系：TSG999 成本对象的项目", "GET", f"{O}/objects/CostObject/TSG999/links/project", expect={200, 404},
      check=lambda p: data(p) == [] if isinstance(p, dict) and "data" in p else True)

    # ---- available actions on locked objects
    sey101 = c.get("/api/v1/projects/SEY101").json()["data"]
    done = next(m for m in sey101["milestones"] if m["status"] == "completed")
    open_milestone = next(m for m in sey101["milestones"] if m["status"] != "completed")
    paid = next(n for n in sey101["payment_nodes"] if n["status"] == "paid")
    unpaid = next(n for n in sey101["payment_nodes"] if n["status"] != "paid")
    linked_term = next(t for t in sey101["payment_terms"] if t["linked_milestone_id"])
    jtk = c.get("/api/v1/projects/JTK205").json()["data"]
    closed = next(i for i in jtk["interventions"] if i["status"] == "closed")

    def criteria_fail(action: str) -> Callable[[Any], bool | str]:
        def check(p: Any) -> bool | str:
            entry = next((a for a in data(p) if a["apiName"] == action), None)
            if entry is None:
                return f"可用动作中没有 {action}"
            return any(not c_["passed"] for c_ in entry["criteria"]) or f"{action} 的提交条件全部满足，应被锁定"
        return check

    A("AC01", "可用动作", "已完成里程碑：编辑应不满足条件", "GET", f"{O}/objects/Milestone/{done['milestone_id']}/actions",
      check=criteria_fail("editMilestone"))
    A("AC02", "可用动作", "已到账回款节点：编辑应不满足条件", "GET", f"{O}/objects/PaymentNode/{paid['payment_node_id']}/actions",
      check=criteria_fail("editPaymentNode"))
    A("AC03", "可用动作", "已关闭干预：更新状态应不满足条件", "GET", f"{O}/objects/Intervention/{closed['intervention_id']}/actions",
      check=criteria_fail("updateInterventionStatus"))
    A("AC04", "可用动作", "已关联里程碑的回款约定：删除应不满足条件", "GET", f"{O}/objects/PaymentTerm/{linked_term['payment_term_id']}/actions",
      check=criteria_fail("deletePaymentTerm"))
    A("AC05", "可用动作", "不存在的对象", "GET", f"{O}/objects/Milestone/ms_nope/actions", expect=404)

    # ---- actions: request shape
    def apply(action: str, parameters: Any, validate: Any = True) -> dict:
        return {"parameters": parameters, "validateOnly": validate}

    act = f"{O}/actions"
    valid_false = lambda p: data(p)["valid"] is False  # noqa: E731
    A("X01", "动作", "不存在的动作", "POST", f"{act}/nope/apply", body=apply("nope", {}), expect=404)
    A("X02", "动作", "parameters 为数组", "POST", f"{act}/createPaymentTerm/apply", body={"parameters": [1, 2]}, expect=422)
    A("X03", "动作", "parameters 为字符串", "POST", f"{act}/createPaymentTerm/apply", body={"parameters": "x"}, expect=422)
    A("X04", "动作", "validateOnly 为字符串", "POST", f"{act}/createPaymentTerm/apply", body={"parameters": {}, "validateOnly": "yes"}, expect=422)
    A("X05", "动作", "请求体为数组", "POST", f"{act}/createPaymentTerm/apply", body=[1], expect=422)
    A("X06", "动作", "缺少必填参数（预检）", "POST", f"{act}/createPaymentTerm/apply", body=apply("createPaymentTerm", {"project": "SEY101"}),
      expect={200, 422}, check=lambda p: valid_false(p) if p.get("data") else True)
    A("X07", "动作", "缺少必填参数（执行）", "POST", f"{act}/createPaymentTerm/apply", body=apply("createPaymentTerm", {"project": "SEY101"}, False), expect=422)
    A("X08", "动作", "未知参数", "POST", f"{act}/createPaymentTerm/apply",
      body=apply("createPaymentTerm", {"project": "SEY101", "name": "x", "plannedAmount": "1", "evil": 1}), expect={200, 422},
      check=lambda p: valid_false(p) if p.get("data") else True)
    A("X09", "动作", "对象引用不存在", "POST", f"{act}/createPaymentTerm/apply",
      body=apply("createPaymentTerm", {"project": "NOPE", "name": "x", "plannedAmount": "1"}), expect={200, 404, 422},
      check=lambda p: valid_false(p) if p.get("data") else True)
    A("X10", "动作", "对象引用类型错误（数组）", "POST", f"{act}/createPaymentTerm/apply",
      body=apply("createPaymentTerm", {"project": ["SEY101"], "name": "x", "plannedAmount": "1"}), expect={200, 422},
      check=lambda p: valid_false(p) if p.get("data") else True)

    def bad(case: str, title: str, action: str, params: dict) -> None:
        A(case, "动作参数", title, "POST", f"{act}/{action}/apply", body=apply(action, params), expect={200, 422},
          check=lambda p: valid_false(p) if p.get("data") else True)

    def good(case: str, title: str, action: str, params: dict) -> None:
        A(case, "动作参数", title, "POST", f"{act}/{action}/apply", body=apply(action, params),
          check=lambda p: data(p)["valid"] is True or f"预检未通过：{data(p)}")

    term = {"project": "SEY101", "name": "测试约定"}
    bad("P01", "金额为负数", "createPaymentTerm", {**term, "plannedAmount": "-1"})
    bad("P02", "金额为文本", "createPaymentTerm", {**term, "plannedAmount": "abc"})
    bad("P03", "金额为无穷大 1e309", "createPaymentTerm", {**term, "plannedAmount": "1e309"})
    bad("P04", "金额为 NaN", "createPaymentTerm", {**term, "plannedAmount": "NaN"})
    good("P05", "金额 0（边界）", "createPaymentTerm", {**term, "plannedAmount": "0"})
    good("P06", "金额 0.01", "createPaymentTerm", {**term, "plannedAmount": "0.01"})
    bad("P07", "金额 3 位小数 0.001", "createPaymentTerm", {**term, "plannedAmount": "0.001"})
    bad("P08", "金额带千分位 1,000", "createPaymentTerm", {**term, "plannedAmount": "1,000"})
    bad("P09", "回款比例 101", "createPaymentTerm", {**term, "plannedAmount": "1", "paymentRatio": "101"})
    bad("P10", "回款比例 -1", "createPaymentTerm", {**term, "plannedAmount": "1", "paymentRatio": "-1"})
    good("P11", "回款比例 100（边界）", "createPaymentTerm", {**term, "plannedAmount": "1", "paymentRatio": "100"})
    bad("P12", "名称为空字符串", "createPaymentTerm", {"project": "SEY101", "name": "", "plannedAmount": "1"})
    bad("P13", "名称只有空格", "createPaymentTerm", {"project": "SEY101", "name": "   ", "plannedAmount": "1"})
    bad("P14", "名称 10000 字符", "createPaymentTerm", {"project": "SEY101", "name": "长" * 10000, "plannedAmount": "1"})
    good("P15", "名称含 HTML（按文本保存）", "createPaymentTerm", {"project": "SEY101", "name": "<img src=x onerror=alert(1)>", "plannedAmount": "1"})
    milestone = {"project": "SEY101", "name": "测试里程碑"}
    bad("P20", "日期 2026-02-30", "createMilestone", {**milestone, "plannedEnd": "2026-02-30"})
    bad("P21", "日期格式 2026/10/01", "createMilestone", {**milestone, "plannedEnd": "2026/10/01"})
    bad("P22", "结束早于开始", "createMilestone", {**milestone, "plannedStart": "2026-10-10", "plannedEnd": "2026-10-01"})
    good("P23", "开始等于结束（边界）", "createMilestone", {**milestone, "plannedStart": "2026-11-10", "plannedEnd": "2026-11-10"})
    bad("P24", "整数参数为小数 sequenceNo=1.5", "createMilestone", {**milestone, "plannedEnd": "2026-11-10", "sequenceNo": 1.5})
    bad("P25", "整数参数为文本", "createMilestone", {**milestone, "plannedEnd": "2026-11-10", "sequenceNo": "abc"})
    bad("P26", "整数参数为布尔 true", "createMilestone", {**milestone, "plannedEnd": "2026-11-10", "sequenceNo": True})
    bad("P27", "关联不存在的回款约定", "createMilestone", {**milestone, "plannedEnd": "2026-11-10", "paymentTermId": "term_nope"})
    bad("P28", "关联已被其他里程碑关联的回款约定", "createMilestone",
        {**milestone, "plannedEnd": "2026-11-10", "paymentTermId": linked_term["payment_term_id"]})
    bad("P29", "关联其他项目的回款约定", "createMilestone", {"project": "SEY103", "name": "跨项目", "plannedEnd": "2026-11-10",
                                                   "paymentTermId": next(t for t in c.get('/api/v1/projects/SEY101').json()['data']['payment_terms'] if not t['linked_milestone_id'] or True)['payment_term_id']})
    bad("P30", "枚举值不在选项中（干预类型）", "openIntervention", {"project": "SEY101", "title": "x", "dueDate": "2026-12-01", "interventionType": "politics"})
    bad("P31", "缺少截止日期（8.0.0 必填）", "openIntervention", {"project": "SEY101", "title": "x"})
    good("P32", "截止日期早于今天（允许，立即逾期）", "openIntervention", {"project": "SEY101", "title": "x", "dueDate": "2000-01-01"})
    bad("P33", "系统实施项目清空预算", "configureProject", {"project": "SEY101", "contractType": "construction", "plannedPersonDays": None})
    bad("P34", "预算人天为负数", "configureProject", {"project": "SEY101", "plannedPersonDays": "-10"})
    bad("P35", "每人天小时数 0", "configureProject", {"project": "SEY101", "hoursPerPersonDay": "0"})
    bad("P36", "每人天小时数 25", "configureProject", {"project": "SEY101", "hoursPerPersonDay": "25"})
    bad("P37", "项目计划结束早于开始", "configureProject", {"project": "SEY101", "plannedStart": "2026-12-01", "plannedEnd": "2026-01-01"})
    bad("P38", "项目状态枚举外的值", "configureProject", {"project": "SEY101", "lifecycleStatus": "archived"})
    bad("P39", "经营成本：未来月份", "recordOperatingCost", {"policy": "default", "month": "2099-01", "laborCost": "1"})
    bad("P40", "经营成本：月份 2026-00", "recordOperatingCost", {"policy": "default", "month": "2026-00", "laborCost": "1"})
    bad("P41", "经营成本：月份含日期 2026-10-01", "recordOperatingCost", {"policy": "default", "month": "2026-10-01", "laborCost": "1"})
    bad("P42", "经营成本：负数", "recordOperatingCost", {"policy": "default", "month": "2026-10", "travelCost": "-5"})
    bad("P43", "年度计划：年份 0", "setAnnualOperatingPlan", {"policy": "default", "year": 0})
    bad("P44", "年度计划：年份 99999", "setAnnualOperatingPlan", {"policy": "default", "year": 99999})
    bad("P45", "年度计划：负目标", "setAnnualOperatingPlan", {"policy": "default", "year": 2026, "collectionTarget": "-1"})
    bad("P46", "年度计划：利用率目标 150%", "setAnnualOperatingPlan", {"policy": "default", "year": 2026, "utilizationTargetPercent": "150"})
    bad("P47", "运营口径：关注线高于最低线", "updateOperatingPolicy", {"policy": "default", "attentionUtilizationPercent": "90", "minimumUtilizationPercent": "75"})
    bad("P48", "运营口径：每日小时数 0", "updateOperatingPolicy", {"policy": "default", "standardHoursPerDay": "0"})
    bad("P49", "运营口径：同期项目上限 0", "updateOperatingPolicy", {"policy": "default", "maxConcurrentProjects": "0"})
    bad("P50", "商机结论：结论 won（不在允许值中）", "recordOpportunityOutcome", {"costObject": "XNY307", "outcome": "won", "reason": "其他"})
    bad("P51", "商机结论：对合同项目判丢单", "recordOpportunityOutcome", {"costObject": "SEY101", "outcome": "lost", "reason": "其他"})
    bad("P52", "重新激活：没有人工结论的商机", "reopenOpportunity", {"costObject": "WDR101", "reason": "测试"})
    bad("P53", "重新激活：理由为空", "reopenOpportunity", {"costObject": "MRL304", "reason": ""})
    bad("P54", "商机结论：结论日期在未来", "recordOpportunityOutcome", {"costObject": "XNY307", "outcome": "lost", "reason": "其他", "outcomeDate": "2099-01-01"})
    bad("P55", "确认到账：金额为负", "confirmPaymentReceived", {"paymentNode": unpaid["payment_node_id"], "actualAmount": "-1"})
    bad("P56", "确认到账：到账日期在未来", "confirmPaymentReceived", {"paymentNode": unpaid["payment_node_id"], "actualAmount": "1", "actualDate": "2099-01-01"})
    bad("P57", "里程碑状态枚举外的值", "editMilestone", {"milestone": open_milestone["milestone_id"], "status": "done"})

    # locked objects: real executions, must be rejected
    A("Z01", "锁定", "再次确认已完成里程碑", "POST", f"{act}/confirmMilestoneCompletion/apply",
      body=apply("", {"milestone": done["milestone_id"]}, False), expect=422)
    A("Z02", "锁定", "编辑已完成里程碑", "POST", f"{act}/editMilestone/apply",
      body=apply("", {"milestone": done["milestone_id"], "name": "改名"}, False), expect=422)
    A("Z03", "锁定", "删除已完成里程碑", "POST", f"{act}/deleteMilestone/apply", body=apply("", {"milestone": done["milestone_id"]}, False), expect=422)
    A("Z04", "锁定", "再次确认已到账回款", "POST", f"{act}/confirmPaymentReceived/apply",
      body=apply("", {"paymentNode": paid["payment_node_id"], "actualAmount": "1", "actualDate": "2026-10-01"}, False), expect=422)
    A("Z05", "锁定", "编辑已到账回款节点", "POST", f"{act}/editPaymentNode/apply",
      body=apply("", {"paymentNode": paid["payment_node_id"], "plannedAmount": "1"}, False), expect=422)
    A("Z06", "锁定", "删除已到账回款节点", "POST", f"{act}/deletePaymentNode/apply", body=apply("", {"paymentNode": paid["payment_node_id"]}, False), expect=422)
    A("Z07", "锁定", "重新打开已关闭干预", "POST", f"{act}/updateInterventionStatus/apply",
      body=apply("", {"intervention": closed["intervention_id"], "status": "open"}, False), expect=422)
    A("Z08", "锁定", "删除已关联里程碑的回款约定", "POST", f"{act}/deletePaymentTerm/apply",
      body=apply("", {"paymentTerm": linked_term["payment_term_id"]}, False), expect=422)
    A("Z09", "锁定", "编辑已到账的回款约定", "POST", f"{act}/editPaymentTerm/apply",
      body=apply("", {"paymentTerm": next(t for t in sey101['payment_terms'] if t['name'] == '首付款')['payment_term_id'], "plannedAmount": "1"}, False),
      expect=422)
    after = c.get("/api/v1/projects/SEY101").json()["data"]
    A("Z10", "锁定", "被拒绝的执行没有改动数据（SEY101 里程碑、节点、约定不变）", "GET", "/api/v1/projects/SEY101",
      check=lambda p: (len(data(p)["milestones"]), len(data(p)["payment_nodes"]), len(data(p)["payment_terms"])) ==
      (len(sey101["milestones"]), len(sey101["payment_nodes"]), len(sey101["payment_terms"])) and
      next(m for m in data(p)["milestones"] if m["milestone_id"] == done["milestone_id"])["name"] == done["name"])

    # ---- object sets
    load = f"{O}/objectSets/loadObjects"
    agg = f"{O}/objectSets/aggregate"
    base = {"type": "base", "objectType": "Project"}
    A("Q01", "对象集", "基础对象集", "POST", load, body={"objectSet": base}, check=lambda p: len(p["data"]) > 0)
    A("Q02", "对象集", "缺少 objectSet", "POST", load, body={}, expect=422)
    A("Q03", "对象集", "objectSet 为字符串", "POST", load, body={"objectSet": "Project"}, expect=422)
    A("Q04", "对象集", "未知对象集类型", "POST", load, body={"objectSet": {"type": "magic"}}, expect=422)
    A("Q05", "对象集", "未知对象类型", "POST", load, body={"objectSet": {"type": "base", "objectType": "Nope"}}, expect={404, 422})
    flt = lambda where: {"objectSet": {"type": "filter", "objectSet": base, "where": where}}  # noqa: E731
    for i, where in enumerate([
        {"type": "eq", "field": "riskLevel", "value": "critical"},
        {"type": "in", "field": "projectNumber", "value": ["SEY101", "HXJ201"]},
        {"type": "gt", "field": "budgetUtilization", "value": 100},
        {"type": "gte", "field": "budgetUtilization", "value": 85},
        {"type": "lt", "field": "budgetUtilization", "value": 60},
        {"type": "lte", "field": "budgetUtilization", "value": 60},
        {"type": "startsWith", "field": "projectNumber", "value": "SEY"},
        {"type": "contains", "field": "subject", "value": "实施"},
        {"type": "isNull", "field": "plannedPersonDays", "value": True},
        {"type": "and", "value": [{"type": "eq", "field": "lifecycleStatus", "value": "active"}, {"type": "not", "value": {"type": "eq", "field": "riskLevel", "value": "healthy"}}]},
        {"type": "or", "value": [{"type": "eq", "field": "projectNumber", "value": "SEY101"}, {"type": "eq", "field": "projectNumber", "value": "HXJ201"}]},
    ]):
        A(f"Q1{i:02d}", "对象集筛选", f"筛选 {where['type']}", "POST", load, body=flt(where), check=lambda p: isinstance(p["data"], list))
    A("Q20", "对象集筛选", "gt 结果正确（只有 SEY104 超 100%）", "POST", load, body=flt({"type": "gt", "field": "budgetUtilization", "value": 100}),
      check=lambda p: sorted(o["primaryKey"] for o in p["data"]) == ["SEY104"])
    A("Q21", "对象集筛选", "未知字段", "POST", load, body=flt({"type": "eq", "field": "nope", "value": 1}), expect=422)
    A("Q22", "对象集筛选", "缺 field", "POST", load, body=flt({"type": "eq", "value": 1}), expect=422)
    A("Q23", "对象集筛选", "in 的值不是数组", "POST", load, body=flt({"type": "in", "field": "projectNumber", "value": "SEY101"}), expect=422)
    A("Q24", "对象集筛选", "文本属性做大小比较（按字典序，设计如此）", "POST", load, body=flt({"type": "gt", "field": "subject", "value": "S"}),
      check=lambda p: all(o["properties"]["subject"] > "S" for o in p["data"]))
    A("Q25", "对象集筛选", "数值比较给文本值", "POST", load, body=flt({"type": "gt", "field": "budgetUtilization", "value": "abc"}), expect=422)
    A("Q26", "对象集筛选", "isNull 的值不是布尔", "POST", load, body=flt({"type": "isNull", "field": "plannedPersonDays", "value": "yes"}), expect=422)
    A("Q27", "对象集筛选", "and 的值为空数组", "POST", load, body=flt({"type": "and", "value": []}), expect={200, 422})
    A("Q28", "对象集筛选", "未知条件类型", "POST", load, body=flt({"type": "regex", "field": "subject", "value": ".*"}), expect=422)
    A("Q29", "对象集筛选", "in 空数组（返回空）", "POST", load, body=flt({"type": "in", "field": "projectNumber", "value": []}),
      check=lambda p: p["data"] == [])
    deep: dict = {"type": "eq", "field": "projectNumber", "value": "SEY101"}
    for _ in range(40):
        deep = {"type": "not", "value": deep}
    A("Q30", "对象集筛选", "条件嵌套 40 层", "POST", load, body=flt(deep), expect={200, 422})
    very_deep: dict = {"type": "eq", "field": "projectNumber", "value": "SEY101"}
    for _ in range(3000):
        very_deep = {"type": "not", "value": very_deep}
    A("Q30b", "对象集筛选", "条件嵌套 3000 层（应拒绝而不是递归溢出）", "POST", load, body=flt(very_deep), expect=422)
    nested: dict = base
    for _ in range(20):
        nested = {"type": "filter", "objectSet": nested, "where": {"type": "isNull", "field": "projectNumber", "value": False}}
    A("Q31", "对象集", "对象集嵌套 20 层（上限 12）", "POST", load, body={"objectSet": nested}, expect=422)
    A("Q32", "对象集", "searchAround 项目→里程碑", "POST", load, body={"objectSet": {"type": "searchAround", "objectSet": base, "link": "milestones"}},
      check=lambda p: all(o["objectType"] == "Milestone" for o in p["data"]))
    A("Q33", "对象集", "searchAround 未知关系", "POST", load, body={"objectSet": {"type": "searchAround", "objectSet": base, "link": "nope"}}, expect=422)
    A("Q34", "对象集", "union 不同对象类型", "POST", load, body={"objectSet": {"type": "union", "objectSets": [base, {"type": "base", "objectType": "Milestone"}]}}, expect=422)
    A("Q35", "对象集", "union 只有一个对象集", "POST", load, body={"objectSet": {"type": "union", "objectSets": [base]}}, expect=422)
    A("Q36", "对象集", "static 对象集含不存在的主键", "POST", load, body={"objectSet": {"type": "static", "objectType": "Project", "objects": ["SEY101", "NOPE"]}},
      check=lambda p: [o["primaryKey"] for o in p["data"]] == ["SEY101"])
    A("Q37", "对象集", "pageSize=0", "POST", load, body={"objectSet": base, "pageSize": 0}, expect=422)
    A("Q38", "对象集", "pageSize=501（上限 500）", "POST", load, body={"objectSet": base, "pageSize": 501}, expect=422)
    A("Q39", "对象集", "pageSize 为文本", "POST", load, body={"objectSet": base, "pageSize": "10"}, expect=422)
    A("Q40", "对象集", "select 未知属性", "POST", load, body={"objectSet": base, "select": ["nope"]}, expect=422)
    A("Q41", "对象集", "select 不是数组", "POST", load, body={"objectSet": base, "select": "projectNumber"}, expect=422)
    A("Q42", "对象集", "orderBy 方向非法", "POST", load, body={"objectSet": base, "orderBy": {"fields": [{"field": "projectNumber", "direction": "up"}]}}, expect=422)
    A("Q43", "对象集", "orderBy 未知字段", "POST", load, body={"objectSet": base, "orderBy": {"fields": [{"field": "nope"}]}}, expect=422)
    A("Q44", "对象集", "分页令牌伪造", "POST", load, body={"objectSet": base, "pageSize": 1, "pageToken": "forged"}, expect=422)
    first = c.post(load, json={"objectSet": base, "pageSize": 3}).json()
    A("Q45", "对象集", "分页令牌：第二页与第一页不重叠", "POST", load, body={"objectSet": base, "pageSize": 3, "pageToken": first.get("nextPageToken")},
      check=lambda p: not ({o["primaryKey"] for o in p["data"]} & {o["primaryKey"] for o in first["data"]}))
    A("Q50", "聚合", "count 按风险分组", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "count"}], "groupBy": [{"type": "exact", "field": "riskLevel"}]},
      check=lambda p: sum(int(g["metrics"][0]["value"]) for g in p["data"]) == p["totalCount"] == 20)
    A("Q51", "聚合", "sum 合同额", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "sum", "field": "contractValue"}]})
    A("Q52", "聚合", "sum 文本属性", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "sum", "field": "subject"}]}, expect=422)
    A("Q53", "聚合", "aggregation 为空数组", "POST", agg, body={"objectSet": base, "aggregation": []}, expect=422)
    A("Q54", "聚合", "缺少 aggregation", "POST", agg, body={"objectSet": base}, expect=422)
    A("Q55", "聚合", "groupBy 不是数组", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "count"}], "groupBy": {"type": "exact"}}, expect=422)
    A("Q56", "聚合", "maxGroupCount=0", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "count"}], "groupBy": [{"type": "exact", "field": "riskLevel", "maxGroupCount": 0}]}, expect=422)
    A("Q57", "聚合", "maxGroupCount=1001（上限 1000）", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "count"}], "groupBy": [{"type": "exact", "field": "riskLevel", "maxGroupCount": 1001}]}, expect=422)
    A("Q58", "聚合", "fixedWidth 宽度 0", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "count"}], "groupBy": [{"type": "fixedWidth", "field": "budgetUtilization", "fixedWidth": 0}]}, expect=422)
    A("Q59", "聚合", "ranges 元素格式错误", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "count"}], "groupBy": [{"type": "ranges", "field": "budgetUtilization", "ranges": [1, 2]}]}, expect=422)
    A("Q60", "聚合", "min/max 日期", "POST", agg, body={"objectSet": {"type": "base", "objectType": "Milestone"}, "aggregation": [{"type": "min", "field": "plannedEnd"}, {"type": "max", "field": "plannedEnd"}]})
    A("Q61", "聚合", "avg 空集合", "POST", agg, body={"objectSet": flt({"type": "in", "field": "projectNumber", "value": []})["objectSet"], "aggregation": [{"type": "avg", "field": "budgetUtilization"}]})
    A("Q62", "聚合", "未知指标类型", "POST", agg, body={"objectSet": base, "aggregation": [{"type": "median", "field": "budgetUtilization"}]}, expect=422)

    # ---- functions
    fn = f"{O}/functions"
    for f in schema["functions"]:
        if not f["permitted"] or any(p.get("required") for p in f["parameters"]):
            continue
        A(f"F-{f['apiName']}", "函数", f"{f['apiName']} 默认参数", "POST", f"{fn}/{f['apiName']}/execute", body={"parameters": {}})
    A("F01", "函数", "不存在的函数", "POST", f"{fn}/nope/execute", body={"parameters": {}}, expect=404)
    A("F02", "函数", "缺少必填项目参数", "POST", f"{fn}/projectOverview/execute", body={"parameters": {}}, expect=422)
    A("F03", "函数", "项目不存在", "POST", f"{fn}/projectOverview/execute", body={"parameters": {"project": "NOPE"}}, expect={404, 422})
    A("F04", "函数", "工作日历：开始晚于结束", "POST", f"{fn}/workingCalendar/execute", body={"parameters": {"startDate": "2026-10-31", "endDate": "2026-10-01"}}, expect=422)
    A("F05", "函数", "工作日历：跨 10 年", "POST", f"{fn}/workingCalendar/execute", body={"parameters": {"startDate": "2020-01-01", "endDate": "2030-12-31"}}, expect={200, 422})
    A("F06", "函数", "工作日历：非法日期", "POST", f"{fn}/workingCalendar/execute", body={"parameters": {"startDate": "2026-02-30", "endDate": "2026-03-01"}}, expect=422)
    A("F07", "函数", "工作日历：同一天", "POST", f"{fn}/workingCalendar/execute", body={"parameters": {"startDate": "2026-10-01", "endDate": "2026-10-01"}},
      check=lambda p: data(p)["result"]["working_days"] == 0)
    for i, months in enumerate([0, -1, 7, "abc", 1, 6]):  # the stated range is 1–6
        A(f"F1{i}", "函数", f"资源测算 months={months!r}", "POST", f"{fn}/resourceForecast/execute", body={"parameters": {"months": months}},
          expect=200 if months in (1, 6) else 422)
    A("F20", "函数", "资源健康：非法月份", "POST", f"{fn}/resourceHealth/execute", body={"parameters": {"month": "2026-13"}}, expect=422)
    A("F21", "函数", "资源健康：没有工时的月份 2025-01", "POST", f"{fn}/resourceHealth/execute", body={"parameters": {"month": "2025-01"}},
      check=lambda p: data(p)["result"]["data"] == [])
    A("F22", "函数", "部门复盘：年份 1900", "POST", f"{fn}/departmentOperatingReview/execute", body={"parameters": {"year": 1900}}, expect={200, 422})
    A("F23", "函数", "部门复盘：月份不在年份内", "POST", f"{fn}/departmentOperatingReview/execute", body={"parameters": {"year": 2026, "month": "2025-05"}}, expect=422)
    A("F24", "函数", "项目期间健康：结束早于开始", "POST", f"{fn}/projectHealthForPeriod/execute",
      body={"parameters": {"project": "SEY101", "startMonth": "2026-10", "endMonth": "2026-01"}}, expect=422)
    A("F25", "函数", "员工利用率：不存在的员工", "POST", f"{fn}/employeeUtilization/execute", body={"parameters": {"employee": "E999", "month": "2026-10"}}, expect={404, 422})
    A("F26", "函数", "项目台账：pageSize 0", "POST", f"{fn}/projectList/execute", body={"parameters": {"pageSize": 0}}, expect=422)
    A("F27", "函数", "项目台账：伪造游标", "POST", f"{fn}/projectList/execute", body={"parameters": {"cursor": "forged"}}, expect=422)
    A("F28", "函数", "待维护清单：未知对象类型", "POST", f"{fn}/maintenanceWorklist/execute", body={"parameters": {"objectType": "Nope"}}, expect={200, 422})
    A("F29", "函数", "工时评价：period=week（不支持）", "POST", f"{fn}/employeeWorktimeAssessment/execute", body={"parameters": {"period": "week"}}, expect=422)
    A("F30", "函数", "参数不是对象", "POST", f"{fn}/portfolioSummary/execute", body={"parameters": [1]}, expect=422)

    # ---- action log
    A("G01", "动作审计", "全部", "GET", f"{O}/action-log", check=lambda p: isinstance(p["data"], list))
    A("G02", "动作审计", "按对象类型筛选（含以项目为目标的新建里程碑）", "GET", f"{O}/action-log", params={"objectType": "Milestone"},
      check=lambda p: all(e["objectType"] == "Milestone" or e["actionType"] == "createMilestone" for e in p["data"]))  # creating one targets the project
    A("G03", "动作审计", "未知对象类型（空）", "GET", f"{O}/action-log", params={"objectType": "Nope"}, check=lambda p: p["data"] == [])
    A("G04", "动作审计", "pageSize=101（上限 100）", "GET", f"{O}/action-log", params={"pageSize": 101}, expect=422)
    A("G05", "动作审计", "被拒绝的执行留有审计", "GET", f"{O}/action-log", params={"objectType": "Milestone", "pageSize": 100},
      check=lambda p: any(e["status"] == "rejected" for e in p["data"]))
    A("G06", "动作审计", "预检不写审计", "GET", f"{O}/action-log", params={"pageSize": 100},
      check=lambda p: not any((e.get("parameters") or {}).get("name") == "测试约定" for e in p["data"]))


# ====================================================================== phase B
def phase_b() -> None:
    sys.path.insert(0, str(BACKEND / "tests"))
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app
    from test_api import settings
    from test_roles_and_scope import login, with_project_manager

    tmp = Path(tempfile.mkdtemp())
    with TestClient(create_app(replace(settings(tmp), auth_enabled=True))) as c:
        B = lambda *args, **kwargs: run(c, "B", *args, **kwargs)  # noqa: E731
        admin, pm = with_project_manager(c)  # 刘浪 owns TPL149 and WDR101

        def user(username: str, role: str, permissions: list[str] | None = None, person: str | None = None) -> dict:
            body = {"username": username, "password": "secret-pass-1", "role": role}
            if permissions is not None:
                body["permissions"] = permissions
            if person:
                body["person_name"] = person
            response = c.post("/api/v1/users", headers=admin, json=body)
            assert response.status_code == 201, response.text
            return login(c, username, "secret-pass-1")

        pmo = user("pmo.one", "pmo")
        analyst = user("analyst.one", "analyst")
        narrow = user("narrow.one", "analyst", permissions=["health", "ontology"])  # no FINANCE, no PERSONNEL
        B("R01", "认证", "未登录读取 schema", "GET", f"{O}/schema", expect=401)
        B("R02", "认证", "伪造令牌", "GET", f"{O}/schema", headers={"Authorization": "Bearer forged"}, expect=401)
        B("R03", "认证", "未登录执行动作", "POST", f"{O}/actions/createPaymentTerm/apply", body={"parameters": {}}, expect=401)
        valid = {"parameters": {"project": "TPL149", "name": "x", "plannedAmount": "1"}, "validateOnly": True}
        B("R10", "浏览器动作", "管理员在浏览器执行动作（预检）", "POST", f"{O}/actions/createPaymentTerm/apply", body=valid, headers=admin)
        B("R11", "浏览器动作", "PMO 在浏览器执行动作被拒（D12）", "POST", f"{O}/actions/createPaymentTerm/apply", body=valid, headers=pmo, expect=403)
        B("R12", "浏览器动作", "PMO 经业务渠道执行同一动作", "POST", f"{O}/actions/createPaymentTerm/apply", body=valid,
          headers={**pmo, "X-TSG-Channel": "business"})
        B("R13", "浏览器动作", "分析人员经业务渠道执行（没有项目维护菜单）", "POST", f"{O}/actions/createPaymentTerm/apply", body=valid,
          headers={**analyst, "X-TSG-Channel": "business"}, expect=403)
        B("R14", "浏览器动作", "渠道头大小写变体 BUSINESS 不放行", "POST", f"{O}/actions/createPaymentTerm/apply", body=valid,
          headers={**pmo, "X-TSG-Channel": "BUSINESS"}, expect=403)
        B("R15", "元数据", "非管理员读取版本", "GET", f"{O}/versions", headers=analyst, expect={200, 403})
        B("R16", "索引", "非管理员刷新索引", "POST", f"{O}/index/refresh", headers=pmo, expect=403)
        # project manager scope
        own = {"TPL149", "WDR101"}
        B("R20", "数据范围", "项目经理：项目列表只有本人项目", "GET", f"{O}/objects/Project", headers=pm, params={"pageSize": 200},
          check=lambda p: {o["primaryKey"] for o in p["data"]} <= own)
        B("R21", "数据范围", "项目经理：他人项目详情", "GET", f"{O}/objects/Project/ZMJ113", headers=pm, expect=404)
        B("R22", "数据范围", "项目经理：对象集 static 指定他人项目", "POST", f"{O}/objectSets/loadObjects", headers=pm,
          body={"objectSet": {"type": "static", "objectType": "Project", "objects": ["ZMJ113"]}}, check=lambda p: p["data"] == [])
        B("R23", "数据范围", "项目经理：聚合计数只算本人项目", "POST", f"{O}/objectSets/aggregate", headers=pm,
          body={"objectSet": {"type": "base", "objectType": "Project"}, "aggregation": [{"type": "count"}]},
          check=lambda p: p["data"][0]["metrics"][0]["value"] == 2 or f"计数为 {p['data'][0]['metrics'][0]['value']}")
        B("R24", "数据范围", "项目经理：里程碑列表只含本人项目", "GET", f"{O}/objects/Milestone", headers=pm, params={"pageSize": 200},
          check=lambda p: all(o["properties"]["projectNumber"] in own for o in p["data"]))
        B("R25", "数据范围", "项目经理：动作审计只看本人项目", "GET", f"{O}/action-log", headers=pm, params={"pageSize": 100},
          check=lambda p: all((e.get("projectNumber") or "TPL149") in own for e in p["data"]))
        B("R26", "数据范围", "项目经理：函数 portfolioSummary 只算本人项目", "POST", f"{O}/functions/portfolioSummary/execute", headers=pm,
          body={"parameters": {}}, check=lambda p: p["data"]["result"]["total_projects"] == 2)
        B("R27", "数据范围", "项目经理：经业务渠道给他人项目建约定", "POST", f"{O}/actions/createPaymentTerm/apply",
          headers={**pm, "X-TSG-Channel": "business"}, body={"parameters": {"project": "ZMJ113", "name": "x", "plannedAmount": "1"}, "validateOnly": True},
          expect={200, 403, 404, 422}, check=lambda p: p.get("data", {}).get("valid") is False if isinstance(p, dict) and p.get("data") else True)
        B("R28", "数据范围", "项目经理：读取员工（PERSONNEL 对象，无菜单）", "GET", f"{O}/objects/Employee", headers=pm, expect=403)
        # markings: a user without FINANCE must not learn amounts in any way
        B("R30", "安全标记", "列表中金额被遮蔽", "GET", f"{O}/objects/Project", headers=narrow,
          check=lambda p: all(o["properties"].get("contractValue") is None and "contractValue" in o["redactedProperties"] for o in p["data"]))
        B("R31", "安全标记", "按金额排序被拒", "GET", f"{O}/objects/Project", headers=narrow, params={"orderBy": "-contractValue"}, expect=403)
        B("R32", "安全标记", "按金额筛选（列表 where）被拒", "GET", f"{O}/objects/Project", headers=narrow, params={"where.contractValue": "149900.00"}, expect=403)
        B("R33", "安全标记", "对象集按金额比较被拒", "POST", f"{O}/objectSets/loadObjects", headers=narrow,
          body={"objectSet": {"type": "filter", "objectSet": {"type": "base", "objectType": "Project"},
                              "where": {"type": "gt", "field": "contractValue", "value": 100000}}}, expect={403, 422})
        B("R34", "安全标记", "对金额求和被拒", "POST", f"{O}/objectSets/aggregate", headers=narrow,
          body={"objectSet": {"type": "base", "objectType": "Project"}, "aggregation": [{"type": "sum", "field": "contractValue"}]}, expect={403, 422})
        B("R35", "安全标记", "按金额分组被拒", "POST", f"{O}/objectSets/aggregate", headers=narrow,
          body={"objectSet": {"type": "base", "objectType": "Project"}, "aggregation": [{"type": "count"}],
                "groupBy": [{"type": "exact", "field": "contractValue"}]}, expect={403, 422})
        B("R36", "安全标记", "对金额 min/max 被拒", "POST", f"{O}/objectSets/aggregate", headers=narrow,
          body={"objectSet": {"type": "base", "objectType": "Project"}, "aggregation": [{"type": "max", "field": "contractValue"}]}, expect={403, 422})
        B("R37", "安全标记", "select 金额属性（应遮蔽）", "POST", f"{O}/objectSets/loadObjects", headers=narrow,
          body={"objectSet": {"type": "base", "objectType": "Project"}, "select": ["projectNumber", "contractValue"]},
          check=lambda p: all(o["properties"].get("contractValue") is None for o in p["data"]))
        B("R38", "安全标记", "搜索金额字样不泄露（q=149900）", "GET", f"{O}/objects/Project", headers=narrow, params={"q": "149900"},
          check=lambda p: p["data"] == [])
        B("R39", "安全标记", "详情中金额被遮蔽", "GET", f"{O}/objects/Project/TPL149", headers=narrow,
          check=lambda p: p["data"]["properties"].get("contractValue") is None)
        B("R40", "安全标记", "函数项目概览的金额被遮蔽", "POST", f"{O}/functions/projectList/execute", headers=narrow, body={"parameters": {}},
          expect={200, 403}, check=lambda p: all(r.get("contract_value") is None for r in p["data"]["result"]["data"]) if isinstance(p, dict) and p.get("data") else True)
        B("R41", "安全标记", "金额 fixedWidth 分组被拒", "POST", f"{O}/objectSets/aggregate", headers=narrow,
          body={"objectSet": {"type": "base", "objectType": "Project"}, "aggregation": [{"type": "count"}],
                "groupBy": [{"type": "fixedWidth", "field": "contractValue", "fixedWidth": 100000}]}, expect={403, 422})
        B("R42", "安全标记", "isNull 探测金额被拒", "POST", f"{O}/objectSets/loadObjects", headers=narrow,
          body={"objectSet": {"type": "filter", "objectSet": {"type": "base", "objectType": "Project"},
                              "where": {"type": "isNull", "field": "contractValue", "value": False}}}, expect={403, 422})
        B("R43", "安全标记", "通过关系读取回款节点金额被遮蔽", "GET", f"{O}/objects/Project/TPL149/links/paymentNodes", headers=narrow,
          expect={200, 403}, check=lambda p: all(o["properties"].get("plannedAmount") is None for o in p["data"]) if isinstance(p, dict) and isinstance(p.get("data"), list) else True)


def write_report() -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    failed = [r for r in RESULTS if not r.ok]
    lines = ["# 本体浏览器边界与异常测试：用例与结果", "",
             f"由 `backend/scripts/ontology_browser_check.py` 生成。共 {len(RESULTS)} 个用例，通过 {len(RESULTS) - len(failed)}，未通过 {len(failed)}。",
             "阶段 A：本地演示数据、管理员；阶段 B：开启登录的测试数据、五种角色。", "",
             "| 阶段 | 编号 | 范围 | 用例 | 预期 | 实际 | 结果 | 说明 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in RESULTS:
        note = r.note.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.phase} | {r.case_id} | {r.area} | {r.title} | {r.expected} | {r.actual} | {'通过' if r.ok else '**未通过**'} | {note} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    phase_a()
    phase_b()
    write_report()
    failed = [r for r in RESULTS if not r.ok]
    print(f"{len(RESULTS)} cases, {len(failed)} failed")
    for r in failed:
        print(f"✗ {r.phase} {r.case_id} [{r.area}] {r.title}: expected {r.expected}, got {r.actual}  {r.note[:160]}")
