# Test fixtures

This directory stores test-only input data. Fixtures are used by automated tests and are not imported by the application at runtime.

## `time_entries_202601_202602.json`

Source workbook: `202601-202602.xlsx`

Purpose: validate the time-entry import API with real-shaped January and February 2026 timesheet data without hardcoding records in application code.

Normalization used for the test fixture:

| Source column | Test API field |
|---|---|
| `项目代码` | `project_number` |
| `姓名` | `employee_name` |
| `部门` | `department` |
| `日期` | `work_date`, `source_updated_at` date |
| `天数` | `hours = 天数 * 8` |
| `流程状态=流转完成` | `approval_status=approved`, `approved_hours=hours` |
| `流程状态=进行中` | `approval_status=submitted`, `approved_hours=0` |
| `工作内容` | `work_content` |
| `case` | `source_case_id` |

The fixture intentionally keeps `source_status`, `department`, and `source_case_id` as source-trace fields. The current `/api/v1/time-entries/import` endpoint ignores unsupported extra fields, which lets the test preserve source context while validating the existing normalized contract.
