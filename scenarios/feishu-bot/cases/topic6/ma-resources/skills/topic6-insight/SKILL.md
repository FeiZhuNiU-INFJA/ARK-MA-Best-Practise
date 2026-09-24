---
name: topic6-insight
version: 1.0.0
description: 社媒热点周刊 Phase E+F 洞察生成：从 Phase C 交付的标注宽表出发，四路并发（E1 平台借势 / E2 商业合作 / E3 风险预警 / E4 创意借鉴）调用 Ark OpenAI 兼容接口生成周报四大版块，再由 pipeline_f 拼接成完整草稿。当用户需要基于标注结果生成周报正文洞察时使用。
---

# topic6-insight

> MA 版洞察生成技能 — 从标注宽表出发,四路并发生成周报四大版块。
> Fork自 topic6 原 `skill/insight/`,重写为 MA 沙箱口径。

## 目录结构

```
topic6-insight/
├── SKILL.md                    本文件
├── scripts/
│   ├── pipeline_e.py           入口: 数据预处理 + 4 路并发 LLM 生成洞察
│   └── pipeline_f.py           入口: 拼接 4 个版块为完整周报
├── 01_统计/                    数据预处理模块(pipeline_e.py 会调用 run_stats.py)
│   ├── run_stats.py            编排入口: 宽表 → 4 版块数据
│   ├── _common.py              分数/链接/pipe 展开等工具函数
│   ├── e1_industry.py          E1 行业及热门话题
│   ├── e2_nodes.py             E2 营销节点(依赖 marketing_calendar.md)
│   ├── e3_platforms.py         E3 平台新鲜事
│   └── e4_marketing.py         E4 营销发现(舆情风险/合作动态/营销观察/消费洞察)
├── 02_洞察/                    Prompt 目录
│   ├── _shared/role_style.md   共享角色/风格,拼在每个版块 prompt 前
│   ├── E1_行业话题/v1.md
│   ├── E2_营销节点/v1.md
│   ├── E3_平台新鲜事/v1.md
│   └── E4_营销发现/
│       ├── v1.md               撰写 prompt
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
  --publish-date 2026-08-31 \
  --model ep-your-endpoint-id
```

关键参数:
- `--project-dir`: 项目目录(绝对路径,或相对 `/workspace`)
- `--publish-date`: 报告发布日,影响 E2 节点窗口判断
- `--model`: 火山方舟 endpoint id
- `--version N`: 强制指定洞察版本(默认自增)
- `--sections e1,e2`: 只跑指定版块
- `--skip-data-prep`: 跳过统计脚本,从上一轮复制数据

产出目录 `{project_dir}/06_洞察/v{N}/`:
- `e1_v{N}.md` ~ `e4_v{N}.md`
- `e1_data.md` / `e3_data.md` / `e4_data.md`
- `e2_flags.json` / `e4_candidates.json` / `e4_tagging_audit.md` / `e3_word_freq_audit.md`
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
  - `MARKETING_CALENDAR_PATH`(可选,默认 `/mnt/skills/topic6-annotation/references/marketing_calendar/marketing_calendar.md`)
- 上游数据: `{project_dir}/05_合并/wide_table_full_r{N}.xlsx`(来自 topic6-annotation)
- 跨 skill 依赖: cost-tracker 调用 `/mnt/skills/topic6-annotation/tool/cost-tracker/cost_tracker.py`

## MA 适配要点(相对客户原版)

1. 删掉 `PROJECT_ROOT = SCRIPT_DIR.parents[N]` 的目录上溯逻辑,项目路径改从 `--project-dir` 传入。
2. LLM SDK 从 `anthropic.AsyncAnthropic` 换成 `openai.AsyncOpenAI`,走火山方舟 OpenAI 兼容 endpoint。
3. `cost_tracker.py` 从 topic6-annotation skill 挂载路径调用,两个 skill 共用一份账本。
4. E2 `MARKETING_CALENDAR_PATH` 默认指向 topic6-annotation 的日历文件,不再单独 fork 一份。

## 前置约束

- 只支持 `--mode full`,test 模式(500 条抽样)直接拒绝生成洞察。
- 同一项目按 v1、v2... 迭代,每轮独立子目录,不覆盖历史。
- 上游 `05_合并/wide_table_full_r{N}.xlsx` 必须存在,来源 topic6-annotation。
