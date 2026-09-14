"""equivalence.py —— 冻结层：重放轨迹与原轨迹的「等效性」评估（永不随客户变）。

性能对比（report.py）只回答"MA 跑得快不快/省不省"；本模块回答另一半、也是更该担心的问题：
**在 MA 上重放出来的这条轨迹，和客户原轨迹是不是"同一回事"？**

拆成两层，各自独立可读：
  1) 业务逻辑等效性 —— 比对"业务工具调用序列"。原轨迹调了哪些 skill/接口、按什么顺序，
     MA 侧重放时是否复现。给出：
       - coverage   ：原轨迹的业务调用有多少被 MA 复现（|O∩M|/|O|）；
       - extra      ：MA 多做了多少条原轨迹没有的调用（|M−O|，custom 模式下这些多半就是 REPLAY_MISS）；
       - jaccard    ：调用集合相似度 |O∩M|/|O∪M|；
       - order      ：调用**顺序**一致性（最长公共子序列 / |O|）。
     MA 侧每条轨迹有多次并发重复（rep），逐 rep 算再取均值，并留最好/最差 rep 作参考。
  2) 最终产物等效性 —— 比对两侧"最终交付文本"。给结构指标（长度比、章节标题覆盖率），
     并可选调一个 judge 回调（LLM 语义判分）打"是否等效"的分。

分层原则（和 report.py 一致）：
  * 冻结层只做"平台无关"的对齐算法与编排；
  * "哪个工具算业务调用、调用键怎么算、最终产物从哪取、judge 怎么打分"全是**客户特异**，
    由调用方（客户样板 run.py）以回调传入，本文件不假设轨迹/事件结构。

自包含：只用标准库。
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Callable, Optional

# ── 回调签名（全部客户特异，由 run.py 提供）──
# 一次业务调用统一表示成 dict：{"key": 可比对的规范键, "name": 工具名, "label": 人读摘要}。
Call = dict
OrigCallsFn = Callable[[Path], "list[Call]"]      # 原轨迹文件 -> 业务调用序列
MaCallsFn = Callable[[Path], "list[Call]"]        # 一个 rep 的 events.jsonl -> 业务调用序列
OrigProductFn = Callable[[Path], str]             # 原轨迹文件 -> 最终产物文本
MaProductFn = Callable[[Path], str]               # 一个 rep 的 events.jsonl -> 最终产物文本
# judge(轨迹名, 原产物, MA产物) -> {"score": 0-100, "verdict": 简评, "notes": 细节}；不给则跳过语义判分。
JudgeFn = Callable[[str, str, str], dict]


# ---------- 业务调用序列对齐 ----------
def _lcs_len(a: list, b: list) -> int:
    """两个序列的最长公共子序列长度（用于"顺序一致性"）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def seq_metrics(orig: list[Call], ma: list[Call]) -> dict:
    """对一条 MA rep 的业务调用序列与原轨迹做对齐。"""
    ok = [c["key"] for c in orig]
    mk = [c["key"] for c in ma]
    so, sm = set(ok), set(mk)
    inter = so & sm
    union = so | sm
    hit = sorted(inter)
    missing = sorted(so - sm)      # 原轨迹有、MA 没复现（漏做）
    extra = sorted(sm - so)        # MA 多做、原轨迹没有（偏离/幻觉；custom 下多为 REPLAY_MISS）
    return {
        "orig_n": len(ok), "ma_n": len(mk),
        "coverage": round(len(inter) / len(so), 4) if so else 0.0,
        "jaccard": round(len(inter) / len(union), 4) if union else 0.0,
        "order": round(_lcs_len(ok, mk) / len(ok), 4) if ok else 0.0,
        "extra_n": len(extra),
        "hit_keys": hit, "missing_keys": missing, "extra_keys": extra,
    }


def _avg(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 4) if xs else None


# ---------- 最终产物结构对比 ----------
def _headings(text: str) -> list[str]:
    """抽取"章节骨架"：markdown 标题（#..######）+ 独占一行的 **加粗小标题**。

    用于结构覆盖率——两侧交付卡片是否长出同样的章节（洞察/盘点/话术…），
    不看具体措辞，只看骨架是否对齐。
    """
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            out.append(s.lstrip("#").strip())
        elif s.startswith("**") and s.endswith("**") and len(s) > 4 and "**" not in s[2:-2]:
            out.append(s[2:-2].strip())
    return out


def product_metrics(orig_text: str, ma_text: str) -> dict:
    """两侧最终产物的结构指标（不依赖 LLM，纯文本骨架比对）。"""
    oh, mh = _headings(orig_text), _headings(ma_text)
    so, sm = set(oh), set(mh)
    inter = so & sm
    olen, mlen = len(orig_text), len(ma_text)
    return {
        "orig_len": olen, "ma_len": mlen,
        "len_ratio": round(mlen / olen, 3) if olen else 0.0,
        "orig_headings": len(oh), "ma_headings": len(mh),
        "heading_coverage": round(len(inter) / len(so), 4) if so else 0.0,
        "missing_headings": sorted(so - sm),
        "extra_headings": sorted(sm - so),
    }


# ---------- 编排：逐轨迹算等效性 ----------
def _rep_events(runs_dir: Path, stem: str) -> list[Path]:
    """某轨迹目录下所有 rep 原始事件流，按 rep 序排。"""
    fs = glob.glob(str(runs_dir / stem / "rep*.events.jsonl"))
    return sorted(fs, key=lambda p: Path(p).name)


def collect_product_pairs(*, trajectories_dir: Path, runs_dir: Path,
                          orig_product: OrigProductFn, ma_product: MaProductFn,
                          product_reps: int = 1) -> list[dict]:
    """收集每条轨迹「原产物 vs MA产物」文本对，供调用方异步做 LLM 语义判分。

    与 build_equivalence 取同一代表性 rep（前 product_reps 个里的第一个），保证判分对象一致。
    返回 [{"traj", "orig_text", "ma_text"}, ...]（两侧都非空才收）。
    """
    run_files = sorted(glob.glob(str(runs_dir / "*" / "run.json")))
    pairs = []
    for rf in run_files:
        stem = Path(rf).parent.name
        run = json.loads(Path(rf).read_text())
        traj_name = run.get("trajectory") or (stem + ".json")
        traj_path = trajectories_dir / traj_name
        rep_files = _rep_events(runs_dir, stem)[:max(1, product_reps)]
        o = orig_product(traj_path) if traj_path.exists() else ""
        m = ma_product(Path(rep_files[0])) if rep_files else ""
        if o and m:
            pairs.append({"traj": traj_name, "orig_text": o, "ma_text": m})
    return pairs


def build_equivalence(*, trajectories_dir: Path, runs_dir: Path,
                      orig_calls: OrigCallsFn, ma_calls: MaCallsFn,
                      orig_product: OrigProductFn, ma_product: MaProductFn,
                      judge: Optional[JudgeFn] = None,
                      product_reps: int = 1) -> dict:
    """读 runs_dir/<stem>/rep*.events.jsonl + trajectories_dir/<traj>，产出等效性数据。

    返回结构（喂给 report.py 的两节）：
        {
          "trajectories": [ {traj, calls:{...均值+best/worst}, product:{...结构+可选judge}}, ... ],
          "summary": {mean_coverage, mean_order, mean_extra, mean_product_score, judged, ...},
          "measurable": bool,   # MA 侧是否采到业务调用（files 模式没注册 custom tool → 采不到 → False）
        }

    product_reps：对每条轨迹取前 N 个 rep 的产物做 judge（控 LLM 调用量，默认 1；0=不判分只给结构）。
    judge=None 时跳过语义判分，仅给结构指标。
    """
    run_files = sorted(glob.glob(str(runs_dir / "*" / "run.json")))
    if not run_files:
        raise SystemExit(f"未找到 MA 实跑结果于 {runs_dir}（先用 run.py 跑一遍）")

    trajs = []
    any_ma_call = False
    cov_all, order_all, extra_all, prod_score_all = [], [], [], []

    for rf in run_files:
        stem = Path(rf).parent.name
        run = json.loads(Path(rf).read_text())
        traj_name = run.get("trajectory") or (stem + ".json")
        traj_path = trajectories_dir / traj_name

        # 原轨迹的业务调用 + 最终产物（只算一次）
        o_calls = orig_calls(traj_path) if traj_path.exists() else []
        o_prod = orig_product(traj_path) if traj_path.exists() else ""

        # 每个 rep 的业务调用对齐
        rep_files = _rep_events(runs_dir, stem)
        per_rep = []
        for ep in rep_files:
            m_calls = ma_calls(Path(ep))
            if m_calls:
                any_ma_call = True
            per_rep.append({"rep": Path(ep).name, "metrics": seq_metrics(o_calls, m_calls)})

        # rep 间聚合：均值 + 最好/最差（按 coverage 排）
        covs = [r["metrics"]["coverage"] for r in per_rep]
        calls_agg = {
            "orig_n": len(o_calls),
            "mean_coverage": _avg(covs),
            "mean_jaccard": _avg([r["metrics"]["jaccard"] for r in per_rep]),
            "mean_order": _avg([r["metrics"]["order"] for r in per_rep]),
            "mean_extra": _avg([float(r["metrics"]["extra_n"]) for r in per_rep]),
            "reps": len(per_rep),
        }
        if per_rep:
            best = max(per_rep, key=lambda r: r["metrics"]["coverage"])
            worst = min(per_rep, key=lambda r: r["metrics"]["coverage"])
            calls_agg["best"] = {"rep": best["rep"], **{k: best["metrics"][k]
                                 for k in ("coverage", "order", "extra_n", "missing_keys", "extra_keys")}}
            calls_agg["worst"] = {"rep": worst["rep"], **{k: worst["metrics"][k]
                                  for k in ("coverage", "order", "extra_n", "missing_keys", "extra_keys")}}
        if calls_agg["mean_coverage"] is not None:
            cov_all.append(calls_agg["mean_coverage"])
        if calls_agg["mean_order"] is not None:
            order_all.append(calls_agg["mean_order"])
        if calls_agg["mean_extra"] is not None:
            extra_all.append(calls_agg["mean_extra"])

        # 最终产物：取代表性 rep（前 product_reps 个）做结构 + 语义判分
        prod = {"structural": None, "judge": None}
        rep_prod_files = rep_files[:max(0, product_reps)] or rep_files[:1]
        m_prod = ma_product(Path(rep_prod_files[0])) if rep_prod_files else ""
        if o_prod and m_prod:
            prod["structural"] = product_metrics(o_prod, m_prod)
            prod["judged_rep"] = Path(rep_prod_files[0]).name
            if judge is not None:
                verdict = judge(traj_name, o_prod, m_prod)
                prod["judge"] = verdict
                if isinstance(verdict, dict) and isinstance(verdict.get("score"), (int, float)):
                    prod_score_all.append(float(verdict["score"]))

        trajs.append({"traj": traj_name, "calls": calls_agg, "product": prod})

    summary = {
        "measurable_calls": any_ma_call,
        "mean_coverage": _avg(cov_all),
        "mean_order": _avg(order_all),
        "mean_extra": _avg(extra_all),
        "mean_product_score": _avg(prod_score_all),
        "judged": bool(prod_score_all),
        "n_trajectories": len(trajs),
    }
    return {"trajectories": trajs, "summary": summary, "measurable": any_ma_call}
