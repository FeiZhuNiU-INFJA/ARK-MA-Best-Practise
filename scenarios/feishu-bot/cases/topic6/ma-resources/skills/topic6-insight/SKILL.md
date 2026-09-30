---
name: topic6-insight
version: 1.0.0
description: 社媒热点周刊 Phase E+F 洞察生成：从 Phase C 交付的标注宽表出发，流式有限并发生成 E1~E4 四个可断点恢复的周报版块，再由 pipeline_f 拼接成完整草稿。当用户需要基于标注结果生成周报正文洞察时使用。
---

# topic6-insight

> MA 版洞察生成技能 — 从标注宽表出发,流式有限并发生成周报四大版块。
> Fork自 topic6 原 `skill/insight/`,重写为 MA 沙箱口径。

## 目录结构

```
topic6-insight/
├── SKILL.md                    本文件
├── scripts/
│   ├── pipeline_e.py           入口: 数据预处理 + 流式有限并发生成洞察
│   └── pipeline_f.py           入口: 拼接 4 个版块为完整周报
├── 01_统计/                    数据预处理模块(pipeline_e.py 会调用 run_stats.py)
│   ├── run_stats.py            编排入口: 宽表 → 4 版块数据
│   ├── _common.py              分数/链接/pipe 展开等工具函数
│   ├── e1_industry.py          E1 行业及热门话题
│   ├── e2_nodes.py             E2 营销节点(依赖共享 marketing_calendar_2026.csv)
│   ├── e3_platforms.py         E3 平台新鲜事
│   └── e4_marketing.py         E4 营销发现(舆情风险/合作动态/营销观察/消费洞察)
├── 02_洞察/                    Prompt 目录
│   ├── _shared/role_style.md   共享角色/风格,拼在每个版块 prompt 前
│   ├── E1_行业话题/v1.md
│   ├── E2_营销节点/v3.md
│   ├── E3_平台新鲜事/v1.md
│   └── E4_营销发现/
│       ├── v6.md               撰写 prompt
│       └── _tagging/v1.md      打标 prompt(舆情风险可接性/合作动态参考价值)
└── references/
    ├── E3_统计口径说明.md
    └── 综合热度指数算法说明.md
```

## 入口调用

### 步骤 1: 生成 4 版块洞察

```bash
python /mnt/skills/topic6-insight/scripts/pipeline_e.py \
  --project-dir /workspace/Projects/W35_20260824-20260830 \
  --mode skip_sampling \
  --publish-date 2026-08-31 \
  --model ep-your-endpoint-id
```

该入口一次完成数据预处理，并在进程内以默认最大并发 2 流式调用 E1~E4。每次调用
失败最多尝试 5 次（429、5xx、断连等可恢复错误使用指数退避和全新客户端），每个成功
版块立即原子写入 Markdown 和 checkpoint。相同参数重跑时会复用成功版块，只补失败
版块。正常整轮不得拆成四次 `--sections eN` 调用；`--sections` 只用于审核打回后的
单版块强制重跑。

关键参数:
- `--project-dir`: 项目目录(绝对路径,或相对 `/workspace`)
- `--publish-date`: 报告发布日,影响 E2 节点窗口判断
- `--model`: 火山方舟 endpoint id
- `--version N`: 强制指定洞察版本(默认自增)
- `--sections e1,e2`: 只跑指定版块
- `--skip-data-prep`: 跳过统计脚本,从上一轮复制数据
- `--max-workers`: LLM 最大并发数,默认 2
- `--max-attempts`: 每次版块调用最大尝试次数,默认 5

产出目录 `{project_dir}/06_洞察/v{N}/`:
- `e1_v{N}.md` ~ `e4_v{N}.md`
- `e1_data.md` / `e3_data.md` / `e4_data.md`
- `e2_flags.json` / `e4_candidates.json` / `e4_tagging_audit.md` / `e3_word_freq_audit.md`
- `pipeline_e_checkpoint_v{N}.json`
- `pipeline_e_report_v{N}.json`

### 步骤 2: 拼接完整周报

```bash
python /mnt/skills/topic6-insight/scripts/pipeline_f.py \
  --project-dir /workspace/Projects/W35_20260824-20260830
```

产出: `{project_dir}/07_报告/热点报告_{period_label}_v{N}.md`

## 依赖

- 环境变量:
  - `ARK_API_KEY`(fallback `OPENAI_API_KEY`)
  - `ARK_BASE_URL`(fallback `OPENAI_BASE_URL`) — MA 环境请指向火山方舟 endpoint
  - `MARKETING_CALENDAR_PATH`(可选,默认 `/mnt/skills/topic6-fetch-normalize/references/marketing_calendar_2026.csv`)
- 上游数据:正式模式使用 `{project_dir}/05_合并/wide_table_skip_sampling_r{N}.xlsx`,演示模式使用 `wide_table_demo_r{N}.xlsx`
- 跨 skill 依赖: cost-tracker 调用 `/mnt/skills/topic6-annotation/tool/cost-tracker/cost_tracker.py`

## 架构要点

1. 项目路径从 `--project-dir` 传入,不做目录上溯推导。
2. LLM SDK 走 OpenAI 兼容 endpoint(`openai.AsyncOpenAI`)，使用流式响应、有限并发、
   指数退避重试和版块级原子 checkpoint。
3. `cost_tracker.py` 从 topic6-annotation skill 挂载路径调用,两个 skill 共用一份账本。
4. E2 `MARKETING_CALENDAR_PATH` 默认指向 topic6-fetch-normalize 的共享 CSV 日历，也兼容旧 Markdown 表格。

## 前置约束

- 支持 `--mode skip_sampling` 正式全量交付和 `--mode demo` 50 条样本演示。
- `--mode full` 的 500 条结果仅用于标注校准,直接拒绝生成洞察。
- demo 产出的所有报告必须注明“基于 50 条分层样本,仅供流程演示,不可作为正式全量结论”。
- 同一项目按 v1、v2... 迭代,每轮独立子目录,不覆盖历史。
- 对应模式的上游宽表 `05_合并/wide_table_{skip_sampling|demo}_r{N}.xlsx` 必须存在,来源 topic6-annotation。
