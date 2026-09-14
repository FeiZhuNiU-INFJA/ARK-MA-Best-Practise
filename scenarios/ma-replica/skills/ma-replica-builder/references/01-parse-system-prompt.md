# 01 · 拆解 system prompt

## 目标

客户自研 Agent 的第一条 `system` 消息，通常是把**多个组件拼在一起**的一大段文本：
角色设定、工具描述、memory、skill 目录、运行期动态注入的用户上下文……在 MA 上重放前要先看清
它由哪些部分组成，判断哪些是**静态 system**（放进 MA agent 的 `system`）、哪些是**运行期动态注入**
（如用户上下文、memory，应走 Session 事件或 Memory，而非固化进 system）。

## 怎么做

层③ 客户生成式脚本 `example-demo/scripts/extract_trajectories.py` 会（换客户时**照它现写**，见
`00-frozen-vs-client.md`）：

1. 取第一条轨迹的 `messages[0].content` 全文，写到 `system_prompt.txt`（作为 MA 的静态 system 基准）。
2. 若 system prompt 含 `<<<BEGIN SECTION: X>>> / <<<END SECTION: X>>>` 成对围栏，就按 section 拆解，
   写到 `system_sections.md`；否则整段作为静态 system 兜底。

```bash
cd scenarios/ma-replica/skills/ma-replica-builder
python example-demo/scripts/extract_trajectories.py --traj-dir <轨迹目录> --out-dir <工作目录>
# 产物：system_prompt.txt, system_sections.md, ...
```

## 判断原则

- **静态**（进 MA `system`）：角色、约束、长期规则、术语规范、输出格式。
- **动态**（不要固化进 system，改走 Session `user.message` / `system.message` / Memory）：
  当前用户身份、实时上下文（如轨迹里的 `DYNAMIC_USER_CONTEXT`）、`MEMORY`。
- **可删**：只服务于自研 harness 的说明（如"用 skill_invoke 加载技能"这类——MA 有自己的 skill 机制，
  见 `02-extract-skills.md`）。

## 换客户注意

不同客户的 section 标记不同（可能是 `##` 标题、XML 标签、或无标记）。
`extract_trajectories.decompose_system()` 目前识别 `<<<...SECTION...>>>`；换成别的标记时，
改这个函数的正则即可，其余流程不变。
