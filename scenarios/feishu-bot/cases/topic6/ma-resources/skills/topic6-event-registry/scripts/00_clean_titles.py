#!/usr/bin/env python3
"""阶段 0：平台专属标题清洗（流水线唯一的平台差异点）。

各平台的差异在**标题包装**上，不在判定逻辑上。实测各 100 条：
知乎提问外壳 100%；B站方括号标签 30%、期数 7%、emoji 5%；微博空格分词 18%；抖音基本干净。
包装噪声干扰的是 embedding 向量、事件命名和召回，不是事件性判定本身。

所以剥壳放在这里，判定阶段（01）保持一套共享标准提示词，只判不剥。

硬约束：清洗只删不加。新增数字或新增汉字都会被本地校验拦下并回退原标题——
清洗篡改事实比不清洗危险得多，它把编造前移到了最早的环节，下游查不出来。
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relay import (Relay, ids_hash, make_batches, parse_json, report,
                   require_full_coverage, run_batches, write_csv, write_jsonl)

OPS = {
    "STRIP_QUESTION_SHELL",   # 剥提问外壳（如何看待 / 你怎么看 / 为什么）
    "DROP_OPINION_CLAUSE",    # 删纯观点征求分句
    "DROP_CHANNEL_TAG",       # 去 UP 主、频道、机构标记
    "DROP_COLLECTION_TAG",    # 去合集、全收集、系列、栏目标签
    "DROP_EPISODE",           # 去期数、集数、第 N 弹
    "DROP_DECORATION",        # 去 emoji、颜文字、装饰性符号与叠字标点
    "NORMALIZE_SEPARATOR",    # 分隔符规范化（空格、竖线 → 顿号）
    "DROP_TOPIC_MARK",        # 去话题井号
    "NONE",                   # 无需清洗
}

CLEAN_CORE = """你是热搜标题的清洗员。把平台的包装剥掉，只留事件本体，供下游做语义比对和事件命名。

# 唯一的硬约束：只删不加

- **不许新增任何原文没有的字**。不补主体、不补背景、不补谓语、不补背景年份。
- **不许改写数字形式**。汉字数字保持汉字，阿拉伯数字保持阿拉伯，不做换算不做四舍五入。
- **必须保住**日期、数字、比分、版本号、金额、届次、作品名、人名、机构名——它们是下游事实校验的唯一出处。
- **必须保住情态与消息来源标记**：疑似、网传、传闻、据称、据传、据报道、或将、有望、
  预计、涉嫌、拟、曝。删掉它们不是少了点修饰，而是把传闻和预告写成了既成事实。
  ```
  原：网传某明星疑似隐婚，如何看待？
  对 → 网传某明星疑似隐婚
  错 → 某明星隐婚          ← 传闻被写成事实，这是造假
  ```
- 允许的唯一「加」是标点：把空格、竖线这类分隔符换成顿号，让句子可读。

原标题本身就干净时，clean_title 原样返回，ops 填 ["NONE"]。**不要为了显得在干活而改写。**

清洗不是改写，不是摘要，不是润色。剥掉包装之后剩下什么就是什么，哪怕它读起来不像一句完整的话。

# 遇到这几种情况怎么办

- 剥完只剩一个裸词（例如只剩一个作品名）→ 就返回那个裸词。它会在下游被判成非事件，这是正确结果，不要靠补字把它救成句子。
- 一条标题里有两三个分句，其中一部分是事实、一部分是观点征求 → 留事实，删观点征求。
- 分不清是包装还是事实 → **留着**。漏删一点包装无害，删掉事实是严重错误。

# 输出

ops 从这个列表里选，可以多个，不要新造：
STRIP_QUESTION_SHELL 剥提问外壳
DROP_OPINION_CLAUSE 删纯观点征求分句
DROP_CHANNEL_TAG 去 UP 主/频道/机构标记
DROP_COLLECTION_TAG 去合集/全收集/系列/栏目标签
DROP_EPISODE 去期数/集数/第 N 弹
DROP_DECORATION 去 emoji/颜文字/装饰符号/叠字标点
NORMALIZE_SEPARATOR 分隔符规范化
DROP_TOPIC_MARK 去话题井号
NONE 无需清洗

只输出 JSON，不要任何解释。"""

PLATFORM_RULES = {
    "知乎": """# 知乎的包装：提问外壳

实测 100 条里 100 条都是「提问外壳 + 事实内核」，外壳必须剥掉。

常见外壳：如何看待 / 如何评价 / 怎么看 / 你怎么看 / 为什么 / 为何 / 是不是 / 能不能 /
会不会 / 靠谱吗 / 有哪些 / 是何 / 可能会有哪些 / 对此你有哪些期待 / 你接受吗

```
原：如何看待 8 月 10 日沈腾主演电影《欢迎来龙餐馆》总票房预测值大幅提升至 35.4 亿人民币？
清：8 月 10 日沈腾主演电影《欢迎来龙餐馆》总票房预测值大幅提升至 35.4 亿人民币
ops：STRIP_QUESTION_SHELL

原：全国票房日冠地图显示「东北龙餐馆，上海奥德赛」，如何看待这一现象？反应了怎样的区域观影偏好差异？
清：全国票房日冠地图显示「东北龙餐馆，上海奥德赛」
ops：STRIP_QUESTION_SHELL, DROP_OPINION_CLAUSE

原：今年科技圈已裁 20 多万人，程序员该怎么给自己留后路？
清：今年科技圈已裁 20 多万人
ops：STRIP_QUESTION_SHELL, DROP_OPINION_CLAUSE

原：Anthropic Q2 营收逾 115 亿美元，同比增 14 倍，是何引爆了其业绩，大模型商业化成功了？
清：Anthropic Q2 营收逾 115 亿美元，同比增 14 倍
ops：STRIP_QUESTION_SHELL, DROP_OPINION_CLAUSE
```

注意两件事：

一是**外壳里也可能夹着事实**，别连着剥掉：
```
原：Flandre 加盟 BLG 后，Bin 还会有登场机会吗？
清：Flandre 加盟 BLG
```

二是**纯假设句没有内核可剥**，整句原样返回，让下游去判它不是事件：
```
原：假如你是一个顶尖电竞俱乐部管理人，你还会招募 Bin 选手吗？
清：假如你是一个顶尖电竞俱乐部管理人，你还会招募 Bin 选手吗
ops：NONE
```
剥壳是删包装，不是替下游做判断。看不出内核就整句留着。""",
    "B站": """# B站的包装：标签、合集、期数、装饰

实测 100 条：方括号标签 30 条、书名号 31 条、期数集数 7 条、空格分词 13 条、
提问外壳 8 条、emoji 与装饰符号 5 条、竖线 3 条。

方括号里可能是 UP 主标记，也可能是作品的正式子标题，**要分开处理**：

```
原：原神7.0荧的雷霆语言系统，这谁绷的住？———【bilibilionly】
清：原神7.0荧的雷霆语言系统
ops：DROP_CHANNEL_TAG, DROP_OPINION_CLAUSE, DROP_DECORATION
说明：【bilibilionly】是 UP 主标记，———是装饰性叠字标点，"这谁绷的住"是情绪，都删

原：如何评价 2026 年 8 月米哈游《原神》7.0 剧情任务【无神怜爱的雪国】？
清：2026 年 8 月米哈游《原神》7.0 剧情任务【无神怜爱的雪国】
ops：STRIP_QUESTION_SHELL
说明：【无神怜爱的雪国】是任务正式名称，必须保留

原：【原神一条龙全收集】至冬7.0(成就数/冰神瞳/摩拉/影生翼滴/枪械蓝图)古兽冰原+焰羽谷+永凝冻土/原神7.0一条龙
清：原神至冬7.0
ops：DROP_COLLECTION_TAG
说明：全收集攻略的清单枚举全是包装，事件本体只有版本；末尾重复的关键词堆砌也删
```

其余动作：
- 期数集数（第 N 期、EP12、Vol.3、第 N 弹）→ DROP_EPISODE
- 井号话题 → DROP_TOPIC_MARK，井号里的词保留
- 竖线与空格分隔 → NORMALIZE_SEPARATOR
- emoji、颜文字、`———`、`！！？` → DROP_DECORATION，正常标点不动
- 提问外壳同知乎规则 → STRIP_QUESTION_SHELL

角色台词式标题（`荧："奥黛塔，软软的小小的，你好香啊"！！？`）只删装饰标点，
引号里的内容原样保留，不要试图概括它。""",
    "微博": """# 微博的包装：空格分词与裸话题词

实测 100 条平均只有 9 字，是四个平台里最短最干净的，唯一的包装是空格分词（18 条）。

```
原：周星驰经纪人 内涵龙餐馆偷票房
清：周星驰经纪人内涵龙餐馆偷票房
ops：NORMALIZE_SEPARATOR

原：东北龙餐馆 上海奥德赛
清：东北龙餐馆、上海奥德赛
ops：NORMALIZE_SEPARATOR
```

空格连接的两部分是同一个短语时直接接上。**判不准就保留原空格，不要加顿号。**

顿号只在两部分**明显是并列的同类项**时才用——比如两个都是作品名、两个都是地名。
这条的默认值原来是「判不准就用顿号」，实测 159 次分隔符归一里 **109 次（69%）是误改**：
微博热搜的空格多数是「主体 + 事项」，不是并列。

```
错：库克 华为            → 库克、华为             实际是「库克评价华为」
错：杨幂 花开不设限        → 杨幂、花开不设限         实际是「杨幂代言新歌」
错：郑钦文 赞助商高兴坏了   → 郑钦文、赞助商高兴坏了    实际是「郑钦文的赞助商」
对：东北龙餐馆 上海奥德赛   → 东北龙餐馆、上海奥德赛    两个都是作品名，确实并列
```

**加顿号是加字符，也是替模型断言了一层原文没有的关系。** 空格保持原样不损失任何信息，
下游 Frame 抽取照样能拿到主体；而错误的顿号把「未知关系」伪装成「并列关系」。
这正是「只删不加」要防的事——本地校验只拦新增汉字，拦不住标点。

`龙餐馆` 这种裸话题词原样返回，ops 填 NONE。它没有动作，会在下游被判成非事件——
**绝对不要给它补谓语补背景**。微博标题短是它的常态，短不是缺陷，补字才是造假。

有井号话题标记就删标记留词（DROP_TOPIC_MARK）。除此之外微博基本不需要动。""",
    "抖音": """# 抖音的包装：几乎没有

实测 100 条平均 11 字，提问外壳只有 1 条，其余全是干净的事件短语：

```
易烊千玺百花奖最佳男主角      → 原样返回，ops：NONE
百花奖王宝强0票              → 原样返回，ops：NONE
```

**默认动作就是原样返回 ops=NONE。** 只在确实出现这几种包装时才动：
提问外壳 → STRIP_QUESTION_SHELL；空格分词 → NORMALIZE_SEPARATOR；
井号话题 → DROP_TOPIC_MARK；emoji 与装饰符号 → DROP_DECORATION。

这个平台上最容易犯的错是**手痒改写**。干净标题被"润色"成另一种说法，会让同一事件的
两条记录在向量空间里分开，反而降低召回。看不出包装就别动。""",
}

ALIASES = {"zhihu": "知乎", "weibo": "微博", "douyin": "抖音", "tiktok": "抖音",
           "bilibili": "B站", "bili": "B站", "b站": "B站", "哔哩哔哩": "B站"}

USER_TMPL = """清洗以下 {n} 条{platform}标题。

{lines}

输出格式：
{{"records":[{{"record_id":"","clean_title":"","ops":[""]}}]}}

每个 record_id 必须出现且仅出现一次。"""

CJK_RE = re.compile(r"[一-鿿]")
# \d+ 会把 1.3版本 拆成 1 和 3 两个假发现，必须带小数部分
NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 情态与消息来源标记。删掉它们不是「少了点信息」，而是**把传闻和预告写成了既成事实**，
# 这是清洗唯一一类「靠删就能造假」的动作，所以单独查。
# 只收多字词与在热搜标题里几乎不会作为其他词构件出现的单字。
MODALITY = ("疑似", "网传", "传闻", "据称", "据传", "据报道", "或将", "有望",
            "预计", "涉嫌", "拟", "曝")


def verify(original: str, clean: str) -> list[str]:
    """本地校验清洗结果。主要查「加」——删多了是可接受损失，加了是造假。

    例外是情态标记：删掉「疑似」「网传」会翻转真假值，等于用删来造假，也必须拦。

    顿号单独列一类：它不是汉字，躲过了 NEW_CHAR，但**加顿号是在断言一层原文没有的
    并列关系**。微博实测 159 次分隔符归一里 109 次（69%）是把「主体 + 事项」误判成
    并列（`库克 华为` → `库克、华为`）。所以原文无顿号而清洗后有，一律回退。
    真正并列的场景（两个作品名）损失的只是可读性，不损失信息。
    """
    flags = []
    if not clean:
        return ["EMPTY"]
    added_num = [n for n in NUM_RE.findall(clean) if n not in original]
    if added_num:
        flags.append("NEW_NUMBER:" + ",".join(added_num))
    added_cjk = set(CJK_RE.findall(clean)) - set(CJK_RE.findall(original))
    if added_cjk:
        flags.append("NEW_CHAR:" + "".join(sorted(added_cjk)))
    if "、" in clean and "、" not in original:
        flags.append("NEW_SEPARATOR")
    lost = [w for w in MODALITY if w in original and w not in clean]
    if lost:
        flags.append("LOST_MODALITY:" + ",".join(lost))
    if len(clean) > len(original):
        flags.append("LONGER")
    return flags


def parse_hot(value: object) -> tuple[float | None, str]:
    """解析热度值。**解析不了就返 None，绝不当 0。**

    `12.5万` 当成 0 不是「少了点精度」，而是把当周最热的记录排到了最后，而且
    下游看不出来——排序照样出得来，只是错的。所以宁可标成缺失：阶段 08 会把缺失
    排除在百分位之外并单独计数，那是可见的失败；静默置 0 是不可见的失败。

    单位换算跟着来：万 / w / 亿 / k / m 都是热搜导出里常见的写法。
    """
    if value is None or str(value).strip() == "":
        return None, "missing"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = float(value)
        return (n, "ok") if math.isfinite(n) and n >= 0 else (None, "invalid")
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"\s+", "", text.replace(",", "").replace("，", ""))
    m = re.fullmatch(r"\+?(\d+(?:\.\d+)?)(万|亿|k|m|w)?(?:热度|次|人|播放|阅读|浏览)?", text)
    if not m:
        return None, "ambiguous_or_invalid"
    mult = {None: 1.0, "万": 10_000.0, "w": 10_000.0, "亿": 100_000_000.0,
            "k": 1_000.0, "m": 1_000_000.0}[m.group(2)]
    n = float(m.group(1)) * mult
    return (n, "ok") if math.isfinite(n) else (None, "invalid")


def load_records(path: Path, id_col: str, title_col: str, heat_col: str | None,
                 platform_col: str, platform: str) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"{path} 没有数据行")
    for col in (id_col, title_col):
        if col not in rows[0]:
            raise SystemExit(f"缺列 {col}，实际列名：{list(rows[0])}")
    if heat_col and heat_col not in rows[0]:
        raise SystemExit(f"缺列 {heat_col}，实际列名：{list(rows[0])}")
    has_platform = platform_col in rows[0]
    out, skipped = [], 0
    bad_heat: list[str] = []
    for row in rows:
        rid = str(row[id_col]).strip()
        title = str(row[title_col]).strip()
        if not rid or not title:
            continue
        row_platform = str(row.get(platform_col, "")).strip() if has_platform else platform
        if has_platform and ALIASES.get(row_platform.lower(), row_platform) != platform:
            skipped += 1
            continue
        heat = None
        if heat_col:
            heat, status = parse_hot(row[heat_col])
            if status not in ("ok", "missing"):
                bad_heat.append(str(row[heat_col])[:20])
        out.append({"record_id": rid, "title": title, "heat": heat,
                    "platform": platform})
    if skipped:
        print(f"按 {platform_col}={platform} 过滤，跳过其他平台 {skipped} 条")
    if bad_heat:
        print(f"热度列有 {len(bad_heat)} 条解析不了，已标成缺失（不当 0）："
              f"{bad_heat[:5]}\n  如果是整列都这样，检查 --heat-col 是不是指错了列",
              file=sys.stderr)
    if not out:
        raise SystemExit(f"没有 {platform} 的记录。检查 --platform 与 --platform-col")
    return out


def normalize(raw: dict, items: list[dict]) -> list[dict]:
    """校验不通过就回退原标题。宁可不清洗，不可清错——错误的清洗下游无法察觉。"""
    by_id = {r["record_id"]: r for r in items}
    seen: set[str] = set()
    out = []
    for row in raw.get("records", []):
        rid = str(row.get("record_id", "")).strip()
        if rid not in by_id or rid in seen:
            continue
        seen.add(rid)
        original = by_id[rid]["title"]
        clean = str(row.get("clean_title") or "").strip()
        ops = [str(o).upper().strip() for o in (row.get("ops") or [])
               if str(o).upper().strip() in OPS] or ["NONE"]
        flags = verify(original, clean)
        # LONGER 单独出现只是提示，不回退——分隔符换标点会让长度持平或微增
        fatal = [f for f in flags if not f.startswith("LONGER")]
        if fatal:
            clean, ops = original, ["NONE"]
        out.append({**by_id[rid], "clean_title": clean, "ops": ops,
                    "flags": flags, "reverted": bool(fatal)})
    for rid in require_full_coverage(list(by_id), [r["record_id"] for r in out], "clean"):
        out.append({**by_id[rid], "clean_title": by_id[rid]["title"], "ops": ["NONE"],
                    "flags": ["MISSING"], "reverted": True})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path, help="原始记录 CSV")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--platform", required=True,
                    help="知乎 / 微博 / 抖音 / B站（也接受 zhihu weibo douyin bilibili）")
    ap.add_argument("--id-col", default="record_id")
    ap.add_argument("--title-col", default="title")
    ap.add_argument("--platform-col", default="platform",
                    help="输入里有这一列就按 --platform 过滤，省掉调用方先拆表")
    ap.add_argument("--heat-col", default=None, help="热度列。给了就按热度降序处理")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=16000)
    args = ap.parse_args()

    platform = ALIASES.get(args.platform.lower(), args.platform)
    if platform not in PLATFORM_RULES:
        raise SystemExit(f"未知平台 {args.platform}，支持：{list(PLATFORM_RULES)}")

    records = load_records(args.input, args.id_col, args.title_col, args.heat_col,
                           args.platform_col, platform)
    records.sort(key=lambda r: -float(r.get("heat") or 0))
    print(f"{platform} 读入 {len(records)} 条，"
          f"input_hash={ids_hash([r['record_id'] for r in records])}")

    system = CLEAN_CORE + "\n\n" + PLATFORM_RULES[platform]
    relay = Relay(args.model, args.max_tokens, system)
    batches = make_batches(records, args.batch_size, tag="CL")

    def worker(batch: dict) -> dict:
        lines = "\n".join(f"{r['record_id']}\t{r['title']}" for r in batch["items"])
        text = relay.call(USER_TMPL.format(n=len(batch["items"]), platform=platform,
                                          lines=lines))
        return {"batch_id": batch["batch_id"],
                "records": normalize(parse_json(text), batch["items"])}

    results, failures = run_batches(batches, worker, args.run_dir / "raw" / "clean",
                                    args.concurrency, "clean")
    if failures:
        print(f"有 {len(failures)} 批失败，重跑本脚本即可续跑：{failures}", file=sys.stderr)

    rows = [r for payload in results for r in payload["records"]]
    write_jsonl(args.run_dir / "work" / "clean_titles.jsonl", rows)
    for row in rows:
        row["ops"] = ",".join(row["ops"])
        row["flags"] = ",".join(row["flags"])
    write_csv(args.run_dir / "out" / "00_clean_titles.csv", rows,
              ["record_id", "platform", "title", "clean_title", "ops", "flags",
               "reverted", "heat"])

    total = len(rows) or 1
    changed = sum(1 for r in rows if r["clean_title"] != r["title"])
    reverted = [r for r in rows if r["reverted"]]
    op_dist: dict[str, int] = {}
    for row in rows:
        for op in row["ops"].split(","):
            op_dist[op] = op_dist.get(op, 0) + 1
    print(f"清洗 {len(rows)} 条，改动 {changed} 条 = {changed / total:.1%}")
    print("动作分布：" + " ".join(f"{k}={v}" for k, v in
                              sorted(op_dist.items(), key=lambda kv: -kv[1])))
    if reverted:
        print(f"校验不通过已回退原标题 {len(reverted)} 条 = {len(reverted) / total:.1%}", file=sys.stderr)
        for row in reverted[:5]:
            print(f"  {row['flags']}\t{row['title'][:40]}", file=sys.stderr)
    report(relay, "00_clean_titles", args.run_dir / "run_manifest.json",
           {"platform": platform, "records": len(rows), "changed": changed,
            "reverted": len(reverted), "failed_batches": len(failures)})

    if failures:
        return 1
    print("\n下一步：打开 out/00_clean_titles.csv 逐条核对原标题与 clean_title。"
          "\n清洗是确定性产物，这是整条流水线里唯一能人工全量验收的环节，别跳过。"
          "\n重点看 reverted=True 与 ops 为空的行，再跑 01。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
