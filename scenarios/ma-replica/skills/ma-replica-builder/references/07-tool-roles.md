# 07 · 识别工具角色（加载器 / 网关 / 取时间）

抽取脚本要按**角色**分流处理轨迹里的每次工具调用。不同客户工具名千奇百怪，但角色就那么几类。
你的任务是：**打开轨迹，看清这个客户的工具各自扮演哪个角色**，再把名字填进抽取逻辑。

## 四类角色

| 角色 | 本样例 example-demo 的名字 | 抽取脚本对它的特殊处理 |
|---|---|---|
| **技能加载器** | `skill_invoke` | 把它的**返回值**抽进 `skill_bodies.json` → 转成 MA 的 `SKILL.md`。**不进 mock**（MA 有自己的 skill 机制，不需要这个工具） |
| **业务网关** | `api_call` | 用 `api_call_key()` 生成稳定回放键，抽进 `replay_map.json` 做**严格回放** |
| **取时间** | `current_time` | 固定成录制时的时间戳（保证"距今 N 天"这类相对时间可复现） |
| **其它所有工具** | —— | 也 mock 掉，按 `name+参数哈希` 入 `replay_map`，保证"所有调用都可回放" |

## 在 example-demo 里怎么填的

`example-demo/scripts/extract_trajectories.py` 用三个参数指明角色名，默认值恰好命中本样例：

```bash
python example-demo/scripts/extract_trajectories.py --traj-dir <轨迹目录> --out-dir <工作目录> \
    --skill-loader skill_invoke \   # 技能加载器：返回转 SKILL.md，不进 mock
    --api-tool     api_call \       # 业务网关：严格回放
    --time-tool    current_time     # 取时间：固定时间戳
```

- `--skill-loader`：谁是"渐进式披露的加载器"。它的返回是 skill 正文，要转成 SKILL.md 而非当业务数据。
  还有 `--doc-field`（默认 `document_key`）指明加载器入参里"子文档名"的字段。
- `--api-tool`：谁是"调内部系统查数据的网关"。它的调用要用**语义键**归一化回放（键逻辑在
  `replay_lib.api_call_key()`，客户特有）。
- `--time-tool`：谁是"取当前时间"。固定时间戳，否则重跑时"距今多少天"会漂。

## 换客户怎么判断角色

打开轨迹，对每个出现的工具名问三个问题：

1. **它的返回像不像"一段 SOP / 技能正文"**（大段 markdown、教模型怎么做事）？→ 是**加载器**。
   典型信号：入参有 `skill_code` / `document_key` 之类；返回很长且是说明性文字。
2. **它的返回像不像"一条业务数据"**（查出来的记录、字段值）？→ 是**业务网关**。
   典型信号：入参有 `api_code` / `object_code` / 查询条件；返回是结构化数据。
3. **它是不是在取时间/环境这类确定性小工具**？→ 归到**取时间**（或按需扩一类"固定返回"工具）。

判断完，把名字填进 `--skill-loader/--api-tool/--time-tool`（或直接改脚本默认值）。若客户**没有**加载器
（skill 正文直接写死在 system prompt 里），`skill_bodies.json` 会是空的，重建 skill 这步跳过即可。

> 注意：这几个 `--flag` 只解决"改个工具名"。若客户轨迹**结构**不同（工具调用不在 `tool_calls`、
> 或返回不在 `role:tool` 消息里），要改的是 `extract_trajectories.py` 的解析逻辑本身——
> 这正是它属于"层③ 客户生成式代码"（分析完轨迹现写）而非冻结层的原因（见 `00-frozen-vs-client.md`）。
