# Topic 6 · MA 迁移资源说明

本目录保存 Topic 6「社媒热点周刊」迁移到火山方舟 Managed Agents（MA）后的资源定义、Skill 源码和配套发布工具。

- `ma-resources/`：MA 运行所需的 Agent、Environment、Memory Store 和 Skill 源码，是部署资源的唯一源码目录。
- `tools/`：在本地打包并上传自定义 Skill 的辅助工具。
- `topic6_ma_architecture.html`：MA 分层架构、Agent 组成及交互关系。
- `topic6_pipeline_overview.html`：从取数、标注、审核到报告发布的完整流水线。

## 目录结构

```text
topic6/
├── README.md
├── topic6_ma_architecture.html
├── topic6_pipeline_overview.html
├── ma-resources/
│   ├── agents/
│   │   ├── coordinator.json
│   │   ├── coordinator.system.md
│   │   ├── annotator.json
│   │   ├── annotator.system.md
│   │   ├── insighter.json
│   │   └── insighter.system.md
│   ├── memory/
│   │   └── topic6/
│   │       ├── MEMORY.md
│   │       ├── _版本状态.md
│   │       └── 错误案例库.md
│   ├── skills/
│   │   ├── topic6-fetch-normalize/
│   │   ├── topic6-annotation/
│   │   ├── topic6-event-registry/
│   │   ├── topic6-insight/
│   │   └── topic6-web-report/
│   ├── environment.json
│   ├── memory-store.json
│   ├── skill_ids.json
│   ├── version_mapping.md
│   └── create_all.sh
└── tools/
    ├── pack_skills.sh
    ├── upload_skills.py
    └── out/                      # 本地生成，不入库
```

## `ma-resources/`

### Agent 定义

Topic 6 采用 `1 Coordinator + 2 类子 Agent` 的结构。

| 文件 | 作用 |
|---|---|
| `agents/coordinator.json` | 主协调器资源定义。挂载 5 个自定义 Skill、热点 MCP，并注册 annotator、insighter 和自身作为可调度 Agent。 |
| `agents/coordinator.system.md` | 主流程契约。定义阶段顺序、子 Agent 并发委派、HC1/HC2/HC3 人工审核、路径规则和结构化输出。 |
| `agents/annotator.json` | 标注子 Agent 资源定义，只挂载 `topic6-annotation`，可被并发创建多个会话。 |
| `agents/annotator.system.md` | 单路标注契约。规定 C0、C3、R1-R5 的输入、Prompt 映射、执行边界和返回 JSON。 |
| `agents/insighter.json` | 洞察子 Agent 资源定义，只挂载 `topic6-insight`，用于 E1-E4 并发生成。 |
| `agents/insighter.system.md` | 单版块洞察契约。规定 E1-E4 的数据输入、Prompt、产物和返回 JSON。 |

JSON 中的 `${skill_id.*}`、`${version.*}`、`${agent_id.*}` 和 `${HOT_TOPICS_MCP_URL}` 是部署占位符，由创建脚本结合上传结果和环境变量渲染，不应写入真实凭据。

### Skill 源码

每个 `skills/topic6-*/` 都是一个可独立打包的 MA 自定义 Skill，根目录的 `SKILL.md` 是其使用入口和运行契约。

| Skill | 流程阶段 | 关键内容 |
|---|---|---|
| `topic6-fetch-normalize/` | Phase A+B | `fetch_hot_topics.py` 从热点 MCP 取数；`log1p_p1p99_normalize.py` 标准化热度；`sample_500.py` 生成 test 样本；`references/平台热度基准_2026.json` 保存四平台基准。 |
| `topic6-annotation/` | Phase C+D | `prompts/` 保存 C0、C3、R1-R5 标注 Prompt；`ks/` 保存业务判定口径；`datahub_annotate.py` 完成 DataHub 提交、轮询与后处理；其余脚本负责筛选和七路合并；`tool/cost-tracker/` 记录成本。 |
| `topic6-event-registry/` | C2 事件合并 | `scripts/00_*` 至 `11_*` 实现标题清洗、事件识别、召回、分块、归档、排序和 Registry 提交；`x0_*` 至 `x4_*` 处理跨平台合并与复核；`references/` 保存判据、流程、校准和运行手册。 |
| `topic6-insight/` | Phase E+F | `01_统计/` 从宽表生成 E1-E4 数据快照；`02_洞察/` 保存四版块 Prompt；`pipeline_e.py` 生成洞察；`pipeline_f.py` 合并完整报告。 |
| `topic6-web-report/` | Phase G | `assets/source/` 是保留的网页模板与素材；`artifact-template.json` 定位模板；校验脚本检查模板、图片来源和内容完整性；`upload-html.mjs` 上传最终 HTML 快照。 |

Skill 在 MA 中挂载到 `/mnt/skills/<skill-name>/`，运行中间产物写入 `/workspace/Projects/<project_dir>/`，最终交付物写入 `/mnt/session/outputs/`。

### Memory

| 文件 | 作用 |
|---|---|
| `memory/topic6/MEMORY.md` | Agent 启动时读取的记忆索引和必读清单。 |
| `memory/topic6/_版本状态.md` | 记录各任务当前生效的 Prompt/规则版本。 |
| `memory/topic6/错误案例库.md` | 保存历史错误模式和规避规则，供后续运行复用。 |
| `memory-store.json` | 声明 Memory Store 及上述文件到 `/mnt/memory/topic6/` 的映射。 |

Memory 源文件与 Skill 一样已和客户原始材料解耦。Agent 侧只读，更新由 Gateway 或 Memory Store API 完成。

### 部署与版本文件

| 文件 | 作用 |
|---|---|
| `environment.json` | 声明 MA 托管环境的网络白名单、Python 依赖、环境变量占位符和 TOS 输出存储。 |
| `skill_ids.json` | `upload_skills.py` 回填的 Skill ID、版本、上传时间和源码 SHA-256，是 Agent 配置渲染的数据源。 |
| `version_mapping.md` | 面向维护者的 Skill 版本对照、迁移基线和更新流程记录。 |
| `create_all.sh` | 创建 Environment、Memory Store、两个子 Agent 和 Coordinator，并将占位符渲染为实际资源 ID。 |
| `created_ids.json` | `create_all.sh` 成功后生成的本地资源 ID 汇总，不属于静态源码。 |

## `tools/`

| 文件 | 作用 |
|---|---|
| `pack_skills.sh` | 从 `ma-resources/skills/` 打包 5 个 Skill；校验文件数、单文件大小、ZIP 大小和 `SKILL.md` 数量；生成 ZIP 与 SHA-256。 |
| `upload_skills.py` | 将 ZIP 上传到方舟 SkillHub；默认按 SHA-256 跳过未变化的包，成功后更新 `ma-resources/skill_ids.json`。支持 `--force` 和 `--only <skill-name>`。 |
| `out/` | 打包输出目录，包含 `*.zip` 和 `*.zip.sha256`。这是可重复生成的本地产物，已被 Git 忽略。 |

## 资源发布关系

```text
ma-resources/skills/
        │
        ▼
tools/pack_skills.sh
        │  生成 ZIP + SHA-256
        ▼
tools/out/
        │
        ▼
tools/upload_skills.py
        │  回填 Skill ID / version
        ▼
ma-resources/skill_ids.json
        │
        ▼
ma-resources/create_all.sh
        │
        ├── Environment
        ├── Memory Store
        ├── topic6-annotator
        ├── topic6-insighter
        └── topic6-coordinator
```

维护时应先修改 `ma-resources/skills/` 中的源码，再依次打包、上传并更新 Agent；不要直接修改 `tools/out/` 中的 ZIP。
