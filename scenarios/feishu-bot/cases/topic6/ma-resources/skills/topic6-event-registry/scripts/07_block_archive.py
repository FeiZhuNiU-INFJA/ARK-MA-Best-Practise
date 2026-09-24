#!/usr/bin/env python3
"""阶段 20：块内全景归档。一个块一次调用，块间完全独立、可并发。

块内一次看全，所以不存在「第一条记录事实上定义事件框架」的顺序依赖；
块间不含同事件对（实测召回 100%），所以不需要临时事件池、事后去重、二轮重跑
这些补丁。整个中间层被这一步吸收掉了。

输出结构：

  一级  event_name         标准事件名，主键
  二级  facets[].angle     宣传角度（红毯及造型 / 获奖结果 / 票数与争议）
  描述  event_description  说清触发点与讨论范围，用来核对合并对不对
  元数据 anchor / anchor_type / series_instance   跨窗口汇总用，不是主键

二级叫「角度」不叫「阶段」：实测模型填出来的是红毯及造型、票数与争议这类
传播切面，不是时间阶段。字段名要跟实际行为一致。

event_description 不是装饰。实测去掉它时，模型会把两个灵感方向的内容并成一个
事件；要求写出触发点后它自己就拆开了。它同时是人工核对合并对不对的依据。

锚点降级为元数据。它的目标是「最小可唯一识别」而不是「最通用」：实测沈腾
横跨龙餐馆与百花奖两个锚点、杨幂横跨 7 个事件、「中国」横跨 3 件无关的事。
但纯人物话题（`奇文 春雪` 这类）人物本身就是合格锚点，实测 8/8 正确。
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eventlib
from relay import Relay, read_jsonl, report, run_batches, write_csv, write_jsonl

ANCHOR_TYPES = {"WORK", "IP", "BRAND", "PRODUCT", "PERSON", "ORG",
                "DISASTER", "ACTIVITY", "TOURNAMENT", "PLACE", "OTHER"}

FEWSHOT = """# 少样本

## 一级：标准事件名的粒度

记录「欢迎来龙餐馆票房破八亿」「票房破五亿」「预售破亿」「总票房预测值升至35.4亿」
  对 → 一个事件「电影《欢迎来龙餐馆》票房进展」，各次突破进二级角度
  错 → 四个事件（这是同一次上映的数值更新，不是四次发生）

记录「台风白海豚移入湖北」「白海豚减弱为热带风暴」「无锡升级暴雨橙色预警」
     以及「官方辟谣许昌暴雨60万人断水」「官方密集通报涉汛谣言」
  对 → 两个事件：「台风白海豚登陆影响及各地灾情」+「官方密集辟谣涉汛谣言」
       前者灵感方向是灾情与应对，后者是信息治理，不是一个方向
  错 → 合成一个「台风白海豚相关」（要用空词才能覆盖，说明是两个方向）

记录「某企业关闭老店」「该企业调整房租政策」「该企业招聘刑释人员」
  对 → 优先合成一个事件，二级角度：门店调整 / 房租政策 / 用工政策
       对营销洞察来说这是一个方向「这个品牌本周有动静」
  但如果合并后只能叫「某企业系列事件」才覆盖得住 → 说明确实是多个方向，必须分开

记录「7.0剧情任务【无神怜爱的雪国】」「至冬大世界地图」「奥黛塔卡池」「新增射击玩法」
  对 → 一个事件「《原神》7.0至冬版本上线」
       二级角度：主线剧情 / 大世界地图 / 角色卡池 / 新玩法机制
  错 → 四个事件（这些是版本上线这一个触发点的侧面）

记录「第38届百花奖闭幕式」「金鹰奖男主提名曝光」
  对 → 两个事件（两个不同活动）

## 一级：名称超 20 字怎么办

`2026WTT瑞典大满贯国乒男单全军覆没及各场比赛进展`（27 字）
  对 → 压成「WTT瑞典大满贯国乒男单全军覆没」（16 字）。删掉年份和「各场比赛进展」
       这个空话，信息一点没少
  错 → 改成「WTT瑞典大满贯国乒相关战况」（省了字但变成空话）

`DeepSeek V4 Pro发布与撤回回滚`
  对 → 保持不动。按阅读单位算是 3+7=10 字，英文品牌名整串算 1 个单位
  错 → 为了凑字数删成「V4 Pro发布与撤回」（丢了品牌名，这是真的信息损失）

## 二级：同一场活动里的人物话题（最容易出错的地方）

记录「刘耀文漂亮孩子站中间」「杨幂现场换发型」「杨幂百花奖24票」「沈腾回应没拿最佳男主」
     「王宝强0票」「易烊千玺获最佳男主」，都发生在同一届百花奖现场
  对 → 一个一级事件「第38届百花奖颁奖典礼」
       二级角度：红毯及造型 / 获奖结果 / 票数与争议 / 落选回应 / 现场花絮
  错 → 拆成刘耀文/杨幂/沈腾/王宝强/易烊千玺各自一个一级事件
       （他们的触发点都是这一场典礼，不是各自独立发生）

**但这条规则被过度套用是本流程最主要的错误来源。** 实测一个 107 成员的百花奖事件里
混进 14 条无关记录，共同特征都是：只共享了同一个人，没有事件层面的关系。

判断依据是**动作发生在哪**——发生在这场活动的时空里就归入，发生在别的场合就不归。
标题里没有活动痕迹（活动名、届次，或红毯/获奖/提名/典礼/票数/影帝影后这类环节词）
的，要么放进 singletons，要么给低于 60 的 member_confidence，不要硬猜。

实测**被误塞进百花奖、应该剔除**的真实案例，括号里是该给的分数：

  `张元英口红像偷吃完辣条`      → 韩国艺人，与百花奖无关联（42）
  `沈腾路演泪崩`                → 路演是另一个活动场合（22）
  `沈腾说被导演讲进戏里`        → 同上，宣传场合发言（28）
  `陈思罕被摄像机撞到头`        → 另一个活动的现场花絮（18）
  `抓娃娃小演员易烊千玺合照`    → 另一部电影的剧组活动（32）
  `易烊千玺芭莎主编合照`        → 芭莎的场合（48）
  `易烊千玺还有三部待播作品`    → 关于未来作品的话题（48）
  `易烊千玺带松果出席活动`      → 未指明是哪个活动（48）
  `朱一龙法拉利老了也是法拉利`  → 只共享一个人，是颜值话题（45）
  `辛芷蕾又拿大奖了`            → 「又」暗示是另一个奖项（42）
  `喻言帮沈月捡到了三万块钱的耳夹` → 与本届评奖无关的八卦（45）

而以下同样只写人名加动作，但**应该保留**，因为推断成立：

  `易烊千玺一直在抠脑壳`   → 他是本届影帝，这是典礼现场的紧张反应（72）
  `是谁让易烊千玺笑出大牙` → 典礼现场花絮（68）
  `竟然不是高叶`           → 指本届某奖项结果出乎意料（72）
  `优秀影片 导演3票`       → 票数，是评奖环节本身（88）

两组的区别：后一组的动作发生在这场活动的时空里（现场反应、评奖结果），
前一组的动作发生在别的场合（路演、杂志活动、另一部戏、另一个奖）。

反例：记录「曝杨幂金鹰奖掉提名」
  对 → 独立一级事件，锚点是金鹰奖（触发点发生在百花奖之外）

## 置信度：两类容易被打低的高分情况

`国乒男单世界排名前10仅剩2人`，本批事件是「WTT瑞典大满贯国乒男单全军覆没」
  对 → 82 分。排名变化是本次赛果的直接后果，属于事件的后续影响
  错 → 55 分（判成「只共享国乒这个题材」）

`这届年轻人真的在整顿婚礼`，本批事件是「上半年结婚离婚登记数据发布及婚恋现象讨论」
  对 → 80 分。这个事件本身就是一个题材集合，同题材成员正是入选标准
  错 → 58 分（判成「只共享婚恋题材」）

判断方法：先看事件名和描述是「一次具体动作」还是「一个题材集合」。
是题材集合时，同题材成员就是高分。

## 二级：宣传角度怎么切

一场颁奖典礼的角度：红毯及造型 / 获奖结果 / 获奖感言 / 票数与争议 / 现场花絮 / 获奖后续
一部影片上映的角度：上映与预售 / 票房进展 / 演技与选角 / 剧情与隐喻解读 / 观后感
角度不必是时间先后。大型活动里按人物切也合理——一场颁奖礼上不同明星各自
带出的话题，就是这场活动的不同传播角度。只有一个角度就不填。

## 单条记录：只给 ID

记录「奇文 春雪」「李飞 使唤人」「法国最有松弛感的劫案是哪起？」
本批没有其他记录与它们是同一次发生
  对 → singletons: ["R...", "R...", "R..."]
  错 → 为每条编一个事件名和「发布内容引发关注」这类描述（纯浪费，还会编造信息）

## 元数据：锚点

记录「欢迎来龙餐馆票房破八亿」
  对 → `电影《欢迎来龙餐馆》`（作品类锚点带上类别前缀，同一实体只用这一种写法）
  错 → `沈腾`（他同时是本片主演和百花奖落选者，那是两个锚点）
  错 → `电影`（没有信息量）
  错 → `《欢迎来龙餐馆》片方`（派生后缀不构成新锚点）
  错 → `电影《欢迎来龙餐馆》上映及内容讨论事件`（这是句子不是锚点）

记录「奇文 春雪」（创作者内容，没有可指的作品或活动）
  对 → `奇文`（纯人物话题，人物就是锚点）
"""

SCHEMA = """# 输出结构

**event_name（一级，主键）**：这次发生的标准事件名。

- 一句话，**不超过 20 字**，能独立读懂
- 计长按**阅读单位**：中文字各计 1，连续的英文字母或数字串各计 1。
  所以 `DeepSeek V4 Pro发布与撤回回滚` 算 3+7=10 字，不算 22 字——英文品牌名
  对读者就是一个词，按字符数硬压只能删掉品牌名，那才是真的信息损失
- 不用「如何看待」「网友热议」这类外壳
- **压字数靠删修饰词，不许改成空话。** 实在压不进 20 字，说明这批成员本来该拆开

**怎么判断一个名字是不是空话**：把它单独拿给一个不了解背景的人看，他能不能知道
这是哪一件事、能不能据此想话题。能就不是空话，不能就是空话。

  不是空话 → `第38届百花奖颁奖典礼`（指明届次和活动，能定位到唯一一件事）
  不是空话 → `电影《欢迎来龙餐馆》上映及票房口碑`（指明作品和动作）
  是空话   → `颁奖典礼`（哪个颁奖典礼？）
  是空话   → `国乒男单各场比赛进展`（哪场？什么结果？）
  是空话   → `七夕节日相关活动`（什么活动？）
  是空话   → 一切「XX系列事件」「XX相关动态」「XX引发关注」「XX全程追踪」

压缩示例：
  `2026WTT瑞典大满贯国乒男单全军覆没及各场比赛进展`
    对 → `WTT瑞典大满贯国乒男单全军覆没`（删年份和「各场比赛进展」这个空话，
         信息一点没少）
    错 → `WTT瑞典大满贯国乒相关战况`（省了字但变成空话）

**名字要覆盖整批成员，不是描述其中最精彩的那一条。** 这条和「不许空话」是两个相反方向
的约束，必须同时满足——**只顾避免空话就会滑到另一头**：挑一个最具体的细节当名字，
它出处清楚、读得懂、字数也够短，但覆盖不了成员。实测这是多成员事件命名的主要失败方式。

  判断方法：把名字和成员列表一起看，问「有多少条记录跟这个名字说的不是同一件事」。
  挑得出一大半，就是名字太细了。

  错 → 53 条关于同一部剧的记录（剧集数据、演员花絮、剧情讨论、见面会宣传），
       命名成 `栾念为了见尚之桃飞西北20次`——这只是其中一条的剧情细节
  错 → 同一批记录命名成 `早春晴朗2026第二部云合破40%的剧`——拿一个数据点当名字
  对 → `剧集《早春晴朗》播出及口碑讨论`

**多成员事件的名字通常是「主体 + 这批记录共同的动作或状态」。** 主体取自锚点，动作取
成员的共同点，**不取任何单条的独有细节**。成员越多越杂，动作就该取得越概括——但概括
不是空话：`播出及口碑讨论` 说明了发生了什么类型的事，`相关动态` 什么都没说。

- 每个主体、数字、时间、届次都必须在记录原文里有出处。没出处的绝对不写，
  尤其届次、年份、编号——材料里没明确出现的一律不写，不要推断。
  也不许给作品补类型定位：材料只写「电视剧《某某》」就不能写成「法治剧《某某》」

**singletons（单条记录）**：不与本批任何其他记录构成同一事件的记录，
只把它的 record_id 放进 singletons 数组即可。**不要为它们写事件名、描述、锚点、角度**
——它们不需要合并，名称由程序沿用原标题。这样能省掉大量无意义的输出。

把一条记录放进 singletons 之前先确认：本批真的没有任何其他记录和它是同一次发生。
宁可放进某个事件，也不要把本该合并的记录丢进 singletons。

**facets（二级，宣传角度）**：这个事件被传播的不同切面，每个角度带自己的成员。
只有一个角度、或看不出角度区分时给空数组。角度名 4~10 字，不要用英文枚举。
角度可以按传播切面切（红毯及造型 / 票数与争议 / 剧情解读），大型活动里也可以
按具体人物切（刘耀文相关话题 / 杨幂相关话题）。两种都行，选更贴近实际传播的那种。

**同一场活动里围绕不同人物的话题，是该活动事件的二级角度，不独立成一级事件。**
颁奖礼上某人的造型、站位、票数、获奖、落选回应、同框，都是这一场活动的传播切面。
只有当该人物话题的触发点明显发生在这场活动之外时，才独立成一级事件。

**但这条最容易被过度套用。** 判断一条人物话题该不该归进活动事件，看标题里有没有
活动痕迹——出现活动名、届次，或红毯 / 获奖 / 提名 / 典礼 / 票数 / 影帝影后这类
环节词。没有痕迹的，要么放进 singletons，要么给低于 60 的 member_confidence，
不要硬猜。

**member_confidence（每条成员对本事件的归属置信度，0~100 整数，必填）**

事件的每一个成员都要给一个分数，说明「这条记录属于这个事件」的确信程度。
务必按这个刻度打分：

| 分数 | 含义 |
|---|---|
| 90~100 | 标题里直接写明了这件事：出现了活动名、届次、作品名，或明确写了这次动作 |
| 75~89 | 标题没点名这件事，但内容本身就是这件事的一个环节或后果 |
| 60~74 | 要补一步常识推断才能挂上，但推断成立 |
| 40~59 | 只共享了同一个人 / 同一个品牌 / 同一个题材，看不出和这件事的关系 |
| 0~39 | 明显是另一件事：另一个活动、另一部作品、另一个时间点 |

**75~89 这一档容易被漏用，以下两类都属于它，不要往下打分：**

1. **事件的后续影响与派生数据**。它是这件事造成的结果，不是另一件事。
   例：`国乒男单世界排名前10仅剩2人` 属于本次赛事的国乒失利事件（排名变化是
   赛果的直接后果）；`票房预测上调至35.4亿` 属于该片上映事件。

2. **话题桶型事件的同题材成员**。有些事件本身就是围绕一个题材聚起来的（节日营销
   热潮、某类数据集中发布、某类现象讨论）。对这类事件来说「共享同一题材」正是
   入选标准，不能当成「只共享题材」而打低分。
   例：`这届年轻人真的在整顿婚礼` 属于结婚登记数据发布及婚恋现象讨论事件。
   判断方法：先看事件名和描述——如果它本身是一个题材集合而不是一次具体动作，
   同题材成员就是高分。

**member_notes**：给每个低于 75 分的成员写一句话理由，说明凭什么挂上或挂不上。
理由要具体，不许写「关联度较低」这类空话。75 分以上的不用写。

**event_description（描述）**：一句话说清「谁、做了什么，导致这件事被讨论」，
以及讨论覆盖了哪些方面。必须能在本批记录原文里找到依据，不能凭空补。
这是判断合并对不对的核对依据：两个事件的 description 说的是同一件事，就该合并；
说的是两个不同的灵感方向，就该分开。

**anchor / anchor_type / series_instance（元数据，用于跨窗口汇总）**
- anchor：这些记录共同指向的具体事物，取能唯一识别它的最简称法。
  存在作品 / 产品 / 活动 / 赛事 / 品牌 / 机构时用它而不是用人物——人物常同时
  参与多件不相干的事。但纯人物话题（没有可指的作品或活动）就用人物本身。
  不要加 `片方` `参与者` `玩家` `官方` 这类派生后缀。
  **给你的「已知锚点」列表优先选用**，列表里没有再新建。
- anchor_type：WORK 作品 / IP / BRAND 品牌 / PRODUCT 产品 / PERSON 人物 /
  ORG 机构 / DISASTER 命名灾害 / ACTIVITY 活动 / TOURNAMENT 赛事 / PLACE 地点 / OTHER
- series_instance：同一锚点下存在多个实例时才填，如 `7.0至冬版本`、`第38届`、
  `2026赛季`；只有一个实例就填 null。**不同实例绝不能算同一个事件。**

# 硬性约束

1. 输入的每个 record_id 必须**恰好出现一次**：要么在某个事件的 member_record_ids 里，
   要么在 singletons 里。一条都不能漏、不能重复出现在两处。
   facets 里的成员必须是该事件成员的子集。
2. events 里的每个事件必须有 **2 个以上成员**。只有一个成员的一律放 singletons。
3. **每个事件成员都必须有 member_confidence**，一条都不能漏。
4. 若给了「已有历史事件」，本批记录可以归入它们——此时 event_key 直接填该历史
   事件的 ID，不要重命名。
5. name_evidence 里逐项写出 event_name 中每个要素的出处，引用记录原文片段。
6. 判不准某条记录归属时，放进 singletons，不要硬塞进别的事件。

只输出 JSON，不要任何解释。"""

SYSTEM = ("你是热点事件归档专家。给你一批同一主体邻域内的热搜记录，"
          "你要一次性给出这批记录的完整事件结构。\n\n"
          "这批记录已经由召回算法圈在一起，但它们**不一定属于同一个事件**——"
          "里面很可能包含多个事件，也可能混进完全无关的内容。你的任务是把它们正确划分。\n\n"
          + eventlib.TRIGGER_RULE + "\n\n"
          + eventlib.IDENTITY_RULES + "\n\n"
          + eventlib.RELATION_ENUM + "\n\n"
          + SCHEMA + "\n\n" + FEWSHOT)

USER_TMPL = """{anchors}{prior}本批记录（共 {n} 条）：

{records}

输出格式：
{{"events":[{{"event_key":"E1","event_name":"","event_description":"","facets":[{{"angle":"","member_record_ids":[]}}],"anchor":"","anchor_type":"","series_instance":null,"member_record_ids":[],"member_confidence":{{"R00001":95,"R00002":68}},"member_notes":{{"R00002":"未点名本次典礼，需推断是现场反应"}},"name_evidence":{{"主体":"","数字":"","时间":""}},"confidence":0.0}}],"singletons":[],"relations":[{{"a":"E1","b":"E2","relation_type":"","reason":""}}]}}

member_confidence 的键是 record_id、值是 0~100 整数，该事件每个成员都要有。
member_notes 只给低于 75 分的成员写。

再检查一遍：本批 {n} 个 record_id 是否都出现了、是否只出现一次；
每个事件成员是否都有 member_confidence。"""

ANCHOR_TMPL = "已知锚点（优先选用，同一个东西不要写出第二种形式）：\n{names}\n\n"
PRIOR_TMPL = ("已有历史事件（本批记录若属于其中某个，event_key 直接填它的 ID，"
              "不要重命名）：\n\n{cards}\n\n")

# 这些词出现在 event_name 里就说明合并出了问题，本地拦下来。
# 只是兜底——空话的主判在提示词里（能不能让不了解背景的人知道是哪件事），
# 因为关键词表拦不住变体：实测模型绕开「系列事件」写成「系列动态」就过了。
# 所以按「系列/相关/近期/综合 + 事件/动态/…」的组合拦，不逐个枚举。
_EW_HEAD = ["系列", "相关", "近期", "综合"]
_EW_TAIL = ["事件", "动态", "内容", "话题", "讨论", "进展", "动作", "报道", "汇总"]
EMPTY_WORDS = ([h + t for h in _EW_HEAD for t in _EW_TAIL]
               + ["全程追踪", "引发关注", "热点汇总"])

# 名称长度上限，按「阅读单位」计而不是字符数
NAME_MAX_UNITS = 20

# 空词命中后的重命名提示词。
#
# 为什么要有这一步：空词拦截原本直接把事件拆成单条，理由是「名字必须靠空词才能覆盖
# 成员，说明这批成员本来就不该在一起」。这个推理只在空词承重时成立。实测微博一周被拆掉
# 的 28 个事件（140 条记录）绝大多数是装饰性后缀——`LPL决赛AL夺冠及相关话题` 去掉
# 「及相关话题」就是 `LPL决赛AL夺冠`，完全具体，25 条记录白毁。
#
# 所以按已有的降级链模式处理：先重命名一次，仍是空词才拆。承重的空词模型第二次也写不出
# 具体名字（`杨洋近期动态引发关注` 剥完只剩人名），拆分的保护作用不变。
RENAME_SYSTEM = """你在给一批已经归档好的热点记录重新命名。成员划分是对的，不要改动，只重命名。

上一个名字因为含空话词被拒。空话词是「系列/相关/近期/综合」加「事件/动态/内容/话题/
讨论/进展/动作/报道/汇总」的组合，以及「引发关注」「全程追踪」「热点汇总」。

新名字要同时满足两条相反方向的约束：

1. 具体——单独拿给不了解背景的人看，他能知道这是哪一件事。指明主体和动作。
2. 覆盖整批成员——不是描述其中最精彩的那一条。挑一个具体细节当名字会满足第 1 条
   却覆盖不了成员，这是多成员事件命名的主要失败方式。

其他硬约束：不超过 20 个阅读单位（中文每字计 1，连续的字母数字串各计 1）；名字里每个词
都要能在成员标题里找到出处，不许补作品类型、不许补背景、不许改写数字形式。

多数情况下把空话后缀直接删掉就已经合格：`苹果折叠屏iPhone Duo发布及相关讨论`
→ `苹果折叠屏iPhone Duo发布`。如果删掉后只剩一个主体名、没有动作，说明这批成员确实是
多个方向，这时返回 null，不要硬凑。

只返回 JSON：{"event_name": "新名字"} 或 {"event_name": null}"""


def name_units(s: str) -> int:
    """名称长度按阅读单位算：中文字各计 1，连续的拉丁字母/数字串各计 1。

    纯字符数会让含英文品牌名的名称永远超限——`DeepSeek V4 Pro` 占 15 个字符，
    但对读者就是三个词。硬压只能删品牌名，那才是真的信息损失。
    """
    return (len(re.findall(r"[一-龥]", s))
            + len(re.findall(r"[A-Za-z0-9]+", s)))


def conf_of(raw: dict, rid: str) -> int | None:
    """取模型给该成员的归属置信度，取不到返回 None（不猜、不填默认值）。"""
    mc = raw.get("member_confidence") or {}
    v = mc.get(rid)
    if v is None:
        return None
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return None


def unsourced_words(name: str, pool: str) -> list[str]:
    """名称里无出处的中文词。防模型给作品补类型定位。

    实测它把「电视剧《重器》」改成「法治剧《重器》」、给「女单及女双」加「国乒」。
    判定放宽到「整词在池子里，或任意相邻二字片段在池子里」——因为名称里的复合词
    （`上映及票房口碑`）不会原样出现在任何标题里，严格匹配会误拒大量正常名称。
    代价是「法治剧」这类能溜过去（「法治」在某条成员标题里出现过），所以这只是
    拦明显编造，其余靠人工扫改名明细。
    """
    out = []
    for tok in re.findall(r"[一-龥]{2,}", name):
        if tok in pool:
            continue
        if any(tok[i:i + 2] in pool for i in range(len(tok) - 1)):
            continue
        out.append(tok)
    return out

# 锚点类型优先级：越靠前越具体，人物垫底
TYPE_PRIORITY = ["WORK", "PRODUCT", "EVENT_ENTITY", "GROUP", "BRAND", "ORG", "PLACE"]
TYPE_MAP = {"WORK": "WORK", "PRODUCT": "PRODUCT", "EVENT_ENTITY": "ACTIVITY",
            "GROUP": "ORG", "BRAND": "BRAND", "ORG": "ORG", "PLACE": "PLACE"}

# 只表示「谁做的」，不构成新锚点。实测模型会写出《欢迎来龙餐馆》片方、
# B萌应援参与者这类派生形式，导致同一个锚点裂成两个
DERIVED_SUFFIX = ["片方", "参与者", "玩家", "网友", "用户", "团队", "官方",
                  "粉丝", "观众", "主办方", "厂商", "剧组"]


# 分类前缀不改变锚点身份：电影《X》/《X》/X 是同一个锚点。
# 定义在 eventlib，阶段 x1 预拆也用同一份。
CATEGORY_PREFIX = eventlib.CATEGORY_PREFIX


def anchor_key(name: str) -> str:
    out = (name or "").strip()
    for _ in range(2):
        for prefix in CATEGORY_PREFIX:
            if out.startswith(prefix) and len(out) > len(prefix) + 1:
                out = out[len(prefix):].strip()
                break
        else:
            break
    return eventlib.norm_key(out)


def unify_anchors(events: list[dict]) -> int:
    # 同一实体的多种写法收敛到最明确的一个（最长、其次最高频）。确定性规则
    groups: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for event in events:
        if event["anchor"]:
            groups[anchor_key(event["anchor"])][event["anchor"]] += 1
    canonical = {}
    for key, variants in groups.items():
        canonical[key] = max(variants, key=lambda v: (len(v), variants[v], v))
    changed = 0
    for event in events:
        if not event["anchor"]:
            continue
        target = canonical[anchor_key(event["anchor"])]
        if target != event["anchor"]:
            event["anchor"] = target
            changed += 1
    return changed


def strip_derived(anchor: str) -> str:
    out = anchor.strip()
    for _ in range(3):
        for suffix in DERIVED_SUFFIX:
            if out.endswith(suffix) and len(out) > len(suffix) + 1:
                out = out[: -len(suffix)].strip()
                break
        else:
            break
    return out or anchor


def pick_specific_entity(members: list[str], frames: dict):
    # 从成员实体里挑类型最具体的那个。确定性规则，不做语义判断
    best = None
    for rid in members:
        for actor in frames[rid].get("actor") or []:
            atype = actor.get("type")
            name = (actor.get("name") or "").strip()
            if not name or atype not in TYPE_PRIORITY:
                continue
            rank = TYPE_PRIORITY.index(atype)
            if best is None or rank < best[0]:
                best = (rank, name, atype)
    return (best[1], TYPE_MAP[best[2]]) if best else None


def degrade_failed_blocks(failures: list[dict], blocks: list[dict],
                          frames: dict, events: list[dict]) -> int:
    """Ada接入补丁 2026-09-22: 块归档持续失败 → 优雅降级为标记 singleton,而非熔断整平台。

    背景: 全量微博跑中,某块(含政治敏感内容)被模型持续返回空/不可解析,重试(5×2)耗尽后
    进入 run_batches 的 failures,其记录不在 results → 不进 events → 覆盖断言
    (covered!=expect)raise SystemExit,导致 91/92 好块 + 全部记录仅因 1 块一起被丢。
    本函数把 skill 既有「漏返本地补为单条」原则(prompts.md L39 / main 内 singleton
    backfill)扩展到「整块失败」: 对失败块里每条 in frames 且尚未被任何事件认领的记录,
    补一个 singleton 事件,额外标 block_archive_failed=True 以区别于普通漏返兜底。
    这样 covered 重新 == expect,覆盖断言(有效完整性护栏,不改它)自然通过,平台继续。
    降级 singleton 走 main 后续 name_check(→SINGLETON_VERBATIM)/rename/split 时,
    因 singleton=True 且 member_count=1 被正确跳过。返回降级的记录条数。应同步上游。
    """
    if not failures:
        return 0
    covered = {m for e in events for m in e["member_record_ids"]}
    by_id = {b["block_id"]: b for b in blocks}
    degraded = 0
    for fail in failures:
        block = by_id.get(fail.get("batch_id"))
        if not block:
            continue
        for rid in block.get("record_ids", []):
            if rid not in frames or rid in covered:
                continue
            covered.add(rid)
            events.append({
                # _F_ 前缀区别于普通 singleton backfill(_S_) 与空词拆分(_X_),避免 id 撞车
                "event_id": f"{block['block_id']}_F_{rid[-6:]}",
                "block_id": block["block_id"],
                "event_name": frames[rid].get("title", ""),
                "event_description": "",
                "facets": [],
                "anchor": "", "anchor_type": "", "anchor_raw": "",
                "series_instance": None, "empty_words": [], "singleton": True,
                "member_record_ids": [rid], "member_count": 1,
                "canonical_anchor_record_id": rid,
                "anchor_title": frames[rid].get("title", ""),
                "identity_core": {"actor": "", "action": frames[rid].get("action", ""),
                                  "object": frames[rid].get("object", ""),
                                  "event_type": "OTHER"},
                "name_evidence": {}, "confidence": None, "fallback": True,
                "block_archive_failed": True,
                "member_confidence": {rid: None}, "member_notes": {},
                "state": {"latest_stage": frames[rid].get("stage"), "member_count": 1,
                          "key_facts": frames[rid].get("key_facts") or {},
                          "platforms": [frames[rid].get("platform", "")]},
            })
            degraded += 1
    return degraded


def render_record(frame: dict) -> str:
    actors = "、".join(a.get("name", "") for a in frame.get("actor") or []) or "-"
    line = f"[{frame['record_id']}] {frame.get('title', '')}"
    meta = (f"    主体：{actors} | 动作：{frame.get('action') or '-'}"
            f" | 对象：{frame.get('object') or '-'}")
    facts = frame.get("key_facts") or {}
    if facts:
        meta += f" | 事实：{json.dumps(facts, ensure_ascii=False)}"
    return line + "\n" + meta


def load_known_anchors(rd: Path, limit: int = 120) -> dict[str, str]:
    # 人物类不进候选列表：人物做锚点会把无关事件并起来。纯人物话题由模型自己判
    path = rd / "out" / "entity_registry.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    good = [r for r in rows
            if r.get("type") in {"WORK", "BRAND", "PRODUCT", "ORG", "EVENT_ENTITY",
                                 "GROUP", "PLACE"}
            and int(r.get("source_records") or 0) >= 2]
    good.sort(key=lambda r: -int(r.get("source_records") or 0))
    out: dict[str, str] = {}
    for r in good[:limit]:
        out[anchor_key(r["canonical_name"])] = r["canonical_name"]
        for alias in (r.get("aliases") or "").split("/"):
            if alias.strip():
                out.setdefault(anchor_key(alias), r["canonical_name"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--only-blocks", default="")
    ap.add_argument("--split-floor", type=int, default=12,
                    help="自动二分的下限：块小到这个条数就不再拆，直接报失败")
    ap.add_argument("--split-depth", type=int, default=3,
                    help="自动二分的最大层数。3 层可把 60 条拆到 7 条")
    # Ada接入补丁 2026-09-22（方案2·拒答缩块）：空返回/不可解析(疑似政治敏感块被模型拒答)
    # 专用的缩块下限与层数，比截断路径更小/更深——目标是把敏感标题浓度分散到很小的子块。
    # 60 条对半到 ≤5：60→30→15→8→4，需 4~5 层。floor=4 保证子块能试到 5→(2+3) 这类极小块。
    ap.add_argument("--refusal-split-floor", type=int, default=4,
                    help="疑似拒答缩块的下限：块小到这个条数仍空返回就不再拆，交降级兜底")
    ap.add_argument("--refusal-split-depth", type=int, default=5,
                    help="疑似拒答缩块的最大层数（比截断路径深，好把 60 条拆到 ≤5）")
    # 待用户换模型：claude-sonnet-4-6 对中国政治类簇策略过严是拒答根因，由用户直接换 --model
    # 解决(见 --model)。故本轮不加「跨平台回退到 DataHub 模型(DeepSeek-V4-Flash/gpt-5.4-nano)」
    # 的代码——那属异步 createtask+轮询，工程量大且与本同步逐块架构不兼容，留二期。
    ap.add_argument("--prior-events", type=Path, default=None)
    args = ap.parse_args()

    rd = args.run_dir
    frames_path = rd / "work" / "frames_resolved.jsonl"
    if not frames_path.exists():
        frames_path = rd / "work" / "frames.jsonl"
    for path in (frames_path, rd / "work" / "blocks.jsonl"):
        if not path.exists():
            raise SystemExit(f"缺少 {path}，先跑 06_build_blocks.py")

    frames = {f["record_id"]: f for f in read_jsonl(frames_path)}
    # frames 的 title 已是阶段 00 清洗后的标题，单成员事件名直接沿用，不再让模型加工
    blocks = list(read_jsonl(rd / "work" / "blocks.jsonl"))
    if args.only_blocks:
        want = {b.strip() for b in args.only_blocks.split(",") if b.strip()}
        blocks = [b for b in blocks if b["block_id"] in want]
        if not blocks:
            raise SystemExit(f"没有匹配的块：{sorted(want)}")
    prior = {e["provisional_event_id"]: e for e in read_jsonl(args.prior_events)} \
        if args.prior_events and args.prior_events.exists() else {}
    known = load_known_anchors(rd)
    print(f"待归档 {len(blocks)} 个块 / {sum(b['record_count'] for b in blocks)} 条记录"
          f"；已知锚点 {len(set(known.values()))} 个；system {len(SYSTEM)} 字符")

    relay = Relay(args.model, args.max_tokens, SYSTEM)
    auto_split: list[str] = []
    # Ada接入补丁 2026-09-22（方案2）：拒答缩块的审计轨迹。refusal_split 记每次缩块，
    # refusal_degraded 记「缩到底仍拒答、经主循环 backfill 降级为单条」的 record_id。应同步上游。
    refusal_split: list[str] = []
    refusal_degraded: list[str] = []
    split_lock = threading.Lock()

    def archive_once(ids: list[str]) -> dict:
        text = "\n".join(render_record(frames[r]) for r in ids)
        anchors_text = ""
        if known:
            hit = {known[k] for r in ids for a in (frames[r].get("actor") or [])
                   for k in [anchor_key(a.get("name"))] if k in known}
            if hit:
                anchors_text = ANCHOR_TMPL.format(
                    names="\n".join(f"  - {n}" for n in sorted(hit)))
        prior_text = ""
        if prior:
            hits = [e for e in prior.values()
                    if set(e["identity_core"].get("actor_ids") or [])
                    & {a for r in ids for a in (frames[r].get("actor_ids") or [])}]
            if hits:
                prior_text = PRIOR_TMPL.format(cards="\n".join(
                    f"[{e.get('stable_event_id') or e['provisional_event_id']}] "
                    f"{e['anchor_title']}" for e in hits[:12]))
        return relay.call_json(USER_TMPL.format(
            anchors=anchors_text, prior=prior_text, n=len(ids), records=text))

    def merge_payloads(parts: list[dict]) -> dict:
        """合并二分子块的产出。

        每个子块独立编号事件（都从 E1 开始），直接合并会撞号——下游用
        `块ID_event_key` 生成事件 ID，撞号会产出两个同 ID 的事件。所以这里给
        event_key 加子块前缀，并同步改写 relations 里的引用。
        """
        out: dict = {"events": [], "singletons": [], "relations": []}
        for i, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            remap: dict[str, str] = {}
            for ev in part.get("events") or []:
                old = str(ev.get("event_key"))
                new = f"H{i}{old}"
                remap[old] = new
                out["events"].append({**ev, "event_key": new})
            out["singletons"].extend(part.get("singletons") or [])
            for rel in part.get("relations") or []:
                a, b = remap.get(str(rel.get("a"))), remap.get(str(rel.get("b")))
                if a and b and a != b:
                    out["relations"].append({**rel, "a": a, "b": b})
        return out

    def archive_split(ids: list[str], block_id: str, depth: int = 0) -> dict:
        """截断时先原样重试，仍截断才对半拆分。替代「整阶段失败 → 手动 x1 → 重跑」。

        为什么先重试而不是直接拆：实测输出长度是随机的，不是块的固有属性。
        同一个块单独跑 4 次输出稳定在 7.5K 字符，而生产环境同一个块撞了 16000
        token 上限；另一个生产环境截断的块重跑只输出 11K。skill 原文说「截断
        重试无用」，但那前提是输出量由块决定——实测不是，所以重试有效且免费
        保住了块的完整性。

        截断(max_tokens)与拒答(空返回/不可解析)分两条 except 路径，成因不同：
        - 截断：relay.call() 直接抛 ValueError→原样重试 1 次→仍旧则对半拆(auto_split)。
        - 拒答：call_json 已做 5×(30s 窗口)退避仍失败才抛 RuntimeError，说明不是瞬时
          网关抖动(那会在窗口内自恢复)而是模型策略拒答(政治敏感块整块 60 条高浓度触发)。
          Ada接入补丁 2026-09-22（方案2）：对拒答块「缩块分散敏感标题浓度」递归再试——
          单块标题越少越不易触发拒答；缩到 refusal-split-floor 仍拒答的子块交主循环
          backfill 降级为单条，不连累兄弟子块，覆盖仍完整。根治靠换更宽松的模型
          (待用户换 --model)。应同步上游。

        拆过的块记进 auto_split(截断)/refusal_split(拒答) 并在结尾打印，便于事后复核。
        """
        try:
            return archive_once(ids)
        except ValueError as exc:
            if "max_tokens" not in str(exc):
                raise
            if depth == 0:
                print(f"[block] {block_id} 输出撞到 max_tokens，原样重试一次"
                      f"（输出长度是随机的，不必先拆）", flush=True)
                try:
                    return archive_once(ids)
                except ValueError as exc2:
                    if "max_tokens" not in str(exc2):
                        raise
            if len(ids) <= args.split_floor or depth >= args.split_depth:
                raise
            mid = len(ids) // 2
            with split_lock:
                auto_split.append(f"{block_id}(L{depth + 1}:{mid}+{len(ids) - mid})")
            print(f"[block] {block_id} 重试后仍截断，自动二分 "
                  f"{len(ids)}→{mid}+{len(ids) - mid}（第 {depth + 1} 层）", flush=True)
            return merge_payloads([
                archive_split(ids[:mid], block_id, depth + 1),
                archive_split(ids[mid:], block_id, depth + 1)])
        except RuntimeError as exc:
            # Ada接入补丁 2026-09-22（方案2·拒答缩块）。应同步上游。
            # 走到这里 = call_json 已做 5×(2/4/8/16s，30s 窗口)退避仍空返回/不可解析。
            # 那不是瞬时网关抖动(抖动在 30s 窗口内自恢复)，而是模型策略拒答(政治敏感块整块
            # 挤 60 条高浓度触发)。此前此分支不存在→RuntimeError 直接冒泡成失败块→整块记录
            # 丢失事件聚类降级为单条。现在「缩块分散敏感标题浓度」递归再试，尽量救回；
            # 救不回的子块再交降级(见下方 gather + worker 兜底)。
            # 换更宽松的模型是根治(claude-sonnet-4-6 对中国政治类簇过严)——待用户换 --model。
            if len(ids) <= args.refusal_split_floor or depth >= args.refusal_split_depth:
                raise
            mid = len(ids) // 2
            with split_lock:
                refusal_split.append(f"{block_id}(R L{depth + 1}:{mid}+{len(ids) - mid})")
            print(f"[block] {block_id} 疑似拒答，缩块重试 "
                  f"{len(ids)}→{mid}+{len(ids) - mid}（第 {depth + 1} 层，分散敏感浓度）",
                  flush=True)
            parts: list[dict] = []
            for sub in (ids[:mid], ids[mid:]):
                try:
                    parts.append(archive_split(sub, block_id, depth + 1))
                except Exception as sub_exc:
                    # 该子块缩到底仍拒答/超时/异常：记录 record_id，其记录经主循环 backfill
                    # 降级为单条(标 block_archive_refusal_degraded)；不让一个失败子块连累
                    # 已成功的兄弟子块，最大化救回、覆盖仍完整。捕获 Exception(而非仅
                    # RuntimeError/ValueError)是为让缩块途中的超时/网络错也只降级该子块。
                    with split_lock:
                        refusal_degraded.extend(sub)
                    print(f"[block] {block_id} 子块({len(sub)}条)缩到底仍失败，"
                          f"该子块记录降级为单条：{sub_exc!r}", file=sys.stderr, flush=True)
                    parts.append({"events": [], "singletons": [], "relations": []})
            return merge_payloads(parts)

    def worker(block: dict) -> dict:
        ids = [r for r in block["record_ids"] if r in frames]
        try:
            payload = archive_split(ids, block["block_id"])
        except Exception as exc:
            # Ada接入补丁 2026-09-22（bug3·稳健失败处理）。应同步上游。
            # 空返回(缩块也救不回)/超时/网络/其它异常一律不 hard-raise 中断整平台：
            #   ① 回看缓存 —— 已由 run_batches 前置的「内容哈希缓存」承担：命中即复用、
            #      根本不进 worker；走到这里=当前内容无可用缓存(重复查只会命中哈希不符的
            #      旧缓存=串味，故此处不再重复查)。
            #   ② 判失败 —— re-raise，run_batches 记入 failures 且不写缓存(避免把失败
            #      结果缓存成功)，并继续跑其余块。
            #   ③ 降级续跑 —— 随后 degrade_failed_blocks 把该块记录降级为标记单条，
            #      覆盖保持完整，main 末尾 return 0 不误报平台失败。
            # 超时(TimeoutError/OSError/URLError)同样落此路径=判失败→降级→继续，不抛错终止。
            kind = ("超时/网络" if isinstance(exc, OSError) else
                    "疑似拒答/空返回" if isinstance(exc, RuntimeError) else "异常")
            print(f"[block] {block['block_id']} {kind}且无可用缓存 → 判失败块，交降级续跑："
                  f"{exc!r}", file=sys.stderr, flush=True)
            raise
        return {"block_id": block["block_id"], "ids": ids, "payload": payload}

    results, failures = run_batches(
        [{**b, "batch_id": b["block_id"]} for b in blocks],
        worker, rd / "raw" / "block_archive", args.concurrency, "block")
    if auto_split:
        print(f"自动二分 {len(auto_split)} 次：{', '.join(auto_split)}\n"
              f"  这些块内部被切开过，跨半区的同一事件可能重复，建议复核", file=sys.stderr)
    if refusal_split:
        # Ada接入补丁 2026-09-22（方案2）：缩块救回轨迹。应同步上游。
        print(f"疑似拒答缩块 {len(refusal_split)} 次：{', '.join(refusal_split)}\n"
              f"  这些块因空返回被缩小后重试，跨子块的同一事件可能重复，建议复核",
              file=sys.stderr)
    if failures:
        print(f"有 {len(failures)} 个块失败，重跑即可续跑：{failures}", file=sys.stderr)

    events: list[dict] = []
    relations: list[dict] = []
    issues: collections.Counter = collections.Counter()
    # Ada接入补丁 2026-09-22（方案2）：缩块救不回、经 gather 降级的 record_id 全集。
    # 这些记录不在任何事件的 seen 里，会走下方 singleton backfill，此处用于打上区分标记。应同步上游。
    refusal_degraded_set = set(refusal_degraded)

    for res in results:
        ids, id_set = res["ids"], set(res["ids"])
        payload = res["payload"] if isinstance(res["payload"], dict) else {}
        seen: set[str] = set()
        key_map: dict[str, str] = {}
        for raw in payload.get("events", []):
            members = [str(m).strip() for m in raw.get("member_record_ids") or []]
            # Ada接入补丁 2026-09-21 (Bug-2): 单事件成员列内去重(保序)。模型偶发在同一
            # 事件的 member_record_ids 里重复返回同一 record_id, 使下游覆盖校验(见本文件
            # ~897 行 covered/expect 比对)出现重复项、实得>期望而误熔断(沙箱微博 BLK0007
            # 复现)。seen 只做「跨事件」去重且在本事件处理完后才 update, 故列内重复此前漏网。
            # 设计文档 prompts.md L39 本意即「每个 record_id 恰好出现一次」,列内去重不违背语义。
            # 应同步上游。
            _in_event: set[str] = set()
            deduped: list[str] = []
            for m in members:
                if m in id_set and m not in seen and m not in _in_event:
                    _in_event.add(m)
                    deduped.append(m)
            members = deduped
            if not members:
                issues["空事件或成员全部无效"] += 1
                continue
            if len(members) == 1:
                # 协议要求单成员走 singletons；模型仍这么返时按 singleton 处理
                issues["模型把单成员写成了事件"] += 1
                continue
            seen.update(members)
            eid = f"{res['block_id']}_{raw.get('event_key') or len(events)}"
            key_map[str(raw.get("event_key"))] = eid

            anchor_in = str(raw.get("anchor") or "").strip()
            anchor = strip_derived(anchor_in)
            if anchor != anchor_in:
                issues["锚点去派生后缀"] += 1
            normalized = known.get(anchor_key(anchor))
            if normalized and normalized != anchor:
                anchor, _ = normalized, issues.update(["锚点被归一"])
            atype = str(raw.get("anchor_type") or "").upper().strip()
            if atype not in ANCHOR_TYPES:
                atype = "OTHER"
                issues["anchor_type 非法"] += 1
            # 多成员事件不该以人物为锚点。单成员的纯人物话题合法（实测 8/8 正确）
            if atype == "PERSON" and len(members) > 1:
                better = pick_specific_entity(members, frames)
                if better:
                    anchor, atype = better
                    issues["人物锚点替换为更具体实体"] += 1

            name = str(raw.get("event_name") or "").strip()
            desc = str(raw.get("event_description") or "").strip()
            if not desc:
                issues["缺 event_description"] += 1
            empty = [w for w in EMPTY_WORDS if w in name]

            member_set = set(members)
            facets = []
            for facet in raw.get("facets") or []:
                kept = [m for m in facet.get("member_record_ids") or [] if m in member_set]
                angle = str(facet.get("angle") or "").strip()
                if kept and angle:
                    facets.append({"angle": angle, "members": kept})
            anchor_rid = max(members,
                             key=lambda r: (frames[r].get("information_score") or 0,
                                            float(frames[r].get("heat") or 0)))
            events.append({
                "event_id": eid, "block_id": res["block_id"],
                "event_name": name, "event_description": desc,
                "facets": facets,
                "anchor": anchor, "anchor_raw": anchor_in, "anchor_type": atype,
                "series_instance": (str(raw.get("series_instance")).strip()
                                    if raw.get("series_instance") else None),
                "empty_words": empty,
                "member_record_ids": members, "member_count": len(members),
                "member_confidence": {m: conf_of(raw, m) for m in members},
                "member_notes": {m: str((raw.get("member_notes") or {}).get(m))
                                 for m in members
                                 if (raw.get("member_notes") or {}).get(m)},
                "canonical_anchor_record_id": anchor_rid,
                "anchor_title": frames[anchor_rid].get("title", ""),
                "identity_core": {"actor": anchor,
                                  "action": frames[anchor_rid].get("action", ""),
                                  "object": frames[anchor_rid].get("object", ""),
                                  "event_type": atype},
                "name_evidence": raw.get("name_evidence") or {},
                "confidence": raw.get("confidence"),
                "state": {"latest_stage": frames[anchor_rid].get("stage"),
                          "member_count": len(members),
                          "key_facts": {k: v for r in members for k, v in
                                        (frames[r].get("key_facts") or {}).items()},
                          "platforms": sorted({frames[r].get("platform", "")
                                               for r in members
                                               if frames[r].get("platform")})},
            })
        declared = {str(m).strip() for m in payload.get("singletons") or []}
        for rid in ids:
            if rid in seen:
                continue
            # Ada接入补丁 2026-09-22（方案2）：区分「缩块救不回而降级」与「模型普通漏返」。应同步上游。
            refusal_mark = {}
            if rid in refusal_degraded_set:
                issues["疑似拒答缩块后仍失败降级为单条"] += 1
                refusal_mark = {"block_archive_refusal_degraded": True}
            elif rid not in declared:
                issues["模型漏返，本地补为单条"] += 1
            events.append({
                "event_id": f"{res['block_id']}_S_{rid[-6:]}",
                "block_id": res["block_id"],
                "event_name": frames[rid].get("title", ""),
                "event_description": "",
                "facets": [],
                # 单条记录不参与合并，锚点对它没有意义，留空避免「中国」
                # 「相关政府部门」这类泛化主体污染锚点表
                "anchor": "", "anchor_type": "", "anchor_raw": "",
                "series_instance": None, "empty_words": [], "singleton": True,
                "member_record_ids": [rid], "member_count": 1,
                "canonical_anchor_record_id": rid,
                "anchor_title": frames[rid].get("title", ""),
                "identity_core": {"actor": "", "action": frames[rid].get("action", ""),
                                  "object": frames[rid].get("object", ""),
                                  "event_type": "OTHER"},
                "name_evidence": {}, "confidence": None, "fallback": True,
                **refusal_mark,
                # 单条记录不参与合并，归属置信度对它没有意义
                "member_confidence": {rid: None}, "member_notes": {},
                "state": {"latest_stage": frames[rid].get("stage"), "member_count": 1,
                          "key_facts": frames[rid].get("key_facts") or {},
                          "platforms": [frames[rid].get("platform", "")]},
            })
        for rel in payload.get("relations") or []:
            a, b = key_map.get(str(rel.get("a"))), key_map.get(str(rel.get("b")))
            if a and b and a != b:
                relations.append({
                    "event_a": a, "event_b": b,
                    "relation_type": str(rel.get("relation_type") or "").upper(),
                    "reason": str(rel.get("reason") or "")[:120], "source": "block"})

    # Ada接入补丁 2026-09-22: 块归档持续失败 → 优雅降级(补 singleton),而非让覆盖断言熔断整平台。
    # 必须在 unify_anchors / name_check 之前补齐,降级 singleton 才能自然纳入后续处理。应同步上游。
    degraded_n = degrade_failed_blocks(failures, blocks, frames, events)
    if degraded_n:
        issues["块归档失败降级为单条"] += degraded_n
        failed_ids = [f.get("batch_id") for f in failures]
        print(f"[degrade] 块归档失败 {failed_ids},其 {degraded_n} 条记录降级为单条"
              f"(fallback),平台继续", file=sys.stderr, flush=True)

    unified = unify_anchors(events)
    if unified:
        issues["锚点写法统一"] += unified

    # 事实校验复用 14 的实现，同一份逻辑不写两遍
    stats: collections.Counter = collections.Counter()
    for event in events:
        if event.get("singleton"):
            event["name_check"] = {"status": "SINGLETON_VERBATIM",
                                   "unsupported": [], "empty_words": []}
            stats["SINGLETON_VERBATIM"] += 1
            continue
        pool = eventlib.source_pool(
            {**event, "parent_event_name": event.get("series_instance")}, frames)
        pool += "\n" + event["anchor"] + "\n" + event["event_description"]
        bad = eventlib.check_title(event["event_name"], pool) \
            if event["event_name"] else ["名称缺失"]
        # 数字之外，还查中文词的出处——防模型给作品补类型定位
        bad += unsourced_words(event["event_name"], pool)
        over = name_units(event["event_name"]) > NAME_MAX_UNITS
        if not event["event_name"]:
            event["event_name"], status = event["anchor_title"], "FALLBACK_MISSING"
        elif bad:
            event["event_name"], status = event["anchor_title"], "FALLBACK_UNSOURCED"
        elif event["empty_words"]:
            status = "EMPTY_WORD"  # 下面先重命名一次，仍不合格才拆成单条
        elif over:
            # 超长不改名也不拆——改名会引入无出处的词，拆要模型判。
            # 只标出来进 human_review，让人决定
            status = "TOO_LONG"
        else:
            status = "OK"
        event["name_check"] = {"status": status, "unsupported": bad,
                               "empty_words": event["empty_words"],
                               "name_units": name_units(event["event_name"])}
        stats[status] += 1

    # 空词命中先重命名一次，仍不合格才拆。和截断那条降级链同一个模式：
    # 先做代价小的动作，只在它失败后才走破坏性的那条。
    retry_pool = [e for e in events
                  if e["name_check"]["status"] == "EMPTY_WORD" and e["member_count"] > 1]
    if retry_pool:
        # 1200 而不是 400：输出只有一个名字，但模型偶尔会先写一段推理再给 JSON，
        # 400 会把它截断（实测 cap100/150 的大块上撞过）
        renamer = Relay(args.model, 1200, RENAME_SYSTEM)

        def rename_one(job: dict) -> dict:
            e = job["event"]
            titles = "\n".join(f"- {frames[m].get('title','')}"
                               for m in e["member_record_ids"])
            ask = (f"被拒的名字：{e['event_name']}\n"
                   f"命中的空话词：{'、'.join(e['empty_words'])}\n"
                   f"事件描述：{e['event_description']}\n"
                   f"锚点：{e['anchor']}\n\n"
                   f"{e['member_count']} 条成员标题：\n{titles}")
            return {"event_id": e["event_id"], "payload": renamer.call_json(ask)}

        renamed, rename_fail = run_batches(
            [{"batch_id": e["event_id"], "event": e} for e in retry_pool],
            rename_one, rd / "raw" / "rename", args.concurrency, "rename")
        got = {r["event_id"]: (r["payload"] or {}).get("event_name") for r in renamed}

        for e in retry_pool:
            new = str(got.get(e["event_id"]) or "").strip()
            if not new:
                continue
            pool = eventlib.source_pool(
                {**e, "parent_event_name": e.get("series_instance")}, frames)
            pool += "\n" + e["anchor"] + "\n" + e["event_description"]
            still = eventlib.check_title(new, pool) + unsourced_words(new, pool)
            empty = [w for w in EMPTY_WORDS if w in new]
            if still or empty or name_units(new) > NAME_MAX_UNITS:
                continue
            e["event_name"], e["empty_words"] = new, []
            e["name_check"] = {"status": "RENAMED_FROM_EMPTY_WORD", "unsupported": [],
                               "empty_words": [], "name_units": name_units(new)}
            stats["EMPTY_WORD"] -= 1
            stats["RENAMED_FROM_EMPTY_WORD"] += 1
            issues["空词事件重命名成功"] += 1
        if rename_fail:
            print(f"重命名有 {len(rename_fail)} 个失败，这些事件按原规则拆为单条",
                  file=sys.stderr)
        # 单独报一行。归档走缓存时主 relay 是 0 次调用，重命名的花费混进去就看不见了
        report(renamer, "07_rename", rd / "run_manifest.json",
               {"renamed": stats["RENAMED_FROM_EMPTY_WORD"], "tried": len(retry_pool)})

    # 重命名也给不出具体名字，说明名称确实要靠空词才能覆盖成员，这批成员本来就不该
    # 在一起。这时拆开是对的——改个名字继续合着只是把误合并藏起来。
    split_out = [e for e in events
                 if e["name_check"]["status"] == "EMPTY_WORD" and e["member_count"] > 1]
    if split_out:
        events = [e for e in events if e not in split_out]
        for bad in split_out:
            issues["空词事件被拆为单条"] += 1
            for rid in bad["member_record_ids"]:
                events.append({
                    "event_id": f"{bad['block_id']}_X_{rid[-6:]}",
                    "block_id": bad["block_id"],
                    "event_name": frames[rid].get("title", ""),
                    "event_description": "", "facets": [],
                    "anchor": "", "anchor_type": "", "anchor_raw": "",
                    "series_instance": None, "empty_words": [], "singleton": True,
                    "split_from": bad["event_id"],
                    "member_record_ids": [rid], "member_count": 1,
                    "canonical_anchor_record_id": rid,
                    "anchor_title": frames[rid].get("title", ""),
                    "identity_core": {"actor": "", "action": frames[rid].get("action", ""),
                                      "object": frames[rid].get("object", ""),
                                      "event_type": "OTHER"},
                    "name_evidence": {}, "confidence": None,
                    "member_confidence": {rid: None}, "member_notes": {},
                    "name_check": {"status": "SPLIT_FROM_EMPTY_WORD",
                                   "unsupported": [], "empty_words": []},
                    "state": {"latest_stage": frames[rid].get("stage"),
                              "member_count": 1,
                              "key_facts": frames[rid].get("key_facts") or {},
                              "platforms": [frames[rid].get("platform", "")]},
                })
        stats["EMPTY_WORD"] -= len(split_out)
        stats["SPLIT_FROM_EMPTY_WORD"] = sum(e["member_count"] for e in split_out)
        kept_ids = {e["event_id"] for e in events}
        relations = [r for r in relations
                     if r["event_a"] in kept_ids and r["event_b"] in kept_ids]

    covered = [m for e in events for m in e["member_record_ids"]]
    expect = [r for b in blocks for r in b["record_ids"] if r in frames]
    if sorted(covered) != sorted(expect):
        raise SystemExit(f"覆盖不一致：期望 {len(expect)} 实得 {len(covered)}，"
                         f"重复 {len(covered) - len(set(covered))}")

    events.sort(key=lambda e: (-e["member_count"], e["anchor"]))
    write_jsonl(rd / "work" / "block_events.jsonl", events)
    write_jsonl(rd / "work" / "block_relations.jsonl", relations)
    write_csv(rd / "out" / "块内归档结果.csv",
              [{"event_id": e["event_id"],
                "一级_事件名": e["event_name"],
                "二级_宣传角度": " / ".join(f["angle"] for f in e["facets"]),
                "描述": e["event_description"],
                "成员数": e["member_count"],
                "锚点": e["anchor"], "锚点类型": e["anchor_type"],
                "实例": e.get("series_instance") or "",
                "平台": "/".join(e["state"]["platforms"]),
                "名称校验": e["name_check"]["status"],
                "锚点原文": e["anchor_raw"],
                "block_id": e["block_id"],
                "confidence": e.get("confidence") or "",
                "成员标题": " ||| ".join(frames[m].get("title", "")
                                     for m in e["member_record_ids"])}
               for e in events],
              ["event_id", "一级_事件名", "二级_宣传角度", "描述", "成员数",
               "锚点", "锚点类型", "实例", "平台", "名称校验", "锚点原文",
               "block_id", "confidence", "成员标题"])
    write_csv(rd / "out" / "记录级归属.csv",
              [{"record_id": m, "platform": frames[m].get("platform", ""),
                "title": frames[m].get("title", ""),
                "event_id": e["event_id"], "一级_事件名": e["event_name"],
                "二级_宣传角度": next((f["angle"] for f in e["facets"]
                                 if m in f["members"]), ""),
                "锚点": e["anchor"], "实例": e.get("series_instance") or "",
                "事件成员数": e["member_count"],
                "归属置信度": ("" if (e.get("member_confidence") or {}).get(m) is None
                          else (e["member_confidence"])[m]),
                "归属说明": (e.get("member_notes") or {}).get(m, "")}
               for e in events for m in e["member_record_ids"]],
              ["record_id", "platform", "title", "event_id", "一级_事件名",
               "二级_宣传角度", "锚点", "实例", "事件成员数",
               "归属置信度", "归属说明"])

    by_anchor = collections.Counter(e["anchor"] for e in events if e["anchor"])
    sizes = [e["member_count"] for e in events]
    singles = sum(1 for s in sizes if s == 1)
    facet_events = [e for e in events if e["facets"]]
    print(f"\n{len(expect)} 条 → {len(events)} 个一级事件 / {len(by_anchor)} 个锚点")
    print(f"  最大 {max(sizes)} 成员 | 单成员 {singles} ({singles / len(events):.1%})"
          f" | 有二级角度 {len(facet_events)}"
          f"（共 {sum(len(e['facets']) for e in facet_events)} 个角度）"
          f" | 有实例 {sum(1 for e in events if e.get('series_instance'))}"
          f" | 关系 {len(relations)}")
    print(f"  名称校验：{dict(stats)}")
    print(f"  描述填充：{sum(1 for e in events if e['event_description'])}/{len(events)}")
    if issues:
        print(f"  本地修正与异常：{dict(issues)}")
    report(relay, "07_block_archive", rd / "run_manifest.json",
           {"blocks": len(blocks), "records": len(expect), "events": len(events),
            "anchors": len(by_anchor), "single_member": singles,
            "facet_events": len(facet_events), "relations": len(relations),
            "name_check": dict(stats), "issues": dict(issues)})
    # Ada接入补丁 2026-09-22: 降级补全后,失败块的记录已作为 block_archive_failed 单条纳入,
    # 覆盖断言(上方 ~970 行)已保证"跑到这里=所有记录均被覆盖(真事件或降级单条)",数据完整。
    # 故 failures 非空不再等于"平台失败"——原 `return 1 if failures` 会让编排器把已优雅降级的
    # 平台误判为失败并拒绝合并(实测微博因 1 个敏感块降级后仍 return 1 → 平台熔断)。改为 return 0,
    # 失败块的信号由 [degrade] WARN + block_archive_failed 标记 + issues 计数保留供人工复核。应同步上游。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
