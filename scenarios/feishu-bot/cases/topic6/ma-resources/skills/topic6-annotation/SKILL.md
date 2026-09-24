# topic6-annotation — 热点标注 Skill (MA 版 v1)

将客户端(Claude Code)版本的 `skill/annotation/` 迁移到火山方舟 Managed Agents。
Prompt 业务口径保留客户版活跃版本；工具链、脚本、路径全部按 MA 环境重写。

## 挂载路径

```
/mnt/skills/topic6-annotation/
├── prompts/          # C0/R1~R5/C3 v1 prompt (客户版活跃版本 fork)
│   ├── C0_基础事实/v1.md
│   ├── R1_平台借势/v1.md
│   ├── R2_商业合作/v1.md
│   ├── R3_风险预警/v1.md
│   ├── R4_创意借鉴/v1.md
│   ├── R5_消费者行为/v1.md
│   └── 节点标注/v1.md
├── scripts/          # 一体化脚本 (submit+poll+postprocess 合并)
│   ├── datahub_annotate.py    # 单任务全流程 (C0/R1~R5/C3)
│   ├── c0_merge_phase1.py     # C0 + C3 → 04_合并/phase1_merged
│   ├── c0_filter_usable.py    # 筛选营销可用子集
│   └── merge_annotations.py   # 七路 → 05_合并/wide_table 28 列宽表
├── ks/               # 判定口径 md (9 篇, 供子 Agent 检索)
├── references/
│   └── marketing_calendar/    # C3 时效窗口计算依赖
│       ├── nodes_definition.csv
│       ├── nodes_definition_supplement.csv
│       └── generate_marketing_calendar.py
└── tool/
    └── cost-tracker/cost_tracker.py    # 双轨成本台账
```

## 项目产出目录 (子 Agent 在 `/workspace/Projects/{project_dir}/` 下产出)

```
{project_dir}/
├── 02_标准化/hot_topics_normalized.xlsx    # (topic6-fetch-normalize 产出, 本 skill 读取)
├── 03_抽样/sample_500.xlsx                 # (topic6-fetch-normalize 产出, test 模式用)
├── 04_标注/
│   ├── C0_基础事实/
│   │   ├── c0_submit_meta.json         # datahub_annotate 上传+建任务后写
│   │   ├── c0_result_raw_r{N}.xlsx     # DataHub 下载结果
│   │   ├── c0_postprocess_r{N}.xlsx    # JSON 展开成列后
│   │   └── c0_completion_meta.json     # token/耗时/valid_rate/成本汇总
│   ├── R1_平台借势/..., R2_商业合作/..., R3_风险预警/...,
│   ├── R4_创意借鉴/..., R5_消费者行为/...,
│   ├── C3_节点标注/    # 内含 c3_result_raw_r{N}.xlsx 和展开后含"距节点天数/是否窗口期内"的 postprocess
│   └── _可用子集/usable_subset_{mode}_r{N}.xlsx  # c0_filter_usable 产出, 供 R1~R5 上传
├── 04_合并/phase1_merged_{mode}_r{N}.xlsx  # c0_merge_phase1 产出
├── 05_合并/
│   ├── wide_table_{mode}_r{N}.xlsx           # merge_annotations 产出 28 列宽表
│   └── health_summary_{mode}_r{N}.md         # HC 健康度摘要
└── costs/
    ├── cost_tracker.jsonl
    └── 本周成本汇总.md
```

## 编排顺序 (由 topic6-annotation 子 Agent 委派)

1. **第一批 (全量并行)**: C0 + C3 独立跑 `datahub_annotate.py` 各拿一次 `--task`
2. `c0_merge_phase1.py`: LEFT JOIN 到基础表 → phase1_merged
3. `c0_filter_usable.py`: 按"是否营销可用"筛出 R1~R5 输入子集
4. **第二批 (可用子集并行)**: R1~R5 各跑一次 `datahub_annotate.py --task rN --input 04_标注/_可用子集/...`
5. C2 事件归档 (由 topic6-event-registry skill 完成, 不在本 skill 内)
6. `merge_annotations.py`: 七路合并到 28 列宽表 + 健康度摘要

## Prompt 版本

所有 prompt 从 MA 环境重新计数为 **v1**, 内容对应客户版活跃版本 (C0 v4, R1 v2, R2 v1, R3 v1, R4 v2, R5 v2, 节点标注 v7)。
未来在 MA 环境的迭代直接从 v1→v2→v3, 与客户版脱钩。

## 环境变量

- `DATAHUB_API_KEY` — DataHub Bearer Token (必填, MA 部署时注入)

## 关键约束

- 所有 prompt / 脚本内的路径全部走 `/mnt/skills/topic6-annotation/` 或 `/workspace/Projects/`, 不再引用客户版 `topic6/skill/annotation/`。
- Prompt 上传给 DataHub 时通过 `--prompt-file` 显式传绝对路径, 不再靠脚本自动扫描 prompts 目录。
- 子 Agent 若遇到 c0_filter_usable 抛 `unresolved_ratio > 5%`, 应由协调器决定是否重新委派 datahub_annotate (`--task c0`) 补一轮, 而不是脚本内部越权放行。
