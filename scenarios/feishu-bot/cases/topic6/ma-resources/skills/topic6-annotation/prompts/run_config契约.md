# prompt/run_config 契约

> 迁移首轮：抽取自 `Projects/_项目模板/run_config.yaml` 的注释。
> **模板文件本身保留在原地**（新建项目仍复制它），本文件只把「状态机定义」这部分编排知识提出来。
>
> ✅ 2026-09-21：模板的 `c3_node` 过期预筛说明、`d_test`/`d_full` 输出路径注释、
> `current_phase` 值域与版本字段均已修正，与本文件一致。

---

## `{PROJECT_DIR}` 格式定义

**全项目只在此处定义一次。** 其余文件一律写占位符 `{PROJECT_DIR}`，不展开具体格式。

`{PROJECT_DIR}` = **项目目录名**，不含 `Projects/` 前缀。路径写法为 `Projects/{PROJECT_DIR}/...`；
脚本的 `--project-dir` 参数接收的是**含前缀**的值（如 `"Projects/W38热点周报_20260914-20260920"`）。

| `period_type` | 目录命名 | 示例 |
|---|---|---|
| `weekly`（标准周） | `W{N}热点周报_{YYYYMMDD}-{YYYYMMDD}` | `W38热点周报_20260914-20260920` |
| `biweekly`（双周） | `W{起始周}双周报_{YYYYMMDD}-{YYYYMMDD}` | `W36双周报_20260831-20260913` |
| `custom`（自定义） | `W{起始周}自定义_{YYYYMMDD}-{YYYYMMDD}` | `W36自定义_20260901-20260914` |

`W{N}` 一律取**起始日所在的 ISO 周次**。

### ⚠️ 为什么 custom 不用 `custom_` 开头

迁移前的 `agent/skill/SKILL.md:35` 定义的是 `custom_{YYYYMMDD}-{YYYYMMDD}`（如 `custom_20260901-20260914`）。
**该规则已弃用**，原因是它与下游的目录名解析不兼容：

`skill/insight/pipeline_f.py:74` 用正则 `(W\d+)[^\d]*(\d{8})-(\d{8})` 从目录名兜底解析周期信息，
**要求目录名以 `W{数字}` 开头**。实测：

```
W38热点周报_20260914-20260920   → ✓ 解析出 ('W38','20260914','20260920')
custom_20260901-20260914       → ✗ 不匹配
W36自定义_20260901-20260914      → ✓ 解析出 ('W36','20260901','20260914')
```

所以三种周期统一以 `W{起始周}` 开头，兜底解析对全部周期类型都有效。
（2026-09-21 决策。若要改回 `custom_` 风格，须同时改 `pipeline_f.py` 的正则。）

### 目录名解析只是第三优先级

`pipeline_f.py::resolve_period_info()` 的取值顺序是：
**① CLI 显式传参 → ② `run_config.yaml` 的 `period` 块 → ③ 目录名解析**，三者都取不到才报错退出。

因此 `period` 块（`period_label` / `date_start` / `date_end`）是 `[MUST_UPDATE]` 必填项 ——
**填全了就不会走到目录名解析**。目录命名规范主要是给人看的，兜底解析是保险，不是主路径。

---

## current_phase 值域与迁移顺序

**全部小写**。合法值按顺序：

```
init → ab → sample → c0_sample → c0_filter_sample → c_route_sample → d_merge_sample → hc1_wait
     → c0_full   → c0_filter_full   → c_route_full   → d_merge_full   → hc2_wait
     → e → f → hc3_wait → g_ui_publish → h_miaoda_publish → done
```

| 值 | 对应文件 | 说明 |
|---|---|---|
| `init` | — | 项目目录刚建，run_config 已写入 period |
| `ab` | `prompt/02` | 取数 + 清洗 + 标准化（一条命令完成） |
| `sample` | `prompt/03` | 分层抽样 500 条，**仅 test 模式经过** |
| `c0_sample` / `c0_full` | `prompt/04` | 第一批：C0 + C3 并发 |
| `c0_filter_sample` / `c0_filter_full` | `prompt/04` | 筛选「营销可用」子集（纯脚本零成本） |
| `c_route_sample` / `c_route_full` | `prompt/04` | 第二批：R1~R5 + C2 并发，只吃子集 |
| `d_merge_sample` / `d_merge_full` | `prompt/05` | 七路 LEFT JOIN 成宽表 |
| `hc1_wait` / `hc2_wait` / `hc3_wait` | `prompt/04` / `05` / `07` | 三个人工关卡，等用户明确回复 |
| `e` | `prompt/06` | 四路并发洞察 |
| `f` | `prompt/07` | 合并 + 自校验 + 飞书发布 |
| `g_ui_publish` | `prompt/08` | 网页版发布（产出自包含 index.html） |
| `h_miaoda_publish` | `prompt/09` | 妙搭公网发布（`miaoda-web-publish` skill，固定关卡，非可选） |
| `done` | — | 流程结束 |

> **2026-09-20 三处修正**：
> ① 补入 `sample` 一环 —— 原值域没有抽样，而 test 模式必经此步（`prompt/03`）；
> ② 补入 `d_merge_sample` / `d_merge_full` —— 原值域里 Phase D 没有名字，D 跑完到 HC1 之间状态无定义；
> ③ **全部改小写** —— 原定义写 `HC1_wait` 大写，但 `prompt/04,05,07` 共 5 处实际写入都是小写，
> 大小写不一致会让字符串比较全部落空。统一为小写，与 `hc1_passed` 等字段风格一致。
>
> **2026-09-22 补入 `h_miaoda_publish`** —— G 产出的 index.html 此前止步于本地文件，
> 用户明确要求把「发到妙搭拿公网链接」纳入固定流程（不是按需触发的额外动作）。
> `done` 现在在 H 完成后才达到，不再在 G 完成后就置位。

架构说明：C0 与 C3 并行跑全量/全样本；C0 完成后触发 `c0_filter` 筛选「营销可用」子集；
R1~R5 与 C2 只处理这个子集（不是全量），C3 不受此筛选影响，独立并行。

---

## 顶层结构

| 块 | 用途 | 谁维护 |
|---|---|---|
| `period` | 周期配置（period_id / label / 起止日 / 类型） | 人工填 `[MUST_UPDATE]` |
| `fetch` | 取数参数（比周期多取 7+1 天以确保能找到完整 ISO 周） | 人工填 |
| `annotation` | 八个任务的活跃 Prompt 版本 | 人工填，权威在 `lm/_版本状态.md` |
| `cost` | 成本换算汇率 | 人工填 |
| `status` | **全部运行状态，Agent 自动维护，禁止手工编辑** | Agent |

## status 子块清单

| 子块 | 对应阶段 | 关键字段 |
|---|---|---|
| `ab` | prompt/02 | completed / week_num / iso_label / raw_rows / clean_rows |
| `sample` | prompt/03 | completed / sample_file / sample_rows |
| `c0_base` `c3_node` | prompt/04 第一批 | status / datahub_task_id / prompt_version / row_count |
| `c0_merge` `c0_filter` | prompt/04 筛选 | status / run_id / usable_count / parse_error_included / missing_included |
| `c0_retry` | prompt/04 熔断后 | triggered / attempt / max_attempts / still_unresolved / force_accept_used |
| `r1_platform`…`r5_consumer` `c2_cluster` | prompt/04 第二批 | 同 c0_base，`input_file` = `c0_filter.subset_file` |
| `d_test` `d_full` | prompt/05 | completed / output_file |
| `hc1_passed` `hc2_passed` `hc3_passed` | 三个人工关卡 | 布尔 + 时间戳 |
| `e_insights` | prompt/06 | status / round_version / e1_file~e4_file / report_file |
| `f` | prompt/07 | completed / merged_report_file / feishu_doc_url / published_at |
| `g` | prompt/08 | completed / output_dir / notified_at / completed_at |
| `h` | prompt/09 | completed / app_id / release_id / online_url / published_at |

## 两条硬约束

1. **同一轮 pipeline 全程共用一个 `run_id`** —— 七路标注 + 筛选 + 合并都传同一个值，
   否则会合并到不同轮次的数据。不要依赖各脚本各自「自动扫描」。
2. **`status` 块禁止手工编辑** —— 断点恢复完全依赖它，手改会让恢复点判断错位。

---

## 断点恢复的判断入口

读 `status.current_phase` 定位阶段；阶段内部的细粒度恢复点（如「c0_base 已 submitted 但未 poll」）
见 `skill/annotation/SKILL.md`「七、断点恢复」的状态对照表。
