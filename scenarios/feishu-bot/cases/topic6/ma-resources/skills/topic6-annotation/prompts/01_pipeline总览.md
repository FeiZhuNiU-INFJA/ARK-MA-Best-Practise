# prompt/01 · pipeline 总览

> **本文件是本 topic 执行时序的唯一权威。** 2026-09-20 第二轮收口：
> 此前同一条流程被三处重复描述（原 `agent/CLAUDE.md`「工作流程」表、原 `agent/skill/SKILL.md`
> 「执行流程」表、原 `agent/CLAUDE.md`「并发执行规范」），已合并为本文件单一主干。
>
> 收口取舍：以原 SKILL.md 执行流程表为主干（4 列最完整、标注了 HC 不可跳过），
> 并入原 CLAUDE.md 并发规范的独有内容（Phase C 串行架构、Phase E 四路并发）；
> 原 CLAUDE.md 工作流程表因信息被主干完全覆盖而删除 —— 它多出的脚本级细节属于
> `skill/annotation/SKILL.md` 的职责范围。
>
> 本轮同时解决两项：**补入原先三处都漏掉的分层抽样步骤**（下表第 2 行）；
> **统一项目目录命名**为 `{PROJECT_DIR}`，格式只在 `prompt/run_config契约.md` 定义一次。

---

## 触发与初始化

**激活条件**：用户消息含 **"热点报告"** 或 **"热点周报"**。
（区别于 `hot-topics-data` skill 的数据取数触发词，不得误判）

角色定义、触发词精确匹配规则、日期解析规则，见 **`prompt/00_角色与触发.md`**（本文件不重复）。

**启动序列**（通用部分见根 `CLAUDE.md`「启动序列」）：

1. 读 `lm/MEMORY.md` → 检查必读清单
2. 读 `lm/错误案例库.md`
3. 读 `lm/_版本状态.md` → 确认 C0/R1~R5/C2/C3 活跃 Prompt 版本
4. 解析用户消息，确定运行参数（见 `prompt/00_角色与触发.md`）
5. 检查是否存在已有项目目录（恢复中断 vs 新建）
6. 若新建：复制 `Projects/_项目模板/` 建项目目录，写入 `run_config.yaml`，进入阶段 A
7. 若恢复：读 `run_config.yaml` 的 `status.current_phase`，从断点继续（值域见 `prompt/run_config契约.md`）

---

## 执行流程（14 步，Phase C 内部串行两批）

| 阶段 | 编排文件 | 任务 | 关键关卡 |
|---|---|---|---|
| **A+B 取数+标准化** | `prompt/02` | 拉取四平台上周热搜 + 分平台 log1p + P1/P99 标准化，存 `01_原始数据/`、`02_标准化/` | 行数 > 200，四平台均有数据，各平台最高分 > 90，无 NaN |
| **抽样** | `prompt/03` | 按平台分层抽 500 条（seed=42），存 `03_抽样/`。**仅 test 模式经过** | 总数 = 500（总量不足时取全量） |
| **C 第一批** | `prompt/04` → `skill/annotation/SKILL.md` | C0 基础事实层 + C3 节点标注并发 | 两路均完成后触发筛选 |
| **C 筛选** | 同上 | 合并 C0+C3，按「是否营销可用」筛出子集（纯脚本零成本） | 解析失败/缺失占比 ≤ 5%，否则熔断 |
| **C 第二批** | 同上 | R1~R5 版块路由层 + C2 事件合并并发，只处理筛选后子集 | 六路均完成后触发 D |
| **D 合并** | `prompt/05` | row_id LEFT JOIN 七路输出（C0+R1~R5+C2+C3），存 `05_合并/` | 输出行数 = 输入行数，row_id 唯一 |
| **☑ HC1 小样本验收** | `prompt/04` | 展示七路质量分布，等待用户确认 | **不可跳过，不可与 HC2 合并** |
| **C→D 全量** | 同上 | 全量数据走完整串行流程（第一批→筛选→第二批→合并） | 同上 |
| **☑ HC2 全量验收** | `prompt/05` | 展示整体分布/空值率/筛选占比/聚类规模，等待用户确认 | **不可跳过** |
| **E 分版块洞察** | `prompt/06` | 四版块并发写作，存 `06_洞察/v{N}/` | 四版块均输出非空 md |
| **F 合并发布** | `prompt/07` | 拼接（`pipeline_f.py`）→ 自校验 → 推飞书 → 立即授权 4 人 | 发布成功，URL 写入 run_config |
| **☑ HC3 报告审核** | `prompt/07` | 飞书文档人工审核 | **不可跳过** |
| **G UI 发布** | `prompt/08` | 调 `$artifact-template-bluefocus-hotspot-web-report` 生成网页版 | 产出 `index.html` |
| **H 妙搭发布** | `prompt/09` | 调 `miaoda-web-publish` skill 把 G 产出的 index.html 发到妙搭拿公网链接 | release_status=finished 且 online_url 非空，**固定关卡，不可跳过**；流程结束 |

Phase C 的详细执行步骤（Prompt 路径 / DataHub 命令 / 筛选熔断 / 补标注闭环）在
`skill/annotation/SKILL.md`「六、执行流程」，本文件只维护跨阶段时序，不重复其内容。

---

## 并发执行规范

### Phase C 串行架构（C0+C3 → 筛选 → R1~R5+C2）

**第一批（并发，跑全量/全样本）**：
- **C0 基础事实层**：DataHub，Prompt `skill/annotation/prompts/C0_基础事实/{活跃版本}.md`，输出商业实体 / 行业归属 / 是否营销可用等 9 字段
- **C3 节点标注**：DataHub，Prompt `skill/annotation/prompts/节点标注/{活跃版本}.md`，标题+描述直接进 LLM 四层判断（不预筛，**不受 C0 筛选影响**）

**筛选（纯脚本，零成本，C0 完成后自动触发）**：
`c0_merge_phase1.py`（合并 C0+C3 到基础表）→ `c0_filter_usable.py`（按「是否营销可用」筛出子集，
解析失败/缺失默认保留；**占比超 5% 时报错**，需先跑补标注闭环 `retry_missing.py --task c0`，
**最多自动重试 3 轮**，第 4 次直接拒绝）

**第二批（并发，只跑筛选后子集）**：
- **R1~R5**（平台玩法 / 商业合作 / 风险预警 / 营销发现 / 消费者行为）：DataHub，各自独立 Prompt，均依赖第一批 + 筛选完成
- **C2 事件合并**：`blueai-canonical-event-registry` skill，输入为筛选后子集

任一路失败不影响同批其余路，单独报告错误。第二批全部完成后触发 D 合并（七路 LEFT JOIN）。

> ⚠️ **系统性故障熔断**：同一批内自愈重试后仍失败的任务 ≥ 2 个时，抛 `RuntimeError` 停止批次 ——
> 因为 ≥2 个独立任务同时失败大概率有共同根因（API Key 失效 / 网关故障），连「看起来成功」的
> 结果都值得怀疑。0~1 个失败不阻塞，标记缺失后其他路照常继续。

### Phase E 四路并发

E1 行业及热门话题 / E2 营销节点 / E3 平台新鲜事 / E4 营销发现

各路输出 `06_洞察/v{N}/e{N}_v{N}.md`（`N` = 本轮版本号）。四路全部完成后触发 F 合并发布。

> E4 内部实际是**两次串行调用**：先打标（类型分类 + 取舍判断），过滤后再撰写。
> 这只影响 E4 自己的耗时，不影响另外三路并发。口径见 `ks/05_四版块统计口径.md`。

---

## 关键引用路径

| 资源 | 路径 |
|---|---|
| 热度基准 JSON | `skill/fetch-normalize/references/平台热度基准_2026.json` |
| 节点日历 CSV（阶段 A/B 用） | `skill/fetch-normalize/references/marketing_calendar_2026.csv` |
| C0/R1~R5/C3 Prompt + 脚本 | `skill/annotation/`（详见其 `SKILL.md`） |
| E1~E4 统计脚本 + Prompt | `skill/insight/`（详见其 `SKILL.md`） |
| 四版块统计口径 | `ks/05_四版块统计口径.md` |
| 报告结构 | `ks/07_报告结构.md` |
| 写作规范 | `ks/06_表达规范.md` |
| 状态机字段与值域 | `prompt/run_config契约.md` |
| 环境变量与运行前置 | `prompt/运行前置.md` |

---

## 成本记录节点

每个 LLM 任务完成后立即调用（Phase C 由 `skill/annotation/scripts/datahub_poll.py` 自动触发，
Phase E 由 `skill/insight/pipeline_e.py` 自动触发，均无需手动执行）：

```bash
python tool/cost-tracker/cost_tracker.py \
  --project-dir "Projects/{PROJECT_DIR}" \
  --phase {阶段} --task {任务名} ...
```

各阶段具体参数见 `prompt/02`~`prompt/08` 内的「成本记录」节。`prompt/09`（Phase H）不涉及 LLM 调用，不记成本。
`{PROJECT_DIR}` 格式只在 `prompt/run_config契约.md` 定义一次。
