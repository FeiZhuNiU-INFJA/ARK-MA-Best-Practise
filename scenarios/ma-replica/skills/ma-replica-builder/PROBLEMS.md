# PROBLEMS · 踩坑与口径边界

这个 skill 在设计与 worked example 中踩过的坑、以及对比报告必须交代的口径边界。
换客户前先读一遍，能省掉大部分返工。

## 一、口径边界（最重要，直接影响结论可信度）

1. **自研侧 token / cache 不可恢复**。客户轨迹导出通常只有 `messages`，**没有 `usage`、没有
   `tools` schema**。所以：
   - token 消耗、cache 命中率**只能给 MA 单边实测**，对比表里自研侧这两列写 `N/A`。
   - 工具描述靠从调用参数反推（`build_custom_tools()` 里手写的 `description` / `input_schema`）。
   - 要双边 token 对比，必须向客户额外要「全量请求导出」（含 `usage`）。
2. **耗时口径两侧不同**。自研侧 = 各步 `_meta.duration` 求和（不含排队/网络）；MA 侧 = 真实墙钟。
   可比，但别当成同一把尺子。
3. **MA cache 命中依赖多轮 prefix 复用**（约 5 分钟 TTL）。单条 query、step 少时命中率可能是 0，
   属正常；要看 cache 收益就 `--all` 拉长样本。

## 二、渐进式披露 → 轨迹覆盖不足

- 自研 Agent 的 skill 详情靠 `skill_invoke` 运行时按需拉取，**一条轨迹只披露这次用到的那部分**。
- `example-demo` 里 3 条轨迹仍有 **4 个子文档从未被披露**（`ticket_triage` 缺 2、`kb_search` 缺 2），
  这些在重建出的 SKILL.md 里**留空并显式标注**，不是 bug。
- 解法：**补覆盖到缺失分支的轨迹**再重跑 extract，`build_skill_bundle` 会自动填上、从 `missing` 移出。

## 三、严格回放：未命中不喂假数据

- 命中录制键 → 返回原始数据；未命中 → 返回结构化 `[tool_error] REPLAY_MISS`，让模型**如实感知
  "走出了录制轨迹"**。绝不编造返回（喂假数据会污染对比）。
- `custom_tool_misses > 0` 说明模型发起了录制里没有的调用（换参数/分叉）。要么补轨迹、要么让
  query 更贴近原轨迹。
- 回放键归一化在 [example-demo/scripts/replay_lib.py](example-demo/scripts/replay_lib.py) 的
  `api_call_key()`——**层③ 客户特有**，换客户必须按其接口参数语义现写这里，否则未命中率虚高、对比不可信。

## 四、MA skill 是"先上传拿 skill_id"，不是本地目录直挂

- 必须先 `POST /api/v3/skills`（multipart，header `X-Ark-Beta: agentic-2026-06-01`）拿 `skill_id`，
  再在 agent 配置里 `skills:[{"type":"custom","skill_id":...}]` 引用。
- `ark_min.upload_skill()` 用**相对路径**（`<顶层目录>/<文件>`）作 multipart filename，符合 CreateSkill 约定。
- **CreateSkill 校验 frontmatter `name` 必须匹配 `^[a-z0-9-]{1,64}$`**（真机 400 踩坑）。轨迹里的
  skill_code 常带下划线（如 `ticket_triage`），会被 400 拒。已在 [scripts/build_skill_bundle.py](scripts/build_skill_bundle.py)
  的 `slug_name()` 里统一 slug 化（下划线/空格→中横线、去非法字符）；目录名与 index key 仍用原始 code。

## 四·补、custom tool 回传的两个真机踩坑（2026-09-10 live 实测暴露并修复）

`--mock custom` 形态第一次真机跑连续踩到两个坑（`files` 模式不走这两条路径，故此前未暴露）：

1. **回传字段名是 `custom_tool_use_id`，不是 `tool_use_id`**。用错会 `400 InvalidPayload`。
   依据官方[会话事件结构参考](https://docs.volcengine.com/docs/82379/2559583)：`user.custom_tool_result`
   用 `custom_tool_use_id` 关联对应的 `agent.custom_tool_use` 事件 ID，必须完全一致。已在
   [scripts/ark_min.py](scripts/ark_min.py) 的 `send_custom_tool_result()` 修正（并加 `is_error`）。
2. **`requires_action` 的 `session.status_idle` 不能当收尾**。官方流程：`agent.custom_tool_use`
   → session 进入 idle 且 `stop_reason.type=requires_action`（在等客户端回传）→ 回传后切回 running。
   事件循环若在**任何** idle 都 break，会在等回传时过早退出。已在
   [scripts/ma_runtime.py](scripts/ma_runtime.py) 的 `run_query()` 里改成：`requires_action` 的 idle
   `continue` 继续等，只有非 `requires_action` 的 idle/terminated 才收尾。

## 五、bash 抢戏观测（历史问题，本轮已 live 复验）

历史结论（前几轮迁移实验，MCP 形态）：doubao 模型手里有 `bash` 时，面对"查数据"会**绕过已注入的工具**，
跑去沙箱 `ls/find/扫 localhost:8900 端口`、试 `/rpc` `/sse` 找远程 MCP，把远程工具误当成本地服务 → 超时/编造数据。

本轮 `--mock custom`（挂 `agent_toolset_20260701` 含 bash + 业务 custom tool `api_call`）**已 live 复验**：

- **结论：custom-tool 形态下抢戏基本不复现（较温和）**。模型正常发起了 `api_call` 5 次；被判为
  抢戏的 2 条 bash 命令原文是 `ls /mnt/skills/<已挂载 skill>/` 与 `.../references/`——是**列已挂载
  skill 的子文档目录**（用 bash 代替 glob/read 探路），**不是**扫端口找远程 MCP 的恶性形态。
- 检测口径偏宽：命令含 `ls/find/grep/localhost/127.0.0.1/curl//rpc//sse/plugins/8900/mcp` 任一即
  标 `snoop=true`，故会把良性探路也标进来——**读结论要结合命令原文**，别只看布尔值。判定逻辑在
  [scripts/ma_runtime.py](scripts/ma_runtime.py) 的 `BASH_SNOOP_HINTS` / `run_query()`；命令原文已随
  `builtin_tool_calls[].command` 落盘。
- 面向 MA 产品/工程的完整排查报告：[docs/ma-bash-snoop-report.md](docs/ma-bash-snoop-report.md)
  （含现象定义、本轮证据、历史严重形态、排查建议、复现步骤）；证据 artifact 在 `docs/evidence/`。

### 本轮 live 实测数字（trajectory1.json，单条 query）

| 模式 | MA 耗时(s) | 模型请求数 | 入/出 token | cache_read | cache 命中率 | custom 未命中 | bash 抢戏 |
|---|---|---|---|---|---|---|---|
| files（自研原轨迹 558.354s / 12 步做对照） | 212.457 | 16 | 769336/5436 | 525984 | 40.61% | — | 触发(温和) |
| custom | 149.013 | 14 | 521344/3089 | 322408 | 38.21% | 3 | 触发(温和) |

> 自研侧 token/cache 不可恢复（导出只含 messages、无 usage）；耗时两侧口径不同（自研=各步
> `_meta.duration` 求和，MA=真实墙钟），仅作参考。样本 n=1，下结论前需 `--all` 多跑取分布。

## 六、清理

`example-demo/scripts/run.py` 会调冻结引擎 `ma_runtime.run_session`，默认跑完删除
session/environment/agent（`ark_min` 删除 best-effort，失败不抛异常以免掩盖主结果）。要留现场排查用 `--keep`。
