# topic6-fetch-normalize · MA 口径 v1

社媒热点周刊 · Phase A+B：取数 → 清洗 → 标准化 → 抽样。

挂载路径：`/mnt/skills/topic6-fetch-normalize/`
产出根：`/workspace/Projects/{PROJECT_DIR}/`

---

## 一、脚本清单

| 脚本 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `scripts/fetch_hot_topics.py` | MA hot-topics MCP | `01_原始数据/hot_topics_skill_raw.json` + `hot_topics_raw.xlsx` | A 段:MCP 取数 + 清洗 + 完整周检查 |
| `scripts/log1p_p1p99_normalize.py` | `01_原始数据/hot_topics_raw.xlsx` | `02_标准化/hot_topics_normalized.xlsx` | B 段:log1p+P1/P99 归一到 [40,100] |
| `scripts/sample_500.py` | `02_标准化/hot_topics_normalized.xlsx` | `03_抽样/sample_500.xlsx` | 分层随机抽样 500 条(test 模式用) |

---

## 二、执行编排

```bash
# A 段:取数 + 清洗
python /mnt/skills/topic6-fetch-normalize/scripts/fetch_hot_topics.py \
    --start "2026-08-18" --end "2026-08-31" \
    --project-dir /workspace/Projects/W35_20260824-20260830

# B 段:标准化
python /mnt/skills/topic6-fetch-normalize/scripts/log1p_p1p99_normalize.py \
    --project-dir /workspace/Projects/W35_20260824-20260830

# (可选)test 模式抽样
python /mnt/skills/topic6-fetch-normalize/scripts/sample_500.py \
    --project-dir /workspace/Projects/W35_20260824-20260830
```

---

## 三、MA 环境说明

- **MCP 接入**:`fetch_hot_topics.py` 通过 MA 平台挂载的 `crawler-hot-topics-server` MCP 走 SSE 拉数,不再直连客户 `smartai.blueviewai.com`。API Key 由 MA 平台注入,不需要脚本自行处理 `~/.claude.json`。
- **路径**:所有产出走绝对路径 `/workspace/Projects/...`,`--project-dir` 支持绝对/相对(相对时以 `/workspace` 为根)。
- **依赖**:`pandas`, `numpy`, `openpyxl`, `requests`(仅 fetch 用)。
- **基准文件**:`references/平台热度基准_2026.json`(4 平台 log1p 的 p1/p99,固定 skill 内)。

---

## 四、字段契约

清洗产物 `hot_topics_raw.xlsx` 列(9 列):
`row_id, platform, title, hottopic_desc, hot_index, hotpost_time, hottopic_url, hot_thumbnail_url, week_num`

标准化产物 `hot_topics_normalized.xlsx` 在 `hot_index` 右侧插入 `heat_score`(10 列)。

`row_id` 格式 `T{i:04d}`,平台限定 `{微博, 抖音, B站, 知乎}`,最小行数 200。
