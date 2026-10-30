# 版本管理

远端仓库：`https://github.com/hengxinliang-design/ontologyPM.git`（远端名 `origin`）。原仓库 `hengxinliang-design/TSG-MVP` 保留为远端 `tsg-mvp`，只读参考，不再推送。

## 两个版本号

| 版本 | 位置 | 规则 |
| --- | --- | --- |
| 产品版本 | PRD「文档版本」、[CHANGELOG](../CHANGELOG.md)、git 标签 `vX.Y.Z` | 每次对用户可见的发布升一次。新增或改变功能升次版本（v1.26.0 → v1.27.0）；只修缺陷、改说明升修订号（v1.26.0 → v1.26.1）；改变业务口径并要求用户重新理解数据时升主版本 |
| 本体版本 | `backend/app/ontology/metadata/ontology.yaml` 的 `version` | 按技能 tsg-ontology 原则 8：破坏性变更（删除或改名属性、改类型、改关系方向）升主版本，新增升次版本，只改说明升修订号；同一版本号的内容不能变 |

产品版本可以不改本体（例如 v1.25.0 仍是本体 7.8.0）；本体每次升版本都对应一个产品版本。

## 分支

- `main` 是发布分支，始终可运行、测试全部通过；不强推，不改写已推送的历史。
- 一批需求在独立分支上做：`feature/<主题>`（例如 `feature/g9-xxx`），完成并验证后快进合并到 `main`；需要评审时开 PR。
- 已发布的标签不移动、不删除；发布后发现问题，修复后发新的修订版本。

## 每次发布的步骤

1. 需求先进需求基线（技能工作流 F），本体改动交付构建包、测试与诊断（技能「交付物」）。
2. 后端 `.venv/bin/pytest -q`、前端 `npx vitest run` 与 `npm run build`、本体 lint 与诊断全部通过。
3. PRD 修订记录加一行并更新「文档版本」；CHANGELOG 在表格顶部和正文各加一条（产品版本、本体版本、日期、构建包、要点）。
4. 提交说明首行写 `本体 X.Y.Z：主题（批次）`，不改本体时写 `主题（产品 vX.Y.Z）`。
5. 打附注标签并推送：

   ```bash
   git tag -a v1.27.0 -m "v1.27.0：主题（本体 X.Y.Z）"
   git push origin main --follow-tags
   ```

6. 确认远端：`git ls-remote --tags origin` 能看到新标签，`main` 与本地一致。

## 不进入仓库的内容

`.env`、数据库文件（`*.sqlite3`）、诊断报告目录、依赖与构建产物都在 `.gitignore` 中；密钥只放在环境变量里。推送新的远端或公开仓库前，先检查提交历史中没有密钥。
