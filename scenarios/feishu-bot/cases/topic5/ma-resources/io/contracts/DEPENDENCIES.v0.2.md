# Topic5 v0.2 执行依赖清单（待搜集）

> 用途：把 `pipeline.v0.2.yaml` 从「本地可跑的 stub 骨架」推到「逐节点接真」所缺的依赖。
> 分四类：**A 已就绪**（不用找）· **B 需搜集**（你去收集的真缺口）· **C 等上线**（控制面，非搜集）· **D 待拍板**（口径决策，非搜集）。
> 现状口径：`已就绪`=本地有实体可直接接；`待迁入`=源项目有、需审计搬进 MA；`缺失`=磁盘无实体，必须去搜集。

---

## A. 已就绪 / 待迁入（无需外部搜集，审计后即可接真）

| 依赖 | 用途 | 解锁节点 | 现状 | 本地位置 |
|---|---|---|---|---|
| `step1_clean.py` | 活动 Excel 清洗、派生平台/KOL 层级 | n07_clean | 已就绪 | 源 `test_run/step1_clean.py` |
| `blueai-stats-aggregation-skill` | 分对象统计聚合（趋势/互动/情感/TopN/KOL/UGC/NSR） | n12_aggregate | 已就绪 | 源 `skills/` |
| `blueai-phase-ugc-discovery-skill` + `phase_discovery.py` | UGC 参与方式动态发现 | n08_ugc_discovery | 待迁入 | 源 `skills/` + `test_run/` |
| `step2_round1_annotate.py` | 轮次一标注（情感+活动相关） | n09_annotate_r1 | 待迁入 | 源 `test_run/` |
| `blueai-opinion-discovery-skill` | 生成观点候选（供 HC 确认） | n10_hc_opinion | 待迁入 | 源 `skills/` |
| `step2_round2_annotate.py` | 轮次二标注（观点/UGC/维度） | n11_annotate_r2 | 待迁入 | 源 `test_run/` |
| `blueai-datahub-skill` | ≥1000 条批量标注入口（SDK `sdk/client.py`） | n09/n11 | 已接真（波次5 去敏后迁入 `ma-resources/skills/`，`annotate` handler 换真+路由+确定性降级） | `skills/blueai-datahub-skill/`（**凭据走环境变量 `DATAHUB_API_KEY`，`.env` 不入库**）；适配层 `pipeline-orchestrator/datahub.py`（批量 CSV/上传/建任务/轮询/行级解析）+ `pipeline-orchestrator/annotate.py`（轮次一情感+相关、轮次二三任务+覆盖检查、阈值路由小引擎/DataHub） |
| `blueai-wordcloud-insight-skill` | 词云三步解读 | n15_wordcloud_insight | 待迁入 | 源 `skills/` |
| KS 报告包（13 份） | 模板/写作/图表/指标/质检/飞书/异常规范 | n16/n17/n18/n19 | 待迁入 | 源 `KS/report_pack/` |
| `build_table_with_hc.py` + `feishu-doc-writer` | 飞书布局层 XML + 文档创建 + 导出后回读核对 | n20_delivery_draft | 待迁入 | 源根 + 待迁入 |
| 覆盖检查脚本 | 轮次二观点/UGC/维度三类标注覆盖率核对 | n11_annotate_r2 | 待开发（本次口径新增） | T5 新增脚本 |
| 拉升对比脚本 | 基准期 vs 活动期拉升、3.1 日均对比 | n06_brand_baseline / n16 | 待开发（本次口径新增） | T5 新增脚本 |
| 内容统计脚本 | 活动相关内容计数与活动层分母口径 | n12_aggregate | 待开发（本次口径新增） | T5 新增脚本 |
| **SSJH-Social-Stat** 调用入口 | 品牌活动期/基准期声量聚合、词云的权威数据源（竞品 SOV 已下线 2026-10-21） | n05_brand_pull · n06_brand_baseline · n13_wordcloud_pull | 已接真（波次3 去敏后迁入 `ma-resources/skills/`，`ssjh_pull` handler 换真+确定性降级） | `skills/SSJH-Social-Stat/`（`skill.py`+`scripts/run.py`+`export_excel.py`；**凭据走环境变量 `SSJH_API_TOKEN`，`config.json` 不入库**）；适配层 `pipeline-orchestrator/ssjh.py` |
| **封面生成 skill** `report-cover-skill` | 报告封面 | n14_charts | 已接真（波次4 去敏后迁入 `ma-resources/skills/`） | `skills/report-cover-skill/scripts/generate_cover_gpt.py`（**凭据走环境变量 `REPORT_COVER_API_KEY`，原硬编码 relay key 已移除、`.env` 不入库**） |
| **图表 skill** `blueai-yuqing-echarts-skill-new` | 按 chart spec 生成全部 ECharts 图表（SSR+resvg，零浏览器依赖） | n14_charts | 已接真（波次4 迁入源码，`node_modules` 不入库、仅 MA Linux 沙箱安装） | `skills/blueai-yuqing-echarts-skill-new/scripts/render_from_spec.js` / `render_chart.js`；适配层 `pipeline-orchestrator/charts.py`（确定性 `build_chart_specs` + 真渲染上传 + 降级占位） |

---

## B. 需搜集（磁盘无实体，接真的硬缺口）★ 你的搜集重点

> 口径更新 2026-10-23：SSJH、封面和图表均已接真。图表产本地 PNG，由 n20 上传飞书 media，不再需要外部对象存储上传器。

| # | 依赖 | 用途 | 解锁节点 | 为什么卡住 | 去哪搜集 |
|---|---|---|---|---|---|
| B5 | **`validate_handoff.py` + handoff schema** | 交回 Supervisor 的闭环校验 | n24_handoff | 无实体 | Supervisor 交接规范/脚本 |

> n14 图表接真已完成：图表 `blueai-yuqing-echarts-skill-new` + 封面 `report-cover-skill` 在仓，`charts.py` 本地渲染 PNG；n20 经飞书 `drive/v1/medias/upload_all` 原生上传进文档 image block。

---

## C. 等上线（控制面，非搜集项）

| 依赖 | 用途 | 影响节点 | 现状 |
|---|---|---|---|
| **Blueflare-Ada 控制面** | Run 初始化/资源锁/上下文快照、交付提交/发布后检查、checkpoint 源 | n04_run_init · n23_delivery_commit | 未上线。本地 stub 执行并保留接入缝，上线后换真即可，无需你去搜集 |

---

## D. 待拍板（口径决策，非搜集项）

这些是 `params` 里标「待裁决」的业务口径，不影响跑通，但接真前需你/业务方定。

| 参数 | 含义 | 候选 | 影响 |
|---|---|---|---|
| `ugc_category_mode` | UGC 参与方式分类 | `dynamic` 动态发现 / `fixed_four` 固定四类 | n08 是否锁死类别 |
| `nsr_denominator_includes_neutral` | NSR 分母是否含中性 | `true` / `false` | n12 的 NSR 口径 |
| `report_chapters` | 报告结构 | `five` 五部分 / `three` 三章 | n16 报告骨架 |

---

## 搜集优先级建议

1. **B5 handoff** — 当前唯一磁盘无实体的硬缺口，只卡末端闭环 n24，可最后补。
2. **A 类迁入** — 不用外部搜集，审计即可（UGC发现/观点/聚合/词云解读/KS 包/飞书），建议并行推进。
3. **D 口径** — 找业务方确认，和搜集无依赖关系，可随时定。

> SSJH / 图表 / 封面均已在仓；n14 与数据源已接真，执行前需验证现有凭证和配额。
