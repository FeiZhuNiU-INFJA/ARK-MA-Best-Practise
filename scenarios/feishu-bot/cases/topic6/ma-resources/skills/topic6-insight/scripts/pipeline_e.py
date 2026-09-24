#!/usr/bin/env python3
"""
E 阶段洞察生成 pipeline（MA 版）
四路并发调用 LLM，生成 4 个版块的报告洞察。

MA 适配要点：
  - 删掉 PROJECT_ROOT.parents[1] 上溯（MA 沙箱下 skill 路径 /mnt/skills/topic6-insight/，
    项目路径独立在 /workspace/Projects/ 下）
  - anthropic SDK → openai 兼容 SDK（AsyncOpenAI），走火山方舟 endpoint
    * ARK_BASE_URL / ARK_API_KEY（fallback OPENAI_BASE_URL / OPENAI_API_KEY）
  - cost-tracker 从 topic6-annotation skill 挂载路径调用（annotation/insight 共用同一份）

设计原则：
  - 只允许 full 模式运行（test 模式基于抽样数据，洞察结论不可信，直接拒绝）
  - 同一项目洞察按版本迭代（v1、v2...），每轮单独一个子目录

用法：
    python pipeline_e.py \\
        --project-dir "/workspace/Projects/W35_20260824-20260830" \\
        [--publish-date 2026-08-31] [--model ep-xxx] [--version 2] \\
        [--skip-data-prep] [--sections e1,e2]

输出（{project_dir}/06_洞察/v{n}/）：
    e1_v{n}.md ~ e4_v{n}.md   4版块洞察
    e{1,3,4}_data.md          数据快照
    e2_flags.json             E2 has_data 判断+数据/固定话术
    e4_candidates.json        E4 打标候选
    e4_tagging_audit.md       E4 打标原始输出（审计向）
    pipeline_e_report_v{n}.json  执行记录+Token 用量
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent  # scripts/ 上一级即 skill 根
PROMPTS_DIR = SKILL_DIR / "02_洞察"
SHARED_ROLE_STYLE = PROMPTS_DIR / "_shared" / "role_style.md"
E_DATA_SCRIPT = SKILL_DIR / "01_统计" / "run_stats.py"
COST_TRACKER = Path("/mnt/skills/topic6-annotation/tool/cost-tracker/cost_tracker.py")
WORKSPACE_ROOT = Path("/workspace")


def _rel_to_proj(p: Path, proj: Path) -> str:
    """产物里只记相对项目根的路径（避免把 MA 沙箱绝对路径写进交付文件）。"""
    try:
        return p.resolve().relative_to(proj.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


SECTIONS = [
    {
        "id": "e1",
        "name": "行业及热门话题",
        "prompt_dir": "E1_行业话题",
        "data_file": "e1_data.md",
        "section_title": "# 一、行业及热门话题",
        "max_tokens": 1536,
    },
    {
        "id": "e2",
        "name": "营销节点",
        "prompt_dir": "E2_营销节点",
        "data_file": "e2_flags.json",
        "section_title": "# 二、营销节点",
        "max_tokens": 4096,
    },
    {
        "id": "e3",
        "name": "平台新鲜事",
        "prompt_dir": "E3_平台新鲜事",
        "data_file": "e3_data.md",
        "section_title": "# 三、平台新鲜事",
        "max_tokens": 18000,
    },
    {
        "id": "e4",
        "name": "营销发现",
        "prompt_dir": "E4_营销发现",
        "data_file": "e4_data.md",
        "section_title": "# 四、营销发现",
        "max_tokens": 16000,
    },
]


# ---------------------------------------------------------------------------
# 版本管理
# ---------------------------------------------------------------------------

def next_version(insight_dir: Path) -> int:
    versions = []
    for d in insight_dir.glob("v*"):
        if d.is_dir():
            m = re.match(r"v(\d+)$", d.name)
            if m:
                versions.append(int(m.group(1)))
    return (max(versions) + 1) if versions else 1


def latest_round_dir(insight_dir: Path) -> Path | None:
    candidates = []
    for d in insight_dir.glob("v*"):
        if d.is_dir():
            m = re.match(r"v(\d+)$", d.name)
            if m:
                candidates.append((int(m.group(1)), d))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def latest_data_run_id(merge_dir: Path, mode: str) -> int | None:
    nums = []
    for f in merge_dir.glob(f"wide_table_{mode}_r*.xlsx"):
        m = re.search(r"_r(\d+)\.xlsx$", f.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else None


# ---------------------------------------------------------------------------
# Prompt 加载
# ---------------------------------------------------------------------------

PROMPT_START_MARKER = "<!-- PROMPT_START -->"


def load_prompt_template(prompt_dir: str) -> str:
    skill_prompt_dir = PROMPTS_DIR / prompt_dir
    if not skill_prompt_dir.exists():
        raise FileNotFoundError(f"Prompt 目录不存在：{skill_prompt_dir}")

    versions = sorted(skill_prompt_dir.glob("v*.md"), key=lambda p: p.stem)
    if not versions:
        raise FileNotFoundError(f"Prompt 目录下无 v*.md 文件：{skill_prompt_dir}")

    latest = versions[-1]
    print(f"  [prompt] 使用：{latest.name}")
    text = latest.read_text(encoding="utf-8")

    if PROMPT_START_MARKER in text:
        text = text.split(PROMPT_START_MARKER, 1)[1].lstrip("\n")

    if SHARED_ROLE_STYLE.exists():
        shared = SHARED_ROLE_STYLE.read_text(encoding="utf-8")
        text = shared.rstrip("\n") + "\n\n" + text
    else:
        print(f"  [prompt] ⚠️ 共享角色/风格文件不存在：{SHARED_ROLE_STYLE}，跳过拼接", file=sys.stderr)

    return text


def fill_prompt(template: str, data: str) -> str:
    if "{{data}}" not in template:
        raise ValueError("Prompt 模板中未找到 {{data}} 占位符")
    return template.replace("{{data}}", data)


# ---------------------------------------------------------------------------
# LLM 调用（OpenAI 兼容 endpoint，MA 走火山方舟）
# ---------------------------------------------------------------------------

async def call_llm_async(
    client: AsyncOpenAI,
    section: dict,
    prompt: str,
    model: str,
) -> dict:
    sid = section["id"]
    name = section["name"]
    t0 = time.time()

    print(f"[{sid}] 开始调用 LLM({name})...")
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=section["max_tokens"],
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.time() - t0
        choice = resp.choices[0]
        content = choice.message.content or ""
        input_tokens = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
        output_tokens = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
        stop_reason = choice.finish_reason

        print(f"[{sid}] ✅ 完成，耗时 {elapsed:.1f}s，"
              f"input={input_tokens} / output={output_tokens}")
        if stop_reason == "length":
            print(f"[{sid}] ⚠️ finish_reason=length，疑似被截断——"
                  f"输出可能不完整，建议调大 SECTIONS 里 {sid} 的 max_tokens 后重跑", file=sys.stderr)

        return {
            "id": sid,
            "name": name,
            "status": "ok",
            "content": content,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "elapsed_s": round(elapsed, 1),
            "model": model,
            "stop_reason": stop_reason,
        }

    except Exception as e:
        elapsed = time.time() - t0
        print(f"[{sid}] ❌ LLM 调用失败({elapsed:.1f}s)：{e}", file=sys.stderr)
        return {
            "id": sid,
            "name": name,
            "status": "error",
            "error": str(e),
            "content": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_s": round(elapsed, 1),
            "model": model,
            "stop_reason": None,
        }


# ---------------------------------------------------------------------------
# E2 专属路由
# ---------------------------------------------------------------------------

def build_e2_prompt_data(flags: dict) -> str:
    parts = []
    if flags["last_week"]["has_data"]:
        parts.append("## 上周节点\n\n" + flags["last_week"]["data_md"])
    if flags["next_week"]["has_data"]:
        parts.append("## 本周节点预告\n\n" + flags["next_week"]["data_md"])
    return "\n\n---\n\n".join(parts)


async def build_e2_task(client: AsyncOpenAI, sec: dict, insight_dir: Path, model: str) -> dict:
    sid, name = sec["id"], sec["name"]
    flags_path = insight_dir / sec["data_file"]
    if not flags_path.exists():
        return await _error_result(sec, f"数据文件不存在：{flags_path}")

    flags = json.loads(flags_path.read_text(encoding="utf-8"))
    lw, nw = flags["last_week"], flags["next_week"]

    if not lw["has_data"] and not nw["has_data"]:
        print(f"[{sid}] 上周/下周均无节点，跳过 LLM，直接输出固定话术")
        content = (
            f"## 1.1 上周节点\n\n{lw['empty_text']}\n\n"
            f"## 1.2 本周节点预告\n\n{nw['empty_text']}"
        )
        return {
            "id": sid, "name": name, "status": "ok", "content": content,
            "input_tokens": 0, "output_tokens": 0, "elapsed_s": 0.0,
            "model": "none(无节点，跳过LLM)", "stop_reason": None,
        }

    try:
        template = load_prompt_template(sec["prompt_dir"])
    except FileNotFoundError as e:
        return await _error_result(sec, str(e))

    try:
        prompt = fill_prompt(template, build_e2_prompt_data(flags))
    except ValueError as e:
        return await _error_result(sec, str(e))

    result = await call_llm_async(client, sec, prompt, model)
    if result["status"] != "ok":
        return result

    llm_body = _strip_markdown_fence(result["content"]).rstrip()
    if lw["has_data"] and nw["has_data"]:
        pass
    elif lw["has_data"]:
        llm_body = f"{llm_body}\n\n## 1.2 本周节点预告\n\n{nw['empty_text']}"
    else:
        llm_body = f"## 1.1 上周节点\n\n{lw['empty_text']}\n\n{llm_body}"

    result["content"] = llm_body
    return result


# ---------------------------------------------------------------------------
# E4 专属路由：候选池"打标"→过滤→撰写
# ---------------------------------------------------------------------------

E4_CANDIDATES_FILE = "e4_candidates.json"
E4_TAGGING_AUDIT_FILE = "e4_tagging_audit.md"
E4_TAGGING_PROMPT_PATH = PROMPTS_DIR / "E4_营销发现" / "_tagging" / "v1.md"
E4_TAGGING_MAX_TOKENS = 3000
PSEUDO_SEP = " || "


def _escape_md_cell(v) -> str:
    return str(v).replace("|", "\\|")


def _render_pseudo_rows(header: list, rows: list) -> str:
    lines = [PSEUDO_SEP.join(header)]
    for row in rows:
        lines.append(PSEUDO_SEP.join(str(c) for c in row))
    return "\n".join(lines)


def _parse_pseudo_rows(text: str) -> list:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [c.strip() for c in lines[0].split(PSEUDO_SEP)]
    rows = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(PSEUDO_SEP)]
        rows.append(dict(zip(header, cells)))
    return rows


def _split_headed_sections(text: str, prefix: str) -> dict:
    sections: dict = {}
    title = None
    buf: list = []
    for line in text.split("\n"):
        if line.startswith(prefix):
            if title is not None:
                sections[title] = "\n".join(buf).strip()
            title = line[len(prefix):].strip()
            buf = []
        else:
            buf.append(line)
    if title is not None:
        sections[title] = "\n".join(buf).strip()
    return sections


def _render_risk_candidate_links(item: dict) -> str:
    lines = []
    for t in item.get("具体热搜", []):
        title = _escape_md_cell(t["标题"])
        link = t.get("链接", "")
        text = f"[{title}]({link})" if (link and link != "—") else title
        lines.append(f"- {text} · {t.get('平台', '—')} · {t.get('热度分', '—')}")
    return "<br>".join(lines)


def _render_coop_candidate_link(item: dict) -> str:
    title = _escape_md_cell(item.get("代表热搜标题", ""))
    link = item.get("代表热搜链接", "")
    return f"[{title}]({link})" if (link and link != "—") else title


def _build_e4_tagging_input(candidates: dict) -> str | None:
    parts = []
    risk = candidates.get("舆情风险", [])
    if risk:
        rows = [[r["事件簇名"], r["行业"], r["热度分"], r["风险判断说明"]] for r in risk]
        parts.append(
            "### 舆情风险候选(待打标)\n\n"
            + _render_pseudo_rows(["事件簇名", "行业", "热度分", "风险判断说明"], rows)
        )
    coop = candidates.get("合作动态", [])
    if coop:
        rows = [[c["事件簇名"], c["商业实体"], c["行业"], c["热度分"], c["合作动态说明"]] for c in coop]
        parts.append(
            "### 合作动态候选(待打标)\n\n"
            + _render_pseudo_rows(["事件簇名", "商业实体", "行业", "热度分", "合作动态说明"], rows)
        )
    if not parts:
        return None
    return "\n\n".join(parts)


def _parse_e4_tagging_output(text: str) -> tuple:
    sections = _split_headed_sections(text, prefix="### ")
    risk_tags = {
        row["事件簇名"]: row
        for row in _parse_pseudo_rows(sections.get("舆情风险打标", ""))
        if row.get("事件簇名")
    }
    coop_tags = {
        row["事件簇名"]: row
        for row in _parse_pseudo_rows(sections.get("合作动态打标", ""))
        if row.get("事件簇名")
    }
    return risk_tags, coop_tags


def _render_e4_data_with_tags(
    candidates: dict,
    raw_e4_data: str,
    risk_tags: dict,
    coop_tags: dict,
) -> str:
    parts = []

    risk = candidates.get("舆情风险", [])
    kept = []
    for item in risk:
        tag = risk_tags.get(item["事件簇名"])
        risk_type = tag.get("风险类型", "未分类") if tag else "未分类(打标遗漏,保守保留)"
        verdict = tag.get("可接性", "接") if tag else "接"
        if verdict != "接":
            continue
        kept.append((risk_type, item))
    if kept:
        lines = [
            f"## 舆情风险数据\n\n共 {len(kept)} 个事件簇(已按乙方公关可接性打标过滤,原候选池TOP20)\n",
            "| 风险类型 | 事件簇名 | 行业 | 热度分 | 具体热搜 | 风险判断说明 |",
            "|---|---|---|---|---|---|",
        ]
        for risk_type, item in kept:
            lines.append(
                f"| {_escape_md_cell(risk_type)} | {_escape_md_cell(item['事件簇名'])} "
                f"| {_escape_md_cell(item['行业'])} | {item['热度分']} "
                f"| {_render_risk_candidate_links(item)} | {_escape_md_cell(item['风险判断说明'])} |"
            )
        parts.append("\n".join(lines))
    else:
        parts.append(
            "## 舆情风险数据\n\n上周无风险预警热点,或候选均判定为乙方公关不接主案"
            "(财务/法律/政治/安全类硬事实问题,普通品牌公关改变不了,不适合出现在"
            "本报告的「品牌公关能怎么应对」框架里)。"
        )

    coop = candidates.get("合作动态", [])
    kept = []
    for item in coop:
        tag = coop_tags.get(item["事件簇名"])
        coop_type = tag.get("变动类型", "未分类") if tag else "未分类(打标遗漏,保守保留)"
        verdict = tag.get("参考价值", "有用") if tag else "有用"
        if verdict != "有用":
            continue
        kept.append((coop_type, item))
    if kept:
        lines = [
            f"## 合作动态数据\n\n共 {len(kept)} 条(已按对营销人参考价值打标过滤,原候选池TOP20)\n",
            "| 变动类型 | 事件簇名 | 商业实体 | 行业 | 热度分 | 代表热搜 | 合作动态说明 |",
            "|---|---|---|---|---|---|---|",
        ]
        for coop_type, item in kept:
            lines.append(
                f"| {_escape_md_cell(coop_type)} | {_escape_md_cell(item['事件簇名'])} "
                f"| {_escape_md_cell(item['商业实体'])} | {_escape_md_cell(item['行业'])} "
                f"| {item['热度分']} | {_render_coop_candidate_link(item)} "
                f"| {_escape_md_cell(item['合作动态说明'])} |"
            )
        parts.append("\n".join(lines))
    else:
        parts.append("## 合作动态数据\n\n上周无对营销人有参考价值的商业合作动态。")

    other = _split_headed_sections(raw_e4_data, prefix="## ")
    for name in ("营销观察数据", "消费洞察数据"):
        if name in other:
            parts.append(f"## {name}\n\n{other[name]}")

    return "\n\n---\n\n".join(parts)


async def _run_e4_tagging(client: AsyncOpenAI, tagging_input: str, model: str) -> dict:
    if not E4_TAGGING_PROMPT_PATH.exists():
        raise FileNotFoundError(f"打标 Prompt 不存在：{E4_TAGGING_PROMPT_PATH}")
    template = E4_TAGGING_PROMPT_PATH.read_text(encoding="utf-8")
    if PROMPT_START_MARKER in template:
        template = template.split(PROMPT_START_MARKER, 1)[1].lstrip("\n")
    prompt = fill_prompt(template, tagging_input)
    fake_sec = {"id": "e4_tagging", "name": "E4候选打标", "max_tokens": E4_TAGGING_MAX_TOKENS}
    return await call_llm_async(client, fake_sec, prompt, model)


async def build_e4_task(client: AsyncOpenAI, sec: dict, insight_dir: Path, model: str) -> dict:
    sid = sec["id"]
    data_path = insight_dir / sec["data_file"]
    if not data_path.exists():
        return await _error_result(sec, f"数据文件不存在：{data_path}")
    raw_data = data_path.read_text(encoding="utf-8")

    candidates_path = insight_dir / E4_CANDIDATES_FILE
    tag_input_tokens = tag_output_tokens = 0
    tag_elapsed = 0.0
    final_data = raw_data

    if not candidates_path.exists():
        print(f"[{sid}] ⚠️ {candidates_path} 不存在,跳过打标,退回全量候选", file=sys.stderr)
    else:
        candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
        tagging_input = _build_e4_tagging_input(candidates)
        if tagging_input is None:
            print(f"[{sid}] 舆情风险/合作动态均无候选,跳过打标")
        else:
            try:
                tag_result = await _run_e4_tagging(client, tagging_input, model)
            except FileNotFoundError as e:
                print(f"[{sid}] ⚠️ {e},跳过打标,退回全量候选", file=sys.stderr)
                tag_result = {"status": "error", "error": str(e)}

            if tag_result["status"] == "ok":
                risk_tags, coop_tags = _parse_e4_tagging_output(tag_result["content"])
                final_data = _render_e4_data_with_tags(candidates, raw_data, risk_tags, coop_tags)
                tag_input_tokens = tag_result["input_tokens"]
                tag_output_tokens = tag_result["output_tokens"]
                tag_elapsed = tag_result["elapsed_s"]
                audit_path = insight_dir / E4_TAGGING_AUDIT_FILE
                audit_path.write_text(tag_result["content"], encoding="utf-8")
                print(f"[{sid}] 打标完成(舆情风险{len(risk_tags)}条/合作动态{len(coop_tags)}条已标记),"
                      f"审计文件：{audit_path.name}")
            else:
                print(f"[{sid}] ⚠️ 打标调用失败,跳过过滤,退回全量候选：{tag_result.get('error')}",
                      file=sys.stderr)

    try:
        template = load_prompt_template(sec["prompt_dir"])
    except FileNotFoundError as e:
        return await _error_result(sec, str(e))
    try:
        prompt = fill_prompt(template, final_data)
    except ValueError as e:
        return await _error_result(sec, str(e))

    write_result = await call_llm_async(client, sec, prompt, model)
    write_result["input_tokens"] += tag_input_tokens
    write_result["output_tokens"] += tag_output_tokens
    write_result["elapsed_s"] = round(write_result["elapsed_s"] + tag_elapsed, 1)
    return write_result


async def run_all_sections(
    project_dir: Path,
    insight_dir: Path,
    model: str,
    sections: list,
) -> list:
    api_key = os.environ.get("ARK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("ARK_API_KEY / OPENAI_API_KEY 均未设置")

    base_url = os.environ.get("ARK_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not base_url:
        raise EnvironmentError("ARK_BASE_URL / OPENAI_BASE_URL 均未设置")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    tasks = []
    for sec in sections:
        if sec["id"] == "e2":
            tasks.append(asyncio.create_task(build_e2_task(client, sec, insight_dir, model)))
            continue
        if sec["id"] == "e4":
            tasks.append(asyncio.create_task(build_e4_task(client, sec, insight_dir, model)))
            continue

        try:
            template = load_prompt_template(sec["prompt_dir"])
        except FileNotFoundError as e:
            print(f"[{sec['id']}] ❌ {e}", file=sys.stderr)
            tasks.append(asyncio.create_task(_error_result(sec, str(e))))
            continue

        data_path = insight_dir / sec["data_file"]
        if not data_path.exists():
            err = f"数据文件不存在：{data_path}"
            print(f"[{sec['id']}] ❌ {err}", file=sys.stderr)
            tasks.append(asyncio.create_task(_error_result(sec, err)))
            continue

        data = data_path.read_text(encoding="utf-8")

        try:
            prompt = fill_prompt(template, data)
        except ValueError as e:
            print(f"[{sec['id']}] ❌ {e}", file=sys.stderr)
            tasks.append(asyncio.create_task(_error_result(sec, str(e))))
            continue

        tasks.append(asyncio.create_task(call_llm_async(client, sec, prompt, model)))

    results = await asyncio.gather(*tasks)
    await client.close()
    return list(results)


async def _error_result(sec: dict, err: str) -> dict:
    return {
        "id": sec["id"],
        "name": sec["name"],
        "status": "error",
        "error": err,
        "content": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "elapsed_s": 0.0,
        "model": "",
        "stop_reason": None,
    }


# ---------------------------------------------------------------------------
# 成本记录
# ---------------------------------------------------------------------------

# 每百万 token 单价(USD估算,MA 走火山方舟实际计价按平台账单为准,这里仅供 cost_tracker 台账估算)。
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-5":              (5.0, 25.0),
    "claude-opus-4-7":              (5.0, 25.0),
    "claude-opus-4-8":              (5.0, 25.0),
    "claude-opus-5":                (5.0, 25.0),
    "claude-sonnet-4-5-20250929":   (3.0, 15.0),
    "claude-sonnet-4-6":            (3.0, 15.0),
    "claude-sonnet-5":              (2.0, 10.0),
    "claude-haiku-4-5-20251001":    (1.0, 5.0),
}


def _price_of(model: str) -> tuple[float, float]:
    for key, value in PRICING.items():
        if model.startswith(key) or key in model:
            return value
    print(f"[pipeline_e] ⚠️ 未知模型定价：{model},成本记为 0", file=sys.stderr)
    return (0.0, 0.0)


def record_costs(project_dir: Path, results: list, model: str) -> None:
    if not COST_TRACKER.exists():
        print(f"[pipeline_e] ⚠️ cost_tracker.py 不存在({COST_TRACKER}),跳过成本记录")
        return

    for r in results:
        if r["status"] != "ok" or r["input_tokens"] == 0:
            continue
        price_in, price_out = _price_of(r["model"])
        raw_cost = (
            r["input_tokens"] / 1_000_000 * price_in
            + r["output_tokens"] / 1_000_000 * price_out
        )
        cmd = [
            sys.executable, str(COST_TRACKER),
            "--project-dir", str(project_dir),
            "append",
            "--phase", "E",
            "--task", f"{r['id']}_{r['name']}",
            "--mode", "full",
            "--model-id", r["model"],
            "--platform", "Ark",
            "--input-tokens", str(r["input_tokens"]),
            "--output-tokens", str(r["output_tokens"]),
            "--raw-cost", str(raw_cost),
            "--currency", "USD",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  [cost] {r['id']} 成本已记录")
        except subprocess.CalledProcessError as e:
            print(f"  [cost] {r['id']} 成本记录失败：{e.stderr.decode()}", file=sys.stderr)


def _strip_leading_heading(text: str, section_title: str) -> str:
    lines = text.lstrip("\n").split("\n")
    if not lines:
        return text
    m = re.match(r"^#{1,3}\s*(.+?)\s*$", lines[0])
    if not m:
        return text
    first_text = m.group(1)
    title_text = re.sub(r"^#+\s*", "", section_title)
    title_text = re.sub(r"^[一二三四五六七八九十]+[、.]\s*", "", title_text).strip()
    if title_text and title_text in first_text:
        lines.pop(0)
        while lines and lines[0].strip() == "":
            lines.pop(0)
    return "\n".join(lines)


def _strip_markdown_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1 and s.rstrip().endswith("```"):
            inner = s[first_nl + 1: s.rfind("```")]
            return inner.strip()
    return text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="E 阶段洞察生成 pipeline(仅支持 full 模式)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-dir", required=True,
                        help="项目目录(绝对路径,或相对 /workspace)")
    parser.add_argument("--mode", choices=["test", "full"], default="full")
    parser.add_argument("--publish-date", default=None)
    parser.add_argument("--model", default="claude-sonnet-4-5-20250929",
                        help="LLM 模型 ID(MA 环境请传火山方舟 endpoint id)")
    parser.add_argument("--version", type=int, default=None)
    parser.add_argument("--skip-data-prep", action="store_true")
    parser.add_argument("--data-run-id", type=int, default=None)
    parser.add_argument("--sections", default=None)
    args = parser.parse_args()

    if args.mode == "test":
        print(
            "[pipeline_e] ❌ 不允许在 test 模式下生成洞察。\n"
            "  原因：test 模式基于抽样数据,洞察结论存在抽样偏差,不可交付。",
            file=sys.stderr,
        )
        sys.exit(1)

    proj = Path(args.project_dir)
    if not proj.is_absolute():
        proj = WORKSPACE_ROOT / proj
    if not proj.exists():
        print(f"[pipeline_e] ❌ 项目目录不存在：{proj}", file=sys.stderr)
        sys.exit(1)

    merge_dir = proj / "05_合并"
    data_run_id = args.data_run_id
    if data_run_id is None:
        data_run_id = latest_data_run_id(merge_dir, args.mode)

    if data_run_id is not None:
        wide_full = merge_dir / f"wide_table_{args.mode}_r{data_run_id}.xlsx"
        print(f"[pipeline_e] 标注数据 run_id = r{data_run_id}")
    else:
        wide_full = merge_dir / f"wide_table_{args.mode}.xlsx"
        print(f"[pipeline_e] 未找到版本化宽表,回退旧命名")

    if not wide_full.exists() and not args.skip_data_prep:
        print(
            f"[pipeline_e] ❌ 全量宽表不存在：{wide_full}\n"
            "  请先完成全量标注并运行 merge_annotations.py --mode full --run-id {N},再执行本脚本。",
            file=sys.stderr,
        )
        sys.exit(1)

    insight_dir = proj / "06_洞察"
    insight_dir.mkdir(parents=True, exist_ok=True)

    prev_round_dir = latest_round_dir(insight_dir)
    version = args.version if args.version else next_version(insight_dir)
    version_tag = f"v{version}"
    round_dir = insight_dir / version_tag
    round_dir.mkdir(parents=True, exist_ok=True)
    print(f"[pipeline_e] 洞察版本：{version_tag}(目录：{round_dir.relative_to(proj)})")

    if (round_dir / f"e1_{version_tag}.md").exists():
        print(
            f"[pipeline_e] ⚠️  {version_tag} 已存在,将覆盖。\n"
            f"  若要保留旧版本,请改用 --version {version + 1}"
        )

    sections = [
        dict(sec, output_file=f"{sec['id']}_{version_tag}.md")
        for sec in SECTIONS
    ]

    if args.sections:
        wanted = {s.strip() for s in args.sections.split(",") if s.strip()}
        known_ids = {sec["id"] for sec in SECTIONS}
        unknown = wanted - known_ids
        if unknown:
            print(f"[pipeline_e] ❌ 未知版块ID：{unknown}(可选：{sorted(known_ids)})", file=sys.stderr)
            sys.exit(1)
        sections = [sec for sec in sections if sec["id"] in wanted]
        print(f"[pipeline_e] --sections 限定：本轮只跑 {[s['id'] for s in sections]}")

    if args.skip_data_prep:
        if prev_round_dir is None:
            print(
                "[pipeline_e] ❌ --skip-data-prep 但没有任何历史轮次可复制数据文件,"
                "请去掉该参数让本轮正常跑一次统计脚本。",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[pipeline_e] --skip-data-prep：从 {prev_round_dir.name} 复制数据文件...")
        for sec in SECTIONS:
            src = prev_round_dir / sec["data_file"]
            if src.exists():
                shutil.copy(src, round_dir / sec["data_file"])
            else:
                print(f"[pipeline_e] ⚠️ {prev_round_dir.name} 下缺 {sec['data_file']},跳过复制", file=sys.stderr)
        audit_src = prev_round_dir / "e3_word_freq_audit.md"
        if audit_src.exists():
            shutil.copy(audit_src, round_dir / "e3_word_freq_audit.md")
        candidates_src = prev_round_dir / E4_CANDIDATES_FILE
        if candidates_src.exists():
            shutil.copy(candidates_src, round_dir / E4_CANDIDATES_FILE)
    else:
        print("[pipeline_e] Step 1：运行 01_统计/run_stats.py 预处理数据...")
        cmd = [
            sys.executable, str(E_DATA_SCRIPT),
            "--project-dir", str(proj),
            "--mode", "full",
            "--out-dir", str(round_dir),
        ]
        if args.publish_date:
            cmd += ["--publish-date", args.publish_date]
        if data_run_id is not None:
            cmd += ["--run-id", str(data_run_id)]

        ret = subprocess.run(cmd)
        if ret.returncode != 0:
            print("[pipeline_e] ❌ run_stats.py 失败,终止", file=sys.stderr)
            sys.exit(1)

    print(f"\n[pipeline_e] Step 2：并发调用 4 路 LLM(模型：{args.model})...")
    t_start = time.time()

    results = asyncio.run(
        run_all_sections(proj, round_dir, args.model, sections)
    )

    total_elapsed = time.time() - t_start
    total_input = sum(r["input_tokens"] for r in results)
    total_output = sum(r["output_tokens"] for r in results)

    print(f"\n[pipeline_e] Step 3：保存洞察文件({version_tag})...")
    ok_count = 0
    for sec, result in zip(sections, results):
        if result["status"] == "ok" and result["content"]:
            body = _strip_markdown_fence(result["content"])
            if sec["id"] != "e2":
                body = _strip_leading_heading(body, sec["section_title"])
            content = f"{sec['section_title']}\n\n{body}\n"
            out_path = round_dir / sec["output_file"]
            out_path.write_text(content, encoding="utf-8")
            print(f"  ✅ {sec['id']}：{out_path.name}")
            ok_count += 1
        else:
            print(f"  ❌ {sec['id']}：{result.get('error', '空输出')}")

    report = {
        "pipeline": "pipeline_e",
        "version": version_tag,
        "project_dir": _rel_to_proj(proj, proj),
        "mode": "full",
        "data_run_id": data_run_id,
        "model": args.model,
        "total_elapsed_s": round(total_elapsed, 1),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "ok_count": ok_count,
        "total_count": len(sections),
        "sections": results,
    }
    report_path = round_dir / f"pipeline_e_report_{version_tag}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[pipeline_e] 执行报告：{report_path.relative_to(proj)}")

    print("[pipeline_e] Step 5：记录成本...")
    record_costs(proj, results, args.model)

    truncated = [r["id"] for r in results if r.get("stop_reason") == "length"]

    print(f"\n{'='*60}")
    print(f"[pipeline_e] {'✅ 全部完成' if ok_count == len(sections) else '⚠️ 部分失败'}")
    print(f"  版本：{version_tag}")
    print(f"  成功：{ok_count}/{len(sections)} 版块")
    print(f"  总耗时：{total_elapsed:.1f}s")
    print(f"  Token 用量：input={total_input:,} / output={total_output:,}")
    print(f"  洞察文件：{round_dir}")
    if truncated:
        print(f"  ⚠️ 疑似被截断(finish_reason=length):{truncated}"
              f"——调大 pipeline_e.py::SECTIONS 里对应版块的 max_tokens 后重跑")

    if ok_count < len(sections):
        sys.exit(1)


if __name__ == "__main__":
    main()
