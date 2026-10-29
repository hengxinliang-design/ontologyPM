# TSG PM 本体的代码地图、命令与已知陷阱

改元数据之前读一遍。路径都相对于仓库根目录 `TSG-MVP/`。

## 文件地图

| 位置 | 内容 |
| --- | --- |
| `backend/app/ontology/metadata/ontology.yaml` | 清单：本体版本号、安全标记、值类型（枚举） |
| `backend/app/ontology/metadata/object-types/*.yaml` | 对象类型与属性 |
| `backend/app/ontology/metadata/link-types.yaml` | 关系类型（join 或 resolver） |
| `backend/app/ontology/metadata/action-types/*.yaml` | 动作：参数、权限、edits、writeback、校验条件 |
| `backend/app/ontology/metadata/functions.yaml` | 函数：参数、输出类型、权限 |
| `backend/app/ontology/metadata_schema.py` | 元数据的 Pydantic 模式（未知字段即报错） |
| `backend/app/ontology/compiler.py` | 编译：结构校验、引用解析、派生 editedBy、计算指纹 |
| `backend/app/ontology/rules.py` | 规则注册表：datasource、resolver、criterion、handler、function |
| `backend/app/ontology/runtime.py` | 运行时服务：对象、关系遍历、动作执行、函数、版本、对象集 |
| `backend/app/ontology/objectsets.py`、`index.py` | 对象集查询；带失效的对象索引 |
| `backend/app/ontology/versioning.py` | 版本登记与差异 |
| `backend/app/db.py` | 表结构、迁移、`OBJECT_DATASETS`（触发器维护数据集版本） |
| `backend/app/project_repository.py`、`investment.py` | 业务计算的主体（风险、健康、资源、投入） |
| `backend/app/maintenance_rules.py` | 人工维护规则的唯一实现（提交条件与基础程序兜底共用） |
| `frontend/src/ontology/` | 本体浏览器；`functionResults.tsx` 是函数结果的可读视图 |
| `docs/BUSINESS_RULES.md` | 计算口径清单，每条口径的“本体位置” |
| `docs/ONTOLOGY_ARCHITECTURE.md` | 本体架构与演进路线 |
| `docs/prd/PRD.md` | 产品需求文档，第 7、8 章与本体相关 |
| `docs/ontology-builds/*.yaml` | 每次本体构建的构建包：规则卡与例外表 |
| `backend/tests/test_ontology_diagnosis.py` | 诊断循环与构建包检查的测试 |

## 常用命令

在 `backend/` 目录：

```bash
.venv/bin/python -c "from app.ontology import load_ontology; o = load_ontology(); print(o.version, len(o.object_types), len(o.link_types), len(o.action_types), len(o.functions))"
```

```bash
.venv/bin/pytest -q
```

```bash
.venv/bin/python ../.claude/skills/tsg-ontology/scripts/lint_ontology.py
```

完整诊断（四阶段，报告写到仓库的 `.ontology-diagnostics/`，该目录不入库）：

```bash
.venv/bin/python ../.claude/skills/tsg-ontology/scripts/diagnose_ontology.py --database <数据库文件> --build ../docs/ontology-builds/<构建包>.yaml --require-build
```

`--database` 指向一份真实或预览数据库；脚本会先复制再读取，不会改动原文件。

在 `frontend/` 目录：

```bash
npx tsc -b && npm test && npm run build
```

## 已知陷阱

这些都在本项目中实际发生过。

1. **YAML 行内映射里的半角逗号会拆坏字段。** 属性写成 `{apiName: x, source: max(a − b, 0)}` 时，`, 0)` 会被当成新字段，编译报 “Extra inputs are not permitted”。说明文字里改用全角逗号「，」，或给值加引号。
2. **同一版本号内容变了，服务拒绝启动。** 版本登记器会比对指纹。改了任何元数据（哪怕只是说明文字）都要升版本号。开发时如果预览库已经登记过一个草稿版本，只能在本地预览库里删除那条草稿记录，绝不能对用户的正式数据库这样做。
3. **新表必须登记为数据集。** 对象的数据来自新表时，要把表名加入 `db.py` 的 `OBJECT_DATASETS`，并写进对象类型的 `backingDatasets`。否则写入后对象索引不失效，页面显示旧数据。规则依赖的参数表（例如 `worktime_baseline_config`）也要加进依赖它的对象。
4. **默认行要在建表时插入。** 参数表的默认行用 `INSERT OR IGNORE` 写在 `SCHEMA` 里。如果在第一次读取时才插入，预热索引时会改动数据集版本，导致缓存反复失效。
5. **本体要读全量数据。** 仓储层的列表接口是游标分页（每页 50 条）。本体加载器要用 `project_repository.all_projects()` 这类不分页的方法，否则对象只剩前 50 个。
6. **计数断言要同步更新。** `backend/tests/test_ontology_metadata.py` 断言了版本号以及对象、关系、动作、函数的数量。新增类型后同时更新它，并把版本相关断言一起改掉。
7. **维护只有一条写入路径。** 4.2.0 起项目维护的写入都是本体动作，原维护接口只是调用动作的薄包装（`main.py` 的 `maintain`）。新增维护操作时先写动作和提交条件，再按需加兼容接口；不要在接口里另写校验。系统设置页的运营参数和投入参数暂未迁移，仍要对照两边的取值范围。
8. **显示名改了，测试和前端也要改。** 例如项目类型从「建设类」改为「系统实施」，涉及值类型、动作校验提示、前端下拉框、对象集预设标签和前端测试。改之前先全局搜索旧名称。
9. **函数新输出类型需要前端视图。** 在 `frontend/src/ontology/functionResults.tsx` 的分发表中加一个视图，否则只能显示通用键值列表。
10. **参数表的值都存成文本。** 本体把参数声明成 `integer` 时，加载器要转换类型（见 `rules.py` 的 `INTEGER_POLICY_FIELDS`），否则数据检查报 DAT-TYPE。
11. **有意独立的对象要声明。** 参数和计划这类对象不与其他对象关联，在说明里写「独立对象：原因」，lint 才不会报孤立对象。
13. **分析计算放进推导规则，不放进基础程序。** 基础程序（`project_repository.py` 等）只做导入、维护、写入校验和审计。新的派生属性写一条 `@RULES.derivation`（或扩展已有规则），代码放在 `ontology/derivations/`，在对象类型的 `derivations` 中登记，属性的 `derivedFrom` 指向它。页面需要的视图做成本体函数，并用 `_redact_payload` 按标记脱敏。
15. **数值属性必须声明量纲。** 新增 integer、decimal、money、percent 属性时写 `unit`，否则编译失败；money 配 `yuan`，percent 配 `percent`，差值用 `percentagePoint`，编号用 `none`。
14. **数据源的输出顺序要确定。** 原始数据来自集合时先排序，否则对象顺序每次不同。
16. **按月查看用统计期间，不要给属性加参数。** 对象类型写 `period`（汇总对象用 activeProperty，带日期的记录用 dateProperty，有计划起止的用 startProperty + endProperty；range 允许月份区间；defaultView 为 all 或 latest；options 只在 activeProperty 时必填），按月计算的属性标 `periodic: true`，数据源从 `ctx.period` 取月份（为空时用默认月份）；需要按月筛选的关系写 `periodProperty`，resolver 关系在解析器里按 `ctx.period` 过滤。按月计算的对象不进共享索引；对象集查询还不支持月份（7.2.0）。
17. **列表列用 listProperties 声明。** 对象列表默认取前四个属性；需要指定列（例如里程碑的状态）时在对象类型写 `listProperties`，不要为了显示而调整属性顺序。
18. **维护规则写在 `app/maintenance_rules.py`。** 新规则先在这里写判定函数（返回中文原因或 None），再由 `rules.py` 的提交条件和 `ProjectRepository` 的兜底（`rules.ensure(...)`）调用；状态锁定加进 `LOCKS` 表、提交条件用 `notLocked`。动作新增写入时同步 `edits` 与 `writeback`，否则 `test_actions_write_only_declared_datasets` 会失败；新增动作要在该测试中补一次执行。
12. **升主版本后，旧构建包要标注被取代。** 旧发布稿的 `ontologyVersion` 不再等于当前版本，给它加 `supersededBy: <新构建包文件名>`；设计稿实施后也这样处理。

## 当前规模（v7.5.0）

13 个对象类型、18 种关系、17 个动作、16 个函数、4 条推导规则、2 个安全标记。数字变化时同步更新本节、PRD 第 8 章和本体架构文档。
