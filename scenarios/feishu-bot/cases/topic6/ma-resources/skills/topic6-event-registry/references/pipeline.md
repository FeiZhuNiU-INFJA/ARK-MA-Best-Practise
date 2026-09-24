# 流程与数据协议

按需跳读，不必通读：

| 想知道什么 | 去哪节 |
|---|---|
| 整体有几步、跨平台在哪分叉 | 全景 |
| 每步跑完该检查什么、不过怎么办 | 阶段门禁 |
| 某一步读什么写什么、字段含义 | ⓿ ① ② ③ ④ ⑤ ⑥ ⑦ ⑧ ⑨ 各节 |
| 跨平台那五步的数据协议与顺序约束 | Ⓧ 跨平台后处理层 |
| 哪些检查是脚本做而不是模型做 | 本地必须做的校验 |
| 并发、断点续跑、JSON 失败怎么处理 | 并发、续跑、成本 |
| 花多少钱、跑多久 | 成本与耗时 |
| 目录里每个文件是什么 | 运行目录结构 |

## 全景

**默认一个平台一个运行目录。** 需要跨平台合并时，各平台跑到 ④ 为止，再走可选的 Ⓧ 层
（x0~x4）。原先「跨平台增量不值得」的依据是 382 条测试集上 80% 多成员事件只落单一平台，
但那份测试集有 3 个簇被 8 倍过采样；3545 条全量的真实占比是 **56.1% 跨平台**，
结论已被推翻。要不要合并现在是业务选择，不是技术结论。

```
输入 CSV（record_id / title / platform / 热度列）
    ↓ ⓿ 平台标题清洗      逐条，可并发    00_clean_titles.py
clean_title（平台包装已剥掉，只删不加）
    ↓ ① 事件性判定        逐条，可并发    01_eventness.py
EVENT / NON_EVENT / UNCERTAIN
    ↓ ② Event Frame 抽取  逐条，可并发    02_extract_frames.py
    ↓ ③ Entity 归一        串行           03_normalize_entities.py
    ↓ ④ Embedding          批量           04_build_embeddings.py
    ↓ ⑤ 多路召回           本地           05_recall_candidates.py
    ↓ ⑥ 有界分块           本地           06_build_blocks.py
    ↓ ⑦ 块内全景归档       一块一次调用，块间并发   07_block_archive.py
    ↓ ⑧ 热度排序           本地           08_rank_events.py
    ↓ ⑨ 提交               本地           09_commit_registry.py

跨平台可选层（在 ④ 之后分叉，替代 ⑧⑨）：
    ④ ×N 个平台 → x0 合库 → ⑤（--top-k 60）⑥⑦
              → x2 置信度筛查 → x3 双向复核 → x4 出明细表

    x1 失败块重拆不在常规路径上：⑦ 现在会自己重试并在块内二分，
    只有它仍报「有 N 个块失败」时才需要 x1 + 重跑 ⑦。
```

跨窗口增量在 ④ 之后插入 `00_seed_from_registry.py`，并给 ⑦ 传 `--prior-events`。
置信度校准用 `10_build_calibration_sample.py` / `11_score_calibration.py`。

数据流全部落盘在 `work/`，产物在 `out/`，每批次原始模型返回在 `raw/`。
每阶段可独立重跑，不重跑上游。

**原始 CSV 只在阶段 ⓿ 出现一次。** 热度、平台、原标题都由 ⓿ 带进流水线，
下游任何阶段都不回读 CSV——多一个入口就多一种「这一趟到底用的是哪份输入」查不清的可能。

**平台差异只在 ⓿ 这一层。** ① 往后全流程共用一套标准提示词，不按平台分叉。

---

## 阶段门禁

不过就停下修，不要带着问题往下跑。

| 阶段 | 门禁 | 不过怎么办 |
|---|---|---|
| ⓿ | 输入 ID 全覆盖且唯一；`reverted` 比例应接近 0；**原标题与 clean_title 逐条人工核对** | reverted 高说明清洗规则在教模型改写而不是删包装，改提示词重跑 |
| ① | 输入 ID 全覆盖且唯一；`NON_EVENT` 抽检确认无误删具名活动 / 在映在更新作品的剧情讨论 / 事件解读 | 补跑缺失批次；误删多则换更强模型或改提示词，禁止直接放行 |
| ② | `actor` 或 `action` 至少有一项，覆盖率应 ≥90% | 覆盖率异常低说明输入过短或提示词有问题，先查再跑 |
| ③ | 每个 actor 名称都有 `actor_id` | 未解析的自成实体，不丢 |
| ④ | 向量数 = Frame 数 | 缺失的重新 embed，不用零向量填 |
| ⑤ | 每条记录都有候选列表（可为空）；`rejected` 必须落盘 | 候选全空的记录会独立成块 |
| ⑥ | 每条记录恰好在一个块；块的预估输出不超预算 | 超预算的块自动封顶，不再吸收新成员 |
| ⑦ | 每个输入 ID 恰好出现一次（事件成员或 singletons）；事件名无空词 | 漏返的本地补为单条；含空词的事件**先重命名一次，仍不合格才拆成单条** |
| ⑧ | 热度能 join 上；运行目录内只有一个平台 | 热度缺失的记录不参与百分位计算；混了多平台会打印告警，百分位不可比 |
| ⑨ | 多成员事件有名称与描述；无未拆分的空词事件 | 门禁不过拒绝提交 |

---

## ⓿ 平台标题清洗

`work/clean_titles.jsonl`，模型输出：

```json
{"records":[{
  "record_id": "R001",
  "clean_title": "8月10日沈腾主演电影《欢迎来龙餐馆》总票房预测值大幅提升至35.4亿人民币",
  "ops": ["STRIP_QUESTION_SHELL"]
}]}
```

落盘时补上本地校验结果 `flags` 与 `reverted`，并带上 `title`（原标题）、`platform`、`heat`。

`heat` 由 `parse_hot` 解析，支持 `12.5万` / `3亿` / `500k` / `2.5M` / `280万热度` 这些写法。
**解析不了的返 None，绝不当 0** ——当 0 会把当周最热的记录排到最后，而排序照样出得来、
下游看不出错；标成缺失则 ⑧ 会把它排除在百分位之外并计入 `heat_missing`，是可见的失败。
整列都解析不了通常意味着 `--heat-col` 指错了列，stderr 会打出前 5 个原始值。

**唯一的硬约束是只删不加。** 本地校验只查「加」不查「删」：新增数字（`NEW_NUMBER`）、
新增汉字（`NEW_CHAR`）、**新增顿号（`NEW_SEPARATOR`）**、空结果（`EMPTY`）一律回退
原标题并标 `reverted=True`。`LONGER` 只是提示不回退。

`NEW_SEPARATOR` 是后加的：顿号不是汉字，躲过了 `NEW_CHAR`，但**加顿号是在断言一层
原文没有的并列关系**。微博实测 159 次分隔符归一里 109 次（69%）把「主体 + 事项」
误判成并列（`库克 华为` → `库克、华为`）。

理由不对称：漏删一点包装无害，下游模型看得懂；而清洗补进原文没有的信息，
等于把编造前移到最早的环节，下游任何阶段都查不出来。

四个平台的包装分布（各 100 条实测）：

| 平台 | 平均长度 | 主要包装 | 默认动作 |
|---|---|---|---|
| 知乎 | 36 字 | 提问外壳 100、书名号 44 | 必剥壳 |
| B站 | 23 字 | 方括号标签 30、书名号 31、空格分词 13、提问外壳 8、期数 7、emoji 5、竖线 3 | 分标签类型处理 |
| 微博 | 9 字 | 空格分词 18，大量裸话题词 | 只规范分隔符 |
| 抖音 | 11 字 | 提问外壳 1 | 默认原样返回 |

B站的方括号要分两类：`【bilibilionly】` 是 UP 主标记该删，`【无神怜爱的雪国】`
是任务正式名称必须留。规则里给了这两个实例作为对照。

微博的裸话题词（`龙餐馆`）原样留着，它会在 ① 被判成非事件，这是正确结果。
**给它补谓语补背景就是造假。**

`ops` 是清洗动作的枚举落盘，用来统计规则是否按预期触发，也用来定位「该删没删」
和「不该动却动了」两类问题。

---

## ① 事件性判定

输入是 `clean_title`，不是原标题。**本阶段只判定不改写**——剥壳已经在 ⓿ 做完。

`work/eventness.jsonl`，模型输出：

```json
{"records":[{
  "record_id": "R001",
  "eventness": "EVENT | NON_EVENT | UNCERTAIN",
  "reason_code": "SPECIFIC_STATE_CHANGE",
  "confidence": 0.0
}]}
```

`reason_code` 枚举：

| code | 含义 |
|---|---|
| `SPECIFIC_STATE_CHANGE` | 明确的状态变化 |
| `OFFICIAL_ACTION` | 官方动作、回应、通报 |
| `RELEASE_OR_LAUNCH` | 作品上线、产品发布 |
| `NAMED_ACTIVITY` | 具名活动、赛事、营销活动 |
| `PUBLIC_DISPUTE` | 公共争议 |
| `EVENT_COMMENTARY` | 对具体事件的解读讨论 |
| `GENERIC_KNOWLEDGE` | 科普、教程、攻略 |
| `OPINION_SOLICIT` | 纯观点征集 |
| `EMOTION_OR_DISPLAY` | 泛情绪、审美展示、个人日常 |
| `ENTITY_ONLY` | 只有主体、无具体动作 |
| `TRUNCATED` | 残缺无法恢复语义 |

**单成员事件的名称直接沿用 `clean_title`**，所以 ⓿ 的产物质量直接决定这部分产出。

`confidence` **只落盘，不参与任何路由**。

### 一条容易搞错的判定规则

「虚构作品内部设定讨论 → NON_EVENT」**有严格的适用前提：作品是完结多年的经典老作品。**

当代作品正在上映、上线、更新、重映期间，观众讨论它的剧情、人物、细节、伏笔，
这些讨论本身就是该作品这一轮传播的组成部分 → **EVENT + EVENT_COMMENTARY**。

踩过的坑：给知乎单独写判定提示词时漏了这个前提，12 条在映影片的剧情讨论
（`《欢迎来龙餐馆》中那枚发芽的土豆意味着什么`、`老扎为什么没有偷偷放走徐福`）
被判成 NON_EVENT 直接丢弃——丢掉的恰好是营销上最有价值的那批素材。

分不清是老经典还是正在传播的当代作品，判 UNCERTAIN，不要判 NON_EVENT。

**必须用强模型。** 实测廉价模型给出的 1112 条 `NON_EVENT + 高置信` 里 547 条（49%）被强模型翻案，误删集中在具名营销活动、具名作品、事件解读。这一层判错会导致下游整块缺失。

---

## ② Event Frame

`work/frames.jsonl`：

```json
{"frames":[{
  "record_id": "R001",
  "actor": [{"name": "龙餐馆", "type": "WORK"}],
  "action": "票房预测上调",
  "object": "票房预测",
  "event_type": "BOX_OFFICE",
  "parent_hint": "龙餐馆票房表现",
  "stage": "UPDATE",
  "time": null,
  "location": null,
  "key_facts": {"predicted_box_office": "35.4亿"}
}]}
```

`actor.type`：`PERSON | GROUP | ORG | BRAND | WORK | PRODUCT | PLACE | EVENT_ENTITY | OTHER`

`stage`：`ANNOUNCEMENT | TICKETING | LAUNCH | ONGOING | UPDATE | RESPONSE | AFTERMATH | DISPUTE | RESULT`

**不强制 `time` / `location`。** 只有标题时实测填充率 6% / 9%，强制要求只会诱导编造。

`parent_hint` 实测填充率 **97.9%**——同一个信息，问成「这条属于哪个更大的事」模型就能答，问成分类标签就答不出。提问方式决定可用性。

Frame 在本流程里的作用是**为分块提供召回信号**（actor / object / parent_hint / embedding 文本），不直接决定事件划分。

`information_score` 由代码算，不让模型打分：

```python
score = (3 * bool(actor) + 3 * bool(action) + 2 * bool(object)
         + len(actor) + 2 * bool(numeric_facts)
         + 2 * bool(work_name_in_title) + min(len(title) // 10, 3))
```

只用于挑块内锚点记录，不参与合并判断。

---

## ③ Entity 归一

`work/entities.jsonl`：

```json
{"entity_id":"ENT002","canonical_name":"王俊凯","type":"PERSON",
 "parent_entity_id":"ENT001","aliases":["小凯"],"source_records":["R012"]}
```

解析后写 `work/frames_resolved.jsonl`，Frame 的 actor 带上 `actor_id` / `parent_entity_id`。

**同一 `parent_entity_id` 只构成实体谱系关系，绝不能据此判定同一事件。** 这是「同组合不同成员」的正确解法：靠谱系表达，不为每个团体写规则。

注册表逐批累积，**必须串行**——本批判定要看到前面建立的实体。

---

## ④ Embedding

`work/embeddings.json`，带 `model` / `dim` / `count` / `vectors` / `texts`，可复用缓存。

默认 embed 的是 Frame 拼装文本：

```
主体：龙餐馆
动作：票房预测上调
对象：票房预测
类型：BOX_OFFICE
父事件线索：龙餐馆票房表现
标题：8月10日《欢迎来龙餐馆》总票房预测值提升至35.4亿
```

`--text-source clean_title` 切成只 embed 清洗后的标题。**这是「清洗能否替代 Frame」的
对照开关**：两种模式读同一份 `frames.jsonl`，其余阶段完全不动，所以召回率差异只归因于
文本来源这一处。文本来源会写进 `embeddings.json` 与 `run_manifest.json`。

为什么需要这个对照：Frame 三通道（parent_hint / actor 精确 / object 精确）对参考同事件对的
**独占贡献只有 5.2%**，而 embedding 单通道覆盖 71.8%、独占 15.2%。Frame 抽取又是最大单笔
成本（2805 条实测 $7.52）。在没有清洗时脏标题直接 embed 效果差，有了清洗则未必——
**这件事只能实测，不能推断。这个对照至今没跑过。**

**注意这个对照只回答召回问题。** Frame 还喂着锚点兜底（`07` 的 `pick_specific_entity`
在 `frames[rid]["actor"]` 上迭代）和 `identity_core`。原先还列了「跨窗口的 actor_ids
交集召回」，**但那条链已经断了**——`actor_ids` 从来没被写进 `identity_core`，
详见 [open-questions.md](open-questions.md) 第 1、2 条。

**吞吐由模型选择决定，不由 `--batch-size` 或 `--concurrency` 决定。** 默认模型是
`Doubao-embedding`（50.8 条/秒）；换成要出海的 `text-embedding-3-small` 会掉到
1~2.5 条/秒。而同一个模型调批量和并发几乎无效，四组对照见
[benchmarks.md](benchmarks.md)。`--concurrency` 只用来避免「全串行」这个最坏情况。

Embedding 只产出候选，**分数不决定任何关系**。

---

## ⑤ 多路召回

`work/candidates.jsonl`：

```json
{"record_id":"R001",
 "candidates":[{"target_id":"R017","target_type":"record",
                "channels":["embedding","actor_exact"],
                "embedding_score":0.716,"rank":1}],
 "rejected":[{"target_id":"R099","embedding_score":0.21,"reason":"无召回信号命中"}]}
```

`channels`：`embedding | actor_exact | object_exact | parent_hint_match`

**用 Top-K 不用阈值。** 阈值拒绝候选，Top-K 保证候选。实测参考同事件对的相似度中位数 0.770、最低 0.537——`老王探班龙餐馆vlog` 与主事件只有 0.513，在 0.55 阈值下被拒，Top-10 下必然出现。

各通道对最终合并的贡献（547 个参考同事件对，来源见 ⑥ 的说明）：

| 通道 | 覆盖 | 仅靠它命中（去掉就漏并） |
|---|---|---|
| embedding | 71.8% | **15.2%** |
| parent_hint_match | 46.4% | 1.5% |
| actor_exact | 40.2% | 3.7% |
| object_exact | 4.9% | 0 |

**必须落 `rejected`。** 静默否决是漏并唯一查不到痕迹的地方。

---

## ⑥ 有界分块

`work/blocks.jsonl`：

```json
{"block_id":"BLK0004","record_ids":["R001","R017"],"record_count":60,
 "est_input_tokens":4100,"est_output_tokens":5400,
 "top_actors":["《欢迎来龙餐馆》","沈腾"]}
```

**分块只需要是超集，不需要是答案。** 这是整套设计的支点：

- 聚类要精度：错一条边就错一次合并
- 分块只要召回 + 有界：多塞无关记录只是让模型看到噪声，而模型对噪声的拒绝率实测 86.6%

所以这里**可以**用传递闭包。图算法不得产出结论，但可以用来划定工作范围——传递性在这里是优点，它保证同主体不被切开。

实测（382 条）：

| 策略 | 块数 | 最大块 | 最大输入 tokens | 参考同事件对召回 |
|---|---|---|---|---|
| 全通道 cap60 | 14 | 60 | 3800 | **100.0%** |
| 全通道 cap120 | 10 | 120 | 7586 | 100.0% |
| 仅精确通道 cap120 | 152 | 116 | 6534 | 99.3% |

547 对参考同事件，cap60 下一对都没被切开。

**这 547 对是上一版批次路线的输出，不是人工标注。** 它给分块层设了下界
（没切开已被合并的对），但不构成合并精度的证据。

**规模上限按预估输出 token 控制，不只按记录数**——截断是这套设计唯一会静默污染数据的失败模式。合并时强边优先，超出记录数或输出预算就不再吸收，块自然封顶。

---

## ⑦ 块内全景归档

`work/block_events.jsonl` + `work/block_relations.jsonl`。模型一次输出该块的完整结构：

```json
{"events":[{
  "event_key":"E1",
  "event_name":"第38届百花奖闭幕式暨颁奖典礼",
  "event_description":"第38届大众电影百花奖举行闭幕式暨颁奖典礼，各奖项揭晓…",
  "facets":[{"angle":"红毯及造型","member_record_ids":["R001"]},
            {"angle":"票数与争议","member_record_ids":["R017"]}],
  "anchor":"第38届百花奖","anchor_type":"ACTIVITY","series_instance":"第38届",
  "member_record_ids":["R001","R017"],
  "member_confidence":{"R001":95,"R017":68},
  "member_notes":{"R017":"未点名本次典礼，需推断是现场反应"},
  "name_evidence":{"主体":"百花奖","数字":"第38届","时间":""},
  "confidence":0.95
}],
 "singletons":["R088","R091"],
 "relations":[{"a":"E1","b":"E2","relation_type":"REACTION","reason":"…"}]}
```

字段职责：

| 字段 | 层级 | 说明 |
|---|---|---|
| `event_name` | **一级，主键** | 标准事件名，≤20 阅读单位，能独立读懂 |
| `facets[].angle` | **二级** | 宣传角度，每个角度带自己的成员 |
| `event_description` | 描述 | 说清触发点与讨论范围，是核对合并对不对的依据 |
| `member_confidence` | 成员级 | 每条成员归属该事件的置信度 0~100，Ⓧ 层的 x2 按它筛查 |
| `member_notes` | 成员级 | 低分成员的具体理由，进 x2 的待裁定表给人看 |
| `anchor` / `anchor_type` / `series_instance` | 元数据 | 跨窗口汇总用，不是主键 |
| `singletons` | — | 不参与合并的记录，只返 ID |

**阅读单位**：中文字各计 1，连续的英文字母或数字串各计 1。
`DeepSeek V4 Pro发布与撤回回滚` 算 10 字不算 22 字——英文品牌名对读者就是一个词，
按字符数硬压只能删品牌名，那才是真的信息损失。

**落盘时 `facets[].member_record_ids` 被改名为 `facets[].members`。** 模型返回的是前者，
`07` 归一后写的是后者，下游（x2/x3/x4）读的都是 `members`。改这个键名要同时改四处。

`relation_type`：`DIFFERENT_PHASE | FOLLOW_UP | CAUSE_EFFECT | REACTION | SAME_ENTITY | SAME_SERIES | OTHER_RELATED`

`anchor_type`：`WORK | IP | BRAND | PRODUCT | PERSON | ORG | DISASTER | ACTIVITY | TOURNAMENT | PLACE | OTHER`

**`event_description` 不是装饰。** 实测去掉它时模型会把两个方向的内容并成一个事件；要求写出触发点后它自己就拆开了。它同时是人工核对合并对不对的依据。

**硬性要求：**

1. 每个输入 `record_id` 恰好出现一次，在某个事件的成员里或在 `singletons` 里
2. `events` 里每个事件必须有 2 个以上成员，单成员一律走 `singletons`
3. `facets` 成员必须是该事件成员的子集
4. `name_evidence` 逐项写出事件名中每个要素的出处
5. `member_confidence` 必须给全每个成员。**缺这个字段 Ⓧ 层的 x2 会直接硬失败**，
   因为分不清「模型认为都很确定」和「模型没打分」，而这两种情况处置相反

---

## ⑧ 热度排序

`work/block_events_ranked.jsonl` + `out/事件排名.csv` + `out/热度明细.csv`

```
平台内热度百分位 = 该记录在本平台入选记录里「小于其热度的数量 / 总数」× 100
事件热度         = 该事件全部成员的百分位峰值
```

只有这一条口径，没有自由参数。热度从 `work/frames.jsonl` 读（⓿ 已把它带进流水线），
不回读原始 CSV。

**不跨平台聚合热度，也不相加原始热度值**——不同平台的热度是不同量纲的自造指标，
即便换成百分位，跨平台相加也只是把「上榜平台多」伪装成「更热」。

排名按事件热度降序，同分依次按成员数、event_id 打破。
运行目录里混进多个平台时会打印告警：百分位在混合池里算出来不可比。

---

## ⑨ 提交

`out/` 下的最终产物：

| 文件 | 内容 |
|---|---|
| `event_registry.csv` | 事件表，一行一个事件，含一级名 / 二级角度 / 描述 / 排名 |
| `record_to_event.csv` | 记录级血缘明细 |
| `relation_graph.csv` | 事件间关系 |
| `non_events.csv` | 非事件与流程中丢失的记录 |
| `human_review.csv` | 名称校验未通过的事件 |
| `registry_snapshot.jsonl` | 跨窗口增量用的快照，含锚点向量 |
| `口径与执行说明.md` | 随产物一起交付的口径说明 |

---

## Ⓧ 跨平台后处理层（可选）

只有需要「全网这周有哪些事」时才跑。各平台跑到④为止，然后合库，复用⑤⑥⑦，
再加两道复核。数据协议：

| 阶段 | 读 | 写 | 调模型 |
|---|---|---|---|
| x0 合库 | 各平台 `work/clean_titles.jsonl` 等 | 合并目录的 `work/` | 否 |
| x1 失败块重拆 | `work/blocks.jsonl` + `raw/block_archive/*.json` | 覆盖 `work/blocks.jsonl`、`out/x1_重拆概览.csv` | 否 |
| x2 置信度筛查 | `work/block_events.jsonl` 的 `member_confidence` | 覆盖同文件、`out/x2_待裁定_边界带.csv`、`out/x2_已剔除.csv` | 仅 `--verdict` 时 |
| x3 双向复核 | `work/block_events.jsonl` | 覆盖同文件、`out/x3_合并明细.csv`、`out/x3_拆分与改名明细.csv` | 是 |
| x4 出明细表 | 进入⓿的那份 CSV + `work/block_events.jsonl` | `out/热点明细_含事件归属.csv`、`out/事件清单.csv` | 否 |

x0 硬失败于 record_id 跨平台撞号——两个平台各自编号时很容易撞，撞了会静默把两条不同
记录当成一条，必须在入口拦住。

x1 是**反应式**的，而且现在是**兜底而非常规**：⑦ 自己会重试并在块内对半拆，
只有它仍报「有 N 个块失败」时才用 x1。曾试过用块内锚点多样性预估输出量提前拆，
拿 12 个真实失败块校准后发现失败区间 [14680, 23000] 被成功区间 [410, 25240] 完全覆盖，
预测不了。而失败名单是已知事实。

x2 与 x3 顺序不可换，x3 内部先合后拆，拆的候选必须在合并落地之后重算。**在旧注册表上
算候选、再落到新注册表上会丢掉合并结果**——踩过一次，第一轮 5→1 的合并被覆盖没了。

x3 的三道本地校验，任一不过就整条不动（不做部分落地）：

| 校验 | 说明 |
|---|---|
| 成员覆盖精确 | 拆分后各子事件成员必须不重不漏地覆盖原事件 |
| 名称 ≤20 阅读单位 | 中文字各计 1，连续英文/数字串各计 1 |
| 名称有出处 | 只查 ≤3 字的词。长串是跨标点拼的，材料里本来就没有原文，全查会大量误报 |

x4 的输出形态是**原表原样 + 追加列**，原有列名列序行序行数一个不动。追加
`事件ID / 一级事件名 / 二级角度 / 事件成员数 / 归属置信度 / 状态 / 说明`，
状态只有「多条记录的事件 / 独立热点 / 剔除后独立 / 未进入聚类」四种，用业务语言，
不出现 `NON_EVENT` 这类内审代号。

---

## 本地必须做的校验

模型不做这些，脚本做。按**能否本地判定**分派，不要把可程序化的检查交给模型。

| 校验 | 处置 |
|---|---|
| 每个 record_id 恰好出现一次 | 漏返的本地补为单条，绝不丢数据 |
| `facets` 成员越界 | 剔除 |
| `events` 里出现单成员事件 | 转为 singleton |
| 事件名里的数字有出处 | 落回锚点原标题 |
| 事件名含空词 | **先重命名一次**（标 `RENAMED_FROM_EMPTY_WORD` 进 human_review），仍含空词才拆成单条 |
| 锚点带派生后缀（片方 / 参与者 / 玩家 / 官方…） | 本地剥除 |
| 锚点多种写法 | 按「去分类前缀 + 去符号」归一，收敛到最明确的写法 |
| 多成员事件用人物锚点 | 按类型优先级换成更具体的实体 |
| 单成员事件 | 不做名称校验，不产出锚点 |

数字正则必须是 `\d+(?:\.\d+)?`。用 `\d+` 会把 `1.3版本` 拆成 `1` 和 `3` 两个假发现。年份要同时比对 `time` 字段，否则时间列的 `2026` 会被误报为无出处。

---

## 并发、续跑、成本

统一走 `relay.py`，不要各自造轮子。

**串行的只有阶段 ③**（Entity 注册表逐批累积）。其余全部可并发，8~16 路是安全区间。

**续跑**以 `batch_id` / `block_id` 为键做文件存在性判断。批大小或分块参数一旦确定不要中途改——改了编号会错位，续跑对不上已完成的批次。

**成本台账**在 `run_manifest.json` 里 append-only 记录每次调用的 stage / model / tokens / cost。不要覆盖写，覆盖会丢掉返工阶段的真实花费。

### 三类长得一样的 JSON 失败，处理方式不同

**⑦ 已内建自动分流，正常跑完不需要人工介入。** 下表是它内部的处置逻辑，
以及你在日志里看到对应报错时该怎么理解：

| 现象 | 真因 | 处置 |
|---|---|---|
| 报错位置在输出末尾，约等于 max_tokens | 截断 | **先原样重试 1 次**，仍截断才在块内对半拆 |
| 报错位置在中间且每次重跑都一样 | 模型在字符串值里写了裸 ASCII 双引号 | `relay.repair_unescaped_quotes` 自动修 |
| `line 1 column 1 (char 0)` | 网关瞬时返回空内容 | 长退避重试，**不要拆块** |

**「截断不要重试」是旧结论，已被推翻。** 它的前提是输出量由块内容决定，实测不是：
同一个块在一次跑里撞了 16000 token 上限，重跑只输出 11K；另一个块四次重跑稳定在
7.5K 字符。**输出长度是随机的**，所以重试有相当概率不再撞上限，且免费保住块的完整性。

**空返回最容易被误判成「块太大」。** 一个失败过两次的 60 条块，单独跑 4/4 成功、
8 路并发 7/8 成功，输出稳定 7.5K 字符、离 max_tokens 差 8 倍——它根本没爆炸，
是并发下的瞬时故障。对它拆块无效，只会白付「子块看不到彼此、跨半区事件被切成两个」
的代价。`relay.call_json` 给 5 次尝试、退避 2/4/8/16s（窗口 30 秒）；旧版只有 3 次、
窗口 3 秒，盖不住一次网关抖动。

第二类的实测原文（`underlying_event` 是当时的字段名，现已不用）：
`"underlying_event":"官方辟谣"许昌暴雨60万人断水停电"为谣言"`。这一类重试无效，
同一批必然再错，所以交给 `repair_unescaped_quotes` 而不是重试。

Embedder 有独立重试并在失败前落盘缓存，超时 600 秒——它的端点响应时间波动极大
（29~197 秒），旧的 120 秒会把正常的慢请求误杀。

### 提示缓存

只有 system 块超过约 1024 tokens 才可能命中。中文约 1 字符 1 token，实测边界在 **1600 字符左右**。块内归档的 system 是 5000+ 字符，实测命中 39760 tokens。

**不要为了凑门槛给提示词灌水。** 判定规则统一放在 `eventlib.py` 的 `TRIGGER_RULE` / `IDENTITY_RULES` / `RELATION_ENUM`，多阶段共享——抽出共享判据后一次修掉三个问题：`OTHER_RELATED` 兜底率从 71% 降到 13%、缓存从 0 命中、事件合并数上升。

---

## 成本与耗时

实测 382 条（4 平台一周抽样）：

| 阶段 | 调用 | 成本 |
|---|---|---|
| ⓿ 平台清洗 | 8 批并发 | 待实测，量级同 ① |
| ① 事件性 | 8 批并发 | $0.44 |
| ② Frame | 10 批并发 | $1.19 |
| ③ Entity | 5 批串行 | $0.45 |
| ④ Embedding | — | $0.0008 |
| ⑤⑥⑧⑨ | 本地 | 0 |
| ⑦ 块内归档 | **15 块并发** | **$0.77** |
| 合计 | | **约 $2.9** |

跨平台可选层（3545 条实测，只在走 Ⓧ 时发生）：

| 阶段 | 调用 | 成本 |
|---|---|---|
| x0 / x1 / x4 | 本地 | 0 |
| x2 边界带裁定 | 65 条交 opus | $0.6（`--verdict off` 时为 0） |
| x3 双向复核 | 两个方向各分批并发 | 约 $1.5 |

单块量级参考：20 条记录的块归档一次约 $0.027，可用来估任意块数。

放大到全量 3545 条：块数约 124 全部并发，成本约 **$21**，耗时以网络延迟为主。
**这个 $21 是按 382 条线性外推的，不是实测。** 块数不与记录数成正比（取决于话题集中度），
缓存命中率也会变，失败块重拆还要多付一次。当预算用要留余量。

⓿ 是新增阶段，成本待实测。它的输入输出都是短标题，量级应与 ① 相当。
若 ④ 的 `--text-source clean_title` 对照证明 Frame 可砍，② + ③ 的 $1.64（全量约 $12）
会被省掉，净成本低于现在。

**单平台跑的量级要单独算。** 上面的数字是 4 平台混跑；分平台后每个平台的记录数约为
四分之一，块数与成本按比例下降，但块内归档的固定 SYSTEM 开销会被重复付四次
（约 5900 字符，提示缓存能吃掉大部分）。

---

## 运行目录结构

```
运行目录/
├── run_manifest.json          输入哈希 / 模型配置 / 成本台账
├── work/
│   ├── clean_titles.jsonl
│   ├── eventness.jsonl
│   ├── frames.jsonl
│   ├── frames_resolved.jsonl
│   ├── entities.jsonl
│   ├── embeddings.json
│   ├── candidates.jsonl
│   ├── blocks.jsonl
│   ├── block_events.jsonl
│   ├── block_events_ranked.jsonl
│   └── block_relations.jsonl
├── raw/                       每块原始模型返回，续跑依据
└── out/                       见 ⑨
```

`run_manifest.json` 的 `input_hash` 是**工作单元 ID 集合的哈希**，不是输入文件哈希——否则任何一条无关记录变动都会让整轮无法复用。

走 Ⓧ 层时，`work/block_events.jsonl` 被 x1/x2/x3 反复原地覆盖，`raw/` 下多出
`x2_verdict/` `x3_merge/` `x3_split/` 三个续跑目录，`out/` 多出：

```
out/x1_重拆概览.csv          哪些块被拆、拆成几个
out/x2_待裁定_边界带.csv     50~74 分的成员，留空列给人填
out/x2_已剔除.csv            被摘出的成员及依据
out/x3_合并明细.csv          哪个事件并进了哪个、转成什么二级角度
out/x3_拆分与改名明细.csv    含「拆分被拒」「改名被拒」及拒的原因
out/热点明细_含事件归属.csv  原表原样 + 追加归属列，交付主件
out/事件清单.csv             一行一个多成员事件，人工抽检用
```

**x2 与 x3 都原地覆盖 `block_events.jsonl`，跑之前先备份。** 落地前有硬校验（记录数不变、
没有记录同属两个事件），不过就中止不写入；但校验通过的错误判定它拦不住。
