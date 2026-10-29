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

## 产品需求文档

当前产品逻辑以 [docs/prd/PRD.md](./docs/prd/PRD.md)（v1.9）为准；全部计算口径及其在本体中的位置见 [docs/BUSINESS_RULES.md](./docs/BUSINESS_RULES.md)。PRD 包含页面截图、流程图和业务规则。新增或调整功能时同步更新该文档，方法见文档第 12 章。

## 本体语义层（Ontology）

在物理表之上增加一层语义层，把 CRM、工时与人工维护数据统一为业务对象模型：

- **Object Types / Objects**：项目、成本对象（项目代码）、合同/订单、销售机会、客户、员工、工时记录、里程碑、回款节点、回款约定、管理干预、运营口径参数。
- **Properties**：每个属性登记来源类型（系统接口 / 人工维护 / 派生计算）、物理映射、安全标记和可修改它的动作。
- **Link Types**：18 种双向关系，例如项目 → 里程碑、员工 ↔ 项目（经由工时）、客户 → 合同。
- **Action Types**：人工维护数据的唯一写入口，按“权限 → 参数校验 → 提交条件 → 写回 → 审计”执行，支持 `validateOnly` 预检。
- **Functions**：项目风险评估、预算消耗、回款状态、期间健康度、员工利用率、项目组合概览、成本对象投入、组合投入分析、中国工作日历、待维护清单等。
- **Safe**：对象类型可见性、`FINANCE` / `PERSONNEL` 属性标记脱敏、动作与函数授权，以及 `ontology_action_log` 审计（原有 REST 写接口同样记录）。

本体结构以 YAML 元数据维护（`backend/app/ontology/metadata/`），业务规则在 `rules.py` 中按名称注册；修改元数据必须升级 `metadata/ontology.yaml` 中的版本号，服务启动时会校验并登记版本，可在页面比较版本差异。

对象集查询沿用 Palantir OSDK 的 ObjectSet 结构（过滤、并/交/差集、沿关系检索、聚合），对象由带缓存的对象索引提供，底层数据任何写入都会通过触发器使相关缓存失效，导入后自动重建。

修改本体时，Claude Code 会使用仓库内的技能 `.claude/skills/tsg-ontology/`：它规定了吸收业务文档、增改对象、核验问题和评审变更的流程，并附带质量检查脚本（在 `backend` 目录运行 `.venv/bin/python ../.claude/skills/tsg-ontology/scripts/lint_ontology.py`）。

接口前缀 `/api/v1/ontology`，前端菜单“语义层 → 本体浏览器”（权限 `ontology`）。完整设计见 [docs/ONTOLOGY_ARCHITECTURE.md](./docs/ONTOLOGY_ARCHITECTURE.md)。

## 投入成本与沉没成本

项目代码是全生命周期的成本对象：商机阶段的售前工时和签约后的交付工时都记在同一个代码上。菜单“投入成本分析”（权限 `investment`）把每个代码的工时按人天拆为以下几类：

- 履约成本
- 获客成本（赢单项目签约前的投入）
- 在途投入
- 沉没成本（人工确认丢单或放弃，或 CRM 失败状态）
- 内部投入
- 未建档投入（CRM 无记录的代码）

页面还给出赢单率、沉没成本率、在途账龄和停滞商机。配置标准人天成本后，还能折算成金额，金额需要相应权限才能查看。

接口包括 `GET /api/v1/investment/analysis`、`GET /api/v1/investment/cost-objects/{code}`、`GET|PATCH /api/v1/investment/settings`。设计和财务口径见 [docs/INVESTMENT_COST_MODEL.md](./docs/INVESTMENT_COST_MODEL.md)。

## 目录

```text
backend/
  app/                 FastAPI、SQLite、同步与标准化
  app/ontology/        本体语义层：YAML 元数据、编译器、规则注册表、运行时、安全、版本
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

“数据导入”页面支持 `.xlsx`、`.csv`、`.json`。合同/订单与销售机会必须分别上传；合同/订单新模板支持“主题、我方签约人、签约时间、分类、状态、总金额、回款金额、开票金额”，其中“我方签约人”映射为系统所有者（销售人员）。销售机会模板支持“项目编号、机会主题、预计签单日期、可能性、阶段、负责人、状态、更新时间”；其中“阶段”用于销售推进跟踪，“更新时间”保留为 CRM 源更新时间。CSV 同时兼容 UTF-8 与 GB18030/GBK 编码；每行至少需要“主题”和“我方签约人/所有者/负责人”。

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
npm test
npm run build
```

## 项目聚合规则

- `project_number` 是跨 CRM 与工时数据源的业务主键。
- 同一项目编号可以关联多条 CRM 合同记录，项目视图对金额进行汇总。
- CRM 原始记录与项目管理配置分开保存，避免手工配置覆盖源数据。
- 已提交和已审批的工时计入实际人天（不以审批通过为前提），已驳回和草稿不计入；默认 `8 小时 = 1 人天`，可按项目修改。
- 建设类、维护类按预算人天识别消耗风险；开口类按审批工时计算可结算人天。

## 当前实现接口

项目生命周期：

- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_number}`
- `GET /api/v1/projects/{project_number}/records`
- `PATCH /api/v1/projects/{project_number}/settings`
- `POST /api/v1/projects/{project_number}/milestones`
- `PATCH /api/v1/projects/{project_number}/milestones/{milestone_id}`
- `POST /api/v1/projects/{project_number}/milestones/{milestone_id}/confirm`
- `POST /api/v1/projects/{project_number}/payment-nodes/{payment_node_id}/confirm`
- `POST /api/v1/projects/{project_number}/interventions`
- `POST /api/v1/time-entries/import`
- `POST /api/v1/time-entries/import-xlsx`
- `GET /api/v1/resource-operations`
- `GET /api/v1/performance`（管理员：接口 P50/P95、返回行数及导入耗时）

CRM 查询与同步：

- `GET /health`
- `GET /api/v1/records`
- `GET /api/v1/records/{record_id}`
- `GET /api/v1/sync-status`
- `POST /api/v1/crm-imports`
- `GET /api/v1/crm-imports/template?data_type=contract_order`
- `GET /api/v1/exports/latest?format=json|csv`

完整、可执行的 OpenAPI 文档由 FastAPI 在 `http://127.0.0.1:8000/docs` 自动生成。

## 性能与大数据量保护

- 工时查询使用日期前导复合索引；SQLite 连接设置 `busy_timeout=5000ms`，导入事务期间的读取会短暂等待而非直接报锁库错误。
- CRM、项目总览、项目运营监控和资源运营监控均使用游标续页：首屏按 `page_size` 返回，使用响应中的 `meta.next_cursor` 获取下一页，避免深页 `OFFSET` 扫描。
- 每个 `/api/v1` 请求会记录耗时、状态、返回行数和是否导入；管理员可通过 `GET /api/v1/performance` 查看近期 P50/P95。
- PM Assistant 仅检索有匹配的、数量受限的 CRM 和项目候选集，不会在每次问答时把全部快照拼入模型上下文。

## 当前边界

- 工时系统的真实 API 尚未确认；目前通过 `POST /api/v1/time-entries/import` 和页面 Excel 上传验证数据模型和幂等导入。
- 项目类型、PM 负责人、预算工时、里程碑和回款节点实际完成均以人工维护为准；同步任务不得覆盖这些维护字段。
- 工时数据不采集工时类型；售前 / 交付的拆分依据签约日推断（签约日前为售前），运维、内部赋能等更细的类型暂不区分。
- 当前前端完成项目组合与项目详情驾驶舱；项目配置、里程碑、回款节点和管理干预已提供服务端 API，后续可继续补录入界面。
