---
name: pipeline-orchestrator
description: Run the Topic5 durable report pipeline. Use for starting, resuming and inspecting Topic5 runs, human gates, quality checks and native Feishu delivery.
---

# pipeline-orchestrator

Topic5 复盘报告流水线的确定性执行引擎。读取包内 `contracts/pipeline.v0.2.yaml`
（29 节点 + 治理层），按节点 `handler` 驱动执行、在人工关卡暂停、支持续跑、受限换源与状态查看。
该文件由打包脚本从源码树唯一权威契约注入。归属 **Topic5 专属**，由 coordinator 身份持有。

## 文件

| 文件 | 职责 |
|---|---|
| `engine.py` | DAG 加载 + 节点调度 + 续跑（`Dag.load` / `create_run` / `run` / `resume`）；含幂等执行层（render→ledger 重放/对账） |
| `handlers.py` | 各业务节点 handler、质检返修及原生飞书交付 |
| `feishu.py` | N20 原生文档发布、表格扩容、图片回填及回读校验 |
| `excel_source.py` | 读取 Session 挂载的用户活动明细 `.xlsx`，映射为 n07 `clean_campaign` 行结构 |
| `state.py` | Run 状态持久化（`RunState`，落 run 目录 `attempts/artifacts/...`） |
| `run_pipeline.py` | CLI 入口 |
| `contracts/pipeline.v0.2.yaml` | 部署包内 DAG；打包时从 `ma-resources/io/contracts/` 注入 |
| `publish_spec.py` | 发布/飞书布局规格 |
| `acceptance.py` | 验收检查清单 |
| `tests/` | 引擎/handler/state/幂等 单测 |

## 用法

```bash
cd /mnt/skills/pipeline-orchestrator
# 启动一条 run
python run_pipeline.py start  --dag contracts/pipeline.v0.2.yaml \
  --workspace <ma-workspaces/projects/{pid}> --task <task_id> [--run <run_id>]
# 遇人工关卡暂停后续跑（默认用占位人工产物）
python run_pipeline.py resume --dag contracts/pipeline.v0.2.yaml \
  --workspace <ws> --task <task_id> --run <run_id>
# 仅 n04 的 excel_source 校验失败后，把修正版源写入新版 confirmed_plan
python run_pipeline.py replace-source --workspace <ws> \
  --task <task_id> --run <run_id> \
  --source-name <display.xlsx> --source-mount <mounted-basename.xlsx>
# 显式重试当前失败节点
python run_pipeline.py retry --dag contracts/pipeline.v0.2.yaml \
  --workspace <ws> --task <task_id> --run <run_id>
# 查看状态
python run_pipeline.py status --workspace <ws> --task <task_id> --run <run_id>
```

遇 `mode: human` 节点（G1/G2/G3/G4 及操作级 HC）会 `awaiting_human` 暂停并打印待办；`resume` 继续。
`replace-source` 不直接重试；它只在当前 Run 为 `failed`、当前失败节点为 `n04_run_init`
且失败详情包含 `excel_source:` 时更新源引用并生成新版 `confirmed_plan`。随后必须显式执行
`retry`。其他节点或其他失败原因禁止换源。

## 依赖

- Python 3.9+；`PyYAML`（解析 contract）、`openpyxl`（读取用户上传的活动明细 Excel）。
- 运行期事实写入 `--workspace` 指向的 `tasks/{task_id}/runs/{run_id}/`，**不写回本资源目录**。
- G1 只确认业务需求。G2 通过后，Gateway 等待用户上传 Excel，并把
  `source_file.mount_path` 写入 `confirmed_plan`；n07 从
  `/mnt/session/uploads/<mount_path>` 读取该文件。

## 幂等执行层（已实现）

`engine.py` 已消费节点的 `idempotency` 字段：`side_effect != none` 的节点执行前
`render_idempotency` 渲染幂等键，命中 `ledger` 则跳过执行、复用已记产物（`idempotent_replay`）；
执行成功后登记键→产物（`idempotent_commit`）；执行抛错时先查 `ctx["reconcile"]` 外部回执对账
（reconcile-before-retry），命中则视为成功推进。ledger/frontier/counters 随 `run.json` 持久化，
冷进程续跑（`RunState.load`）忠实恢复。覆盖见 `tests/test_idempotency.py`、`tests/test_crossprocess_resume.py`。

> 历史备注：早期 STATUS/SKILL 文档曾称“幂等未实现、接真前须先补执行层”——该说法已过时。
> 实际执行层随 engine 一并交付，曾出现的 7 个测试失败根因是 `tests/_util.py::make_state`
> 未 seed `frontier`（engine 靠 `while st.frontier` 驱动），已修复。

## N20 飞书发布约束与自动纠正

- 调用 `feishu.create_delivery` 发布原生文档；保持报告内容、表格数据和质检版本一致，
  不得为了绕过 API 限制删行、截断表格或退回 Markdown 表格。
- `POST /docx/v1/documents/{document_id}/blocks/{block_id}/children` 创建原生表格时，
  `row_size` 和 `column_size` **均不得超过 9**。这只是创建接口的限制，不是最终表格的尺寸上限。
  2026-10-27 实测 11×7 表格返回 HTTP 400 / `1770001 invalid param`；9×7 成功。
- 发布器自动将初始表格压到至多 9×9，再用 `PATCH blocks/{table_block_id}` 的
  `insert_table_column.column_index=-1` / `insert_table_row.row_index=-1` 补足列和行。
  每次扩容携带稳定 `client_token`；扩容前后读取实际尺寸，核对单元格数再写入全部数据。
  此适配由确定性代码执行，Coordinator 不临时重写报告。
- 若发布失败，保留接口路径、HTTP 状态、飞书 `code/msg/log_id`；不得输出 token、请求头
  或凭据正文。参数错误应先诊断并修正，禁止用相同非法请求反复重试或从 G1 重启。
- 排查时读到的旧 `start.log` / `HC_PENDING` 只代表历史，不得当作当前关卡回报；
  当前 `run.json` 终态与失败回执优先。不得把 N20 发布失败改成人工需求审核。
- N10 确认必须绑定 `opinion_candidates` 与 `ugc_category_list` 版本。Gateway 保存确认
  快照并在后续卡片中持续展示；不要重新生成已确认的标签。

接口依据：
[创建块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create.md)、
[更新块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/patch.md)。
