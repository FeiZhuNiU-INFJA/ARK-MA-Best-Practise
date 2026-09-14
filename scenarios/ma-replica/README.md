# 场景2：MA 复刻客户 Agent

> 把**客户自研 Agent 的运行轨迹**在火山方舟 Managed Agents（MA）上**复刻**出来，用**同样的
> user message** 重跑 Session，再和原轨迹对比**端到端耗时 / token 消耗 / cache 命中率**，
> 用来评估「迁移到 MA 是否更优」。

客户给若干条自研 Agent 轨迹（含 system prompt、skill、工具调用）→ 离线拆成中间产物 →
在 MA 上还原 skill + mock 工具返回 + 实跑 Session → 出一份对比报告。

## 这不是一个命令行工具，而是一个 skill

本场景的交付物是 [skills/ma-replica-builder/](skills/ma-replica-builder/) —— 一个 **Claude Skills 格式的
skill**。用法不是你自己背命令去敲，而是：**把这个 skill 装进一个支持 skill 的 agent，然后用自然语言
和它对话**，agent 会自己读 [SKILL.md](skills/ma-replica-builder/SKILL.md) 把整套流程跑完。

> 本场景是「让 agent 带着这个 skill，陪你做一次可复现的迁移评估实验」，不是一个常驻程序。
> 所以下面先讲**怎么在 agent 里用它对话**，手敲命令放到最后当兜底。

### 第一步：把 skill 交给 agent

- 用一个能加载本地 skill 的 agent（如 Trae / Claude 等），把 `skills/ma-replica-builder/` 作为 skill
  目录挂上（不同 agent 挂载方式不同，通常是指向 skill 根目录或把它放进 agent 的 skills 搜索路径）。
- skill 的触发时机写在 `SKILL.md` 的 frontmatter `description` 里：当你说到「客户自研 agent 轨迹」
  「在 MA 上复刻」「对比耗时/token/是否迁移更优」这类意图时，agent 会**自动**认出该用这个 skill。
  你不需要点名脚本，描述清楚意图即可。

### 第二步：准备素材（对话前先放好）

1. **≥1 条客户自研 Agent 的运行轨迹（JSON）**：放进 `ma-cases/<你的case名>/trajectories/`。
   轨迹越多，还原越完整。格式不强求统一，agent 会先打开看结构。
2. **火山方舟 `ARK_API_KEY`**：live 实跑必需（只做离线抽取/建 mock 可以先不给）。
   放在 agent 能读到的地方（如 `~/.arkagent/config.env`）并在对话里告诉它。

### 第三步：用自然语言驱动（示例对话）

你只需描述意图，agent 会按 SKILL.md 分阶段执行、并在长任务时自己盯进度。典型几轮：

```text
你：我在 scenarios/ma-replica/ma-cases/nio/trajectories/ 放了 6 条客户自研 agent 的轨迹，
    帮我在 MA 上复刻这个 agent。

agent：（读 SKILL.md → 打开轨迹分析结构 → 现写 extract 把轨迹拆成中间产物
       → 还原 skill、把工具调用物化成文件 mock）已完成离线抽取：拆出 N 个 api 调用、
       M 个 skill、生成 K 个文件 mock，放在 ma-cases/nio/shared/。要现在 live 实跑对比吗？

你：跑吧，api key 在 ~/.arkagent/config.env。两种 mock 都跑，每条轨迹并发 5 次。

agent：（起 run.py 后台跑，自己定时轮询直到两份报告生成）跑完了：
       files 模式 MA 比自研快 2.3~3.8×，custom 模式同量级；两模式失败率 0%，
       cache 命中率 ~42%。报告在 ma-cases/nio/reports/。
```

几个对话要点：

- **默认用文件静态 mock**（最简单）。想要更高保真的性能对比，就明确说「用 custom tool 形式」，
  agent 会切到 custom 模式（或两种都跑）。
- **live 实跑是长任务**（几十分钟）。SKILL.md 里写了执行纪律：agent 应**自己盯到报告出来才收尾**，
  不会甩一句「已在后台跑」就停。你也可以随时问「跑到哪了」。
- **一个 case 一个目录**：换一批客户轨迹就新开一个 `ma-cases/<case>/`，互不干扰。

## 底层流程（agent 替你执行，也可手动直跑）

下面是 agent 读 SKILL.md 后实际做的事。**你通常不用手敲**，但想直接驱动、或调试时可照跑。

```text
客户自研 Agent 轨迹（*.json）
        │  ① extract：认这个客户的轨迹结构，拆成中间产物契约
        ▼
  中间产物（replay_map / skill_bodies / system_prompt / queries …）
        │  ② build_skill_bundle：还原成 Claude Skills SKILL.md 树
        │  ③ gen_file_mocks：把每次工具调用物化成文件（变体 B）
        ▼
   两种 mock 模式在 MA 上实跑同样的 user message
        ├── files ：静态文件 mock，模型用内置 read 读
        └── custom：注册 custom tool，客户端严格回放录制数据
        │  ④ ma_runtime：建 agent/env/session、跑事件循环、采指标
        ▼
   ⑤ report：MA 侧 vs 自研侧对比报告（耗时/token/cache/失败率）
```

手动直跑（准备好 Python 3.11 + `httpx`；`cwd = skill 根`）：

```bash
cd scenarios/ma-replica/skills/ma-replica-builder

# ① 离线：抽取中间产物 → 还原 skill → 物化文件 mock
#    （先把客户轨迹放进 ../../ma-cases/<case>/trajectories/）
python example-demo/scripts/extract_trajectories.py --case-dir ../../ma-cases/<case>
python scripts/build_skill_bundle.py               --case-dir ../../ma-cases/<case>
python scripts/gen_file_mocks.py                   --case-dir ../../ma-cases/<case>

# ② live 实跑 + 对比报告（正式交付口径：两模式 + 全部轨迹 + 每条并发 5 次取均值）
export ARK_API_KEY=...
python example-demo/scripts/run.py --case-dir ../../ma-cases/<case> --mock both --all --repeats 5
```

> **两层冻结**是本场景的设计核心：`①`（拆轨迹）随客户变、要照 `example-demo/` 现写；`②③④⑤`
> 只吃中间产物契约、跨客户复用不改。详见
> [skills/ma-replica-builder/references/00-frozen-vs-client.md](skills/ma-replica-builder/references/00-frozen-vs-client.md)。

## 目录结构

```text
scenarios/ma-replica/
├── README.md                 # 本文件：场景导览（用户视角怎么用）
├── skills/
│   └── ma-replica-builder/   # ★ 本场景核心：复刻构建器（方法论 + 冻结层脚本 + 参照样板）
│       ├── SKILL.md          # agent 读的主文档：完整流程、命令、执行纪律、口径
│       ├── PROBLEMS.md       # 踩坑与口径边界（换客户前先读）
│       ├── scripts/          # 冻结层：ark_min / ma_runtime / report / build_skill_bundle / gen_file_mocks / case_paths
│       ├── references/       # 分步参考（00~07）
│       └── example-demo/     # 合成参照样板（Acme 客服工单分诊）：extract/run + 轨迹 + data 基准
├── ma-cases/                 # ★ per-case 运行期数据（原始轨迹/抽取产物/实跑/报告），不入库（.gitignore）
│   └── <case>/               # 一个 case = 一次「拿某客户某批轨迹做复刻实验」
└── context/                  # 场景相关的客户原始材料，不入库（.gitignore）
```

- **skill 自带样板 vs per-case 数据分离**：`example-demo/` 是入库的合成参照（可复现基准）；真实客户
  轨迹与实跑产物一律落在 `ma-cases/<case>/`，属运行期数据、**不入库**。
- **一个 case 一个目录**：`ma-cases/<case>/` 下 `trajectories/`（原始轨迹）、`shared/`（抽取产物，
  两模式共享）、`files-mode/` `custom-mode/`（分模式实跑）、`reports/`（对比报告）。布局由冻结层
  [skills/ma-replica-builder/scripts/case_paths.py](skills/ma-replica-builder/scripts/case_paths.py) 钉死。

## 先拿自带样板跑通再换真实客户

不确定流程时，先用 skill 自带的合成样板 `example-demo` 跑一遍（完全虚构、可外发）：把上面命令的
`--case-dir ../../ma-cases/<case>` 换成 `example-demo` 一路的参数（`extract` 用
`example-demo/scripts/extract_trajectories.py --traj-dir example-demo/trajectories --out-dir example-demo/data`
复现基准），对照 `example-demo/data/` 的入库基准验证无差异，再换真实客户轨迹。

## 参考资料

- 本场景 skill 主文档：[skills/ma-replica-builder/SKILL.md](skills/ma-replica-builder/SKILL.md)
- 踩坑与口径边界：[skills/ma-replica-builder/PROBLEMS.md](skills/ma-replica-builder/PROBLEMS.md)
- MA 官方文档合集（跨场景通用）：[common/docs/火山方舟_ManagedAgents_docs.md](../../common/docs/火山方舟_ManagedAgents_docs.md)
- [火山方舟：Managed Agents API](https://docs.volcengine.com/docs/82379/2555910?lang=zh)（create agent/environment/session、事件流等一手文档）
- 仓库最佳实践索引：[根 README](../../README.md)
