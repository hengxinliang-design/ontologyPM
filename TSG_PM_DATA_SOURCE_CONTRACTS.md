# TSG PM 数据源契约

## CRM 快照输入

当前兼容中文快照字段和 REST 英文字段。推荐每条记录提供不可展示的 `crm_internal_id`，服务端据此生成稳定 `record_id`。

CRM 同步输出仍保留合同记录级数据；项目视图按 `project_number` 聚合。

## CRM 增量同步

- 数据库为空时自动执行首次全量同步。
- 首次成功后，`sync_mode=auto`（默认）按增量方式合并，只有发生变化的 `record_id` 会更新。
- 数据源可显式发送 `sync_mode=incremental` 或 `delta`。
- 删除记录必须通过 `deleted_record_ids[]` 明确声明；未出现在本次增量中的旧记录继续保留。
- 若需要权威全量重建，显式发送 `sync_mode=full`。
- 每次同步仍生成一个完整、只读的物化快照，校验成功后才切换当前版本；失败保留上次成功快照。
- `project_settings`、`milestones`、`payment_nodes` 和确认记录存放在独立人工维护表中，CRM 同步不会更新或删除这些字段。

增量请求示例：

```json
{
  "sync_mode": "incremental",
  "updated_at": "2026-07-03T03:00:00-04:00",
  "records": [{ "record_id": "rec_xxx", "data_type": "contract_order" }],
  "deleted_record_ids": ["rec_deleted"]
}
```

## 工时快照输入（暂定）

```json
{
  "schema_version": "0.1",
  "updated_at": "2026-07-02T10:00:00-04:00",
  "entries": [
    {
      "external_id": "TIME-20260702-0001",
      "project_number": "TPL149",
      "employee_id": "E001",
      "employee_name": "Zhang San",
      "work_date": "2026-07-02",
      "hours": "8.00",
      "approved_hours": "8.00",
      "approval_status": "approved",
      "work_content": "完成 SRM 接口联调",
      "source_updated_at": "2026-07-02T18:00:00+08:00"
    }
  ]
}
```

约束：

- `external_id` 必须全局唯一。
- `project_number` 必须符合项目编号规则；无法匹配时进入异常清单。
- 小时使用十进制字符串，范围为 0 至 24。
- `approved_hours` 仅在审批通过时计入实际成本。
- `hours` 表示员工已实际填报、已经发生的成本工时。
- `approved_hours` 表示客户已确认、可结算或已认可的工时。
- `hours - approved_hours` 为客户确认缺口；系统按项目聚合并产生醒目提醒。
- `approval_status` 暂定：`draft`、`submitted`、`approved`、`rejected`。
- 更新采用 upsert；同一 `external_id` 的新版本覆盖旧版本。

## 适配器边界

```text
CrmSnapshotSource.fetch() -> CRM snapshot
TimeEntrySource.fetch(since) -> time-entry snapshot
```

实际工时 API 确认后，只新增 `TimeEntrySource` 实现，不修改项目领域模型、SQLite 表和前端 API。
