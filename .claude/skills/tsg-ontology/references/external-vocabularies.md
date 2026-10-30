# 外部语义词汇复用规范

本次为 Skill 指引扩展（指引版本 1.0.0，2026-10-30），不是运行时本体发布。能力问题：哪些通用语义可复用？如何在不改变业务含义的前提下交换？仅按能力问题选择词汇，不全量导入，不引入 RDF/OWL 运行时要求。

## 权威、命名空间与来源

| 前缀 | 精确命名空间 | 用途与固定来源版本 |
| --- | --- | --- |
| 内部 | 沿用企业现有标识；无 URI 时先申请注册，禁止虚构生产 URI | 对象、属性、关系、动作、规则、权限均以内部定义为准 |
| schema | https://schema.org/ | 通用实体；30.1，2026-09-16；https://schema.org/docs/releases.html |
| foaf | http://xmlns.com/foaf/0.1/ | 人员组织；0.99，2014-01-14；https://xmlns.com/foaf/spec/20140114.html |
| dcterms | http://purl.org/dc/terms/ | 元数据；2020-01-20；https://www.dublincore.org/specifications/dublin-core/dcmi-terms/2020-01-20/ |
| dc | http://purl.org/dc/elements/1.1/ | 仅兼容旧系统；不得与 dcterms 自动互换 |

来源核对日为 2026-10-30：三项版本与日期已对照上表官方页面核对一致（Schema.org 发布页列出 30.1 / 2026-09-16；FOAF 规范页为 0.99 / 2014-01-14；DCMI Terms 为 2020-01-20 版 Recommendation）。FOAF 文档使用 HTTPS 不意味着命名空间变成 HTTPS；前缀不是身份，完整 URI 才是身份。Schema.org 的 HTTP 历史别名须由明确的输入规范化规则处理。禁止运行时跟随 latest 自动升级。每次升级记录快照文件与 SHA-256、检索时间、词条状态和变更差异，重新验收后启用；上表是文档版本依据，没有保存词汇快照；离线校验用登记文件中的词条白名单（含类别），新增词条须先对照官方页面核对。

## 映射登记与候选

每条登记必须包含 mappingId、内部模型版本、内部元素路径、外部完整 URI、类型/属性类别、方向、关系强度（参考/较宽/精确）、转换、适用条件、信息损失、来源 URL、词汇版本、登记人、审核人、状态（candidate/approved/rejected）、生效和失效时间。只有 approved 且匹配当前内部版本的记录才可由适配器使用。映射登记独立于严格的运行时元数据模型；需要落地时先按原 Skill 的需求基线、构建包和诊断流程扩展契约。

映射登记文件是 [vocabulary-mappings.yaml](vocabulary-mappings.yaml)：固定的词汇版本、词条白名单、可映射的工件字段和全部映射。内部元素写法：对象类型 `Employee`、属性 `Employee.employeeName`、关系 `Project@team`、构建包字段 `artifact:OntologyBuild.date`。当前登记（本体 7.7.0，均为候选或已拒绝，没有已批准项）：

| 映射 | 内部元素（已核对存在） | 外部词条 | 方向与条件 |
| --- | --- | --- | --- |
| tsg-employee-person | Employee | foaf:Person（较宽） | 只导出编号与姓名；Person 不反向生成员工或账号 |
| tsg-employee-name | Employee.employeeName | foaf:name | 文本，不据同名合并身份 |
| tsg-customer-organization | Customer | schema:Organization（较宽） | 客户以 CRM 名称为主键，不做外部实体链接；FINANCE 汇总不导出 |
| tsg-customer-name | Customer.customerName | schema:name | 导出文本 |
| tsg-project-team-member | Project@team | foaf:member（参考） | member 的主体是 Group：导出时生成「项目团队」Group，方向 团队 → member → 员工；项目本身不是 Group |
| tsg-build-title / issued / replaced-by | 构建包 topic / date / supersededBy | dcterms:title / issued / isReplacedBy | 日期不补造时间；supersededBy 先解析为资源标识，不输出文件名字符串 |
| tsg-contract-order（拒绝） | Contract | schema:Order | 合同是含税、多期回款的框架协议，Order 是一次交易确认，含义不同 |
| tsg-build-version（拒绝） | 构建包 ontologyVersion | dcterms:hasVersion | hasVersion 指向版本资源，内部值是版本号字符串 |

项目、里程碑、回款约定、经营规则没有精确对应时保留内部定义。schema:Action 不替代内部动作契约；foaf:Agent 不等于 AI Agent 服务。

## 冲突处理

内部定义优先。同名但类型、量纲、基数、方向或业务含义不同，拒绝精确映射，记录冲突并转为候选；信息有损只允许明确标注的单向导出。禁止自动声明 owl:equivalentClass、owl:equivalentProperty 或 owl:sameAs。未知词条和已废弃词条默认不启用。

foaf:knows 不代表上下级、雇佣或授权；邮箱和账号不作为自动合并身份依据（尤其不因 FOAF 逆函数属性触发身份合并）。dcterms:accessRights、rights、license 是描述，不能授予权限。导出先执行内部字段及行级授权，再做映射；隐藏值不得变为空值或公开来源链接。

## 校验与验收

自动校验：在 backend 目录运行 `.venv/bin/python ../.claude/skills/tsg-ontology/scripts/check_vocabulary_mappings.py`，测试 `backend/tests/test_vocabulary_mappings.py` 覆盖每条规则的反例。脚本检查：必填字段与编号唯一；前缀属于已固定版本的词汇，词条在白名单中且类别匹配（type 对类，property / link 对属性）；不允许 owl:sameAs、owl:equivalentClass、owl:equivalentProperty；内部对象、属性、关系、工件字段真实存在且类别正确；带 FINANCE / PERSONNEL 标记的属性必须声明 `respectsMarking`；已拒绝须写原因，已批准须有审核人、审核日期且版本等于当前本体版本；非拒绝项须写适用条件。

人工评审仍分三层：语法（登记字段、URI、版本及状态）→ 一致性（内部路径真实、外部词条存在且类别正确、方向/类型/基数/单位兼容）→ 结构与业务（无循环版本关系、身份误合并、权限扩大及不可逆往返）。Schema.org 的 domainIncludes/rangeIncludes 是用法指引，不能直接转换成封闭世界必填约束。RDFS domain/range 可能带来类型推断；业务必填和基数仍由内部校验决定。实际 RDF 交换可另配 SHACL；JSON/YAML 仍走原有编译器和 Pydantic，不能声称两者等价。

每次批准映射必须有正例、反例、缺失输入和权限用例；涉及运行时变更必须运行原 Skill 的诊断流程及相关回归，并登记真实验证结果。映射版本独立管理；破坏方向、转换或含义升主版本，新增映射升次版本，说明修正升补丁版本，词汇升级不自动改变内部版本。

## 示例与规则卡

映射示例即上面的登记文件（含两条冲突拒绝的反例）。

规则卡 R-EXT-1：内部权威、显式批准才可映射。依据：本次用户要求及 Skill 核心原则 1、6、9。验收：candidate 映射不执行；approved 但内部版本不匹配也不执行；有效批准记录只执行声明方向。

规则卡 R-EXT-2：导出不扩权、不推断身份。依据：内部权限模型和上述 FOAF、DCMI 词条定义。验收：无权字段从输出移除；同名两人仍为两个内部 ID；rights 文本不改变访问结果。

| 规则 | 例外情形 | 处理 | 依据 |
| --- | --- | --- | --- |
| R-EXT-1 | 无对应词条或输入缺失 | 保留内部语义，登记 unmapped/unknown，不伪造值 | 内部权威与部分知识原则 |
| R-EXT-1 | 文本作者无法解析为 Agent | 拒绝直接映射 dcterms:creator，提交转换候选 | DCMI 值域 |
| R-EXT-1 | 上游不可访问或版本变更 | 使用已批准固定快照；无快照则暂停新映射 | 来源可追溯 |
| R-EXT-2 | 有文档访问权但无创建者个人信息权限 | 删除受限字段；不能透过外部 URI 泄漏身份 | 内部权限优先 |
| R-EXT-2 | 缺少成员信息 | 记未知，不能推断团队为空或无权限 | 缺失不等于否 |

登记与校验已实现并有测试；导出适配器尚未实现，R-EXT-1、R-EXT-2 中关于导出行为的验收是适配器上线时的标准，现在不声称已实现。
