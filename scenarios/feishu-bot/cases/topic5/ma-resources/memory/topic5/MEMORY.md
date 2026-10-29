# Topic5 营销活动复盘报告 · Agent 长期记忆索引

> 每次启动前必读本文件,再按索引决定是否读其他记忆文件。
> 本文件由 gateway 侧 Memory Store API 维护,Agent 通过 Session Memory 工具只读访问。
> Memory Store 不是沙箱文件系统。启动时先用 `memory_ls("/")` 发现当前挂载的
> `<memory_root>`,再用 `memory_read` 读取 `<memory_root>/topic5/...`;禁止使用 bash、
> `/mnt/memory/...` 路径或硬编码 Memory Store ID。

---

## 必读清单

| 文件 | 何时读 | 说明 |
|---|---|---|
| `<memory_root>/topic5/错误案例库.md` | **每次启动** | 历史错误 + 修正,防止重蹈覆辙 |
| `<memory_root>/topic5/_版本状态.md` | **每次启动** | 各节点接真状态 + 口径红线快照 |

> 执行期状态不进 memory:由 gateway `pipeline_jobs` 表 + 沙箱内
> `ma-workspaces/projects/{pid}/tasks/{task}/runs/{run}/run.json`(编排器 checkpoint)承接。

---

## 关键规则索引

- **触发词**:**"复盘报告" / "营销复盘" / "活动复盘"**(由 gateway `Topic5Runner` 分派进入 topic5 主线)。
- **薄 Agent 边界**:协调器**不做业务判断**,只跑 `run_pipeline.py start/resume/status` 驱动确定性编排器,
  遇人工关卡抛 HC JSON 结束 turn,HC 决定回注后续跑。所有口径逻辑在编排器 handler,**不在 Prompt**。
- **人工关卡(7 门,不可跳过/合并)**:`G1`(n01 需求)/`G2`(n03 方案)/`N10`(n10 观点标签)/
  `G3`(n17 Validator 质检)/`G4`(n21 用户终验)/`HC7`(QC 超限升级)/`USER_ESCALATE`(终验升级)。
  由 gateway 渲染飞书交互卡片,回帖以 `user.message`(`HCx [pass/reject/remark]: note`)塞回 Session。
- **Validator 独立身份**:G3 质检由平级子 Agent `topic5-validator` 执行,只读报告、不改正文、不写飞书;
  `pass_policy = p0_count==0`,≤3 轮,超限走 HC7。
- **交付**:走**飞书文档**(转移所有权 + 导出回读核对 + 记录 revision_id 三门禁),**不走妙搭 Miaoda**。
  终态返回飞书 docx URL。

---

## 口径红线(权威:飞书《Topic5 复盘报告输出流程及框架》+ `pipeline.v0.2.yaml`)

- **指标白名单**:活动层只用【条均互动量 / 品牌提及率 / 产品提及占比 / NSR】;
  **禁用**【人均 / 转化率 / 转化 / 性价比】(命中即 P0)。
- **NSR 分母默认含中性**(`nsr_denominator_includes_neutral=true`,三处权威文件一致)。
- **提及漏斗嵌套**:产品提及是品牌提及子集,产品提及占比 ≤ 1。
- **多活动逐个算、不相加**:不得合计「总声量 / 合计声量」。
- **日期取发表时间** `publish_time`(epoch ms, CST);去重键 `fingerprint → id → item_id → url`。
- **情感以 AI 标注为准**,活动相关行**缺 AI 情感直接报错不兜底**。
- **竞品分析(SOV / 3.2 章)已下线**。

---

## 架构要点

1. **五层模型**:飞书薄 Agent(topic5-coordinator)→ 沙箱确定性 durable 编排器(pipeline-orchestrator)
   → 无状态 Worker 脚本(SSJH / datahub / echarts / cover)→ 瘦 Gateway(复用 topic6 一套,改触发词/HC 门/DB)
   → 版本化状态(engine checkpoint + SQLite + ma-workspaces)。
2. **子 Agent**:按架构简洁原则,DAG 里 Discovery/Opinion/Insight/Narrative 等 owner 标签是**逻辑归属**,
   由确定性编排器 handler 实现,**不**各建独立 MA Agent。仅注册 **2 个 MA Agent**:
   `topic5-coordinator`(薄前台)+ `topic5-validator`(独立质检)。
3. **路径基准**:skill 走 `/mnt/skills/<skill>/`(平铺同级,编排器按 `../<skill>/` 相对定位);
   记忆走 Session Memory 工具的 `<memory_root>/topic5/`;运行期事实走
   `ma-workspaces/projects/{pid}/tasks/{task}/runs/{run}/`,**不写回技能目录**。
4. **凭据**:SSJH / DataHub / 封面 / 飞书 token 由 MA Vault 以环境变量注入,Agent 看不到明文。
5. **模式**:`demo` 只预填 G1 表单;进入流水线后与 `full` 同标准,核心能力降级必须阻断。
