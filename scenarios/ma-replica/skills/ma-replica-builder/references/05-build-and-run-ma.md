# 05 · 在 MA 上建 Agent/Env/Session 并重跑

## 目标

把前四步的产物（`system_prompt.txt` + `skills/` + `replay_map.json` + `mocks-skill/`）在火山方舟
Managed Agents(MA) 上真正跑起来：上传 skill、建 Agent、建 Environment、建 Session，用**和原轨迹
同一条 user query** 发起对话，采集指标。

**分层**：MA 平台交互全在冻结层 `scripts/`（`ark_min.py` + `ma_runtime.py`），客户只在
`example-demo/scripts/run.py` 提供工具面/路由/轨迹耗时三块回调。

## 前置

```bash
export ARK_API_KEY=...     # live 实跑必需
# 可选：export ARK_BASE_URL=...（默认 https://ark.cn-beijing.volces.com/api/v3）
```

## 一步跑通（example-demo 样板）

```bash
cd scenarios/ma-replica/skills/ma-replica-builder
python example-demo/scripts/run.py --case-dir ../../ma-cases/demo                     # 默认 files 模式，跑第一条
python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --mock custom --all # custom 模式，跑全部
python example-demo/scripts/run.py --case-dir ../../ma-cases/demo --mock both --all --repeats 5  # 最终交付口径
# --model 换模型（默认 doubao-seed-evolving）；--keep 跑完不删 agent/env/session
# 旧口径仍可用：python example-demo/scripts/run.py --out-dir ../../data
```

**最终交付口径**（正式出结论时用，SKILL.md「最终交付的实跑口径」段有完整说明）：
- `--mock both`：files 与 custom **串行各跑一遍**，各出一份 `comparison-<mode>.md`。
- `--repeats 5`：每条轨迹**并发**重复 5 次取均值；**轨迹之间串行**（`--concurrency` 可收窄并发）。
- **失败剔除**：失败的重复（`session.error` / stop_reason=error / 零模型请求）不计入耗时/token 均值，
  但统计**失败率**作参考（逐条 + 整体）。

> **建 Agent/Env/Session、上传 Skill 的脚本都在冻结层 `scripts/`，不在 case 目录里。**
> `ark_min.py` 提供原子能力（`create_agent` / `create_environment` / `create_session` /
> `upload_skill` / `send_*` / `stream_events` / `delete_*`），`ma_runtime.py` 把它们编排成
> 「建 agent→env→逐条 session 重跑→落盘→清理」。这些永不随客户变，所以 case 目录只存**数据**、
> 不复制脚本。客户样板 `run.py` 只是**调用**它们并注入客户特有的工具面。

`run.py` 内部依次做四件事（前三件调冻结层 `ma_runtime`）：

1. **上传 skill（CreateSkill）**：遍历 `<case>/shared/skills/<code>/`，逐个
   `POST /skills`（multipart，带 beta header `X-Ark-Beta: agentic-2026-06-01`）拿 `skill_id`。
   `--mock files` 时额外把 `shared/mocks-skill/` 也上传。
   > MA 的 skill 不是本地目录直挂——必须先上传拿到 `skill_id`，再在 agent 配置里
   > 以 `skills:[{"type":"custom","skill_id":...}]` 引用。
2. **create_agent**（`ma_runtime.build_agent_config`）：`model.id` = `--model`；`system` =
   `shared/system_prompt.txt`（files 模式再拼上 `mock_system_appendix.md`）；`skills` = 上传得到的引用；
   `tools` = `agent_toolset_20260701`（含 bash，**故意挂上以观测 bash 抢戏**）+
   custom 模式下每个业务工具一个 `type:"custom"` 声明（files 模式不注册 custom tool）。
3. **create_environment(unrestricted) + create_session**（`ma_runtime.run_session`）：每条 query
   单开 session，保证 cache/耗时不互相污染。
4. **事件循环**（`ma_runtime.run_query`，见 `03`/`06`）：处理 `agent.custom_tool_use`（客户
   `resolve` 回调严格回放回传）、`agent.tool_use`（记录 bash 抢戏）、`span.model_request_end`
   （累计 `model_usage`）、`session.status_idle/terminated/error`（收尾）。

跑完默认删除 session/environment/agent（`--keep` 保留用于排查）。每条轨迹每次重复的精简指标落
`<case>/<mode>-mode/<轨迹stem>/rep<i>.json`，同次重复的**原始事件流**落同目录 `rep<i>.events.jsonl`
（未加工的 MA 事件逐行 JSONL，可回放、可做内容级 diff 的"MA 侧新轨迹"原料；不需要时加 `--no-record-events` 关闭），
聚合（含 fail_ratio 与成功均值）落同目录 `run.json`
（files-mode 与 custom-mode 分开、不覆盖），随后每模式自动生成 `<case>/reports/comparison-<mode>.md`（见 `06`）。

## 换客户 / 排错

- 复制 `example-demo/scripts/run.py`，改 `build_custom_tools()`（工具声明）、`make_resolve()`
  （结果路由）、`make_self_side()`（轨迹耗时）。冻结层 `ma_runtime` / `ark_min` 不动。
- 工具名不叫 `api_call` / `current_time`：`--api-tool` / `--time-tool` 覆盖（extract 端也要同名覆盖）。
- CreateSkill / create_agent 报 4xx：`ArkMinError` 会带上状态码 + `x-request-id` + 响应片段，
  按 request-id 找方舟侧日志。
- 想留现场：`--keep` 后 agent/env/session 不删，可去控制台看 Session 事件流回放。
