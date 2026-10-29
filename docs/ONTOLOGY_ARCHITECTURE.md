# TSG PM 本体（Ontology）语义层架构

> 版本：ontology v7.4.0 · 2026-10-29（元数据驱动；本文第 1–10 节写于 v1.1，后续版本的变化见第 11 节）
> 目标：在不推翻现有 CRM / 工时 / 人工维护数据架构的前提下，引入一层**语义层（Ontology）**，把“表和字段”提升为“业务对象、关系、动作和规则”，让 UI、API、PM Assistant 和后续系统都面向同一套业务语言工作。

## 1. 为什么要加语义层

现有 MVP 的数据架构是“表 → 仓储方法 → REST 接口 → 页面”：

- 业务概念散落在 SQL 列名（`records.data_type`、`project_settings.contract_type`）和仓储函数里（`_build_project` 有 300 行聚合逻辑）。
- 写操作是一组零散的 REST 接口，规则（已完成里程碑锁定、已到账回款不可删除）藏在代码分支里，没有统一的审计记录。
- 权限只到“菜单”一级，无法表达“能看项目但不能看金额”这类语义级规则。

本体层把这些隐性知识显式化、可查询、可治理：

| 本体概念 | 回答的问题 | 在 TSG PM 中的落点 |
|---|---|---|
| **Object Type** 对象类型 | 业务世界里有哪些“东西”？ | 项目、合同、销售机会、客户、员工、工时记录、里程碑、回款节点、管理干预 |
| **Object** 对象 | 某个具体的东西是什么？ | `TPL149` 这个项目、员工 `E001` |
| **Property** 属性 | 这个东西有哪些特征？来自哪里？ | `Project.contractType` ← `project_settings.contract_type`（人工维护） |
| **Link Type** 关系类型 | 东西之间怎么关联？ | 项目 → 里程碑（1:N）、员工 ↔ 项目（M:N，经由工时） |
| **Action Type** 动作类型 | 人可以对它做什么？有什么前置条件？ | 确认里程碑完成、确认回款到账、设置项目 |
| **Function** 函数 | 从事实怎么推导出判断？ | 项目风险评估、预算消耗、员工工时利用率 |
| **Safe** 安全 | 谁能看什么、做什么？ | 对象类型可见性、属性标记（金额/人员）、动作授权、审计 |

## 2. 修订后的逻辑架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 应用层  React 页面 · 本体浏览器 · PM Assistant · 外部系统（REST）           │
├──────────────────────────────────────────────────────────────────────────┤
│ 安全层 Safe   对象类型可见性 · 属性标记脱敏 · 动作授权 · 动作审计日志          │
├──────────────────────────────────────────────────────────────────────────┤
│ 动力层 Kinetic                                                            │
│   Action Types：参数 → 校验 → 提交条件 → 写回（Writeback）→ 审计            │
│   Functions：风险评估 · 预算消耗 · 回款状态 · 健康度 · 员工利用率           │
├──────────────────────────────────────────────────────────────────────────┤
│ 语义层 Semantic（Ontology）                                                │
│   Object Types · Properties（含来源映射 / 派生函数 / 安全标记）· Link Types  │
├──────────────────────────────────────────────────────────────────────────┤
│ 存储层  SQLite                                                            │
│   系统接口数据（只读）：snapshots / records / time_entries                  │
│   人工维护数据（写回）：project_settings / milestones / payment_nodes /     │
│                        interventions / worktime_baseline_config            │
│   本体运行数据：ontology_action_log                                         │
├──────────────────────────────────────────────────────────────────────────┤
│ 集成层  CRM 手工导入（xlsx/csv/json）· 工时导入 · 快照校验与切换             │
├──────────────────────────────────────────────────────────────────────────┤
│ 数据源  CRM（合同/订单、销售机会） · 工时系统                               │
└──────────────────────────────────────────────────────────────────────────┘
```

设计原则：

1. **语义层不复制数据**：对象在查询时由后端数据集“物化”（materialize），物理表仍是唯一事实来源。
2. **读写分离**：系统接口数据只能经集成层更新；人工维护数据只能经 **Action** 写回。两类数据在属性上分别标注 `source_kind = system | manual | derived`。
3. **规则显式化**：派生属性必须指向一个 Function；写操作的前置条件必须登记为 Action 的提交条件（submission criteria）。
4. **安全贯穿**：每次读对象、遍历关系、调用函数、执行动作都经过同一个安全策略判断。
5. **兼容演进**：原有 `/api/v1/projects/...` 写接口保留，但内部同样写入本体动作审计日志；新能力统一走 `/api/v1/ontology/...`。

## 3. 对象类型（Object Types）

| API 名 | 中文名 | 主键 | 标题属性 | 后端数据集 | 可见权限 |
|---|---|---|---|---|---|
| `Project` | 项目 | `projectNumber` | `subject` | CRM 记录按项目编号聚合 + `project_settings` + 工时/里程碑/回款 | projects / health / resource |
| `CostObject` | 成本对象（项目代码） | `code` | `title` | 工时与 CRM 中出现过的全部项目代码 + `opportunity_outcomes` + `investment_settings` | projects / investment / resource |
| `Contract` | 合同/订单 | `recordId` | `subject` | `records`（当前快照，`data_type=contract_order`） | crm / projects |
| `Opportunity` | 销售机会 | `recordId` | `subject` | `records`（当前快照，`data_type=sales_opportunity`） | crm / projects |
| `Customer` | 客户 | `customerName` | `customerName` | `records.customer` 去重聚合 | crm / projects |
| `Employee` | 员工 | `employeeId` | `employeeName` | `time_entries` 按员工聚合 | projects / employeeWorktime / resource |
| `TimeEntry` | 工时记录 | `externalId` | `workContent` | `time_entries` | projects / employeeWorktime / resource |
| `Milestone` | 里程碑 | `milestoneId` | `name` | `milestones` | projects |
| `PaymentNode` | 回款节点 | `paymentNodeId` | `name` | `payment_nodes` | projects |
| `PaymentTerm` | 回款约定 | `paymentTermId` | `name` | `payment_terms` + 关联的回款节点与里程碑 | projects |
| `Intervention` | 管理干预 | `interventionId` | `title` | `interventions` | projects |
| `OperatingPolicy` | 运营口径参数 | `configId` | `configId` | `worktime_baseline_config` + `investment_settings` | projects / health / resource / employeeWorktime / investment / settings |
| `AnnualOperatingPlan` | 年度经营计划（v3.0） | `planId` | `planId` | `annual_operating_plans` | department / settings |

`Customer` 和 `Employee` 在物理层**没有独立的表**——它们是语义层识别出来的业务实体，这正是本体的价值：业务上存在的东西即使在源系统里只是一列，也能成为可查询、可关联的对象。

## 4. 属性（Properties）

每个属性登记：`apiName`（camelCase 语义名）、`displayName`、`dataType`、`sourceKind`、`source`（物理列或派生函数）、`marking`（安全标记）、`enumValues`、`editedBy`（可修改它的动作）。

数据类型：`string`、`integer`、`decimal`、`money`、`percent`、`date`、`datetime`、`boolean`、`enum`、`json`。

`Project` 的关键属性示例：

| 属性 | 类型 | 来源类型 | 来源 | 标记 | 由动作修改 |
|---|---|---|---|---|---|
| `projectNumber` | string | system | `records.project_number` | — | — |
| `subject` | string | system | `records.subject`（主合同） | — | — |
| `contractType` | enum | manual | `project_settings.contract_type` | — | `configureProject` |
| `lifecycleStatus` | enum | manual | `project_settings.lifecycle_status` | — | `configureProject` |
| `plannedPersonDays` | decimal | manual | `project_settings.planned_person_days` | — | `configureProject` |
| `contractValue` | money | system | Σ `records.total_amount` | `FINANCE` | — |
| `receivedAmount` | money | derived | Σ 已确认回款节点 | `FINANCE` | `confirmPaymentReceived` |
| `reportedPersonDays` | decimal | derived | Σ 工时 / 每人天小时数 | — | — |
| `budgetUtilization` | percent | derived | `projectBudgetStatus` | — | — |
| `riskLevel` | enum | derived | `projectRiskAssessment` | — | — |

完整定义见 `backend/app/ontology/metadata/` 下的 YAML 文件，运行时可通过 `GET /api/v1/ontology/schema` 查询。

## 5. 关系类型（Link Types）

| API 名（正向 / 反向） | 源 → 目标 | 基数 | 连接方式 |
|---|---|---|---|
| `contracts` / `project` | Project → Contract | 1:N | `projectNumber` |
| `opportunities` / `project` | Project → Opportunity | 1:N | `projectNumber` |
| `customer` / `projects` | Project → Customer | N:1 | `customerName` |
| `contracts` / `customer` | Customer → Contract | 1:N | `customerName` |
| `opportunities` / `customer` | Customer → Opportunity | 1:N | `customerName` |
| `timeEntries` / `project` | Project → TimeEntry | 1:N | `projectNumber` |
| `timeEntries` / `employee` | Employee → TimeEntry | 1:N | `employeeId` |
| `team` / `workedProjects` | Project ↔ Employee | M:N | 经由 `TimeEntry` |
| `milestones` / `project` | Project → Milestone | 1:N | `projectNumber` |
| `paymentNodes` / `project` | Project → PaymentNode | 1:N | `projectNumber` |
| `paymentNode` / `milestone` | Milestone → PaymentNode | 1:1 | `paymentNodeId` |
| `interventions` / `project` | Project → Intervention | 1:N | `projectNumber` |

关系可双向遍历，例如：`Customer → projects → team` 回答“这个客户的项目都有谁在投入”。

## 6. 动作类型（Action Types）

动作是**唯一**允许修改人工维护数据的入口。执行管线：

```text
请求参数
  → ① 安全：调用者是否具备动作所需权限
  → ② 参数校验：必填、类型、枚举、对象引用存在
  → ③ 提交条件：业务前置条件（例如“已完成的里程碑不可编辑”）
  → ④ 写回：调用领域仓储，写入人工维护表（单事务）
  → ⑤ 审计：写入 ontology_action_log（成功 / 被拒绝 / 失败）
```

| 动作 | 目标对象 | 写回 | 提交条件 |
|---|---|---|---|
| `configureProject` 设置项目 | Project | `project_settings` | 建设类/维护类必须有预算人天；计划结束日不早于开始日 |
| `createMilestone` 新建里程碑 | Project | `milestones`（+`payment_nodes`） | 回款节点需同时提供回款日期与金额 |
| `editMilestone` 编辑里程碑 | Milestone | `milestones` | 已完成的里程碑锁定 |
| `deleteMilestone` 删除里程碑 | Milestone | `milestones` | 已完成锁定；关联回款已到账则不可删除 |
| `confirmMilestoneCompletion` 确认里程碑完成 | Milestone | `milestones` | 未完成 |
| ~~`createPaymentNode` 新建回款节点~~ | Project | `payment_nodes` | 6.0.0 已取消，回款节点由回款约定关联里程碑产生 |
| `editPaymentNode` 编辑回款节点 | PaymentNode | `payment_nodes` | 未到账 |
| `deletePaymentNode` 删除回款节点 | PaymentNode | `payment_nodes` | 未到账；不能是里程碑关联节点 |
| `confirmPaymentReceived` 确认回款到账 | PaymentNode | `payment_nodes` | 未到账 |
| `openIntervention` 发起管理干预 | Project | `interventions` | — |
| `updateInterventionStatus` 更新干预状态 | Intervention | `interventions` | 已关闭的干预不可重新打开 |

支持 `validateOnly`：只跑 ①②③，返回每一条提交条件的结果，不写数据——前端可在提交前提示“为什么不能做”。

## 7. 函数（Functions）

函数是**可复用、可解释的业务规则**，输入对象（或对象 + 参数），输出结构化结果。派生属性都指向函数。

| 函数 | 输入 | 输出 |
|---|---|---|
| `projectRiskAssessment` | Project | 风险等级、原因、异常告警 |
| `projectBudgetStatus` | Project | 预算人天、已填报人天、剩余人天、使用率、客户确认缺口 |
| `projectPaymentStatus` | Project | 合同额、开票、CRM 回款、回款节点计划/到账、下一个回款节点 |
| `projectHealthForPeriod` | Project, startMonth, endMonth | 期间人天、累计人天、趋势、健康状态 |
| `employeeUtilization` | Employee, month | 期间工时、基线、利用率、状态 |
| `portfolioSummary` | 筛选条件 | 项目数、合同总额、回款、风险分布 |

## 8. 安全（Safe）

在原有“菜单权限”之上，增加三层语义安全：

1. **对象类型可见性**：每个 Object Type 声明 `readPermissions`（满足任一即可）。不可见的类型不出现在 schema、搜索和关系遍历结果中。
2. **属性标记（Markings）**：
   - `FINANCE` 财务金额：合同额、回款、开票、计划回款金额。需要 `projects` 或 `crm` 权限。
   - `PERSONNEL` 人员工时：员工个人工时、利用率。需要 `employeeWorktime` 或 `resource` 权限。项目管理人员可以看到项目团队成员和工作内容，但看不到个人工时数。
   - 无权查看的属性值返回 `null`，并在 `redactedProperties` 中列出——调用方能知道“这里有数据但你看不到”，而不是误以为是空值。
3. **动作授权**：每个 Action Type 声明 `requiredPermissions`；函数同理。
4. **审计**：所有动作（含旧 REST 写接口）写入 `ontology_action_log`：谁、何时、对哪个对象、用什么参数、结果如何。对象详情可直接查看其动作历史。

管理员（`is_admin`）拥有全部权限。新增菜单权限 `ontology`，用于访问“本体浏览器”页面。

## 9. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/ontology/schema` | 当前用户可见的完整本体定义 |
| GET | `/api/v1/ontology/objects/{objectType}` | 对象列表：`q` 全文、`where.<property>=` 精确筛选、`orderBy`、`page`、`pageSize` |
| GET | `/api/v1/ontology/objects/{objectType}/{primaryKey}` | 单个对象（含关系计数） |
| GET | `/api/v1/ontology/objects/{objectType}/{primaryKey}/links/{linkApiName}` | 关系遍历 |
| GET | `/api/v1/ontology/objects/{objectType}/{primaryKey}/actions` | 该对象可执行的动作及提交条件结果 |
| POST | `/api/v1/ontology/actions/{actionType}/apply` | 执行动作；`{"parameters": {...}, "validateOnly": false}` |
| GET | `/api/v1/ontology/action-log` | 动作审计日志，可按对象过滤 |
| POST | `/api/v1/ontology/functions/{functionName}/execute` | 执行函数 |
| POST | `/api/v1/ontology/objectSets/loadObjects` | 对象集查询（OSDK 结构）：`objectSet`、`select`、`orderBy`、`pageSize`、`pageToken` |
| POST | `/api/v1/ontology/objectSets/aggregate` | 对象集聚合：`objectSet`、`aggregation`、`groupBy` |
| GET | `/api/v1/ontology/index` | 对象索引状态（需要 `ontology` 权限） |
| POST | `/api/v1/ontology/index/refresh` | 强制重建全部对象索引（需要 `ontology` 权限） |

## 10. 元数据驱动与版本治理（v1.1）

参考 Hasura OpenDD 和 operational-ontology，采用“**结构写成数据，规则写成代码**”的原则：

| 放在 YAML 元数据（`backend/app/ontology/metadata/`） | 放在代码（`rules.py`，按名称注册） |
|---|---|
| 本体清单与版本号、值类型（枚举）、安全标记 | 数据源（datasource）：把物理表物化为对象 |
| 对象类型、属性、物理映射、来源类型、标记 | 关系解析器（resolver）：多对多等派生关系 |
| 关系类型（join 键或 resolver 名称） | 提交条件（criterion），可带参数，例如 `statusIsNot(status, reason)` |
| 动作类型：参数、权限、修改范围、写回数据集、提交条件引用 | 动作处理器（handler）：经 `ProjectRepository` 写回 |
| 函数：参数、输出类型、权限、实现名称 | 函数实现（function） |

每个元数据文档统一为 `{kind, version, definition}`，kind 包括 `Ontology / ValueType / Marking / ObjectType / LinkType / ActionType / Function`。

**编译流程**（`compiler.py`）：解析 YAML → 按 kind 做结构校验（未知字段、类型、格式即报错，并定位到文件和第几个文档） → 解析引用（值类型、标记、权限、对象类型、属性、规则名称、提交条件参数签名） → 派生 `editedBy`（由动作的 `edits` 自动反推，不在属性上重复声明） → 计算内容指纹（规范化 JSON 的 SHA-256，与格式和注释无关）。任何错误都会让服务启动失败，并一次性列出所有问题。

**版本治理**（`versioning.py`，表 `ontology_versions`）：

- 启动时登记 `Ontology.version`（语义化版本号）和内容指纹。
- 同一版本号内容不同 → 拒绝启动，提示升级版本号。
- 新版本号低于已登记的最高版本 → 拒绝启动；重新启用已登记的旧版本（内容一致）允许，用于回滚。
- 每个版本保存完整元数据快照，可按对象、属性、参数、提交条件逐项比较；删除对象/属性/参数、修改类型或主键、新增必填参数、收紧权限会标记为**破坏性变更**。

接口：`GET /api/v1/ontology/versions`、`GET /api/v1/ontology/versions/{version}`、`GET /api/v1/ontology/versions/diff?from=&to=`（需要 `ontology` 权限）。前端“本体浏览器 → 元数据与版本”页签展示定义文件、值类型、版本历史和差异。

**修改本体的流程**：编辑 YAML → 升级 `metadata/ontology.yaml` 中的 `version` → 运行 `pytest` → 重启服务。新增规则时先在 `rules.py` 注册，再在 YAML 中按名称引用。

## 11. 对象集查询与对象索引（v1.2）

### 11.1 对象集（Object Set）

请求结构按 Palantir 官方 SDK（`foundry-platform-python` 的 Ontology v2 模型）对齐，概念可以直接迁移：

| 对象集类型 | 结构 | 含义 |
|---|---|---|
| `base` | `{type, objectType}` | 某对象类型的全部对象 |
| `static` | `{type, objectType, objects: [主键]}` | 指定对象 |
| `filter` | `{type, objectSet, where}` | 按条件过滤 |
| `union` / `intersect` / `subtract` | `{type, objectSets: [...]}` | 并集 / 交集 / 差集（同一对象类型） |
| `searchAround` | `{type, objectSet, link}` | 沿关系检索，结果变为目标对象类型，自动去重 |

where 条件：`eq`、`in`、`gt`、`gte`、`lt`、`lte`、`isNull`、`startsWith`、`contains`（数组元素/文本包含）、`containsAnyTerm`、`containsAllTerms`、`and`、`or`、`not`。比较按属性类型进行：金额、人天按十进制，日期按 ISO 字符串，枚举值会校验取值范围。

聚合指标：`count`、`sum`、`avg`、`min`、`max`、`exactDistinct`、`approximateDistinct`（当前为精确计算）；分组：`exact`（支持 `maxGroupCount`，超出部分计入 `excludedItems`）、`fixedWidth`、`ranges`。

与 Foundry 的差异（有意为之）：
- `static` 用主键而不是 RID，因此需要带 `objectType`；
- 数值聚合结果返回十进制字符串，不用浮点，保证金额精确；
- 数组属性（如 `riskReasons`）按元素分组，可直接统计“各风险原因涉及多少项目”。

安全：对象集中每个对象类型都要可见；按带标记的属性过滤、排序、分组或计算指标，需要该标记的权限（否则 403），防止通过查询结果反推敏感值。

示例——“逾期里程碑所在项目的项目团队”：

```json
{"type": "searchAround", "link": "team",
 "objectSet": {"type": "searchAround", "link": "project",
   "objectSet": {"type": "filter",
     "objectSet": {"type": "base", "objectType": "Milestone"},
     "where": {"type": "and", "value": [
       {"type": "lt", "field": "plannedEnd", "value": "2026-10-23"},
       {"type": "not", "value": {"type": "eq", "field": "status", "value": "completed"}}]}}}}
```

### 11.2 对象索引

之前每次请求都从头计算全部对象；现在每个对象类型的物化结果缓存在进程内的对象索引中（`index.py`）：

- **变更感知**：`ontology_dataset_versions` 表记录每个底层数据集的版本号，由 SQLite 触发器在任何写入（CRM 导入、工时导入、动作写回、原有接口、快照切换）时自动加一。无论数据从哪条路径写入都不会漏掉。
- **按需失效**：对象类型的缓存签名 = 当天日期 + 其 `backingDatasets` 的版本号。只有相关数据集变化（或跨天，因为逾期状态依赖日期）才重建，其余类型继续命中缓存。例如改项目设置只重建“项目”，员工和工时保持缓存。
- **导入时刷新**：CRM 导入和工时导入成功后，在后台任务中立即重建失效的类型；服务启动时也会预热全部索引。
- **元数据约束**：`backingDatasets` 必须是已登记触发器的数据集，否则编译失败，避免缓存永远不失效。
- **可观测**：`GET /api/v1/ontology/index` 返回每个类型的对象数、是否最新、构建耗时、构建次数和命中次数；前端“对象集查询”页顶部可查看和强制重建。

当前粒度是“对象类型级重建”。后续数据量变大时，可以改为按主键的增量更新，或把索引落到 SQLite/外部搜索引擎。

### 11.3 计算口径如何落在本体里（v2.0）

业务口径分成两类，各自放在本体的固定位置：

- **可调的数值**放在 `OperatingPolicy`（运营口径参数）这个对象上，例如预算关注线、利用率阈值、商机停滞天数、丢单状态。修改只能经由「调整运营口径」动作或系统设置页，两条路径都写审计日志。参数所在的表已登记为对象索引的数据集，所以修改后依赖它的项目风险和健康度会自动重算。
- **计算方法**写在属性的 `source` 说明和规则代码中。例如 `Project.outstandingReceivableAmount` 的说明就是待回款的公式；本体浏览器的「本体模型」页可以直接查到。

完整的口径清单，以及每条口径对应的对象、属性或函数，见 [BUSINESS_RULES.md](./BUSINESS_RULES.md)。

### 11.4 部门经营（v3.0.0）

v3.0.0 是破坏性变更：`Project.recognizedRevenueAmount` 退役，由经营口径的 `Project.confirmedRevenue` 取代。新增内容：

- **项目**：人工维护的经营输入（合同人天单价、税率、外部采购、商务费用、目标毛利率及覆盖原因、剩余人天估算），以及派生的暂估收入、可用成本、可支配人天、动态成本、三种毛利率、成本超前和经营状态。派生属性的 `derivedFrom` 都指向函数 `projectEconomics`，计算在 `backend/app/economics.py`。
- **员工、客户、回款节点**：员工增加资源健康属性，客户增加经营汇总属性，回款节点增加「计入确认收入」。
- **新对象 `AnnualOperatingPlan`**：部门年度目标。它和 `OperatingPolicy` 一样不与其他对象关联，说明里以「独立对象：原因」声明，lint 据此跳过孤立对象检查。
- **动作**：「设置项目」增加经营输入与校验条件 `economicsInputsValid`（直接调用仓储的校验函数）；「调整运营口径」增加 9 个经营参数与 `economicsPolicyValid`（与系统设置页同一取值范围）；新增「设定年度经营计划」。
- **函数**：`projectEconomics`、`customerEconomics`、`resourceHealth`、`departmentOperatingReview`。没有 `FINANCE` 标记的用户执行时，金额和含金额的依据文本被脱敏。
- **权限**：新增菜单权限 `department`。它可以读取项目、客户和员工对象，并持有 `FINANCE` 与 `PERSONNEL` 标记，与部门经营页面 `/api/v1/department/*` 的数据范围一致。
- **值类型**：`RevenueRecognitionMethod`、`EconomicsStatus`、`ResourceHealthStatus`。

规模：13 个对象类型、18 种关系、18 个动作、14 个函数、2 个安全标记。构建包见 [2026-10-26-pmo-economics.yaml](./ontology-builds/2026-10-26-pmo-economics.yaml)。

### 11.5 分析从本体推导（v3.1.0，第一阶段）

用户 2026-10-26 要求：基础程序只维护项目主数据和关键字段，分析和监控都从本体推导。第一阶段覆盖项目管理页、项目详情和部门经营。

- **分层**：`ProjectRepository` 只负责导入、维护、写入校验和审计，用 `project_sources()` 交出每个项目的原始数据，不再含任何项目分析计算，也不引用本体（有测试守护）。原先的计算代码原样移到 `backend/app/ontology/derivations/`（`project.py`、`economics.py`），年度计划的维护移到 `app/plans.py`。
- **推导规则（derivation）**：新增的元数据概念。对象类型用 `derivations` 声明推导顺序，规则在 `rules.py` 中以 `@RULES.derivation` 注册；每个派生属性的 `derivedFrom` 必须是本对象的推导规则之一。运行时先由数据源物化主数据，再依次执行推导规则；规则的输出与元数据声明不一致时直接报错（`DerivationError`）。规则之间以「__」开头的内部字段传递中间结果，这些字段不是属性，永远不会序列化。Project 的三条规则：`projectFacts`（风险、预算、回款、里程碑、待办与异常提醒）、`projectEconomics`（人天经营口径）、`projectInvestment`（投入阶段）。
- **页面函数**：`projectList`、`portfolioHealth`、`projectOverview`、`portfolioEconomics`、`customerEconomicsRollup`，加上已有的 `departmentOperatingReview`、`resourceHealth`。页面直接调用这些函数；原有的分析接口保留一个版本，内部调用同一批函数，返回格式不变。
- **脱敏**：页面函数按 FINANCE / PERSONNEL 标记隐藏金额和个人工时，结果中的 `redacted_markings` 列出被隐藏的标记。
- **缓存**：推导结果随 Project 对象缓存在对象索引中，写入通过数据集版本失效，跨天重建。
- **尚未迁移**：资源运营监控、调度沙盘、员工工时评价（第二阶段）和投入成本分析（第三阶段）。它们的计算暂留在基础程序，但读取的项目事实已经来自本体。

规模：13 个对象类型、18 种关系、18 个动作、19 个函数、3 条推导规则、2 个安全标记。构建包见 [2026-10-26-ontology-first-phase1.yaml](./ontology-builds/2026-10-26-ontology-first-phase1.yaml)。

### 11.6 暂停人天经营口径（v4.0.0）

财务数据还不能对应到项目，用户决定暂不维护财务字段，并连同经营口径一起暂停（破坏性变更）：

- 删除项目的 7 个经营输入（合同人天单价、税率、外部采购、商务费用、目标毛利率、覆盖原因、剩余人天估算）与 15 个经营属性、推导规则 `projectEconomics`、客户的经营汇总属性、回款节点的「计入确认收入」、值类型 `RevenueRecognitionMethod` 与 `EconomicsStatus`。
- 删除函数 `projectEconomics`、`customerEconomics`、`portfolioEconomics`、`customerEconomicsRollup`，以及接口 `/api/v1/department/customers`、`/api/v1/department/projects`。
- 「设置项目」动作和基础程序的项目设置接口不再接收经营输入；运营口径参数移除只服务经营口径的五项；校验规则改名为 `policyRangesValid`；年度计划不再维护确认收入与毛利率目标。
- 部门复盘（`DepartmentAnalysis.department_review`）只保留新签、回款、投入人天、利用率、人数与不依赖单价的信号。
- 保留：数据库列与已录数据、`derivations/economics.py` 中的纯计算函数及 SEY 示例测试、3.0.0 构建包的规则卡，供恢复时使用。

规模：13 个对象类型、18 种关系、18 个动作、15 个函数、2 条推导规则、2 个安全标记。构建包见 [2026-10-27-pause-economics.yaml](./ontology-builds/2026-10-27-pause-economics.yaml)。

### 11.7 属性量纲（v4.1.0）

每个数值属性（integer、decimal、money、percent）都要声明量纲 `unit`：`yuan`（元）、`personDay`（人天）、`hour`（小时）、`day`（天）、`month`（个月）、`percent`（%）、`percentagePoint`（个百分点）、`person`（人）、`count`（个）或 `none`（无量纲，用于年度、序号等编号）。编译器检查三件事：数值属性必须有量纲；非数值属性不能有量纲；money 当且仅当 `yuan`，percent 当且仅当 `percent`。

- 本体结构接口为每个属性返回 `unit` 和中文单位名 `unitLabel`；本体浏览器的属性表在类型后显示单位，对象详情按量纲加单位（个数和编号不加）。
- lint 按量纲识别金额（`yuan` 没有 FINANCE 标记即提示），不再按属性名猜测——这条规则曾两次误报（经验库 L06、L15）。
- 依据：Kokar 等《Ontology-based situation awareness》(2009) 为属性值引入量纲类型。构建包见 [2026-10-27-property-units.yaml](./ontology-builds/2026-10-27-property-units.yaml)。

### 11.8 维护在本体中进行（v4.2.0）

导入（CRM、工时的新增和修改）仍由基础程序完成；导入后的补充维护全部在本体中进行。

- **维护要求**：对象类型用 `maintenanceRequirements` 声明导入后必须补充的内容。每条要求二选一：某个人工维护属性必须有值（`property`，可用 `missingValues` 排除「未分类」这类占位值），或至少有若干关联对象（`link` + `minCount`）；可加适用条件 `when`（一条或多条，全部满足才适用；每条写 `in` 或 `notIn`，枚举取值由编译器核对），并用 `action` 指明由哪个动作维护。编译器检查属性是人工维护的、关系带连接键、动作确实修改该属性或创建关联对象。
- **维护状态**：通用推导规则 `maintenanceStatus` 按声明给出 `maintenanceChecklist`（每条适用要求是否已维护）、`maintenanceGapCount` 和 `maintenanceComplete`；项目事实中的「人工维护尚未完成」提醒用同一结果。函数 `maintenanceWorklist` 列出待维护对象，并统计每条要求的已维护与待维护数量；`portfolioHealth` 增加维护状态筛选和待维护项目数。
- **写入**：项目设置、里程碑、回款约定、回款节点、管理干预、年度计划的写入都是本体动作。页面直接调用动作；原维护接口保留一个版本，改为调用同一动作并按原格式返回写入的记录（动作以 `include_record` 返回）。审计日志只剩本体动作一种渠道。系统设置页的运营参数和投入成本参数暂时仍走基础程序接口。
- **顺带修复**：新建里程碑的日期校验曾读取目标对象（项目）的计划开始日；对象索引预热时漏报被连带重建的对象类型。

规模：13 个对象类型、18 种关系、18 个动作、16 个函数、3 条推导规则、2 个安全标记。构建包见 [2026-10-27-ontology-maintenance.yaml](./ontology-builds/2026-10-27-ontology-maintenance.yaml)。

### 11.9 追加维护要求（v4.3.0）

只改元数据就追加了要求，没有改维护状态的代码，验证了 4.2.0 的声明式设计。

- **项目**：新增「指定项目经理」「填写计划开始日期」「填写计划结束日期」，由「设置项目」维护，共 6 条要求。
- **回款约定**：新增「关联里程碑」（`linkedMilestoneId`），由「编辑里程碑」维护；「新建里程碑」也可以带 `paymentTermId` 直接关联。回款约定的派生属性（回款节点、计划回款日、回款状态）移入推导规则 `paymentTermFacts`，数据源只物化维护的数据。

规模：13 个对象类型、18 种关系、18 个动作、16 个函数、4 条推导规则、2 个安全标记。构建包见 [2026-10-27-maintenance-requirements.yaml](./ontology-builds/2026-10-27-maintenance-requirements.yaml)。

### 11.10 维护要求的豁免条件（v4.4.0）

售前阶段和已结束、已关闭的项目不要求项目经理和计划起止。适用条件新增 `notIn` 并支持多条件：

```yaml
when: [{property: contractType, notIn: [sales]}, {property: lifecycleStatus, notIn: [completed, closed]}]
```

用「不在」表达豁免，以后新增项目类型时要求默认适用。构建包见 [2026-10-27-maintenance-exemptions.yaml](./ontology-builds/2026-10-27-maintenance-exemptions.yaml)。

### 11.11 取消资源调度沙盘（v5.0.0）

用户 2026-10-27 决定取消资源调度沙盘，后续基于本体重新设计资源分析测算。删除函数会让调用方失效，属于破坏性变更，升主版本。

- **删除**：函数 `resourceSandbox`；基础程序中的沙盘计算与新项目需求模拟；接口 `GET /api/v1/resource-sandbox`、`POST /api/v1/resource-sandbox/simulate`；前端「资源调度沙盘」页面和本体浏览器中的沙盘结果视图。
- **保留**：资源运营监控与 `resourceHealth`、中国工作日历 `workingCalendar`；运营口径参数的利用率最低线和优秀线（资源运营监控的可调剂人天和负荷分级仍在使用），说明中去掉了沙盘用途。
- 历史构建包和设计稿中关于沙盘的内容保留原文，记录当时的决定。

规模：13 个对象类型、18 种关系、18 个动作、15 个函数、4 条推导规则、2 个安全标记。构建包见 [2026-10-27-retire-resource-sandbox.yaml](./ontology-builds/2026-10-27-retire-resource-sandbox.yaml)。

### 11.12 有效工时（v5.1.0）

用户 2026-10-28 决定：已驳回和草稿工时不计入统计，已提交仍计入（不以审批通过为前提）。

- **工时记录**新增派生属性「计入有效工时」（`countsAsEffort`），可以用对象集查出不计入的工时。判定写在基础程序的 `_counts_as_effort`，项目推导、员工、资源运营监控、部门资源健康和投入成本分析共用同一判定，保证项目和员工两侧同口径。
- 项目详情的月度工时清单仍列出全部工时，不计入的单独标明条数和人天。
- **Excel 重复导入**：同一条工时的审批状态只向前更新（已提交 → 已确认或已驳回），因为 Excel 没有可靠的更新时间，不能让旧文件把已确认改回去。接口导入仍按源更新时间覆盖。

规模不变（新增 1 个属性）。构建包见 [2026-10-28-effective-worktime.yaml](./ontology-builds/2026-10-28-effective-worktime.yaml)。

### 11.13 销售机会的转化状态（v5.2.0）

用户 2026-10-28 发现已签合同的 TPL149 仍出现在「销售机会」中。原因是对象说明写着「尚未被合同合并的销售机会」，数据源却从未过滤，页面上也看不出商机已转化。用户选择保留已转化的商机并标明状态：

- 「已转化」是商机的状态而不是另一种对象（原则 3）。新增派生属性「已转化为合同」（`convertedToContract`），判定与导入时的转化规则一致：同一项目编号已有合同或订单即为已转化。对象说明改为与数据一致。
- 字段与 CRM 商机模板对齐：`signedAt` 显示为「预计签单日期」（合同的签订日期不变，同名异义按对象拆开），新增「销售阶段」（`salesStage`）。
- 对象列表固定显示前四个属性，因此调整属性顺序为项目编号、状态、赢率、已转化为合同；原来的客户、分类在商机模板中不存在，列表一直为空。

规模不变（新增 2 个属性）。构建包见 [2026-10-28-opportunity-conversion.yaml](./ontology-builds/2026-10-28-opportunity-conversion.yaml)。

### 11.14 客户的已回款与已开票（v5.3.0）

客户新增「已回款」（`totalReceivedAmount`）和「已开票」（`totalInvoicedAmount`），与「合同总额」同源：都取该客户合同/订单的 CRM 金额，对应项目上的 `crmReceivedAmount`、`invoicedAmount`；加 FINANCE 标记，在客户详情的「CRM 汇总」中紧接合同总额显示。回款节点的人工到账不混入，避免两个来源相加。构建包见 [2026-10-28-customer-receipts.yaml](./ontology-builds/2026-10-28-customer-receipts.yaml)。

### 11.15 签约年度维度（v5.4.0）

用户 2026-10-28 要求以 CRM 合同的「签约时间」划分年度，测算按年度的运营指标。

- **属性**：`Contract.signedYear`（签约时间的年份）；`Project.signedYear`（最早一份合同的年份，由推导规则 `projectFacts` 计算）。年份判定写在基础程序的 `_signed_year`，合同、项目、客户和函数共用。
- **客户本年度**：`currentYearContractValue`、`currentYearReceivedAmount`、`currentYearInvoicedAmount`，只汇总签约年度等于服务器当前年度的合同；对象索引的缓存键含当天日期，跨年自动切换。
- **函数**：`customerAnnualSummary(year?)` 按年度、客户汇总合同数、项目数、新签额、开票、回款，金额按 FINANCE 脱敏。CRM 的开票、回款没有发生日期，按签约年度归集。
- **浏览器**：对象浏览中，带 `signedYear` 的对象类型显示年度筛选（列表接口的 `where.signedYear`）；对象集查询可按签约年度筛选和精确值分组。

规模：13 个对象类型、18 种关系、18 个动作、16 个函数、4 条推导规则、2 个安全标记。构建包见 [2026-10-28-signing-year.yaml](./ontology-builds/2026-10-28-signing-year.yaml)。

### 11.16 客户最近签订只取合同（v5.4.1）

`Customer.latestSignedAt` 原取客户全部 CRM 记录 `signed_at` 的最大值，而商机把「预计签单日期」存在同一列，客户有商机时会显示尚未签约的日期。改为只取合同/订单的签订日期（与 L30「同一物理列在两类记录中含义不同」同类）。只改派生口径与说明，升修订号。构建包见 [2026-10-28-customer-latest-signing.yaml](./ontology-builds/2026-10-28-customer-latest-signing.yaml)。

### 11.17 引用参数的候选与带出（v5.5.0）

- **候选**：对象动作接口为 `object_ref` 参数返回候选对象（与目标同一项目，按 `projectNumber` 限定）和 `currentValue`（已指向目标的对象，如已关联该里程碑的回款约定）；本体浏览器显示为下拉框，可「解除关联」。
- **fills**（7.0.0 起改为 `optionDetails`，见 11.19）：参数元数据新增 `fills: {参数: 被引用对象的属性 | "=本动作参数"}`。选中被引用对象后，表单带出这些参数并锁定、提交时不发送；编译器校验键和值都存在。新建、编辑里程碑的「关联回款约定」声明 `fills: {paymentAmount: plannedAmount, paymentDate: "=plannedEnd"}`，与基础程序实际写入回款节点的口径一致；校验条件 `paymentFieldsComplete` 在关联约定时不再要求另填日期和金额。
- **项目**：新增 `paymentTermCount`、`unlinkedPaymentTermCount`（推导规则 `projectFacts`），与 `paymentNodeTotal` 并列，说明约定未关联里程碑时还没有回款节点。

构建包见 [2026-10-28-term-linked-milestone-form.yaml](./ontology-builds/2026-10-28-term-linked-milestone-form.yaml)。

### 11.18 取消新建回款节点（v6.0.0）

用户 2026-10-28 说明回款都按合同条款登记，独立新建回款节点用不到。删除动作 `createPaymentNode` 和接口 `POST /api/v1/projects/{项目编号}/payment-nodes`（返回 404），属于破坏性变更，升主版本。新的回款计划只有一条路径：新建回款约定 → 新建或编辑里程碑时关联 → 生成回款节点。编辑、确认到账、删除回款节点的动作不变，存量节点照常维护；里程碑上不关联约定的「商务回款节点」（历史回款事件）本次保留。基础程序的 `create_payment_node` 仍供示例数据初始化使用，不对外提供。

规模：13 个对象类型、18 种关系、17 个动作、16 个函数、4 条推导规则、2 个安全标记。构建包见 [2026-10-28-retire-create-payment-node.yaml](./ontology-builds/2026-10-28-retire-create-payment-node.yaml)。

### 11.19 回款节点只由回款约定产生（v7.0.0）

用户 2026-10-28 决定一并取消里程碑上单独填写回款日期和金额的写法。新建、编辑里程碑删除参数 `isPaymentNode`、`paymentDate`、`paymentAmount` 和对应校验条件（破坏性变更，升主版本）；里程碑只有关联回款约定时才有回款节点。5.5.0 的 `fills` 随目标参数删除而失去用途，改为 `optionDetails`：选中候选对象后在下方显示其若干属性（回款约定显示约定金额和回款比例），不作为参数提交。

改造测试时发现两个旧问题并修正：编辑已关联约定的里程碑而不重新提交约定时，基础程序按历史回款事件处理，回款日期不随计划完成日更新、节点被改名为里程碑名称；现在关联约定的节点始终按约定同步，已到账的节点不再被改动。历史回款事件保留，关联约定时被替换。构建包见 [2026-10-28-retire-milestone-payment-events.yaml](./ontology-builds/2026-10-28-retire-milestone-payment-events.yaml)。

### 11.20 员工项目投入占比（v7.1.0）

`Employee.projectEffortShares`（json，PERSONNEL）：按项目代码列出有效工时和占该员工全部有效工时的百分比，含内部代码（标 internal），名称取成本对象的 CRM 主题；员工的 backingDatasets 加入 records，CRM 名称变化时缓存失效。本体浏览器以表格和比例条显示。构建包见 [2026-10-29-employee-project-shares.yaml](./ontology-builds/2026-10-29-employee-project-shares.yaml)。

### 11.21 统计期间（v7.2.0）

对象属性不能带参数，但「按月查看」是对象级需求，因此在元模型中新增统计期间：

- **对象类型** `period: {unit: month, displayName, activeProperty, options: {objectType, property}}`：可按月份（YYYY-MM）查看；选了月份时列表只列 `activeProperty` > 0 的对象，可选月份取 `options` 指向的日期属性。
- **属性** `periodic: true`：按所选月份计算（未选时取默认月份）；未标记的是累计汇总。
- **关系** `periodProperty`：从按月查看的对象出发时，只返回该月有活动的目标（join 关系按目标的日期属性筛选，resolver 关系由解析器按 via 对象的日期筛选）。
- **运行时**：请求上下文带 `period`；按月计算的对象按请求计算，不进共享索引（索引保存默认月份）。对象列表、详情、关系接口接受 `month` 参数，响应的 `meta.period` / `period` 给出当前月份、默认月份和可选月份。对象集查询暂不支持月份（构建包例外）。
- **员工**：声明 period（activeProperty = monthEntryCount，月份来自 TimeEntry.workDate）；新增统计月份、当月工时条数/填报/确认/参与项目数/项目投入占比；资源健康六个属性改为 periodic；工时记录、参与项目、投入的成本对象三条关系按月筛选。

编译器校验：periodic 只能出现在声明了 period 的类型上，activeProperty 必须是 periodic 数值属性，options 与 periodProperty 必须指向日期属性。构建包见 [2026-10-29-employee-month-period.yaml](./ontology-builds/2026-10-29-employee-month-period.yaml)。

### 11.22 统计期间：按日期归属与默认视图（v7.3.0）

`period` 新增 `dateProperty`（对象本身带日期，属于日期所在月份；与 `activeProperty` 二选一）和 `defaultView`（`all` 或 `latest`：页面打开时显示全部月份还是最近有数据的月份）。工时记录声明 `dateProperty: workDate, defaultView: latest`；员工保持 `activeProperty` 与 `all`。接口不带 month 时始终返回全部，默认月份只是页面的初始选择。没有 periodic 属性的类型按月查看时仍使用共享索引。构建包见 [2026-10-29-time-entry-month-period.yaml](./ontology-builds/2026-10-29-time-entry-month-period.yaml)。

### 11.23 起止区间、月份区间、列表列与引用名称（v7.4.0）

- **统计期间**：第三种归属 `startProperty + endProperty`（起止区间与所选月份区间重叠即属于）；`range: true` 允许选择月份区间（接口参数 `month` + `monthTo`）；有 periodic 属性的类型不能声明 range。`options` 只在 activeProperty 时必填，其余取本类型自己的日期。里程碑声明计划起止与 range。
- **列表列**：对象类型可声明 `listProperties`；里程碑为项目编号、状态、序号、计划开始、计划完成。名为 status 的枚举属性在浏览器中以标签显示，已完成 / 已到账 / 已关闭为绿色，其他为灰色。
- **引用名称**（运行时，不改元数据）：对象详情返回 `references`，凡是保存着另一个对象主键的属性（由 join 关系推出），给出该对象的类型、主键和标题，页面显示名称并可点开，例如里程碑的关联回款节点显示「验收款」。

构建包见 [2026-10-29-milestone-span-period.yaml](./ontology-builds/2026-10-29-milestone-span-period.yaml)。

## 12. 代码结构

```text
backend/app/ontology/
  metadata/           YAML 元数据：ontology.yaml、object-types/、link-types.yaml、action-types/、functions.yaml
  metadata_schema.py  元数据文档结构（Pydantic，禁止未知字段）
  compiler.py         元数据编译：校验、引用解析、editedBy 派生、内容指纹
  rules.py            规则注册表：数据源、关系解析器、提交条件、动作处理器、函数实现
  versioning.py       版本登记、历史与结构化差异
  model.py            运行时元模型：ObjectType / Property / LinkType / ActionType / Function / Marking
  security.py         Safe：权限判定、属性脱敏
  index.py            对象索引：按数据集版本缓存、失效与预热
  objectsets.py       对象集引擎：集合运算、where 条件、沿关系检索、分页、聚合
  runtime.py          OntologyService：对象物化、查询、关系遍历、动作管线、函数执行、审计
backend/tests/test_ontology.py           定义一致性、对象/关系/函数、动作管线与审计、安全策略
backend/tests/test_ontology_metadata.py    元数据编译校验、版本治理、差异与权限
backend/tests/test_ontology_objectsets.py  对象集条件、集合运算、沿关系检索、分页、聚合、安全、索引缓存
frontend/src/ontology/
  OntologyExplorer.tsx  本体浏览器（模型视图 + 对象浏览 + 关系 + 动作 + 元数据与版本 + 审计）
  ObjectSetWorkbench.tsx 对象集查询工作台（构建器、JSON、示例、结果、聚合、索引状态）
  shared.tsx            属性值展示等共享组件
  api.ts / types.ts     本体 API 客户端与类型
  ontology.css          本体浏览器样式
```

## 13. 演进路线

| 阶段 | 内容 |
|---|---|
| v1 | 元模型、9 个对象类型、12 种关系、11 个动作、6 个函数、安全标记、审计、本体浏览器 |
| v1.1 | 元数据驱动（YAML + 规则注册表）、编译校验、版本登记、版本差异与破坏性变更识别 |
| v1.2 | OSDK 风格对象集查询（集合运算、条件、沿关系检索、聚合）、带触发器失效的对象索引 |
| v1.3 | 投入成本模型：`CostObject`（项目代码作为成本对象）、工时成本阶段、商机结论动作、投入分析函数，详见 [INVESTMENT_COST_MODEL.md](./INVESTMENT_COST_MODEL.md) |
| v2.0（本次） | 吸收《业务规则与计算口径》：新增 `PaymentTerm`、`OperatingPolicy`（口径参数作为本体对象，可经动作调整并审计）、`workingCalendar` 与 `resourceSandbox` 函数；`Project` 新增 CRM 分类、项目状态来源、结算方式、确认收入，退役 `contractStatus`；项目类型与项目状态改用业务名称。属性的「来源」说明即计算口径，详见 [BUSINESS_RULES.md](./BUSINESS_RULES.md) |
| v2.0.1 | 「调整运营口径」覆盖全部参数（含丢单状态、内部代码、年度参考工作日），新增提交条件 `policyThresholdOrder`、`policyValuesValid`；本体质量检查脚本纳入测试 |
| v2.0.2 | 声明预期悬空的关系（「允许悬空：原因」）；本体构建流程改为「构建包 + 四阶段诊断」：规则卡写明依据和验收口径，例外表为空即不合格，诊断报告驱动修复，经验写入技能的经验库 |
| v3.0（设计中） | 部门 PMO 经营管理：项目人天经营口径（暂估收入、可用成本、可支配人天、动态成本、暂估与确认毛利率、成本超前、经营状态）、客户汇总、资源健康、年度经营计划与策略信号，见 [设计稿](./ontology-design/2026-10-26-pmo-ontology-v3.md) |
| 后续 · 写入统一 | 现有项目驾驶舱的编辑表单改为调用 Action；PM Assistant 以本体 schema + 对象作为检索上下文（基于对象和关系回答问题） |
| 后续 · 行级安全 | 行级安全（项目经理只能看自己负责的项目）；对象索引缓存（快照切换时重建）；本体定义版本化与变更评审 |
| 后续 · 系统集成 | 接入工时系统真实 API 后，`Employee` 增加部门/技能属性；与 ERP、OA 等外部系统的对象映射 |
