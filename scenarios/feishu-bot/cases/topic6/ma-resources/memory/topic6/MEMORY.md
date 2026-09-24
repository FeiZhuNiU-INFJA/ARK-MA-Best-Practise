# Topic6 热点报告 · Agent 长期记忆索引

> 每次启动前必读本文件,再按索引决定是否读其他记忆文件。
> 本文件由 gateway 侧 Memory Store API 维护,Agent 只读挂载到 `/mnt/memory/topic6/`。

---

## 必读清单

| 文件 | 何时读 | 说明 |
|---|---|---|
| `/mnt/memory/topic6/错误案例库.md` | **每次启动** | 历史错误 + 修正,防止重蹈覆辙 |
| `/mnt/memory/topic6/_版本状态.md` | **每次启动** | 八任务(C0/R1~R5/C2/C3)当前活跃 Prompt 版本 |

> 客户原 `lm/执行日志.md`(项目级临时状态)不 fork 到 MA;
> MA 侧的执行状态由 gateway `pipeline_jobs` 表 + 沙箱内 `/workspace/{PROJECT_DIR}/run_config.yaml` 承接。

---

## 关键规则索引

- 触发词:**"热点报告"** 或 **"热点周报"**(由 gateway `orchestrator` 分派进入 topic6 主线)
- 日期默认:当前时间的上一个完整自然周(周一到周日)
- 人工干预点:**3 个 HC,不可跳过,不可合并**;由 gateway 渲染飞书交互卡片,回帖以 `user.message` 塞回 MA Session
- Prompt 管理:不通过验收时,skill 内新建 `v{N+1}.md`,不覆盖旧版本;重新打包上传 → SkillHub 生成新 version → 协调器 pin 到明确 version
- 成本记录:每个 LLM 任务完成后立即调用 `python3 /mnt/skills/topic6-annotation/tool/cost-tracker/cost_tracker.py --project-dir /workspace/{PROJECT_DIR} ...`,不攒着批量写

---

## 当前 Skill 版本(MA 口径)

| Skill | 版本 | 挂载路径 | 说明 |
|---|---|---|---|
| 主编排 | — | 协调器 system prompt(`ma-resources/agents/coordinator.system.md` 的运行时投影 `/workspace/AGENTS.md`) | 本 topic 执行时序的唯一权威 |
| `topic6-fetch-normalize` | v1 | `/mnt/skills/topic6-fetch-normalize/` | Phase A+B 取数标准化 + 抽样;通过挂载的 hot-topics MCP 直连,不再依赖客户 `hot-topics-data` skill |
| `topic6-annotation` | v1(内含 C0=v4 / R1=v2 / R2=v1 / R3=v1 / R4=v2 / R5=v2 / C3=v7) | `/mnt/skills/topic6-annotation/` | Phase C:C0+R1~R5+C3 七路标注;`datahub_annotate.py` 5 合 1 精简版(提交+轮询+合并+筛选+时窗) |
| `topic6-insight` | v1(内含 E1=v1 / E2=v1(客户 v3 快照)/ E3=v1 / E4=v1(客户 v6 快照)+_tagging=v1) | `/mnt/skills/topic6-insight/` | Phase E:E1~E4 四版块洞察;Ark(OpenAI 兼容)接口,已彻底移除 anthropic 依赖 |
| `topic6-event-registry` | v2.1.0-ma-fork.1 | `/mnt/skills/topic6-event-registry/` | Phase C:C2 事件合并;fork 自 `blueai-canonical-event-registry v2.1.0`,与 upstream 解耦 |
| `topic6-web-report` | v1.1.0-ma-fork.1 | `/mnt/skills/topic6-web-report/` | Phase G:UI 网页版报告;fork 自 `artifact-template-bluefocus-hotspot-web-report v1.1.0`,与 upstream 解耦 |

---

## 与客户原版的差异要点

1. **路径口径**:所有 skill 相对路径基准由客户版的 `skill/xxx/` 改为 MA 沙箱挂载 `/mnt/skills/topic6-xxx/`;记忆文件由 `lm/` 改为 `/mnt/memory/topic6/`;项目产物由 `Projects/{PROJECT_DIR}/` 改为 `/workspace/{PROJECT_DIR}/`。
2. **工具替换**:
   - `lark-cli` → gateway 侧 `FeishuSender`(卡片渲染与消息推送)
   - `datahub-cli` / `hot-topics-mcp` → MA MCP 挂载
   - `anthropic-llm` 依赖已删除,统一走 Ark OpenAI 兼容接口(`ARK_API_KEY` / `ARK_BASE_URL`,支持回退到 `OPENAI_*`)
3. **子 Agent 并发**:Phase C 第二批 R1~R5 五路、Phase E 四路,由 `topic6-annotator` / `topic6-insighter` 子 Agent 并发执行;其余阶段串行走协调器。
4. **HITL**:HC1/HC2/HC3 关卡输出结构化 JSON + `end_turn`,由 gateway 渲染飞书交互卡片,回帖以 `user.message` 塞回 MA Session。
