---
name: ssjh-social-stat
version: "1.7.2"
description: |
  数说聚合（datastory / dc.datastory.com.cn）社媒统计取数 Skill。按关键词、排除词、平台、时间范围等参数，一次性调用数说聚合全部 11 个社媒统计接口，返回结构化聚合舆情指标——总声量、总互动量、总情感度、声量趋势、互动量趋势、数据类型分布、情感分布、声量类型分布（UGC/PGC）、关键词云、原文 Top 内容、区域分布，覆盖微博/微信/小红书/抖音/快手/视频号/B站/知乎/新闻/论坛/电商，并可导出 Excel。
  何时触发（宁可多触发也别漏）：用户提到"数说""数说聚合""datastory""dc.datastory"；或说"拉社媒统计/舆情数据""社媒取数""数说取数""social listening""做份社媒数据/舆情报表"；或想看某品牌/关键词在某些社媒平台上的总声量、互动量、情感度/口碑、声量或互动趋势、词云、区域分布、UGC/PGC 占比、竞品声量对比、传播声量盘点——即便没点名"数说"，只要是跨平台跑这类聚合统计指标，就触发本 Skill。
  【直接触发关键词】含"统计"、"指标"、"计算"、"声量"、"互动量"等词，且提及要分析某个词/品牌的数值型舆情指标时，无需弹选择卡，直接触发本 Skill。
  【强制】用户明确说"数说取数"/"数说"/"数说聚合"/"datastory"时，必须使用本 Skill 完成取数，禁止改用社媒采集类 Skill、手写脚本或其他数据源替代；本 Skill 报错时也不得静默换路，须如实报错并等待用户指示。
  DO NOT trigger：(1) 只是在讨论或解读已有数据、并不需要去数说取数；(2) 用户要的是抓取原始帖子/评论正文、采集 KOL 内容（属社媒原始数据采集，不是统计聚合）；(3) 要对手头数据做 LLM 情感分析/打标（属数据标注）；(4) 查账号粉丝数/报价/媒介库资源（属媒介资源库）；(5) 与 dc.datastory.com.cn 取数/导出无关的任务。
metadata:
  version: "1.7.2"
  author: atlas
  family: SSJH
  tags: [social-media, analytics, datashuoshuo]
---

# datashuoshuo-socialmedia Skill

## 功能说明

调用数说聚合平台（dc.datastory.com.cn）社媒平台统计 API，
一次性拉取以下 11 个维度的全量数据：

1. 总声量
2. 总互动量
3. 总情感度
4. 声量趋势（按平台按日）
5. 互动量趋势（按平台按日）
6. 数据类型分布（各平台声量数值）
7. 情感类型分布（正/中/负，按平台）
8. 声量类型分布（UGC/PGC/UKN，按平台）
9. 关键词云（按平台）
10. 原文内容（按平台，按互动量排序）
11. 区域分布

## 平台列表

| 传入值 | 含义 | 内部映射 |
|--------|------|---------|
| weibo | 微博 | source=weibo |
| weixin | 微信公众号 | source=weixin |
| xiaohongshu | 小红书 | source=xiaohongshu |
| douyin | 抖音 | source=shortVideo, siteIds=1003583 |
| kuaishou | 快手 | source=shortVideo, siteIds=1334510 |
| shipinhao | 微信视频号 | source=shortVideo, siteIds=25047925 |
| bilibili | B站 | source=video, siteIds=44 |
| zhihu | 知乎 | source=qa, siteIds=101944 |
| news | 新闻 | source=news |
| forum | 论坛 | source=forum |
| ecom | 电商 | source=ecom |

## 对话流程（Agent 执行规范）

**触发后禁止直接调用 `skill.run()`，必须走以下流程。Step 2 不可跳过。**

---

### Step 1 · 参数收集 / 提取（可跳过）

**如果用户消息中已包含足够信息**（关键词、时间范围可判断），直接提取参数，跳过询问，进入 Step 2。

**如果参数不足**，向用户收集以下信息：

| 参数 | 是否必填 | 默认值 | 说明 |
|------|:------:|--------|------|
| 关键词 | ✅ | — | 一个或多个，多个时每词独立查询后合并摘要 |
| 排除词 | 可选 | 无 | 用 `\|` 连接多个，如 `生鲜鸡\|生鸡` |
| 平台 | 可选 | weibo,weixin,xiaohongshu,douyin | 见平台列表 |
| 开始日期 | ✅ | — | 格式 `YYYY-MM-DD` |
| 结束日期 | ✅ | — | 格式 `YYYY-MM-DD` |
| 过滤广告 | 可选 | 否（不过滤） | 是否过滤品牌广告投放内容 |

---

### Step 2 · 飞书卡片展示取数方案（**必须执行，不可跳过**）

调用 `scripts/send_plan_card.py` 发送飞书原生卡片，卡片展示全部参数及 4 项高影响参数的当前值与推荐说明：

```bash
SKILL_DIR="/mnt/skills/ssjh-social-stat"
python "$SKILL_DIR/scripts/send_plan_card.py" \
  --keywords "<kw1>" "<kw2>" \
  --filterword "<排除词>" \
  --platforms "<p1,p2,...>" \
  --start-date "<YYYY-MM-DD>" --end-date "<YYYY-MM-DD>" \
  --msg-type <0|1|2> \
  --content-mode <ALL|UGC|PGC> \
  [--filter-ad] \
  [--no-ocr]
```

**参数默认值**（与 `run.py` 保持一致，无需硬编码）：
- `--msg-type 1`（仅主贴）
- `--content-mode ALL`
- 不加 `--filter-ad`（不过滤广告）
- 不加 `--no-ocr`（开启 OCR）

**等待用户回复**：
- 回复「确认」/「执行」→ 进入 Step 3
- 回复修改某项参数 → 更新对应 flag，**重新调用 `send_plan_card.py`**（重新发卡片）
- **禁止在收到用户确认前进入 Step 3**

---

### Step 3 · 执行取数

用户确认后，调用 `scripts/run.py` 的 `--mode execute`：

```bash
python "$SKILL_DIR/scripts/run.py" \
  --keywords "<kw1>" "<kw2>" \
  --filterword "<排除词>" \
  --platforms "<p1,p2,...>" \
  --start-date "<YYYY-MM-DD>" --end-date "<YYYY-MM-DD>" \
  --msg-type <0|1|2> \
  --content-mode <ALL|UGC|PGC> \
  [--filter-ad] \
  [--no-ocr] \
  --output-dir "<run_dir>/external/ssjh" \
  --mode execute
```

执行完成后，将以下内容展示给用户：
- 每个关键词的声量 / 互动量
- 多关键词时的合计声量 / 合计互动量
- 结果文件路径（`<run_dir>/external/ssjh/`，多关键词时含 `merged_*.json`）

---

## 使用方式

**唯一入口是 `scripts/run.py`，严禁在对话流程中直接调用 `skill.run()`**（见上方「对话流程」Step 1-3）：

```bash
# Step 3：用户确认后执行取数（Step 1/2 见「对话流程」）
python "$SKILL_DIR/scripts/run.py" \
  --keywords "小米" \
  --filterword "汽车" \
  --platforms "weibo,xiaohongshu,douyin" \
  --start-date "2024-05-01" --end-date "2024-05-31" \
  --content-mode ALL --msg-type 1 \
  --output-dir "<run_dir>/external/ssjh" \
  --mode execute
```

在 MA 中必须用 `--output-dir` 将结果写入当前 run 目录，不得写回 skill 挂载目录。
结果为 JSON（`mode=full` 形态）；如需导出正式 Excel，用 `export_excel.py` 读取该 JSON——
摘要形态（`mode=summary`）数据会被拒绝导出，须用完整结果重新执行。

## 返回结构

```json
{
  "meta": { "keyword": "...", "platforms": [...], "start_date": "...", "end_date": "..." },
  "total_volume": 4072376,
  "total_interaction": 129429076,
  "total_sentiment": "73.79%",
  "volume_trend": { "weibo": {"20240501": 14604, ...}, ... },
  "interaction_trend": { "weibo": {"20240501": 543844, ...}, ... },
  "data_type": { "weibo": 1192358, "xiaohongshu": 415001, ... },
  "sentiment_dist": {
    "_total": {"positive": 863477, "neutral": 3078936, "negative": 129963},
    "weibo": {"positive": ..., "neutral": ..., "negative": ...},
    ...
  },
  "volume_type": {
    "_total": {"UGC": 2219654, "PGC": 122275, "UKN": 1730447},
    "weibo": {...},
    ...
  },
  "cloud_word": { "weibo": {"小米": 177243, ...}, ... },
  "articles": { "weibo": [...], "xiaohongshu": [...], ... },
  "region_dist": { "上海": 254779, "广东": 157631, ... }
}
```

## CHANGELOG

完整变更历史见同目录 [`CHANGELOG.md`](CHANGELOG.md)。

> 此处不再内嵌副本：v1.7.1 之前变更历史只写在 SKILL.md 里、v1.7.1 只写在 CHANGELOG.md 里，
> 两份都不完整，导致「当前版本是多少」取决于读者打开了哪个文件。现统一以 `CHANGELOG.md` 为单一来源。
