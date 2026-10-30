# TSG PM API 文档

本文档对应当前代码基线：FastAPI 后端 `TSG PM Data API v1.0.0`，React 前端通过 `frontend/src/api.ts` 调用这些接口。

## 1. 基础约定

### 1.1 Base URL

本地开发默认：

```text
http://127.0.0.1:8000
```

前端开发环境通过 Vite 代理访问：

```text
http://127.0.0.1:5173
```

### 1.2 鉴权

后端未设置 `XTCRM_API_TOKEN` 时，接口不校验鉴权，适用于本地开发。

生产环境设置 `XTCRM_API_TOKEN` 后，所有 `/api/v1/*` 接口需要：

```http
Authorization: Bearer <XTCRM_API_TOKEN>
```

`GET /health` 不需要鉴权。

### 1.3 金额与日期格式

- 金额统一使用十进制字符串，避免浮点误差，例如 `"420000.00"`。
- CRM 金额字段在 JSON 中使用：

```json
{
  "amount": "149900.00",
  "currency": "CNY",
  "display": "¥149,900.00"
}
```

- 日期使用 `YYYY-MM-DD`。
- 日期时间使用 ISO 8601，例如 `2026-07-06T03:00:00-04:00`。

### 1.4 错误响应

后端统一返回 `application/problem+json`：

```json
{
  "type": "https://xtcrm.local/problems/validation_error",
  "title": "Validation failed",
  "status": 422,
  "detail": "...",
  "instance": "/api/v1/projects/WDR101/milestones/ms_xxx",
  "code": "VALIDATION_ERROR",
  "trace_id": "local"
}
```

常见状态码：

| 状态码 | 含义 |
|---:|---|
| 200 | 请求成功 |
| 201 | 创建成功 |
| 202 | 同步任务已接受 |
| 401 | Token 缺失或无效 |
| 404 | 记录、项目或任务不存在 |
| 409 | 当前已有同步任务运行 |
| 422 | 请求字段不合法或业务规则不允许 |
| 503 | 当前没有可用的成功快照 |

`503` 也用于 PM Assistant 的模型服务暂不可用、授权无效或额度不足；响应 `detail` 会给出可执行的处理建议，不会包含密钥信息。

## 2. 枚举定义

### 2.1 CRM 数据类型

| 值 | 说明 |
|---|---|
| `contract_order` | 合同/订单 |
| `sales_opportunity` | 销售机会 |

### 2.2 合同类型

| 值 | 说明 | 是否要求预算人天 |
|---|---|---|
| `construction` | 建设类 | 是 |
| `maintenance` | 维护类 | 是 |
| `open_ended` | 开口类 | 否 |
| `sales` | 销售类 | 否 |
| `unknown` | 未分类 | 否，但会进入人工维护提醒 |

### 2.3 生命周期状态

| 值 | 说明 |
|---|---|
| `planned` | 计划中 |
| `active` | 执行中 |
| `on_hold` | 暂停 |
| `completed` | 已完成 |
| `closed` | 已关闭 |

### 2.4 风险等级

| 值 | 说明 |
|---|---|
| `healthy` | 正常 |
| `warning` | 预警 |
| `critical` | 严重 |

### 2.5 待办紧急程度

| 值 | 说明 |
|---|---|
| `completed` | 已确认完成 |
| `overdue` | 已逾期 |
| `due_today` | 今天到期 |
| `due_soon` | 7 天内到期 |
| `upcoming` | 未到期 |

## 3. 系统接口

### 3.1 健康检查

```http
GET /health
```

响应：

```json
{
  "status": "ok"
}
```

### 3.2 中国节假日与调休日历

```http
GET /api/v1/calendar/china-holidays
```

为项目计划、里程碑等日期控件提供只读标记。后端读取并缓存中国节假日与调休补班 iCalendar 订阅；缓存默认 12 小时，订阅暂不可用时不会影响项目页面正常使用。

```json
{
  "data": [
    {"date": "2026-10-01", "name": "国庆节假期", "kind": "holiday"},
    {"date": "2026-10-10", "name": "国庆节补班", "kind": "makeup_workday"}
  ],
  "meta": {"available": true, "stale": false, "cached_at": "2026-09-04T12:00:00+00:00", "source": "ical.muhan.org"}
}
```

- `holiday` 在控件中显示为红色标记；`makeup_workday` 显示为蓝色标记，悬停可查看日期名称。
- 该接口只辅助排期判断，不会自动改写项目计划日期、里程碑日期或现有工时统计。

### 3.3 项目异常 AI 研判

```http
POST /api/v1/projects/{project_number}/ai-analysis
```

仅在项目详情的“项目待处理事项”区域由用户点击触发，不会在页面加载时自动调用模型。服务端基于该项目当前的异常提示、CRM 合同金额/开票/回款、预算与实际人天、人员投入汇总、月度工时、里程碑与回款节点，输出自然语言研判。

分析固定覆盖：风险判断、人员与资源安排、工时与成本合理性、回款与项目行动。系统尚未维护人员成本单价或结算单价时，回答会明确说明不能计算金额成本和利润。

响应结构与 [3.4 PM Assistant 问答](#34-pm-assistant-问答) 一致。

### 3.4 PM Assistant 问答

```http
POST /api/v1/pm-assistant/chat
Content-Type: application/json
```

服务端会先从当前 SQLite 中按项目编号、主题、客户、负责人、风险和 CRM 字段检索相关资料，再将精简后的事实资料提交给 LLM。密钥仅保存在服务端环境变量 `OPENAI_API_KEY`，不会发送到浏览器。

请求：

```json
{
  "message": "哪些项目需要优先关注？",
  "history": [
    {"role": "user", "content": "本月回款情况如何？"},
    {"role": "assistant", "content": "……"}
  ]
}
```

- `message` 必填，最长 2000 个字符。
- `history` 可选；最多取最近 6 条对话作为上下文。
- 模型为服务端配置的 `TSG_PM_ASSISTANT_MODEL`，默认 `gpt-5.2`。

响应：

```json
{
  "data": {
    "answer": "当前优先关注……",
    "sources": ["项目主数据库：3 个相关项目", "CRM 数据：5 条相关记录"],
    "model": "gpt-5.2"
  }
}
```

## 4. CRM 记录接口

CRM 记录是系统接口同步后的只读事实数据。人工维护字段不写入 CRM 记录表。
列表会返回全部成功解析并已入库的合同与销售机会原始记录；项目主数据库才按项目编号执行汇总。

### 4.1 查询 CRM 记录列表

```http
GET /api/v1/records
```

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `data_type` | string | 否 | `contract_order` 或 `sales_opportunity` |
| `project_number` | string | 否 | 项目编号 |
| `owner` | string | 否 | CRM 所有者 |
| `status` | string | 否 | 状态 |
| `probability` | integer | 否 | 0 至 100 |
| `sales_stage` | string | 否 | 销售机会阶段，例如“项目方案 60%” |
| `q` | string | 否 | 主题、客户等关键字 |
| `signed_from` | string | 否 | 签约开始日期 |
| `signed_to` | string | 否 | 签约结束日期 |
| `updated_since` | string | 否 | 若当前快照未更新，返回空列表 |
| `cursor` | string | 否 | 上一响应 `meta.next_cursor`，用于获取下一页 |
| `page_size` | integer | 否 | 默认 `50`，最大 `200` |
| `sort` | string | 否 | 默认 `project_number`，支持前缀 `-` 降序 |

`sort` 支持字段：

```text
project_number, subject, signed_at, owner, probability, status
```

示例：

```http
GET /api/v1/records?data_type=contract_order&page_size=50&sort=-signed_at
```

响应：

```json
{
  "data": [
    {
      "record_id": "rec_xxx",
      "data_type": "contract_order",
      "project_number": "TPL149",
      "subject": "TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目",
      "customer": "〖TPL〗",
      "category": "实施服务",
      "signed_at": "2026-06-16",
      "total_amount": {
        "amount": "149900.00",
        "currency": "CNY",
        "display": "¥149,900.00"
      },
      "received_amount": {
        "amount": "0.00",
        "currency": "CNY",
        "display": "¥0.00"
      },
      "invoiced_amount": {
        "amount": "74950.00",
        "currency": "CNY",
        "display": "¥74,950.00"
      },
      "owner": "Li Qiang",
      "probability": 100,
      "status": "未完成",
      "merged_from_opportunity": true,
      "updated_at": "2026-07-01T16:01:22-04:00"
    }
  ],
  "meta": {
    "page_size": 50,
    "total_records": 1,
    "total_pages": 1,
    "next_cursor": null,
    "schema_version": "1.0",
    "updated_at": "2026-07-01T16:01:22-04:00"
  },
  "links": {
    "self": "/api/v1/records?data_type=contract_order&page_size=50",
    "next": null,
    "previous": null
  }
}
```

当 `next_cursor` 非空时，将它原样作为 `cursor` 参数传回以获取下一页。为避免深页扫描，`page>1` 且未提供 `cursor` 会返回 `422`。

### 4.2 查询单条 CRM 记录

```http
GET /api/v1/records/{record_id}
```

响应：

```json
{
  "data": {
    "record_id": "rec_xxx",
    "data_type": "contract_order",
    "project_number": "TPL149",
    "subject": "TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目"
  }
}
```

## 5. 项目接口

项目以 `project_number` 为主键聚合 CRM 合同/商机、人工维护配置、工时、里程碑、回款节点和风险提醒。

### 5.1 查询项目主数据库视图

```http
GET /api/v1/projects
```

该接口是“项目主数据库”的查询视图，不额外复制一份主表数据。后端按 `project_number` 实时聚合：

- CRM 合同/商机主数据。
- 员工工时主数据。
- 人工维护的合同类型、生命周期、预算工时。
- 里程碑和回款节点。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `contract_type` | string | 否 | 合同类型枚举 |
| `lifecycle_status` | string | 否 | 生命周期枚举 |
| `risk_level` | string | 否 | 风险等级枚举 |
| `q` | string | 否 | 项目编号、主题、客户、项目经理关键字 |

响应：

```json
{
  "data": [
    {
      "project_number": "TPL149",
      "subject": "TPL149-天津鹏翎集团股份有限公司ERP/MOM与SRM接口项目",
      "customer": "〖TPL〗",
      "owner": "Li Qiang",
      "project_manager": "Li Qiang",
      "master_category": "项目",
      "contract_type": "construction",
      "lifecycle_status": "active",
      "status": "active",
      "planned_start": "2026-06-16",
      "planned_end": "2026-08-31",
      "contract_count": 1,
      "opportunity_count": 0,
      "contract_value": "149900.00",
      "crm_received_amount": "0.00",
      "outstanding_receivable_amount": "149900.00",
      "received_amount": "0.00",
      "invoiced_amount": "74950.00",
      "planned_person_days": "30.00",
      "budget_hours": "240.00",
      "reported_hours": "20.00",
      "reported_person_days": "2.50",
      "customer_confirmed_hours": "20.00",
      "customer_confirmed_person_days": "2.50",
      "confirmation_gap_hours": "0.00",
      "confirmation_gap_person_days": "0.00",
      "actual_hours": "20.00",
      "actual_person_days": "2.50",
      "budget_utilization": 8.3,
      "billable_person_days": null,
      "milestone_total": 2,
      "milestone_completed": 0,
      "overdue_milestones": 0,
      "current_milestone": {
        "milestone_id": "ms_xxx",
        "name": "接口方案确认",
        "status": "in_progress",
        "planned_start": "2026-07-10",
        "planned_end": "2026-07-10",
        "deadline": {
          "days_remaining": 2,
          "urgency": "due_soon",
          "label": "还有 2 天"
        }
      },
      "payment_node_total": 2,
      "overdue_payment_nodes": 0,
      "payment_summary": {
        "total_nodes": 2,
        "paid_nodes": 0,
        "planned_amount": "149900.00",
        "actual_amount": "0.00",
        "next_node": {
          "payment_node_id": "pay_xxx",
          "name": "首期款",
          "planned_date": "2026-07-20",
          "planned_amount": "74950.00",
          "status": "invoiced",
          "deadline": {
            "days_remaining": 12,
            "urgency": "upcoming",
            "label": "还有 12 天"
          }
        }
      },
      "open_interventions": 0,
      "maintenance_complete": true,
      "maintenance_reasons": [],
      "pending_todos": 4,
      "overdue_todos": 0,
      "next_todo": null,
      "anomaly_count": 0,
      "anomaly_alerts": [],
      "risk_level": "healthy",
      "risk_reasons": []
    }
  ],
  "summary": {
    "total_projects": 3,
    "contract_value": "149900.00",
    "received_amount": "0.00",
    "critical_projects": 0,
    "warning_projects": 1
  }
}
```

项目主数据库建议表头与 API 字段映射：

| 页面表头 | API 字段 | 说明 |
|---|---|---|
| 项目代码 | `project_number` | 跨 CRM 与工时的业务主键 |
| 项目名称 | `subject` | 优先取合同/商机主题，无源数据时取项目代码 |
| 类别 | `master_category` | 有合同为“项目”，仅有商机为“商机” |
| 状态 | `status` / `lifecycle_status` | 人工维护生命周期状态 |
| CRM 回款金额 | `crm_received_amount` | 全部导入合同/订单的回款金额汇总 |
| 待回款金额 | `outstanding_receivable_amount` | 导入合同总金额减导入回款金额，最小为零 |
| 里程碑 | `current_milestone` | 当前未完成的最近里程碑；全部完成则返回最后完成节点 |
| 项目类型 | `contract_type` | 建设类、维护类、开口类、销售类、未分类 |
| 回款 | `payment_summary` | 回款节点完成数、计划金额、实收金额、下个节点 |
| 预算工时 | `budget_hours` | `planned_person_days * hours_per_person_day` |
| 累计实际工时 | `actual_hours` / `reported_hours` | 员工填报工时汇总 |

项目总览和项目运营监控不再提供“合同状态”字段或筛选。合同/订单的 CRM 状态按导入值保留，用于原始数据审计；当所有关联合同的来源状态均为结束类状态且统计期仍有工时填报时，项目运营监控会标记“额外投入”。待回款金额为全部导入合同总金额减全部导入回款金额，最小为零。项目明细中“合同金额、已开票、已回款（CRM）”均使用 CRM 导入模板字段；`received_amount` 仍表示里程碑中由管理人员确认到账的回款金额，两者互不覆盖。

### 5.2 查询项目详情

```http
GET /api/v1/projects/{project_number}
```

项目详情在项目组合字段基础上增加：

| 字段 | 说明 |
|---|---|
| `crm_records` | 该项目下的 CRM 合同/商机记录 |
| `milestones` | 里程碑明细 |
| `payment_nodes` | 回款节点明细 |
| `interventions` | 管理干预记录 |
| `settings` | 人工维护配置 |
| `todo_events` | 里程碑和回款待办 |
| `monthly_time_entries` | 按 `YYYY-MM` 聚合的月度工时清单，可用于点击月份展开明细；不再重复返回“最近工时”列表 |

`monthly_time_entries` 示例：

```json
[
  {
    "month": "2026-02",
    "entry_count": 53,
    "reported_hours": "424.00",
    "approved_hours": "384.00",
    "confirmation_gap_hours": "40.00",
    "reported_person_days": "53.00",
    "approved_person_days": "48.00",
    "confirmation_gap_person_days": "5.00",
    "entries": [
      {
        "external_id": "TIMESHEET-202601-202602-0001",
        "employee_name": "唐旋",
        "work_date": "2026-02-28",
        "hours": "8.0000",
        "approved_hours": "8.0000",
        "approval_status": "approved",
        "work_content": "1.研发领料取值逻辑变更#100%#8h"
      }
    ]
  }
]
```

### 5.3 查询项目健康度视图

```http
GET /api/v1/project-health
```

用途：按月份或时间范围生成项目健康度监控表，支撑页面中的“项目健康度监控”视图。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `month` | string | 否 | 单月统计，格式 `YYYY-MM` |
| `start_month` | string | 否 | 时间范围开始月份，格式 `YYYY-MM` |
| `end_month` | string | 否 | 时间范围结束月份，格式 `YYYY-MM` |
| `project_number` | string | 否 | 指定项目代码 |

规则：

- 传 `month` 时按单月统计。
- 传 `start_month/end_month` 时按范围统计。
- 都不传时默认使用系统当前月份。
- `累计实际(天)` 统计到 `end_month` 为止。
- `本期投入(天)` 统计当前单月或范围内工时。
- `上月投入(天)` 取 `start_month` 的上一个月，用于趋势对比。

响应：

```json
{
  "data": [
    {
      "project_number": "SEY101",
      "project_name": "SEY101-项目名称",
      "pm_owner": "PM Test",
      "project_status": "active",
      "planned_start": "2026-01-01",
      "planned_end": "2026-06-30",
      "budget_person_days": "120.00",
      "plan_months": 6,
      "monthly_budget_person_days": "20.00",
      "cumulative_actual_person_days": "147.62",
      "period_actual_hours": "424.00",
      "period_actual_person_days": "53.00",
      "previous_month_person_days": "94.62",
      "participant_count": 4,
      "burn_rate_percent": "123.0",
      "overrun_warning": "已超预算",
      "monthly_trend": "下降",
      "health_status": "已超预算",
      "completion_forecast": "已超预算",
      "remaining_budget_person_days": "-27.62",
      "current_milestone": null,
      "payment_summary": {
        "total_nodes": 0,
        "paid_nodes": 0,
        "planned_amount": "0.00",
        "actual_amount": "0.00",
        "next_node": null
      },
      "risk_reasons": ["尚未设置里程碑"]
    }
  ],
  "summary": {
    "total_projects": 1,
    "critical_projects": 1,
    "warning_projects": 0,
    "healthy_projects": 0,
    "no_baseline_projects": 0
  },
  "meta": {
    "start_month": "2026-02",
    "end_month": "2026-02",
    "mode": "month"
  }
}
```

项目健康度页面表头与字段映射：

| 页面表头 | API 字段 |
|---|---|
| 项目代码 | `project_number` |
| 项目名称 | `project_name` |
| PM负责人 | `pm_owner` |
| 项目状态 | `project_status` |
| 计划开始 | `planned_start` |
| 计划结束 | `planned_end` |
| 预算工时(天) | `budget_person_days` |
| 计划月数 | `plan_months` |
| 月均预算(天) | `monthly_budget_person_days` |
| 累计实际(天) | `cumulative_actual_person_days` |
| 本期投入(天) | `period_actual_person_days` |
| 上月投入(天) | `previous_month_person_days` |
| 参与人数 | `participant_count` |
| 燃尽率 | `burn_rate_percent` |
| 超支预警 | `overrun_warning` |
| 月度趋势 | `monthly_trend` |
| 综合健康状态 | `health_status` |
| 预计完工 | `completion_forecast` |
| 剩余预算(天) | `remaining_budget_person_days` |

### 5.4 查询项目 CRM 记录

```http
GET /api/v1/projects/{project_number}/records
```

响应：

```json
{
  "data": [],
  "meta": {
    "total_records": 0
  }
}
```

### 5.5 更新项目人工配置

```http
PATCH /api/v1/projects/{project_number}/settings
Content-Type: application/json
```

请求：

```json
{
  "contract_type": "construction",
  "lifecycle_status": "active",
  "planned_person_days": "40",
  "hours_per_person_day": "8",
  "planned_start": "2026-06-01",
  "planned_end": "2026-08-31",
  "project_manager": "Manager Wang",
  "notes": "项目备注"
}
```

业务规则：

- `construction` 和 `maintenance` 必须填写 `planned_person_days`。
- `open_ended` 不要求预算人天。
- 该接口只更新人工维护表，不会改写 CRM 原始记录。
- CRM 增量同步不会覆盖这些人工维护字段。

响应：

```json
{
  "data": {
    "project_number": "WDR101",
    "contract_type": "construction",
    "lifecycle_status": "active",
    "planned_person_days": "40.00"
  }
}
```

## 6. 里程碑接口

里程碑用于项目交付计划和商务回款节点统一维护。

### 6.1 创建里程碑

```http
POST /api/v1/projects/{project_number}/milestones
Content-Type: application/json
```

请求：

```json
{
  "name": "需求确认",
  "planned_start": "2026-06-01",
  "planned_end": "2026-06-30",
  "owner": "Manager Wang",
  "deliverable": "需求说明书",
  "payment_term_id": "term_9822ecef3f1e4ce6",
  "notes": "首付款节点"
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 里程碑名称 |
| `planned_start` | 否 | 计划开始日期；为空时取 `planned_end` |
| `planned_end` | 是 | 计划结束日期 |
| `owner` | 否 | 负责人 |
| `deliverable` | 否 | 输出物 |
| `payment_term_id` | 否 | 关联的回款约定；关联后以约定金额、计划结束日期生成回款节点。编辑时传 `null` 解除关联 |

本体 7.0.0（2026-10-28）起不再接受 `is_payment_node`、`payment_date`、`payment_amount`：兼容接口忽略这三个字段，回款节点只由关联的回款约定产生。已有的历史回款事件保留，关联回款约定后被替换。
| `notes` | 否 | 备注 |

响应：

```json
{
  "data": {
    "milestone_id": "ms_xxx",
    "project_number": "WDR101",
    "sequence_no": 1,
    "name": "需求确认",
    "planned_date": "2026-06-30",
    "planned_start": "2026-06-01",
    "planned_end": "2026-06-30",
    "status": "planned",
    "owner": "Manager Wang",
    "deliverable": "需求说明书",
    "is_payment_node": 1,
    "payment_node_id": "pay_xxx",
    "payment_date": "2026-07-01",
    "payment_amount": "412500.00",
    "payment_status": "planned"
  }
}
```

### 6.2 编辑待确认里程碑

```http
PATCH /api/v1/projects/{project_number}/milestones/{milestone_id}
Content-Type: application/json
```

请求：

```json
{
  "name": "需求与范围确认",
  "planned_start": "2026-06-02",
  "planned_end": "2026-07-02",
  "owner": "Admin User",
  "deliverable": "签字版需求说明书",
  "is_payment_node": true,
  "payment_date": "2026-07-05",
  "payment_amount": "420000"
}
```

最新业务规则：

- `status != completed` 的里程碑允许 admin 再次编辑。
- `status = completed` 的里程碑已确认完成，后端锁定，不允许编辑。
- 不允许通过 PATCH 直接把 `status` 设置为 `completed`；必须调用确认接口。
- 待确认里程碑关联的未到账回款节点可同步更新名称、计划回款日期、计划金额和备注。
- 待确认里程碑取消 `is_payment_node` 时：
  - 若关联回款节点未支付，则删除关联回款节点（本体 7.6.0 起为逻辑删除：标记失效，记录保留）。
  - 若关联回款节点已支付，则不允许取消，返回 422。

成功响应同创建接口。

已确认里程碑再次编辑时返回：

```json
{
  "status": 422,
  "detail": "completed milestones are locked and cannot be edited"
}
```

### 6.3 确认里程碑完成

```http
POST /api/v1/projects/{project_number}/milestones/{milestone_id}/confirm
Content-Type: application/json
```

请求：

```json
{
  "confirmed_by": "Manager Wang"
}
```

效果：

- 设置里程碑 `status=completed`。
- 写入 `completed_date`、`confirmed_by`、`confirmed_at`。
- 该里程碑后续不可再编辑。

响应：

```json
{
  "data": {
    "milestone_id": "ms_xxx",
    "status": "completed",
    "confirmed_by": "Manager Wang"
  }
}
```

## 7. 回款节点接口

### 7.1 创建回款节点（已取消）

`POST /api/v1/projects/{project_number}/payment-nodes` 已于本体 6.0.0（2026-10-28）取消，返回 404。新的回款计划先用「新建回款约定」登记合同回款条款，再在新建或编辑里程碑时关联约定，系统以约定金额和里程碑计划完成日生成回款节点。已有回款节点仍可编辑、确认到账和删除（未到账时）。

本体 7.6.0 起，里程碑、回款约定、回款节点的删除（`DELETE` 接口与本体删除动作）都是逻辑删除：行保留，写入 `deleted_at`、`deleted_by`，所有查询和本体对象不再返回它；对已删除对象再操作返回「找不到」。

### 7.2 确认回款到账

```http
POST /api/v1/projects/{project_number}/payment-nodes/{payment_node_id}/confirm
Content-Type: application/json
```

请求：

```json
{
  "confirmed_by": "Manager Wang",
  "actual_amount": "74950",
  "actual_date": "2026-07-21"
}
```

字段说明：

- `confirmed_by` 必填。
- `actual_amount` 为空时，默认取计划金额。
- `actual_date` 为空时，默认取系统当前日期。

响应：

```json
{
  "data": {
    "payment_node_id": "pay_xxx",
    "status": "paid",
    "actual_amount": "74950.00",
    "actual_date": "2026-07-21",
    "notes": "确认人：Manager Wang"
  }
}
```

## 8. 管理干预接口

### 8.1 创建管理干预

```http
POST /api/v1/projects/{project_number}/interventions
Content-Type: application/json
```

请求：

```json
{
  "title": "协调客户确认延期里程碑",
  "intervention_type": "management",
  "status": "open",
  "owner": "PMO",
  "due_date": "2026-07-10",
  "notes": "需要项目经理跟进"
}
```

响应：

```json
{
  "data": {
    "intervention_id": "act_xxx",
    "project_number": "TPL149",
    "title": "协调客户确认延期里程碑",
    "intervention_type": "management",
    "status": "open",
    "owner": "PMO",
    "due_date": "2026-07-10"
  }
}
```

## 9. 工时接口

当前工时系统真实 API 未最终确认，后端提供幂等导入接口用于接收标准化后的工时数据，也支持页面直接导入工时系统导出的 `.xlsx` 文件。

### 9.1 导入工时记录

```http
POST /api/v1/time-entries/import
Content-Type: application/json
```

请求：

```json
[
  {
    "external_id": "TIME-20260702-0001",
    "project_number": "TPL149",
    "employee_id": "E001",
    "employee_name": "Zhang San",
    "work_date": "2026-07-02",
    "hours": "8.00",
    "overtime_hours": "2.00",
    "approved_hours": "4.00",
    "approval_status": "approved",
    "work_content": "客户待确认工作",
    "source_updated_at": "2026-07-02T18:00:00+08:00"
  }
]
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `external_id` | 是 | 工时系统唯一 ID；用于识别已导入记录 |
| `project_number` | 是 | 项目编号 |
| `employee_id` | 是 | 员工编号 |
| `employee_name` | 是 | 员工姓名 |
| `work_date` | 是 | 工作日期 |
| `hours` | 是 | 员工填报工时，0 至 24 |
| `overtime_hours` | 否 | 加班工时，默认 0；与填报工时合计不得超过 24 |
| `approved_hours` | 是 | 客户确认工时，0 至 24 |
| `approval_status` | 是 | `draft`、`submitted`、`approved`、`rejected` |
| `work_content` | 是 | 工作内容 |
| `source_updated_at` | 是 | 工时源系统更新时间 |

响应：

```json
{
  "imported": 1,
  "unchanged": 0
}
```

业务规则：

- 导入采用追加模式：`external_id` 已存在时不覆盖、不修改任何已入库工时，只计入 `unchanged`。
- 只有此前不存在的 `external_id` 才会新增入库，并计入 `imported`。
- Excel 导入会按源行内容生成记录指纹；完全相同的行再次上传会跳过。由于一个员工在同一项目、同一天可能有多条真实工作记录，系统不会仅以员工、项目和日期去重；若源行内容发生变化，会保留原记录并追加为新记录。
- `hours` 表示已发生内部成本。
- `approved_hours` 表示客户确认或认可工时。
- `hours - approved_hours` 会聚合为项目级客户确认缺口，并触发 `TIME_CONFIRMATION_GAP` 提醒。
- 建设类、维护类项目按 `reported_person_days / planned_person_days` 计算预算使用率。

### 9.2 导入工时 Excel

```http
POST /api/v1/time-entries/import-xlsx
Content-Type: application/json
```

用途：员工工时评估页面上传工时系统导出的 `.xlsx` 文件。前端把文件转为 base64 后提交，后端解析 Excel 并复用标准工时导入逻辑。

请求：

```json
{
  "filename": "202601-202602.xlsx",
  "content_base64": "<xlsx file base64>"
}
```

Excel 支持两种格式。系统会遍历工作簿并自动选择包含必需列的明细工作表，忽略透视汇总页和`提交时间`等非业务列。

| Excel 列名 | 转换后字段 | 说明 |
|---|---|---|
| `姓名` | `employee_name` | 员工姓名 |
| `日期` | `work_date` | 支持 Excel 日期、`YYYY-MM-DD`、`YYYY/M/D` |
| `项目代码` | `project_number` | 项目编号 |
| `天数` | `hours` | 按 8 小时/天转换为填报工时 |
| `工作内容` | `work_content` | 工作内容 |
| `流程状态` | `approval_status` / `approved_hours` | `流转完成` 转为 `approved`，确认工时=填报工时；其他状态转为 `submitted`，确认工时=0 |

工时系统导出明细格式：

| Excel 列名 | 转换后字段 | 说明 |
|---|---|---|
| `姓名` | `employee_name` | 员工姓名 |
| `项目代码` | `project_number` | 与项目主数据库的关联键 |
| `时间` | `work_date` | 支持 Excel 日期、`YYYY-MM-DD`、`YYYY/M/D` |
| `天数` | `hours` | 常规人天，按 8 小时/天转换 |
| `加班天数` | `overtime_hours` | 加班人天，按 8 小时/天转换并计入累计实际工时 |
| `工作内容` | `work_content` | 工作内容 |

该格式没有流程状态时，导入记录按已确认处理：`approved_hours = hours + overtime_hours`，不会产生客户确认缺口。

响应：

```json
{
  "imported": 1303,
  "unchanged": 0,
  "parsed": 1303,
  "filename": "202601-202602.xlsx",
  "date_range": { "start": "2026-01-01", "end": "2026-02-28" }
}
```

业务规则：

- 导入不需要新增后端依赖，后端使用标准库解析 `.xlsx`。
- `external_id` 由姓名、项目编号、日期、天数、工作内容、流程状态生成稳定哈希；重复导入相同数据会更新，不会重复累加。
- Excel 中的 `所属部门`、`部门`、`case`、`提交时间` 等列当前不进入系统模型。

### 9.3 查询工时基线配置

```http
GET /api/v1/worktime-baseline
```

用途：查询员工工时数据模块使用的统一基线参数。`standard_hours_per_day` 与利用率阈值参与运行时计算；年度、月均和季度工时字段保留为管理参考。每个统计周期的实际产能由中国法定工作日（法定节假日和调休补班）自动折算。资源调剂判断必须满足 `attention < minimum < excellent < overload`：依次对应可优先调剂、可适度支持、基本均衡、高负荷与超负荷。

默认配置参考当前表格设计：

```json
{
  "data": {
    "config_id": "default",
    "standard_work_days_per_year": "230.00",
    "standard_hours_per_day": "8.00",
    "annual_baseline_hours": "1840.00",
    "effective_baseline_hours": "1564.00",
    "monthly_baseline_hours": "153.00",
    "quarterly_baseline_hours": "460.00",
    "minimum_utilization_percent": "75.00",
    "excellent_utilization_percent": "90.00",
    "attention_utilization_percent": "60.00",
    "overload_utilization_percent": "100.00",
    "notes": "默认工时基线参数，可在页面人工维护。",
    "updated_at": "2026-07-08T11:00:00-04:00"
  }
}
```

### 9.4 更新工时基线配置

```http
PATCH /api/v1/worktime-baseline
Content-Type: application/json
```

请求示例：

```json
{
  "standard_work_days_per_year": "230",
  "standard_hours_per_day": "8",
  "annual_baseline_hours": "1840",
  "effective_baseline_hours": "1564",
  "monthly_baseline_hours": "153",
  "quarterly_baseline_hours": "460",
  "minimum_utilization_percent": "75",
  "excellent_utilization_percent": "90",
  "attention_utilization_percent": "60",
  "overload_utilization_percent": "100",
  "notes": "年度基线=230天*8小时"
}
```

### 9.5 查询员工工时数据

```http
GET /api/v1/employee-worktime
```

查询参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `period` | string | 否 | `month` 或 `year`，默认 `month` |
| `month` | string | 否 | 月度统计，格式 `YYYY-MM` |
| `year` | string | 否 | 年度汇总，格式 `YYYY` |
| `employee_name` | string | 否 | 员工姓名 |

当前只做按月统计和年度汇总。工时数据不采集“工时类型”，因此接口不提供项目交付、售前支持、运维服务、内部赋能等结构拆分字段。

工时利用率：

```text
统计周期：reported_hours / (实际工作日 * standard_hours_per_day) * 100
```

`meta` 同时返回 `working_days`、`holiday_excluded_days`、`makeup_workdays`、`calendar_available` 与 `calendar_stale`，供界面说明当期可用产能的计算口径；订阅临时不可用时，将明确标识并回退为仅排除周末。

状态规则：

| 工时利用率 | 状态 |
|---:|---|
| `> 90%` | 工时充足 |
| `> 75%` | 基本达标 |
| `> 60%` | 需要关注 |
| 其他 | 严重不足 |

响应示例：

```json
{
  "data": [
    {
      "employee_id": "EMP-001",
      "employee_name": "查宏斌",
      "reported_hours": "152.00",
      "overtime_hours": "8.00",
      "approved_hours": "152.00",
      "confirmation_gap_hours": "0.00",
      "reported_person_days": "19.00",
      "overtime_person_days": "1.00",
      "approved_person_days": "19.00",
      "baseline_hours": "153.00",
      "utilization_percent": "99.3",
      "entry_count": 19,
      "project_count": 3,
      "status": "工时充足"
    }
  ],
  "summary": {
    "total_employees": 1,
    "sufficient_count": 1,
    "qualified_count": 0,
    "attention_count": 0,
    "critical_count": 0
  },
  "meta": {
    "period": "month",
    "period_label": "2026-02",
    "start_date": "2026-02-01",
    "end_date": "2026-02-28",
    "baseline_hours": "153.00",
    "latest_work_month": "2026-02"
  }
}
```

## 10. 同步接口

同步接口负责从配置的数据源读取 CRM 快照，标准化、校验并写入 SQLite。成功后切换当前活跃快照；失败时保留上一份成功快照。

### 10.1 查询同步状态

```http
GET /api/v1/sync-status
```

响应：

```json
{
  "status": "succeeded",
  "last_success_at": "2026-07-06T03:00:00-04:00",
  "next_scheduled_at": "2026-07-07T03:00:00-04:00",
  "timezone": "America/Detroit",
  "source_stats": {
    "contract_order": 1,
    "sales_opportunity": 2
  },
  "output_stats": {
    "contract_order": 1,
    "sales_opportunity": 2
  },
  "validation": {
    "complete": true,
    "merged_opportunities": 1,
    "cross_type_duplicate_project_ids": 0,
    "sync_mode": "incremental",
    "inserted_records": 0,
    "updated_records": 1,
    "deleted_records": 0,
    "unchanged_records": 2
  }
}
```

### 10.2 查询手工导入日志

```http
GET /api/v1/crm-imports?page=1&page_size=10
```

响应：

```json
{
  "data": [
    {
      "log_id": 12,
      "snapshot_id": 18,
      "data_type": "contract_order",
      "filename": "合同数据.xlsx",
      "parsed_rows": 82,
      "inserted_records": 2,
      "updated_records": 0,
      "unchanged_records": 80,
      "imported_at": "2026-08-01T14:30:00+08:00"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 10,
    "total_records": 12,
    "total_pages": 2
  }
}
```

仅成功解析且已写入 SQLite 的上传会生成一条日志；返回顺序为最新优先。默认每页 10 条，`page_size` 最大为 50。

### 10.3 上传 CRM 文件

```http
POST /api/v1/crm-imports
```

请求体：

```json
{
  "data_type": "contract_order",
  "filename": "合同数据.xlsx",
  "content_base64": "..."
}
```

`data_type` 支持 `contract_order`、`sales_opportunity`。上传成功的响应会包含 `import_log`，其中 `imported_at` 即本次上传成功时间。

销售机会 CSV 推荐使用以下表头：`ID、项目编号、客户、机会主题、预计签单日期、可能性、阶段、负责人、状态、更新日期`。其中“阶段”映射为 `sales_stage`，“更新日期”映射为 `source_updated_at`；项目编号优先使用导入列，不再依赖从主题中猜测。

## 11. 导出接口

### 11.1 导出最新 CRM 快照

```http
GET /api/v1/exports/latest?format=json
GET /api/v1/exports/latest?format=csv
```

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `format` | string | 是 | `json` 或 `csv` |

JSON 响应为当前活跃快照：

```json
{
  "schema_version": "1.0",
  "updated_at": "2026-07-06T03:00:00-04:00",
  "records": []
}
```

CSV 导出：

- 编码：UTF-8 with BOM。
- 文件名：`xtcrm-latest.csv`。
- 字段顺序：

```text
record_id,data_type,project_number,subject,customer,category,signed_at,total_amount,received_amount,invoiced_amount,owner,probability,status,merged_from_opportunity,updated_at
```

## 12. 数据归属和同步保护规则

### 12.1 系统接口字段

以下字段来自 CRM 或工时系统，同步更新：

- CRM：主题、客户、分类、签约时间/预计签单日期、总金额、回款金额、开票金额、所有者（合同新模板可使用“我方签约人”）、可能性、销售阶段、状态、CRM 源更新时间。
- 工时：员工、日期、填报工时、客户确认工时、审批状态、工作内容、源系统更新时间。

### 12.2 人工维护字段

以下字段由管理人员在 TSG PM 页面维护，同步不得覆盖：

- 合同类型。
- 生命周期状态。
- 初始预算人天。
- 每人天小时数。
- 项目经理。
- 项目备注。
- 里程碑计划。
- 回款节点计划。
- 管理干预记录。
- 里程碑完成确认。
- 回款到账确认。

### 12.3 合同/销售机会合并规则

- `project_number` 是 CRM 与工时的业务主键。
- 若销售机会与合同/订单存在相同 `project_number`，说明机会已转化为合同。
- CRM 数据接口保留合同/订单和销售机会两条成功入库的原始记录，便于审计、导出和追溯。
- 项目主数据库按 `project_number` 汇总，因此不会因对应销售机会重复生成项目。
- 已由商机转化的合同/订单记录会将 `probability` 填充为 `100`，并标记 `merged_from_opportunity=true`。
- 合同/订单和销售机会的 `status` 均保留导入数据中的原始状态，不再由金额字段推导；合同新模板使用“状态”列。

### 12.4 风险提醒规则

系统按项目聚合生成风险和异常：

| 规则 | 提醒码 | 严重程度 |
|---|---|---|
| 已填报人天超过预算人天 | `BUDGET_OVERRUN` | `critical` |
| 已填报人天达到预算 80% | `BUDGET_NEAR_LIMIT` | `warning` |
| 已填报工时与客户确认工时存在差额 | `TIME_CONFIRMATION_GAP` | `warning` |
| 里程碑到期或逾期未确认 | `MILESTONE_DUE` | `warning` 或 `critical` |
| 回款节点到期或逾期未确认 | `PAYMENT_DUE` | `warning` 或 `critical` |
| 同步后人工维护字段不完整 | `MANUAL_MAINTENANCE_REQUIRED` | `warning` |

## 13. 前端当前使用接口

当前 React 前端已使用以下接口：

| 前端方法 | 后端接口 |
|---|---|
| `fetchRecords` | `GET /api/v1/records` |
| `fetchSyncStatus` | `GET /api/v1/sync-status` |
| `fetchProjects` | `GET /api/v1/projects` |
| `fetchProjectHealth` | `GET /api/v1/project-health` |
| `fetchEmployeeWorktime` | `GET /api/v1/employee-worktime` |
| `fetchWorktimeBaseline` | `GET /api/v1/worktime-baseline` |
| `updateWorktimeBaseline` | `PATCH /api/v1/worktime-baseline` |
| `fetchProject` | `GET /api/v1/projects/{project_number}` |
| `updateProjectSettings` | `PATCH /api/v1/projects/{project_number}/settings` |
| `createMilestone` | `POST /api/v1/projects/{project_number}/milestones` |
| `updateMilestone` | `PATCH /api/v1/projects/{project_number}/milestones/{milestone_id}` |
| `confirmMilestone` | `POST /api/v1/projects/{project_number}/milestones/{milestone_id}/confirm` |
| `confirmPayment` | `POST /api/v1/projects/{project_number}/payment-nodes/{payment_node_id}/confirm` |
| `triggerSync` | `POST /api/v1/sync-jobs` |
| `downloadExport` | `GET /api/v1/exports/latest?format=json\|csv` |

## 14. 本地验证

后端测试：

```bash
cd backend
.venv/bin/pytest -q
```

前端构建：

```bash
cd frontend
npm run build
```

运行服务后，FastAPI 自动生成的 OpenAPI 页面：

```text
http://127.0.0.1:8000/docs
```
