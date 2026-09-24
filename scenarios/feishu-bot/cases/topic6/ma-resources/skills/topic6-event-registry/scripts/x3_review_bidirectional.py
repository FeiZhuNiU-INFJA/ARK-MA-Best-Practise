#!/usr/bin/env python3
"""阶段 x3：双向复核。合并该合的，拆开该拆的。

**为什么必须双向**：只做「过拆合回」会越合越脏。实测第一轮只合不拆，把 3 个小事件
并进了一个颁奖典礼事件，其中就有本该剔除的。合与拆是同一个判断的两面，一起判才准。

# 两个方向的候选召回（程序做，只负责排队，不定案）

过拆方向（该合的）四个通道，缺一不可：

  1. 锚点归一后相同，或存在包含关系。**这条最关键**——`百花奖` 与 `第38届百花奖`
     是两个不同字符串，第一轮没做包含匹配，导致两个百花奖事件（54 条 + 46 条）
     从没被放在一起比过
  2. 锚点相同且实例（届次）相同，兜住锚点写法差异
  3. 一个事件的锚点出现在另一个事件的名字里，捞回锚点被记成人名/机构的
     （`赵丽颖百花奖获奖` 的锚点被记成了电视台）
  4. 两个事件之间有足够多**被 cap 丢掉的召回边**（06 落在 skipped_edges.jsonl）。
     前三条全靠锚点字符串匹配，配不上锚点不相关的那类——`井柏然Luke穿搭走红` 与
     `早春晴朗播出` 是两个不相关的锚点，但之间有 167 条被丢的边

过合方向（该拆的）：名称含并列词、超 20 字、或疑似空话。

# 定案交模型，三条否决必须写进提示词

  1. 不同届次 / 站点 / 赛季 / 场次的同名赛事绝不合并。实测最大的误合并风险点：
     `WTT瑞典大满贯` 与 `WTT横滨冠军赛` 共享锚点、事件名都是「张本兄妹同时夺冠」，
     但是两场不同赛事
  2. 泛化锚点下的不相干事项绝不合并。锚点是「中国」「官方」「媒体」「日本」这类时，
     下面挂的很可能完全不相干（经济数据 / 生态规划 / 谣言治理）
  3. 拿不准就不动。错拆能补救，错合污染整个事件

用法：
    python3 x3_review_bidirectional.py --run-dir .            # 跑完整两个方向
    python3 x3_review_bidirectional.py --run-dir . --dry-run  # 只看候选与判定，不落地
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eventlib
from relay import Relay, log_run, make_batches, read_jsonl, run_batches, write_csv, write_jsonl

NAME_MAX_UNITS = 20
_EW_HEAD = ["系列", "相关", "近期", "综合"]
_EW_TAIL = ["事件", "动态", "内容", "话题", "讨论", "进展", "动作", "报道", "汇总"]
EMPTY_WORDS = ([h + t for h in _EW_HEAD for t in _EW_TAIL]
               + ["全程追踪", "引发关注", "热点汇总"])

NAME_RULES = """# 名称要求

- 不超过 20 字。计长按**阅读单位**：中文字各计 1，连续的英文字母或数字串各计 1。
  所以 `DeepSeek V4 Pro发布与撤回回滚` 算 10 字不算 22 字——英文品牌名对读者
  就是一个词，按字符数硬压只能删品牌名，那才是真的信息损失
- 压字数靠删修饰词，**不许改成空话**
- **怎么判断是不是空话**：把名字单独拿给一个不了解背景的人看，他能不能知道这是
  哪一件事、能不能据此想话题。能就不是空话

    不是空话 → `第38届百花奖颁奖典礼`（指明届次和活动）
    不是空话 → `电影《欢迎来龙餐馆》上映及票房口碑`（指明作品和动作）
    是空话   → `颁奖典礼`（哪个？）
    是空话   → `国乒男单各场比赛进展`（哪场？什么结果？）
    是空话   → 一切「XX系列事件」「XX相关动态」「XX引发关注」

- 名称里每个主体、数字、届次都必须在给你的材料里有出处，不许自己补。
  也不许给作品补类型定位：材料只写「电视剧《某某》」就不能写成「法治剧《某某》」

压缩示例：
  `2026WTT瑞典大满贯国乒男单全军覆没及各场比赛进展`（27 字）
    对 → `WTT瑞典大满贯国乒男单全军覆没`（16 字）。删年份和空话，信息没少
    错 → `WTT瑞典大满贯国乒相关战况`（省了字但变成空话）"""

PARALLEL_RULE = """# 并列连词怎么看

名字里出现「及」「与」「、」本身不是问题，要看并列的几段是不是同一个主体、
同一个触发点。实测名称含并列词的占多成员事件的 41%，绝大多数正确。

  不拆 → `朱镕基同志逝世及各界悼念`（主体只有一个人，悼念是逝世的后续）
  不拆 → `台风白海豚登陆影响及各地灾情`（主体只有这个台风）
  不拆 → `C罗与乔治娜正式完婚`（两个主体，但完婚这件事必须两个人）
  不拆 → `电影《欢迎来龙餐馆》上映及票房口碑`（一次上映的两个侧面）
  拆   → `王传君参加披哥引发多话题及马旭东官宣送考`
         （两个不相干的人各做各的事，两个独立触发点）
  拆   → `胖东来关店、招聘与员工权益系列动态`
         （三件独立的事，且只能靠空词覆盖）

判据：**并列的几段各自有独立主体，而这些主体又不是同一件事的必要参与方 → 拆。**"""

VETO = """# 三条否决（违反任何一条就不要合并）

1. **不同届次 / 站点 / 赛季 / 场次的同名赛事绝不合并。** 这是最容易出错的一类。
   `WTT瑞典大满贯` 与 `WTT横滨冠军赛` 共享锚点、事件名都可以写成「张本兄妹同时
   夺冠」，但它们是两场不同赛事，必须分开。同理不同版本号、不同代产品的发布。
2. **泛化锚点下的不相干事项绝不合并。** 锚点是「中国」「官方」「媒体」「日本」
   这类时，下面挂的很可能完全不相干（经济数据 / 生态规划 / 谣言治理），一律分开。
3. **拿不准就不动。** 错拆能补救（记录变成独立热点，不丢数据），
   错合会污染整个事件的身份定义。"""

MERGE_SYSTEM = ("你在复核一批热点事件，判断它们是同一件事被拆开了、还是本来就是"
                "不同的事。\n\n判据：**这些热点背后能提供的创作灵感参考，是一个还是"
                "多个？** 一个才合并。可核对的代理判据是触发点：写不出各自独立的"
                "触发点，就是一个方向。\n\n"
                "该合并的典型：同一次上映的票房/剧情/演技/幕后/口碑争议；同一场活动的"
                "红毯/获奖/感言/票数/花絮；同一游戏版本的剧情/地图/卡池/玩法。\n\n"
                + VETO + "\n\n" + PARALLEL_RULE + "\n\n" + NAME_RULES
                + "\n\n只输出 JSON，不要任何解释。")

MERGE_USER = """复核以下 {n} 组事件。每组内的事件共享同一个锚点或名称相互指涉。

{groups}

输出格式：
{{"results":[{{"anchor":"","merges":[{{"keep":"事件ID","absorb":["事件ID"],"merged_name":"","facet_names":{{"事件ID":"该事件转成的二级角度名"}},"reason":""}}],"keep_separate_reason":""}}]}}

- merges 里每项是一次合并：keep 是保留的事件 ID，absorb 是被并入的 ID 列表
- facet_names 给每个被并入的事件起一个二级角度名，4~10 字
- 该组不需要合并时 merges 给空数组，并在 keep_separate_reason 写明理由
- 一组内可以有多次合并（5 个事件里 3 个该合、另 2 个各自独立）
- 每个 anchor 必须出现且仅出现一次"""

SPLIT_SYSTEM = ("你在复核一批热点事件的命名与划分。对每个事件回答两件事："
                "内部是不是包含多个传播主题（该拆）；名称合不合格（该改名）。\n\n"
                "判据：**这些成员背后能提供的创作灵感参考，是一个还是多个？**\n"
                "可核对的代理判据是触发点：写不出各自独立的触发点，就是一个主题。\n\n"
                + PARALLEL_RULE + "\n\n" + NAME_RULES + "\n\n"
                "拿不准就不拆、不改名。错拆能补救，错合会污染整个事件。\n\n"
                "只输出 JSON，不要任何解释。")

SPLIT_USER = """复核以下 {n} 个事件。

{events}

输出格式：
{{"results":[{{"event_id":"","action":"KEEP|RENAME|SPLIT","new_name":"","splits":[{{"name":"","member_record_ids":[]}}],"reason":""}}]}}

- action=KEEP：名称合格、无需拆分。new_name 与 splits 留空
- action=RENAME：只改名不拆。new_name 填新名称，splits 留空
- action=SPLIT：要拆。splits 每项一个新事件，member_record_ids 必须覆盖原事件
  全部成员且不重不漏。拆出来只有 1 个成员的那一项也要列出
- 每个 event_id 必须出现且仅出现一次"""


def name_units(s: str) -> int:
    return len(re.findall(r"[一-龥]", s)) + len(re.findall(r"[A-Za-z0-9]+", s))


def has_source(tok: str, pool: str) -> bool:
    if tok in pool:
        return True
    return any(tok[i:i + 2] in pool for i in range(len(tok) - 1))


def unsourced(name: str, pool: str, only_short: bool = False) -> list[str]:
    """名称里没有出处的词。

    only_short=True 时只查 ≤3 字的词。**这个区分是必要的**：长的中文串往往是
    概括时跨标点拼出来的（`童年IP集体向00后发信互动活动` 切出 `后发信互动活动`），
    材料里当然没有原文，误报率高；而凭空加的定性词恰恰都短（`法治剧`『影帝级』）。
    所以召回时用全量（宁可多排队），校验时只用短词（不能因为误报拒掉好名字）。
    """
    toks = re.findall(r"[一-龥]{2,}", name)
    if only_short:
        toks = [t for t in toks if len(t) <= 3]
    return [t for t in toks if not has_source(t, pool)]


def norm_anchor(a: str) -> str:
    """归一锚点。去书名号、去分类前缀、去届次数字，让『百花奖』与『第38届百花奖』相遇。"""
    s = re.sub(r"[《》「」【】\"'\s]", "", a or "")
    for p in eventlib.CATEGORY_PREFIX:
        if s.startswith(p) and len(s) > len(p):
            s = s[len(p):]
    s = re.sub(r"^第?\d+[届季期代]", "", s)
    s = re.sub(r"^20\d\d年?", "", s)
    return s


class UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        while self.p.setdefault(x, x) != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def load_cut_pairs(rd, events: list[dict], min_edges: int
                   ) -> dict[tuple[str, str], int]:
    """把 06 丢掉的边翻译成事件对。没有 skipped_edges.jsonl 就返回空，通道自动关闭。"""
    path = rd / "work" / "skipped_edges.jsonl"
    if not path.exists() or min_edges <= 0:
        return {}
    r2e: dict[str, str] = {}
    multi = set()
    for e in events:
        if not e.get("singleton") and e["member_count"] > 1:
            multi.add(e["event_id"])
        for rid in e.get("member_record_ids") or []:
            r2e[rid] = e["event_id"]
    cnt: collections.Counter = collections.Counter()
    for d in read_jsonl(path):
        a, b = r2e.get(d.get("a")), r2e.get(d.get("b"))
        if a and b and a != b and a in multi and b in multi:
            cnt[tuple(sorted((a, b)))] += 1
    keep = {k: v for k, v in cnt.items() if v >= min_edges}
    if cnt:
        print(f"被 cap 丢掉的边 → {len(cnt)} 个跨事件对，"
              f"≥{min_edges} 条边的 {len(keep)} 个进入候选")
    return keep


def merge_candidates(events: list[dict], max_group: int,
                     cut_pairs: dict[tuple[str, str], int] | None = None) -> list[dict]:
    """四个通道召回该合的。只排队，不定案。"""
    multi = [e for e in events if not e.get("singleton") and e["member_count"] > 1]
    uf = UF()
    hit = collections.Counter()
    norms = {e["event_id"]: norm_anchor(e.get("anchor") or "") for e in multi}

    by_norm: dict[str, list[dict]] = collections.defaultdict(list)
    for e in multi:
        if len(norms[e["event_id"]]) >= 2:
            by_norm[norms[e["event_id"]]].append(e)

    # 通道 1：归一后相同
    for n, grp in by_norm.items():
        for e in grp[1:]:
            uf.union(grp[0]["event_id"], e["event_id"])
        if len(grp) > 1:
            hit["①归一后锚点相同"] += len(grp)

    # 通道 1b：归一后存在包含关系。这条是第一轮漏掉两个百花奖事件的直接原因
    keys = sorted(by_norm, key=len)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a != b and a in b:
                uf.union(by_norm[a][0]["event_id"], by_norm[b][0]["event_id"])
                hit["①锚点存在包含关系"] += 1

    # 通道 2：锚点原文相同且届次相同（兜住归一化没覆盖的写法差异）
    by_inst: dict[tuple, list[dict]] = collections.defaultdict(list)
    for e in multi:
        raw = re.sub(r"\s", "", e.get("anchor_raw") or e.get("anchor") or "")
        if len(raw) >= 2:
            by_inst[(raw, str(e.get("series_instance") or ""))].append(e)
    for grp in by_inst.values():
        for e in grp[1:]:
            uf.union(grp[0]["event_id"], e["event_id"])
        if len(grp) > 1:
            hit["②锚点与届次都相同"] += len(grp)

    # 通道 3：一个事件的锚点出现在另一个事件的名字里。捞回锚点被记成人名/机构的
    for e in multi:
        n = norms[e["event_id"]]
        if len(n) < 3:
            continue
        for o in multi:
            if o["event_id"] != e["event_id"] and n in o["event_name"]:
                uf.union(e["event_id"], o["event_id"])
                hit["③锚点出现在对方名字里"] += 1

    # 通道 4：被 cap 丢掉的召回边。06 撞到 cap 时把边丢了并落盘 skipped_edges.jsonl，
    # 那些边就是「召回层认为可能同事件、但装不进同一块」的连接。
    #
    # 为什么它抓得到前三条通道漏的：前三条全靠锚点字符串匹配，而
    # `井柏然Luke穿搭走红` 与 `早春晴朗播出` 的锚点是两个不相关的字符串，
    # 锚点匹配永远配不上——但它们之间有 167 条被丢的边。实测这一对正是混在
    # 早春晴朗块里的那 18 条记录被切开的地方。
    #
    # 按边数设门限而不是全收，因为并查集会把候选传递并接起来：≥10 条边时
    # 早春晴朗组里混进了刘恋入职奥美、倪妮考科目二；≥30 只剩 17 个对，
    # 覆盖 42→48 个事件，目标案例仍在。
    if cut_pairs:
        for (a, b), n in cut_pairs.items():
            if a in norms and b in norms:
                uf.union(a, b)
                hit["④被cap丢掉的边"] += 1

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for e in multi:
        groups[uf.find(e["event_id"])].append(e)
    out = []
    for grp in groups.values():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda e: -e["member_count"])
        # 组名取组内出现最多的归一锚点，不取最长的。取最长会把一次通道③噪声
        # （`六公主（央视六套）`）当成整组的名字，模型读到的前提就错了
        cnt = collections.Counter(norms[e["event_id"]] for e in grp if norms[e["event_id"]])
        label = (min(sorted(cnt, key=lambda k: (-cnt[k], len(k)))[:1], default="")
                 or grp[0]["event_name"])
        # 组太大时切片。切片会漏掉跨片的对，但一次塞太多事件模型判不准，两害取轻
        for i in range(0, len(grp), max_group):
            part = grp[i:i + max_group]
            if len(part) < 2:
                continue
            out.append({"anchor": label if i == 0 else f"{label}#{i//max_group+1}",
                        "events": part})
    print("过拆方向召回通道命中：" +
          " ".join(f"{k}={v}" for k, v in sorted(hit.items())))
    return out


PARALLEL_TOKENS = ["及", "与", "、", "以及", "和"]


def split_candidates(events: list[dict], frames: dict) -> list[dict]:
    """过合方向：名称含并列词、超 20 字、或疑似空话。"""
    out = []
    for e in events:
        if e.get("singleton") or e["member_count"] < 2:
            continue
        name = e["event_name"]
        why = []
        if any(t in name for t in PARALLEL_TOKENS):
            why.append("名称含并列词")
        if name_units(name) > NAME_MAX_UNITS:
            why.append(f"名称 {name_units(name)} 字超限")
        ew = [w for w in EMPTY_WORDS if w in name]
        if ew:
            why.append("疑似空话:" + "/".join(ew))
        pool = "".join(frames[r].get("title", "") for r in e["member_record_ids"]
                       if r in frames)
        us = unsourced(name, pool)
        if us:
            why.append("名称含材料里没有的词:" + "/".join(us))
        if why:
            out.append({"event": e, "reasons": why})
    return out


def titles_of(e: dict, frames: dict, limit: int) -> list[str]:
    ids = e["member_record_ids"][:limit]
    return [f"[{r}] {frames[r].get('title', '')}" for r in ids if r in frames]


def ask_merge(groups: list[dict], frames: dict, model: str, rd: Path,
              concurrency: int, show: int) -> tuple[dict, float]:
    relay = Relay(model, 16000, MERGE_SYSTEM)
    batches = make_batches(groups, 3, id_field="anchor", tag="MG")

    def worker(b: dict) -> dict:
        blocks = []
        for g in b["items"]:
            lines = [f"## 共享锚点：{g['anchor']}"]
            for e in g["events"]:
                lines.append(
                    f"\n### {e['event_id']}｜{e['event_name']}"
                    f"（{e['member_count']} 条，锚点 {e.get('anchor') or '无'}"
                    f"／届次 {e.get('series_instance') or '无'}）\n"
                    f"描述：{(e.get('event_description') or '（无）')[:160]}\n"
                    + "\n".join("  " + t for t in titles_of(e, frames, show)))
            blocks.append("\n".join(lines))
        p = relay.call_json(MERGE_USER.format(n=len(b["items"]),
                                             groups="\n\n".join(blocks)))
        return {"batch_id": b["batch_id"], "results": p.get("results", [])}

    res, fail = run_batches(batches, worker, rd / "raw" / "x3_merge",
                            concurrency, "x3_merge")
    if fail:
        print(f"过拆方向有 {len(fail)} 批失败，重跑本脚本可续跑：{fail}", file=sys.stderr)
    return {x.get("anchor", ""): x for p in res for x in p.get("results", [])}, relay.cost()


def ask_split(cands: list[dict], frames: dict, model: str, rd: Path,
              concurrency: int) -> tuple[dict, float]:
    relay = Relay(model, 16000, SPLIT_SYSTEM)
    rows = [{"event_id": c["event"]["event_id"], "c": c} for c in cands]
    batches = make_batches(rows, 4, id_field="event_id", tag="SP")

    def worker(b: dict) -> dict:
        blocks = []
        for row in b["items"]:
            e, why = row["c"]["event"], row["c"]["reasons"]
            blocks.append(
                f"### {e['event_id']}｜{e['event_name']}"
                f"（{e['member_count']} 条，名称 {name_units(e['event_name'])} 字）\n"
                f"被挑出来的原因：{'；'.join(why)}\n"
                f"描述：{(e.get('event_description') or '（无）')[:160]}\n"
                f"全部成员：\n" + "\n".join("  " + t for t in
                                        titles_of(e, frames, 999)))
        p = relay.call_json(SPLIT_USER.format(n=len(b["items"]),
                                             events="\n\n".join(blocks)))
        return {"batch_id": b["batch_id"], "results": p.get("results", [])}

    res, fail = run_batches(batches, worker, rd / "raw" / "x3_split",
                            concurrency, "x3_split")
    if fail:
        print(f"过合方向有 {len(fail)} 批失败，重跑本脚本可续跑：{fail}", file=sys.stderr)
    return {x.get("event_id", ""): x for p in res for x in p.get("results", [])}, relay.cost()


def check_name(name: str, pool: str) -> list[str]:
    bad = []
    if not name.strip():
        bad.append("空名称")
    if name_units(name) > NAME_MAX_UNITS:
        bad.append(f"{name_units(name)} 字超 {NAME_MAX_UNITS}")
    ew = [w for w in EMPTY_WORDS if w in name]
    if ew:
        bad.append("空话:" + "/".join(ew))
    us = unsourced(name, pool, only_short=True)
    if us:
        bad.append("无出处:" + "/".join(us))
    return bad


def apply_merge(events: list[dict], verdicts: dict, groups: list[dict],
                frames: dict) -> list[dict]:
    by_id = {e["event_id"]: e for e in events}
    log, dropped = [], set()
    for g in groups:
        v = verdicts.get(g["anchor"])
        if not v:
            continue
        for m in v.get("merges") or []:
            keep = by_id.get(m.get("keep"))
            absorb = [by_id[a] for a in (m.get("absorb") or [])
                      if a in by_id and a not in dropped and a != m.get("keep")]
            if not keep or keep["event_id"] in dropped or not absorb:
                continue
            fn = m.get("facet_names") or {}
            for a in absorb:
                keep["member_record_ids"] += [r for r in a["member_record_ids"]
                                              if r not in keep["member_record_ids"]]
                keep.setdefault("facets", []).append(
                    {"angle": fn.get(a["event_id"]) or a["event_name"],
                     "members": a["member_record_ids"]})
                keep["member_confidence"] = {**(keep.get("member_confidence") or {}),
                                             **(a.get("member_confidence") or {})}
                keep["member_notes"] = {**(keep.get("member_notes") or {}),
                                        **(a.get("member_notes") or {})}
                dropped.add(a["event_id"])
                log.append({"锚点": g["anchor"], "保留事件": keep["event_id"],
                            "被并入事件": a["event_id"],
                            "被并入事件名": a["event_name"],
                            "并入条数": a["member_count"],
                            "转成的二级角度": fn.get(a["event_id"]) or a["event_name"],
                            "合并后名称": m.get("merged_name") or keep["event_name"],
                            "理由": m.get("reason", "")})
            keep["member_count"] = len(keep["member_record_ids"])
            pool = "".join(frames[r].get("title", "") for r in keep["member_record_ids"]
                           if r in frames)
            new = (m.get("merged_name") or "").strip()
            if new and not check_name(new, pool):
                keep["event_name"] = new
            keep["origin"] = "x3_merged"
    return [e for e in events if e["event_id"] not in dropped], log


def apply_split(events: list[dict], verdicts: dict, frames: dict) -> tuple[list[dict], list]:
    """落地拆分与改名。三道校验：成员覆盖、名称字数、名称有出处。任一不过就整条不动。"""
    out, log = [], []
    for e in events:
        v = verdicts.get(e["event_id"])
        act = (v or {}).get("action", "KEEP")
        pool = "".join(frames[r].get("title", "") for r in e["member_record_ids"]
                       if r in frames)
        if not v or act == "KEEP":
            out.append(e)
            if v:
                log.append({"事件ID": e["event_id"], "原名称": e["event_name"],
                            "动作": "保持不动", "结果": "", "理由": v.get("reason", "")})
            continue

        if act == "RENAME":
            new = (v.get("new_name") or "").strip()
            bad = check_name(new, pool)
            if bad:
                log.append({"事件ID": e["event_id"], "原名称": e["event_name"],
                            "动作": "改名被拒", "结果": new,
                            "理由": "；".join(bad)})
            else:
                log.append({"事件ID": e["event_id"], "原名称": e["event_name"],
                            "动作": "改名", "结果": new, "理由": v.get("reason", "")})
                e["event_name"] = new
                e["origin"] = "x3_renamed"
            out.append(e)
            continue

        parts = v.get("splits") or []
        flat = [r for p in parts for r in (p.get("member_record_ids") or [])]
        problems = []
        if set(flat) != set(e["member_record_ids"]):
            miss = set(e["member_record_ids"]) - set(flat)
            extra = set(flat) - set(e["member_record_ids"])
            problems.append(f"成员不匹配（漏 {len(miss)} 多 {len(extra)}）")
        if len(flat) != len(set(flat)):
            problems.append("有记录被分到多个子事件")
        for p in parts:
            if len(p.get("member_record_ids") or []) > 1:
                bad = check_name((p.get("name") or "").strip(), pool)
                if bad:
                    problems.append(f"{p.get('name')}: {'；'.join(bad)}")
        if problems:
            log.append({"事件ID": e["event_id"], "原名称": e["event_name"],
                        "动作": "拆分被拒", "结果": f"拟拆 {len(parts)} 个",
                        "理由": "；".join(problems)})
            out.append(e)
            continue

        for i, p in enumerate(parts, 1):
            ids = p["member_record_ids"]
            single = len(ids) == 1
            nm = frames[ids[0]].get("title", "") if single else p["name"].strip()
            facets = [{**f, "members": [m for m in f.get("members") or [] if m in ids]}
                      for f in e.get("facets") or []]
            out.append({**e, "event_id": f"{e['event_id']}X{i}",
                        "event_name": nm, "singleton": single,
                        "event_description": "" if single else e.get("event_description", ""),
                        "member_record_ids": ids, "member_count": len(ids),
                        "facets": [] if single else [f for f in facets if f["members"]],
                        "member_confidence": {k: vv for k, vv in
                                              (e.get("member_confidence") or {}).items()
                                              if k in ids},
                        "member_notes": {k: vv for k, vv in
                                         (e.get("member_notes") or {}).items() if k in ids},
                        "origin": "x3_split",
                        "name_check": {"status": "SINGLETON_VERBATIM" if single else "OK",
                                       "unsupported": [], "empty_words": []}})
            log.append({"事件ID": e["event_id"], "原名称": e["event_name"],
                        "动作": f"拆出 {i}/{len(parts)}", "结果": f"{nm}（{len(ids)} 条）",
                        "理由": v.get("reason", "")})
    return out, log


def audit(events: list[dict], expect: int, where: str) -> None:
    seen: dict[str, str] = {}
    for e in events:
        for r in e["member_record_ids"]:
            if r in seen:
                raise SystemExit(f"{where}：记录 {r} 同时属于 "
                                 f"{seen[r]} 和 {e['event_id']}，已中止未写入")
            seen[r] = e["event_id"]
    if len(seen) != expect:
        raise SystemExit(f"{where}：记录数 {len(seen)} != 入口 {expect}，已中止未写入")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-group", type=int, default=12,
                    help="一组最多放几个事件。泛化锚点下的组会很大，切片处理")
    ap.add_argument("--cut-edge-min", type=int, default=30,
                    help="通道④门限：两个事件之间至少有几条被 cap 丢掉的边才进候选。"
                         "门限低了会被传递并接污染——实测微博 ≥10 时早春晴朗组里混进"
                         "刘恋入职奥美、倪妮考科目二；≥30 只剩 17 个对、噪声明显少，"
                         "且仍召回到目标案例。设 0 关闭该通道")
    ap.add_argument("--show", type=int, default=8,
                    help="过拆方向每个事件展示几条成员标题")
    ap.add_argument("--only", choices=["both", "merge", "split"], default="both")
    ap.add_argument("--dry-run", action="store_true", help="只看候选，不调模型不落地")
    args = ap.parse_args()

    rd = args.run_dir
    src = rd / "work" / "block_events.jsonl"
    if not src.exists():
        raise SystemExit(f"缺少 {src}，先跑 07_block_archive.py")
    fp = rd / "work" / "frames_resolved.jsonl"
    if not fp.exists():
        fp = rd / "work" / "frames.jsonl"
    events = list(read_jsonl(src))
    frames = {f["record_id"]: f for f in read_jsonl(fp)}
    total = sum(e["member_count"] for e in events)
    n0 = len(events)
    print(f"入口：{n0} 个事件，{total} 条记录")

    cost = 0.0
    mlog: list[dict] = []
    if args.only in ("both", "merge"):
        groups = merge_candidates(events, args.max_group,
                                 load_cut_pairs(rd, events, args.cut_edge_min))
        print(f"过拆方向候选 {len(groups)} 组，"
              f"覆盖 {sum(len(g['events']) for g in groups)} 个事件")
        if args.dry_run:
            for g in groups[:20]:
                print(f"  [{g['anchor']}] " +
                      " ｜ ".join(f"{e['event_id']}:{e['event_name']}({e['member_count']})"
                                 for e in g["events"]))
        elif groups:
            v, c = ask_merge(groups, frames, args.model, rd, args.concurrency, args.show)
            cost += c
            events, mlog = apply_merge(events, v, groups, frames)
            audit(events, total, "合并后")
            print(f"合并：{n0} → {len(events)} 个事件（并掉 {len(mlog)} 个）")

    slog: list[dict] = []
    if args.only in ("both", "split"):
        # 必须在合并落地之后重算。在旧注册表上算候选、再落到新注册表上，会丢掉合并结果
        cands = split_candidates(events, frames)
        print(f"过合方向候选 {len(cands)} 个事件")
        if args.dry_run:
            for c in cands[:20]:
                print(f"  {c['event']['event_id']}: {c['event']['event_name']}"
                      f"（{c['event']['member_count']} 条）← {'；'.join(c['reasons'])}")
        elif cands:
            v, c = ask_split(cands, frames, args.model, rd, args.concurrency)
            cost += c
            before = len(events)
            events, slog = apply_split(events, v, frames)
            audit(events, total, "拆分后")
            acts = collections.Counter(r["动作"].split(" ")[0] for r in slog)
            print(f"拆分与改名：{before} → {len(events)} 个事件｜" +
                  " ".join(f"{k}={n}" for k, n in acts.items()))

    if args.dry_run:
        print("\n（dry-run，未调模型、未改动 block_events.jsonl）")
        return 0

    write_jsonl(src, events)
    if mlog:
        write_csv(rd / "out" / "x3_合并明细.csv", mlog,
                  ["锚点", "保留事件", "被并入事件", "被并入事件名", "并入条数",
                   "转成的二级角度", "合并后名称", "理由"])
    if slog:
        write_csv(rd / "out" / "x3_拆分与改名明细.csv", slog,
                  ["事件ID", "原名称", "动作", "结果", "理由"])

    multi = [e for e in events if e["member_count"] > 1]
    bad = [e["event_name"] for e in multi
           if name_units(e["event_name"]) > NAME_MAX_UNITS
           or any(w in e["event_name"] for w in EMPTY_WORDS)]
    print(f"\n出口：{len(events)} 个事件（多成员 {len(multi)} / "
          f"单条 {len(events)-len(multi)}），{total} 条记录未变")
    print(f"仍不合格的名称 {len(bad)} 个" + (f"：{bad[:5]}" if bad else "（0）"))
    print(f"本次成本 ${cost:.4f}")
    if mlog:
        print("→ out/x3_合并明细.csv")
    if slog:
        print("→ out/x3_拆分与改名明细.csv")
    log_run(rd / "run_manifest.json",
            {"stage": "x3_review_bidirectional", "model": args.model,
             "events_in": n0, "events_out": len(events), "records": total,
             "merged": len(mlog), "split_log": len(slog),
             "bad_names_left": len(bad), "cost_usd": round(cost, 4)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
