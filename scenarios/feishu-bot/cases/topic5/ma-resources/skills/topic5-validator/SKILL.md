# topic5-validator

报告质检关卡（G3 / n17_validate）。

营销活动复盘报告的**独立验证**技能：对 n16 产出的内容层报告（`report_md`）按活动复盘
口径红线做质检，产出分级 `qc_report`，由 `n18_qc_gate` 按 `pass_policy=p0_count==0` 路由。

> **独立性边界**：Validator 以独立 Session/身份运行，不并入生产撰写身份；只读报告与证据
> Artifact，不改报告正文、不写飞书。质检通过后若有人改数字，必须回 n17 复检再重新导出，
> 不在 pass 后静默改数。

## 两段式审计

| 段 | 责任主体 | 范围 | 产物 |
|----|----------|------|------|
| ① 全量扫描 | `scripts/scan_report.py`（确定性脚本） | 需求范围、章节、占位符、内部泄漏、漏斗范围、小样本披露、图表文件、隐私正则等机器可查规则 | `qc_report`（分级 issues + p0_count） |
| ② 全文语义检查 | Validator Agent（本技能清单驱动） | 数据来源、数字溯源、证据充分性、议题质量、主观评价、隐私与跨章节逻辑 | 逐项结果合并进同一 issues |
| ③ 发布后检查 | 编排器 + Validator | 飞书 grid/callout/media、所有权、链接、revision 绑定 | `delivery_draft.readback` / `delivery_record` |

Agent 以脚本产物为基线，再抽样复核语义层；两段合并为最终 `qc_report`。

## qc_report 契约（与 handlers.qc_decision 对齐）

```json
{
  "has_p0": false,
  "p0_count": 0,
  "p1_count": 1,
  "issues": [{"id": "...", "severity": "P0|P1|P2", "title": "...", "evidence": "...", "locus": "L12"}],
  "scan_mode": "full_scan"
}
```

- `n18_qc_gate` 读 `has_p0`：有 P0 → `has_p0_retry`（n19_fix 修复后版本递增复检）；
  超 `qc_max_rounds`（默认 3）→ `has_p0_maxed`（人工升级）；无 P0 → `no_p0`（放行 n20 交付）。
- `pass_policy = p0_count == 0`。

## 规则事实源

完整客户规则及当前架构适配见
[`references/rule-adaptation.md`](references/rule-adaptation.md)。每条规则都标记为
`applicable`、`adapted` 或 `not_applicable`，并指定 `deterministic`、`semantic`、
`post_publish` 或 `human_authority` 判定层。

关键适配：

- NSR 默认含中性，以 DAG 参数 `nsr_denominator_includes_neutral=true` 为准。
- 禁用“人均”；达人效能使用条均互动量或明确总量分布。
- 竞品分析已下线，报告出现竞品章节反而阻断。
- 内容层允许受控本地 PNG，飞书交付后必须转成原生 media，不要求 CDN。

## 分级口径

- **P0 禁发**（拦截交付）：禁用措辞「人均/转化率/转化/性价比」；多活动相加「合计声量/总声量」
  （同行含「不相加/逐个」等免责声明则不算违规）；报告为空；缺核心章节（品牌传播/活动传播/
  综合定位（容忍「综合表现定位」变体）/经验总结）；缺品牌 1.1-1.4 或任一活动
  2.N.1-2.N.5 固定子章节。
- **P1 可披露放行**（记录不拦截）：图表引用缺失、内容偏短、来源含降级产物。
- **P2 提示**：风格/措辞建议。

## 全文语义清单（Agent 段）

1. **数字溯源**：抽样报告中每个指标数字，核对是否出现在 `stats_bundle`；编造即 P0。
2. **指标白名单**：活动层指标名只用【条均互动量/品牌提及率/产品提及占比/NSR】。
3. **提及漏斗嵌套**：产品提及是品牌提及子集，产品提及占比 ≤ 1；越界即 P0。
4. **NSR 口径**：分母默认含中性（`nsr_denominator_includes_neutral=true`）；核对计算一致。
5. **逐活动不相加**：多活动分别统计，不得合计为总声量。
6. **证据充分性**：根因/结论须有数据支撑，证据不足应说明而非硬凑。
7. **内容质量**：议题不得泄漏原帖或混入噪音；主观评价、峰值归因和经验总结必须有证据。
8. **隐私与合规**：UGC 个人账号名、手机号、地址不得出现；PGC 公开机构/KOL账号可展示。
9. **跨章节逻辑**：趋势日期、分子分母、洞察数字和行动建议必须与统计产物一致。

## 用法

```bash
python3 scripts/scan_report.py \
  --report-md <run_dir>/artifacts/report_md/vN/payload.json \
  --requirement <run_dir>/artifacts/requirement_confirmed/vN/payload.json \
  --stats <run_dir>/artifacts/stats_bundle/vN/payload.json \
  --charts <run_dir>/artifacts/chart_urls/vN/payload.json \
  --rules /mnt/skills/topic5-validator/references/rule-adaptation.md \
  --task-id <task_id> --run-id <run_id> --attempt-id <validator_attempt_id> \
  --validator-version <mounted_skill_version> \
  --execution-source <independent_session_or_execution_id> \
  --out <run_dir>/validation/qc_report.json
```

脚本零三方依赖（标准库）。输出 `qc_report.json` 即 n17_validate 关卡注入的人工产物。
