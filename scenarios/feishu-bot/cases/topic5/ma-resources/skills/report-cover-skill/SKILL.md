# report-cover-skill

报告封面生成 skill（解锁 n14_charts 的封面部分，原依赖清单 B3）。调用 relay 图像生成接口产出封面图。

## 文件

| 文件 | 职责 |
|---|---|
| `scripts/generate_cover_gpt.py` | 封面生成入口；从 Vault 环境变量取 key/base_url，调 relay 返回 `b64_json`/`url` |
| `references/api-guide.md` | 接口说明 |

## 用法

```bash
cd /mnt/skills/report-cover-skill
python scripts/generate_cover_gpt.py   # 详见 references/api-guide.md
```

## 依赖

- Python 3.9+；`requests`、`Pillow`。
- 凭证：MA Vault 注入 `REPORT_COVER_API_KEY`；可选
  `REPORT_COVER_API_BASE_URL`，默认走 `https://bmc-llm-relay.bluemediagroup.cn`。

## 接真位置

n14_charts 在沙箱内生成本地封面 PNG；n20 交付时通过飞书 media API 原生插入文档。
