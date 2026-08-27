# TSG PM Data Service

FastAPI + SQLite 后端与 React 前端，用于把 CRM 合同/销售机会、员工工时、项目人天预算、里程碑与回款节点聚合为 TSG PM 项目生命周期视图。

## 架构

```text
CRM 手工导入文件
          │
          ▼
  合同 / 销售机会导入解析
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
- 每次上传先校验后写入新快照；全部校验成功后才切换当前快照。
- 合同与销售机会分别手工上传；系统按 CRM 内部 ID 或项目编号判断新增、更新、无变化。
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

默认不会自动导入 CRM 数据。通过“数据导入”页面分别上传合同/订单与销售机会文件；开发测试可设置 `XTCRM_AUTO_IMPORT_ON_EMPTY=true`，以导入 `backend/data/source_snapshot.example.json`。

## 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问：`http://127.0.0.1:5173`

## CRM 手工导入

“数据导入”页面支持 `.xlsx`、`.csv`、`.json`。合同/订单与销售机会必须分别上传；系统会识别常用字段别名，每行至少需要“主题”和“所有者/负责人”。

- 同一 CRM 内部 ID 优先判定为同一记录；未提供内部 ID 时，以数据类型 + 项目编号判定。
- 文件中未包含的字段视为“不变”，不会清空已存在的 CRM 值。
- 无法从主题识别项目编号的行仍保留在 CRM 审计数据中，但不会进入项目主数据库；建议在模板中补充“项目编号”列。
- CRM 数据审计页会保留全部成功入库的合同和销售机会；已转化为合同的销售机会会让对应合同标记为 `100% / 成功`，项目主数据库再按项目编号归并，不重复生成项目。
- 商机 `状态=成功` 即使暂未导入同编号合同，也会在项目主数据库归类为“项目”；“阶段”只作描述，不参与转换判断。
- CRM 上传不会覆盖合同类别、预算人天、项目经理、里程碑和回款确认等人工维护数据。

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
- `POST /api/v1/time-entries/import-xlsx`

CRM 查询与同步：

- `GET /health`
- `GET /api/v1/records`
- `GET /api/v1/records/{record_id}`
- `GET /api/v1/sync-status`
- `POST /api/v1/crm-imports`
- `GET /api/v1/crm-imports/template?data_type=contract_order`
- `GET /api/v1/exports/latest?format=json|csv`

完整、可执行的 OpenAPI 文档由 FastAPI 在 `http://127.0.0.1:8000/docs` 自动生成。

## 当前边界

- 工时系统的真实 API 尚未确认；目前通过 `POST /api/v1/time-entries/import` 和页面 Excel 上传验证数据模型和幂等导入。
- 项目类型、PM 负责人、预算工时、里程碑和回款节点实际完成均以人工维护为准；同步任务不得覆盖这些维护字段。
- 工时数据不采集工时类型，不做项目交付、售前、运维、内部赋能等结构拆分。
- 当前前端完成项目组合与项目详情驾驶舱；项目配置、里程碑、回款节点和管理干预已提供服务端 API，后续可继续补录入界面。
