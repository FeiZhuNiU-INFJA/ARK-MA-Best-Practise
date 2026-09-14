---
name: ma-replica-builder
description: 把客户自研 Agent 的运行轨迹在火山方舟 Managed Agents(MA) 上尽力模拟重放，用同样的 user message 重跑 Session，再和原轨迹对比耗时/token/cache。当用户拿到客户自研 Agent 的若干条轨迹（含 system prompt、skill、工具调用）想在 MA 上重放并做性能对比、或要评估"迁移到 MA 是否更优"时使用。注意这是基于轨迹的模拟重放、不是复刻或逆向真实 agent（system prompt 只取轨迹可见部分、工具全 mock、未披露 skill 留空）。默认用文件静态 mock，可选 custom tool 动态回放；产出一份对比报告。
---

# ma-replica-builder：客户轨迹 MA 重放构建器

把「客户自研 Agent 的运行轨迹」在火山方舟 Managed Agents(MA) 上**尽力模拟重放**，用**同样的 user
message** 重跑，再和原轨迹对比**端到端耗时 / token 消耗 / cache 命中率**，用来评估"迁移到 MA 是否更优"。

> ⚠️ **能力边界（先读）**：这**不是复刻、也不是逆向出**客户那个真实 agent——拿不到它的源码、完整
> system prompt、真实工具实现和未在轨迹里出现过的分支。本 skill 做的是**基于轨迹的模拟重放**：
> system prompt 只取轨迹里**可见的部分**（可能不完整）；工具全部是 **mock**（回放录制过的返回，没录到就没数据）；
> skill 里**未被渐进式披露**的子文档只能留空。所以对比结论只在「同样输入、同样可见上下文」口径下成立，
> 用于横向感受 MA 的耗时/缓存表现，**不等于**"把这个 agent 搬到 MA 上会一模一样"。

> ⚠️ 这是一套**方法论 + 两层冻结的可复用件**，不是一键脚本。每个客户的轨迹结构、工具名、接口参数
> 都不同，**把原始轨迹拆成中间产物**的那段代码要由你分析完这个客户的轨迹后**现写**（照 `example-demo/` 写）。
> 但只要你产出的中间产物符合契约，下游的 skill 重建 / 文件 mock / MA 实跑 / 对比就全部白嫖复用。

## 怎么用（前置准备）

开始前请准备：

1. **≥1 条客户自研 Agent 的运行轨迹**（JSON）。轨迹越多，重放越接近原状（渐进式披露的 skill 子文档、
   分叉查询都需要更多轨迹覆盖）。轨迹格式**不强求统一**——不同客户导出会有差异，你要先打开看它长什么样
   （顶层是不是 `messages[]`？工具调用在 `tool_calls` 还是别处？有没有 `usage`？）。
2. **火山方舟 `ARK_API_KEY`**（live 实跑必需；只做离线抽取/建 mock 可以先不给）。
3. Python 3.11 环境（脚本仅依赖 `httpx`）。

> ⚠️ 轨迹导出**是否含 token/cache 取决于客户格式**。很多导出（含本 skill 附带的 `example-demo` 合成样例，
> 刻意对齐真实客户导出）**只有 `messages`、没有 `usage`**，此时自研侧 token/cache **无法恢复**，对比报告里这两项只给
> MA 单边实测。要双边 token 对比，须向客户额外要「含 `usage` 的全量请求导出」。

## 工作目录规范（一个 case 一个目录）

**所有产物都落在本 skill 的场景级目录 `../../ma-cases/<case>/`（即 `scenarios/ma-replica/ma-cases/<case>/`）下**，
一个 case（= 一次「拿某客户某批轨迹做重放对比实验」）一个独立目录，互不干扰。
下文命令均**以 skill 根 `scenarios/ma-replica/skills/ma-replica-builder/` 为工作目录**，故 case 目录写作 `../../ma-cases/<case>`。
这套布局由冻结层 [scripts/case_paths.py](scripts/case_paths.py) 钉死，所有脚本共用 `--case-dir` 一个开关：

```
../../ma-cases/<case>/
├── trajectories/            # 原始轨迹（客户给的 *.json），只读输入
├── shared/                  # 两种 mock 模式共享的抽取产物（extract/build/gen_file_mocks 输出）
│   ├── system_prompt.txt / system_sections.md
│   ├── skill_bodies.json / replay_map.json / queries.json / coverage.json / index.json
│   ├── skills/<code>/SKILL.md …        # 依据轨迹可见内容重建的业务 skill（两模式都用）
│   ├── mocks-skill/…                   # 文件静态 mock 数据（仅 files 模式会挂）
│   └── mock_system_appendix.md         # 离线取数附录（仅 files 模式拼进 system）
├── files-mode/<traj>/       # 静态文件模式：每条轨迹一子目录，放 rep<i>.json（精简指标）+ rep<i>.events.jsonl（原始事件流）+ run.json（聚合）
├── custom-mode/<traj>/      # custom tool 模式：每条轨迹一子目录（与 files-mode 分开，不覆盖）
└── reports/                 # 对比报告 comparison-files.md / comparison-custom.md
```

要点：
- **共享 vs 分模式**：`shared/` 是抽取产物，两种 mock 模式共用同一份；每条轨迹每种模式的**实跑结果**
  按 `<mode>-mode/<轨迹stem>/run.json` 分开放，避免多轨迹/多模式互相覆盖。
- **一个开关**：`extract / build_skill_bundle / gen_file_mocks / run.py` 都支持 `--case-dir ../../ma-cases/<case>`,
  自动认 `trajectories/`（读）、`shared/`（读写）、`<mode>-mode/`（写实跑）、`reports/`（写报告）。
  旧口径 `--out-dir <dir>`（平铺目录）仍向后兼容。
- **`../../ma-cases/` 不入库**：它是 per-case 运行期数据（已在 `.gitignore`）；skill 自带的参照样板在
  `example-demo/data/`，那份才进版本库、当回归基准。

## ⚠️ 执行纪律：live 实跑要盯到报告出来才停

`run.py` 的 live 实跑是**长任务**（每条轨迹一个完整 agentic 循环，并发重复多次，两模式各跑一遍，
可能几十分钟）。启动后**不要**交代一句"已在后台跑"就停下来等——那样用户会一直卡在"到底跑完没"。正确做法：

1. 后台起 `run.py`，然后**自己定时轮询**运行状态，直到终态：
   - 进度信号看 `../../ma-cases/<case>/<mode>-mode/<traj>/run.json` 陆续落盘（stdout 有缓冲，不能只等日志）；
   - 完成信号看**需要的每个模式**的 `../../ma-cases/<case>/reports/comparison-<mode>.md` 都生成 + `run.py` 进程退出。
2. 轮询用**一条阻塞命令**兜住等待（示例，避免反复手动查；`--mock both` 时等两份报告都出来）：
   ```bash
   R1=../../ma-cases/<case>/reports/comparison-files.md
   R2=../../ma-cases/<case>/reports/comparison-custom.md      # 只跑单模式时删掉这一半条件
   while { ! [ -f "$R1" ] || ! [ -f "$R2" ]; } && pgrep -f run.py >/dev/null; do
     sleep 30; echo "[$(date +%H:%M:%S)] done=$(find ../../ma-cases/<case> -name run.json | wc -l)"
   done; echo "REPORTS READY"; cat "$R1" "$R2"
   ```
3. **只有报告全部生成、或进程异常退出（报错）时才结束本轮对话**；中途不要提前收尾。若进程已退出但报告没出，
   去读 job 日志定位报错，别谎报完成。

## ⚙️ 最终交付的实跑口径（两模式 + 并发重复 + 失败剔除）

正式出对比结论时，按以下口径跑（都是 `run.py` 的参数，冻结层已实现）：

- **两种 mock 都要跑**：`--mock both` —— files（静态文件）与 custom（custom tool）**串行各跑一遍**，
  各出一份 `comparison-<mode>.md`。想只跑一种就 `--mock files` / `--mock custom`。
- **轨迹之间串行、每条轨迹并发重复**：`--repeats 5` —— 每条轨迹并发跑 5 次取均值抗抖动；
  轨迹与轨迹之间仍串行（避免相互干扰 + 便于观察）。并发上限默认=repeats，可用 `--concurrency` 收窄。
- **失败剔除**：某次重复若 `session.error` / stop_reason 为 error / 零模型请求，判为**失败**，
  **不计入耗时/token 均值**；但会统计并展示**失败率**（逐条 + 整体）作为可靠性参考。
- 落盘：`<mode>-mode/<traj>/rep<i>.json`（每次重复精简指标）+ `rep<i>.events.jsonl`（每次重复的**原始
  事件流**，可回放、可做内容级 diff 的"MA 侧新轨迹"原料；`--no-record-events` 可关）+ `run.json`（该轨迹聚合，含 fail_ratio 与成功均值）。
- 一条命令（最终口径，cwd = skill 根 `scenarios/ma-replica/skills/ma-replica-builder/`）：
  ```bash
  python example-demo/scripts/run.py --case-dir ../../ma-cases/<case> --mock both --all --repeats 5
  ```


## 三层结构（本 skill 的核心分层）

判断一段代码该不该固定：**它依赖"MA 平台的形状"、"我们自定义的中间产物形状"，还是"这个客户轨迹的形状"？**
前两者能钉死成契约 → 冻结；只有第三种每客户都不同 → 现写。据此切成三层：

- **① 冻结·平台运行时 `scripts/`（依赖 MA 平台，直接用）**：
  - `ark_min.py`：最小 MA HTTP 客户端（鉴权 / endpoint / SSE / CreateSkill）。
  - `ma_runtime.py`：通用运行引擎（上传 skill、建 agent/env/session、事件循环、指标累计、清理）。
    靠你传入 `custom_tools` 声明 + `resolve(name,args)` 回调，自身不含客户逻辑。
  - `report.py`：通用对比报告（MA 侧口径固定；自研侧耗时靠你传 `self_side` 回调）。
- **② 冻结·契约转换器 `scripts/`（只依赖中间产物契约，直接用）**：
  - `build_skill_bundle.py`：读 `skill_bodies.json` → 重建成 Claude Skills `SKILL.md` 树（未披露的留空）。
  - `gen_file_mocks.py`：读 `replay_map.json` → 物化变体 B 的文件 mock。
  - 这两个**从不读原始轨迹**，只吃下面那套中间产物，所以跨客户不用改。
- **③ 客户·生成式代码 `example-demo/scripts/`（依赖客户轨迹，现写）**：
  - `extract_trajectories.py`：认**这个客户的**原始 JSON 结构，把它拆成中间产物契约。
  - `replay_lib.py`：`api_call_key()` 是**客户接口参数语义**（写进 replay_map 的 key）。
  - `run.py`：**只写三小块客户特有的东西**——`build_custom_tools()` + `make_resolve()` +
    `make_self_side()`——然后调层① 冻结引擎。

**中间产物契约**（层②③的边界，`extract` 必产，下游只认这些形状）：
`system_prompt.txt` / `skill_bodies.json` / `replay_map.json` / `queries.json` / `index.json`。
`replay_map` 的 key 由客户语义算出，但对下游是**不透明字符串**——语义不外泄，所以层② 能冻结。

**换客户的正确姿势**：读 `references/` 弄懂方法与契约 → 打开新客户轨迹看清结构 →
照 `example-demo/scripts/` **现写**层③（把轨迹拆成契约），**不是复制模板改几行** →
层①② 冻结件原样调用。详见 `references/00-frozen-vs-client.md`。

## 整体流程

```
客户轨迹 → ① 拆 system prompt → ② 重建 skill(可见部分) → ③ mock 所有工具调用(默认文件/可选 custom)
         → ④ 在 MA 建 Agent/Env/Session → ⑤ 发同一 query 重放 → ⑥ 对比耗时/token/cache
```

每一步详细做法见 `references/`（按需读取）：

- 三层结构 + 中间产物契约边界：`references/00-frozen-vs-client.md`
- 识别工具角色（加载器/网关/取时间）：`references/07-tool-roles.md`
- 拆 system prompt：`references/01-parse-system-prompt.md`
- 重建 skill（未披露内容留空标注）：`references/02-extract-skills.md`
- **变体 B**（文件静态 mock，**默认**）：`references/04-mock-variant-b-files.md`
- **变体 A**（custom tool 动态回放，需要更高保真时选用）：`references/03-mock-variant-a-custom-tool.md`
- 在 MA 上建并重跑：`references/05-build-and-run-ma.md`
- 采集并对比指标：`references/06-compare-metrics.md`

## 两套 mock 方案：默认用哪个

**默认走变体 B（文件静态 mock）**——最简单：不注册工具，把每次调用物化成文件随 skill 挂载，
模型用内置 `read` 读。适合快速跑通、行为冒烟。

**只有当用户明确要"高保真性能对比 / 让模型自己决策何时调工具"时，才切到变体 A（custom tool 动态回放）**。
读到本 skill 时，若用户没指明，就默认变体 B，并主动问一句"要不要用 custom tool 形式做更高保真的对比？"。

| | 变体 B：文件静态 mock（默认） | 变体 A：custom tool 动态回放 |
|---|---|---|
| 工具面 | 简化：不注册工具，改成"读文件" | 忠实：仍是 `type:"custom"` 工具，模型照常发起 tool call |
| 数据来源 | 每次调用物化成 `/mnt/skills/.../mocks/*.txt`，模型 `read` | 客户端订阅 `agent.custom_tool_use`，录制数据严格回放后回传 |
| 需要客户端在线 | 否（发完 query 即可） | 是（全程接管工具执行） |
| 保真度 / 用途 | 中，快速跑通 / 无常驻客户端时的兜底 | 高，**性能对比首选** |

`example-demo/scripts/run.py` 用 `--mock files`（默认）/ `--mock custom` 一个开关切换两种模式。

## Worked example（合成客服工单样例 example-demo）

`example-demo/` 是一份**完全虚构、可外发**的完整样例（3 条合成的「Acme 客服工单分诊」轨迹 +
层③ 客户生成式脚本的参照实现），拿它把 `ma-replica-builder` 的全流程跑通，见
[example-demo/README.md](example-demo/README.md)。踩坑与口径边界见 [PROBLEMS.md](PROBLEMS.md)。

## 关键事实（踩坑记录，来自本 skill 的 live 实测）

- MA 的 skill 必须**先 CreateSkill 上传**（`POST /api/v3/skills`，multipart，header
  `X-Ark-Beta: agentic-2026-06-01`）拿到 `skill_id`，再在 agent 配置里以
  `skills:[{"type":"custom","skill_id":...}]` 引用——不是本地目录直挂。
- 指标从事件流采：`span.model_request_end.model_usage` =
  `input_tokens / output_tokens / cache_creation_input_tokens / cache_read_input_tokens`；
  Session 级用量要客户端自己累加。
- 变体 A 故意挂 `agent_toolset_20260701`（含 bash），用来实测"bash 抢戏"是否复现；结论记在 PROBLEMS.md。
