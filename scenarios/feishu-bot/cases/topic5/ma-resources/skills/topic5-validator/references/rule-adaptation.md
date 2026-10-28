# Topic5 Validator 规则适配表

版本：`topic5-validator-qc-v1.6`  
来源：客户 `Topic5_营销活动复盘检查清单.md` v1.5。  
判定层：`deterministic`=脚本全量检查；`semantic`=独立 Validator Agent 全文语义检查；
`post_publish`=飞书文档创建后检查；`human_authority`=只能由用户确认。

当前项目口径优先于客户旧规则：NSR 默认含中性；禁用“人均”；竞品分析下线；图片使用飞书
原生 media，不要求 CDN。标记 `adapted` 的规则按“当前规则”执行，不照抄旧条件。

| ID | 状态 | 判定层 | 当前规则 |
|---|---|---|---|
| 1.1 | applicable | deterministic+semantic | `content_scope!=all` 时不得输出 PGC/UGC 对比块 |
| 1.2 | applicable | deterministic+semantic | 有产品关键词时必须呈现品牌→产品的递进提及漏斗 |
| 1.3 | applicable | deterministic | 活动章节数、名称与冻结需求一致 |
| 1.4 | not_applicable | deterministic | 竞品 SOV 已下线；报告出现竞品分析反而阻断 |
| 1.5 | adapted | deterministic | 3.1 基准期对比为标配；基准数据不可用时必须披露 DataIssue |
| 1.8 | adapted | deterministic | 单平台时平台饼图可省略并改文字，不判缺图 |
| 2.1 | applicable | semantic | 品牌情感只回指受控统计源 `sentiment_dist` |
| 2.2 | applicable | semantic | 活动情感只回指 AI 标注聚合 |
| 2.3 | applicable | deterministic+semantic | 漏斗各层来自同一活动相关数据集且逐层嵌套 |
| 2.4 | adapted | deterministic+semantic | 分维 NSR 分母按参数执行，当前默认含中性；不得套用旧“不含中性”公式 |
| 2.5 | applicable | deterministic+semantic | UGC 类别占比分母是活动相关 UGC |
| 2.6 | adapted | deterministic+semantic | 禁用“人均互动”；达人效能使用条均互动量或明确的总量分布 |
| 2.7 | applicable | deterministic+semantic | 分维样本 `<30` 必须标“样本不足”，不得输出确定性结论 |
| 2.8 | applicable | semantic | 峰值归因必须有帖子聚类证据 |
| 3.1 | applicable | semantic | 议题解读只做归纳，不逐条泄漏原帖 |
| 3.1b | applicable | semantic | 广告、地理定位等噪音词不得作为正式议题 |
| 3.1c | applicable | deterministic+semantic | 关键词行内列示；解读为一段连贯叙事，不输出内部结构 |
| 3.2 | applicable | semantic | 主观评价必须有明确数据证据 |
| 3.3 | applicable | semantic | 有负面样本时必须正视并给有边界的建议 |
| 3.4 | applicable | deterministic+semantic | 基准期对比只表述相关变化，不写活动导致增长 |
| 3.5 | applicable | semantic | 节假日高基数且差异显著时披露不可直接比较 |
| 3.6 | applicable | semantic | 品牌整体情感与活动分维 NSR 不做绝对值横比 |
| 3.7 | applicable | semantic | 经验总结逐条回指章节和数字 |
| 4.1 | applicable | deterministic+semantic | 峰值日期与归因事件日期一致 |
| 4.2 | applicable | deterministic | 品牌提及率等比例与统计产物分子分母一致 |
| 4.3 | applicable | deterministic | 产品提及是品牌提及子集，占比不得超过 100% |
| 4.4 | applicable | deterministic | 达人层级占比在适用集合内闭合，否则说明未知项 |
| 5.1 | applicable | deterministic | 标题、洞察、正文、图表、表格顺序符合模板 |
| 5.2 | applicable | post_publish+human_authority | 多平台时传播概况两图使用 50:50 grid；单平台例外 |
| 5.3 | applicable | post_publish+human_authority | 词云图左、议题解读右，使用 50:50 grid |
| 5.3b | applicable | deterministic | 多平台词云右栏带平台标题 |
| 5.4 | applicable | post_publish+human_authority | 正负观点使用双栏，不用宽表 |
| 5.4b | applicable | post_publish+human_authority | 情感图与数据表使用左图右表 |
| 5.4c | applicable | post_publish+human_authority | 漏斗图与数据表使用左图右表 |
| 5.5 | applicable | deterministic | 整体洞察位于标题之后、首章之前 |
| 5.6 | applicable | post_publish+human_authority | 洞察使用飞书 callout，不用 blockquote 冒充 |
| 5.7 | applicable | deterministic | 五个固定章节和每活动 2.N.1~2.N.5 齐全 |
| 5.8 | adapted | deterministic | 3.1 基准期对比必做或披露不可用；3.2 竞品章节不得出现 |
| 5.9 | applicable | deterministic+semantic | 经验总结含成功经验、待改进问题、行动计划 |
| 5.10 | applicable | deterministic | 上游有 KOL/UGC 数据时输出对应模块 |
| 5.11 | applicable | deterministic | 2.N.4 各子模块顺序为分析文字→图→表 |
| 5.12 | applicable | deterministic | UGC 分布饼图后不重复输出同数据表 |
| 6.1 | applicable | deterministic+visual | 图表配色遵守品牌/情感/内容类型规范；单平台例外 |
| 6.2 | adapted | deterministic+post_publish | 内容层允许受控本地 PNG；飞书交付必须转为 media，不得残留本地路径 |
| 6.3 | applicable | deterministic | 不得有 TODO、待补充、空白章节或占位符 |
| 6.4 | applicable | deterministic+semantic | 不泄漏字段名、脚本/Skill/Agent 名、版本和布局指令 |
| 6.5 | applicable | deterministic | 同一指标在洞察、正文、表格与图表间一致 |
| 7.1 | applicable | deterministic | 启用模块要求的图表文件齐全且非空 |
| 7.2 | adapted | deterministic | 不要求旧文件名；对应数据必须存在于版本化 Artifact |
| 7.3 | adapted | post_publish | 不要求 CDN；飞书 media 必须可回读且图片块有效 |
| 7.4 | applicable | post_publish | 文档必须完成所有权转移 |
| 8.1 | applicable | deterministic+semantic | UGC 个人账号名、手机号、地址不得出现；PGC 公开机构/KOL可展示 |
| 8.2 | applicable | semantic | 不对竞品做无数据支撑的负面定性；当前竞品模块下线 |
| 8.3 | applicable | deterministic+semantic | `<30` 样本只作探索性描述并明确披露 |

## 审核边界

- G1：Validator 只做字段、日期、文件与能力预检；需求意图由用户确认。
- G2：Validator 只做方案完整性、资源与口径预检；方案取舍由用户确认。
- N10：上述策略条件全部满足时自动确认；动态类别、降级、重复、宽泛或证据不足时转人工。
- G3：独立 `topic5-validator` Agent 自动执行；`p0_count==0` 自动放行，P0 自动返修。
- G4：发布后 Validator 检查布局、图片、所有权、链接和 revision；最终接受仍由用户确认。
- HC7 / USER_ESCALATE：异常与超限裁决始终保留人工。
