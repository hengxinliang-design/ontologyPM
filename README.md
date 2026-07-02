# TSG PM Data Service

FastAPI + SQLite 后端与 React 前端，用于把 CRM 合同/销售机会、员工工时、项目人天预算、里程碑与回款节点聚合为 TSG PM 项目生命周期视图。

## 架构

```text
CRM 自动化或数据源 API
          │
          ▼
   SnapshotSource Adapter
   ├─ file: 已验证 JSON 快照
   └─ http: 内部数据源 REST API
          │
          ▼
 Normalize + Validate + Merge
          │ 原子事务
          ▼
 SQLite snapshots / records / sync_jobs
        / project_settings / time_entries
        / milestones / payment_nodes / interventions
          │
          ▼
 FastAPI /api/v1 ───── React frontend/
```

关键边界：

- React 不接触 CRM 登录态或密码。
- 查询请求只读 SQLite，不实时抓取 CRM。
- 同步先写新快照；全部校验成功后才切换当前快照。
- 第一次同步为全量；以后默认按 `record_id` 增量合并，并支持 `deleted_record_ids` 明确删除。
- CRM/工时接口表与人工维护表物理分离，增量同步不会覆盖合同类别、预算人天、项目经理、里程碑或确认状态。
- 登录失败、来源异常、跨类型一对多或状态冲突时保留上一份成功快照。
- SQLite 保留历史快照，当前查询始终绑定一个 `active_snapshot_id`。

## 目录

```text
backend/
  app/                 FastAPI、SQLite、同步与标准化
  data/                SQLite 与示例源快照
  tests/               后端测试
frontend/
  src/                 React UI 与 API Client
```

## 启动后端

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

接口文档：`http://127.0.0.1:8000/docs`

默认会在数据库为空时导入 `backend/data/source_snapshot.example.json`，并为本地演示补充项目配置、工时、里程碑和回款节点数据。生产数据源不会写入这些演示数据。

## 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问：`http://127.0.0.1:5173`

## 接入真实数据源 API

后端支持服务端主动调用内部 JSON API：

```bash
export XTCRM_SOURCE_KIND=http
export XTCRM_SOURCE_URL=https://internal.example.com/xtcrm/export
export XTCRM_SOURCE_TOKEN=your-source-token
export XTCRM_RECORD_ID_SECRET=replace-with-a-long-random-secret
```

数据源需返回包含 `records[]` 的 JSON。记录可以使用当前中文字段，也可以使用 REST API 英文字段。推荐数据源额外提供 `crm_internal_id`，后端使用 HMAC 生成稳定且不可逆的 `record_id`。

如果当前 CRM 只能通过浏览器登录抓取，应由现有浏览器自动化先产出已验证 JSON，再让后端使用 `file` 适配器导入；不要把浏览器 Cookie 或密码存入 SQLite。

## 定时同步

内置调度默认关闭，避免与 Codex 每日任务重复。若后续由服务自身负责每天 03:00 同步：

```bash
export XTCRM_SCHEDULER_ENABLED=true
export XTCRM_SYNC_HOUR=3
export XTCRM_TIMEZONE=America/Detroit
```

只保留一个调度责任方，避免重复抓取。

## API 鉴权

本地开发不设置 `XTCRM_API_TOKEN` 时允许访问。生产环境必须设置 Token：

```bash
export XTCRM_API_TOKEN=replace-with-a-secure-token
```

客户端发送：

```http
Authorization: Bearer replace-with-a-secure-token
```

## 测试

```bash
cd backend
.venv/bin/pytest -q

cd ../frontend
npm run build
```

## 项目聚合规则

- `project_number` 是跨 CRM 与工时数据源的业务主键。
- 同一项目编号可以关联多条 CRM 合同记录，项目视图对金额进行汇总。
- CRM 原始记录与项目管理配置分开保存，避免手工配置覆盖源数据。
- 审批通过的工时计入实际人天；默认 `8 小时 = 1 人天`，可按项目修改。
- 建设类、维护类按预算人天识别消耗风险；开口类按审批工时计算可结算人天。

详细定义见 [TSG_PM_PRODUCT_REQUIREMENTS.md](./TSG_PM_PRODUCT_REQUIREMENTS.md) 和 [TSG_PM_DATA_SOURCE_CONTRACTS.md](./TSG_PM_DATA_SOURCE_CONTRACTS.md)。

## 当前实现接口

项目生命周期：

- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_number}`
- `GET /api/v1/projects/{project_number}/records`
- `PATCH /api/v1/projects/{project_number}/settings`
- `POST /api/v1/projects/{project_number}/milestones`
- `PATCH /api/v1/projects/{project_number}/milestones/{milestone_id}`
- `POST /api/v1/projects/{project_number}/milestones/{milestone_id}/confirm`
- `POST /api/v1/projects/{project_number}/payment-nodes`
- `POST /api/v1/projects/{project_number}/payment-nodes/{payment_node_id}/confirm`
- `POST /api/v1/projects/{project_number}/interventions`
- `POST /api/v1/time-entries/import`

CRM 查询与同步：

- `GET /health`
- `GET /api/v1/records`
- `GET /api/v1/records/{record_id}`
- `GET /api/v1/sync-status`
- `POST /api/v1/sync-jobs`
- `GET /api/v1/sync-jobs/{job_id}`
- `GET /api/v1/exports/latest?format=json|csv`

完整、可执行的 OpenAPI 文档由 FastAPI 在 `http://127.0.0.1:8000/docs` 自动生成。

## 当前边界

- 工时系统的真实 API 尚未确认；目前通过 `POST /api/v1/time-entries/import` 验证数据模型和幂等导入。
- 员工内部成本单价、开口合同角色费率、工时审批字段和回款自动核销规则仍需业务确认。
- 当前前端完成项目组合与项目详情驾驶舱；项目配置、里程碑、回款节点和管理干预已提供服务端 API，后续可继续补录入界面。
