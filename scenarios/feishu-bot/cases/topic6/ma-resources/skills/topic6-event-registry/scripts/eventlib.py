#!/usr/bin/env python3
"""事件层共用件：判定规则常量、分块工具、事实校验、锚点归一。

判定口径集中在 TRIGGER_RULE / IDENTITY_RULES / RELATION_ENUM 三个常量里，
被多个阶段拼进各自的 system 块。改口径只改这里——两份规则一定会漂移。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Sequence

try:
    import numpy as np
except ImportError:
    raise SystemExit("需要 numpy：pip install numpy")


# 共三处在判「是不是同一件事」：归档（07）、去重（09）、仲裁（12）。
# 判据必须完全一致，否则同一份数据在不同阶段得到不同结论。
# 传播事件的唯一判据：触发点
# 「可单点传播」这个说法会过度拆分——票房破2亿/3亿/4亿各自都能单独成一条热搜，
# 但它们背后只有一次上映。所以判据必须锚在触发点，不在话题。
TRIGGER_RULE = """判断一级事件怎么划分，先问这一句：

**这些热点背后能提供的创作灵感参考，是一个还是多个？**

一个 → 合成一个一级事件，不同切入点放进二级角度
多个 → 分成多个一级事件

可核对的代理判据是**触发点**：写不出各自独立的触发点，通常就是一个灵感方向。
反过来，两个事件的触发点说的是同一件事，就该合并。

不要用「能不能单独成为一条热搜」当判据——票房破2亿/3亿/4亿各自都能上榜，
但它们提供的创作灵感只有一个「影片大卖」。

按这条判据：
- 票房破2亿 / 破3亿 / 破4亿 / 破5亿 → 一个灵感方向，**一个事件**，各次进二级角度
- 影片上映 + 剧情解读 + 演技讨论 + 隐喻细节 → 一个灵感方向，**一个事件**
- 游戏某版本的剧情 / 地图 / 卡池 / 新玩法 → 一个灵感方向，**一个事件**
- 同一场典礼的红毯 / 获奖 / 感言 / 票数 / 某明星话题 → 一个灵感方向，**一个事件**
- 台风登陆致灾 vs 官方辟谣涉汛谣言 → 两个灵感方向（灾情 / 信息治理），**两个事件**
- 不同届次、不同版本号、不同赛季、不同场次 → **不同事件**

同一主体的多个事项（例如某企业关店与其招聘政策）：优先合成一个一级事件，
把各事项放进二级角度——对营销洞察来说「这个品牌这周有动静」是一个灵感方向。
但有一个硬约束：合并后的一级名称必须仍然具体。如果只能叫「某某系列事件」
「某某相关动态」才覆盖得住，说明它们确实是两个方向，必须分开。

第三方指控 + 当事方回应：属于判断项，不是固定规则。争议本身的讨论体量足以
独立支撑一个话题方向时独立成一级；体量小、只是主事件的一个插曲时作为二级角度。"""

IDENTITY_RULES = """判定「是否同一事件」只看事件身份三要素是否一致：
- 主体（actor）
- 动作（action）
- 对象（object）

以下都不是同一事件的依据：
- 文本相似
- 共享人物、品牌、题材、城市
- 时间接近或同在一周内
- 语义向量分数高

同一周上榜只说明有相同的趋势，不说明是同一件事。同锚点也不能默认同一件事。

必须区分开的几种情况：

同一主体的多个事项 → 优先合成一个事件，各事项进二级角度
  例：某企业关店 / 房租政策 / 招聘政策，对营销洞察是同一个方向「这个品牌有动静」
  硬约束：合并后的一级名称必须仍然具体。只能叫「某某系列事件」就说明该分开。

同一组合的不同成员各自活动 → 只是实体谱系关系，不构成同一事件
  但他们同时出现在同一场活动时，归入那场活动的事件，各人话题作为二级角度。

区分「多个灵感方向」与「同一方向的多个切入点」是本判据的核心：
  多个方向 → 拆。台风致灾与官方辟谣是两个方向。
  同一方向的多个切入点 → 合。一次版本更新、一次电影上映提供一个方向，
  剧情/玩法/票房/口碑都是它的切入点。

同一系列的不同届次或站点 → 兄弟事件，不是同一事件
  例：某赛事瑞典站 vs 横滨站

同一作品 / 游戏 / 产品的同一版本或同一发布周期 → 合并为一个事件
  主体用作品名指代。剧情、玩法、地图、卡池、角色、机制、活动，都是同一次更新的
  不同侧面，不是不同事件。
  例：某游戏 7.0 版本的主线剧情 / 支线 / 新地图 / 新角色卡池 / 新玩法机制 → 一个事件
  例：某片的定档 / 上映 / 票房进展 / 口碑 / 演技讨论 / 细节解读 → 一个事件
  不同版本号、不同赛季、不同代产品、不同作品才是不同事件。

同一事件的不同阶段 → 归入同一事件，用子事件记录阶段
  例：演唱会官宣 / 开票 / 正式演出 / 演后返图

对某事件的衍生讨论 → 相关事件，不是同一事件
  例：某片票房数据 vs 由此引发的行业救市讨论——后者的灵感方向是行业议题，不是这部片
  注意区分：对作品本身的讨论（剧情、演技、细节）属于该作品事件；
  由作品引发的行业性讨论（大盘、救市、同档期竞争）才是衍生讨论。

比赛不同场次：如果各场次都有独立的大量讨论，分别成事件挂同一父事件；讨论量都很少则合并为一个事件。

关于事件命名的隐含检验：如果合并后这个事件必须用"与"、"及"把两件事并列才能覆盖全部成员，或者只能叫"某某系列事件"、"某某相关动态"，说明不该合并。

注意这不是机械的字面规则。可执行的判据是**并列的几段有没有各自独立的主体**：

  不拆 → "某人逝世及各界悼念"（主体只有这个人，悼念是逝世的后续）
  不拆 → "台风某某登陆影响及各地灾情"（主体只有这个台风）
  不拆 → "某片上映及票房口碑"（一次上映的两个侧面）
  不拆 → "某人与某人完婚"（两个主体，但完婚这件事必须两个人）
  拆   → "甲参加某节目引发多话题及乙官宣参加"（甲和乙不相干，各做各的事）
  拆   → "某品牌关店、招聘与员工权益系列动态"（三件独立的事，且只能靠空词覆盖）

判据一句话：**并列的几段各自有独立主体，而这些主体又不是同一件事的必要参与方 → 拆。**
实测名称含并列连词的占多成员事件的 41%，绝大多数正确，所以不能见并列就拆。

同一场活动的各环节与各话题 → 合并为一个事件
  典礼、颁奖礼、发布会、展会、晚会、赛事单场都算「一场活动」。红毯、造型、
  获奖结果、感言、合影、同框、票数、节目单、闭幕，都是这一场活动的环节，
  不是独立事件。主体用活动名指代。
  不同届次、不同场次、不同年份的同名活动才是不同事件。

  **但这条规则最容易被过度套用，它是实测最主要的误合并来源。** 只有标题、没有
  时间地点，所以「这条到底发生在这场活动上吗」无法凭常识断定。硬性要求：
  **标题里必须有活动痕迹**——出现活动名、届次，或红毯 / 获奖 / 提名 / 典礼 /
  票数 / 影帝影后这类环节词。没有痕迹的不许猜，判成单条记录。

  实测一个 107 成员的颁奖典礼事件里混进 14 条无关记录，共同特征都是「只共享
  同一个人、没有事件层面的关系」：某艺人在另一场路演流泪、某艺人给杂志拍封面、
  某外国艺人的妆容话题、某人"又"拿了别的奖。它们都没有活动痕迹。

  反过来，同样只写人名加动作但**应该归入**的，区别在动作发生在这场活动的时空里：
  影帝在台上的紧张反应、现场花絮、对某个奖项结果的意外反应、票数讨论。

单条记录找不到合适的已有事件 → 它自己就是一个事件，事件名保留原标题
  不要为了凑齐主体/动作/对象去编造字段。三要素填不满就留空，
  也不要判成非事件——它仍然是一个上榜的热点。

精度优先于召回。宁可标相关或新建，也不要为了不丢关系而强行合并。错合会污染整个事件的身份定义，错拆还能在后续阶段补救。"""

RELATION_ENUM = """relation_type 从以下选一个：
DIFFERENT_PHASE  同一父事件的不同阶段
FOLLOW_UP        后续进展，但已独立成事件
CAUSE_EFFECT     一个导致另一个
REACTION         对某事件的回应或衍生讨论
SAME_ENTITY      同一主体或同一谱系的不同事项
SAME_SERIES      同一系列的不同届次或站点
OTHER_RELATED    确有关联但不属以上

OTHER_RELATED 是兜底值，只在前六种都明显不适用时才用。填 OTHER_RELATED 等于没给出关系。"""


import json
import re

# 数字正则必须带小数部分：用 \d+ 会把「1.3版本」拆成 1 和 3 两个假发现
NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 分类前缀不改变锚点身份：电影《X》/《X》/X 是同一个锚点。
# 只用于计算归一键，不用于展示——展示取最明确的那个写法。
# 阶段 07 归档与阶段 x1 预拆共用，两边必须是同一份。
CATEGORY_PREFIX = ["电影", "影片", "电视剧", "连续剧", "剧集", "网剧", "动画", "动漫",
                   "游戏", "手游", "综艺", "小说", "纪录片", "短剧"]


def source_pool(event: dict, frames: dict) -> str:
    """标题要素的合法出处：成员标题 + Frame 各字段 + key_facts + 时间。"""
    parts = [event["anchor_title"], event.get("parent_event_name") or ""]
    core = event["identity_core"]
    parts += [str(core.get(k) or "") for k in ("actor", "action", "object", "event_type")]
    parts.append(json.dumps(event["state"].get("key_facts") or {}, ensure_ascii=False))
    for rid in event["member_record_ids"]:
        frame = frames.get(rid) or {}
        parts.append(frame.get("title", ""))
        parts.append(str(frame.get("time") or ""))
        parts.append(str(frame.get("location") or ""))
        parts.append(json.dumps(frame.get("key_facts") or {}, ensure_ascii=False))
        for actor in frame.get("actor") or []:
            parts.append(actor.get("name", ""))
    return "\n".join(parts)


def check_title(title: str, pool: str) -> list[str]:
    """返回标题里无出处的数字要素。只查可字面核对的东西，不做语义判断。"""
    unsupported = []
    pool_nums = set(NUM_RE.findall(pool))
    for num in NUM_RE.findall(title):
        if num in pool_nums:
            continue
        # 34 出现在 pool 的 340 里不算出处，必须是独立数字
        unsupported.append(num)
    return unsupported

def load_stage(name: str):
    """加载以数字开头的阶段脚本（不能用 import 语句）。"""
    path = Path(__file__).resolve().parent / f"{name}.py"
    if not path.exists():
        raise SystemExit(f"缺少 {path}")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def norm_key(text: object) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def unit_matrix(vectors: dict, keys: Sequence[str]) -> "np.ndarray":
    matrix = np.asarray([vectors[k] for k in keys], dtype=np.float32)
    return matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)


def recall_event_cards(events: list[dict], vectors: dict, record_ids: list[str],
                       frames: dict, top_k: int, cand_map: dict[str, list[str]],
                       owner: dict[str, str], exclude: set[str] | None = None) -> list[dict]:
    """候选卡 = 邻居记录所在事件 ∪ 事件锚点向量 Top-K ∪ actor 命中。"""
    exclude = exclude or set()
    pool = {e["provisional_event_id"]: e for e in events
            if e["provisional_event_id"] not in exclude}
    if not pool:
        return []
    picked: dict[str, dict] = {}
    for rid in record_ids:
        for neighbor in cand_map.get(rid, []):
            eid = owner.get(neighbor)
            if eid in pool:
                picked[eid] = pool[eid]

    eids = list(pool)
    anchors = unit_matrix(vectors, [pool[e]["canonical_anchor_record_id"] for e in eids])
    probe = unit_matrix(vectors, record_ids)
    sims = anchors @ probe.T
    k = min(top_k, len(eids))
    for col in range(sims.shape[1]):
        for j in np.argsort(-sims[:, col])[:k]:
            picked[eids[j]] = pool[eids[j]]

    wanted = {aid for rid in record_ids
              for aid in (frames[rid].get("actor_ids") or [])}
    if wanted:
        for event in pool.values():
            if wanted & set(event["identity_core"].get("actor_ids") or []):
                picked[event["provisional_event_id"]] = event
    return sorted(picked.values(), key=lambda e: -len(e["members"]))


def rebuild_owner(events: list[dict]) -> dict[str, str]:
    owner: dict[str, str] = {}
    for event in events:
        for rid in event["members"]:
            owner[rid] = event["provisional_event_id"]
    return owner


def refresh_event(event: dict, frames: dict) -> dict:
    """成员变动后重算 anchor 与 state。Identity Core 不动。"""
    members = event["members"]
    if not members:
        return event
    anchor = max(members, key=lambda r: (frames[r].get("information_score") or 0,
                                         float(frames[r].get("heat") or 0)))
    event["canonical_anchor_record_id"] = anchor
    event["anchor_title"] = frames[anchor].get("title", "")
    stages = [frames[r].get("stage") for r in members if frames[r].get("stage")]
    facts: dict[str, str] = {}
    for rid in members:
        facts.update(frames[rid].get("key_facts") or {})
    event["state"].update({
        "latest_stage": stages[-1] if stages else None,
        "member_count": len(members),
        "key_facts": facts,
        "platforms": sorted({frames[r].get("platform", "")
                             for r in members if frames[r].get("platform")}),
    })
    event["support"] = len(members)
    return event


def greedy_batches(record_ids: list[str], cand_map: dict[str, list[str]],
                   frames: dict, size: int, tag: str) -> list[dict]:
    """取种子 + 直接邻居。有界的局部操作，邻居的邻居不会被拉进来。"""
    pool = set(record_ids)
    order = sorted(pool, key=lambda r: (-float(frames.get(r, {}).get("heat") or 0),
                                        -int(frames.get(r, {}).get("information_score") or 0),
                                        r))
    used: set[str] = set()
    batches = []
    for seed in order:
        if seed in used:
            continue
        group = [seed]
        used.add(seed)
        for neighbor in cand_map.get(seed, []):
            if len(group) >= size:
                break
            if neighbor in pool and neighbor not in used:
                group.append(neighbor)
                used.add(neighbor)
        batches.append({"batch_id": f"{tag}{len(batches):04d}",
                        "seed_record_id": seed, "record_ids": group})
    return batches
