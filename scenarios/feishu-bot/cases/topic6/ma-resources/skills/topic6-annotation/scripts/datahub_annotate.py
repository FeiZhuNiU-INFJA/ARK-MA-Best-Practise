#!/usr/bin/env python3
"""
datahub_annotate.py — MA 环境一体化标注脚本（C0/R1~R5/C3 单任务全流程）

将客户版的 datahub_submit + datahub_poll + annotation_postprocess + c3_flatten
+ c3_timewindow 五个脚本合并为单入口,一次调用即可完成:
  上传 → 建任务 → 轮询 → 下载 → JSON 展开 → C3 时效窗口 → 写 postprocess 输出

MA 挂载路径口径:
  Skill 目录: /mnt/skills/topic6-annotation/{prompts,scripts,references}
  项目目录  : /workspace/Projects/{project_dir}/
             ├── 04_标注/{C0_基础事实,R1_平台借势,...,C3_节点标注}/
             │   ├── {task}_submit_meta.json         (上传+建任务后写)
             │   ├── {task}_result_raw_r{N}.xlsx     (下载原始结果)
             │   ├── {task}_postprocess_r{N}.xlsx    (JSON 展开后)
             │   └── {task}_completion_meta.json     (token/耗时/成本)
             └── run_config.yaml (可选,读 usd_to_cny 汇率)

CLI:
  python datahub_annotate.py \\
    --task c0 --project-dir /workspace/Projects/W35 \\
    --input 03_抽样/sample_500.xlsx --model-id gpt-4o-mini \\
    --prompt-file /mnt/skills/topic6-annotation/prompts/C0_基础事实/v1.md

Env:
  DATAHUB_API_KEY  (必填,MA 环境注入)
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

DATAHUB_BASE = "https://bmc-data-hub.bluemediagroup.cn"
DONE_STATUSES = {"TASK_STATUS_GENERATED_RESULT", "TASK_STATUS_SUCCESS"}
FAILED_STATUSES = {"TASK_STATUS_FAILED", "TASK_STATUS_CANCELED"}
POLL_INTERVAL = 20
POLL_TIMEOUT = 7200
DOWNLOAD_TIMEOUT = 300

TASK_META: dict[str, dict] = {
    "c0": {"label": "基础事实标注", "subdir": "C0_基础事实"},
    "r1": {"label": "平台借势判断", "subdir": "R1_平台借势"},
    "r2": {"label": "商业合作判断", "subdir": "R2_商业合作"},
    "r3": {"label": "风险预警判断", "subdir": "R3_风险预警"},
    "r4": {"label": "创意借鉴判断", "subdir": "R4_创意借鉴"},
    "r5": {"label": "消费者行为判断", "subdir": "R5_消费者行为"},
    "c3": {"label": "节点标注", "subdir": "C3_节点标注"},
}

TASK_SCHEMAS: dict[str, dict] = {
    "c0": {
        "fields": [
            "商业实体", "热点驱动词", "行业归属", "营销触发方式", "营销维度",
            "平台原生形式", "不可用原因", "是否营销可用", "判断说明",
        ],
        "primary_field": "是否营销可用",
    },
    "r1": {"fields": ["是否平台玩法", "判断说明"], "primary_field": "是否平台玩法"},
    "r2": {"fields": ["是否商业合作", "判断说明"], "primary_field": "是否商业合作"},
    "r3": {"fields": ["是否风险预警", "判断说明"], "primary_field": "是否风险预警"},
    "r4": {"fields": ["是否营销发现", "判断说明"], "primary_field": "是否营销发现"},
    "r5": {"fields": ["是否消费者行为", "判断说明"], "primary_field": "是否消费者行为"},
}

VALID_THRESHOLDS = {"pass": 0.92, "warn": 0.80}
C3_SUB_FIELDS = ["提取节点", "是否节日营销", "涉及品牌", "是否节点定制营销", "相关依据"]

REFERENCES_DIR = Path("/mnt/skills/topic6-annotation/references/marketing_calendar")
NAME_MAP = {
    "女生节/妇女节": "妇女节", "植树节": "中国植树节",
    "消费者权益日": "消费者权益保护日", "520/告白季": "520网络情人节",
    "青年节": "五四青年节", "建党日": "建党节", "清明节": "清明",
    "双11": "双11大促", "双12": "双12大促", "双11预售": "双11预售启动",
}
TYPE_WINDOW_OVERRIDE = {"大众节日": (30, 7)}
NODE_WINDOW_OVERRIDE = {"高考": (14, 90)}


for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _api_key() -> str:
    key = os.environ.get("DATAHUB_API_KEY", "").strip()
    if not key:
        raise RuntimeError("环境变量 DATAHUB_API_KEY 未设置")
    return key


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _next_run_id(directory: Path, glob_pattern: str) -> int:
    nums = []
    for f in directory.glob(glob_pattern):
        m = re.search(r"_r(\d+)\.xlsx$", f.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def _resolve_project_dir(project_dir: str) -> Path:
    p = Path(project_dir)
    if not p.is_absolute():
        p = Path("/workspace") / project_dir
    return p


def _prepare_input(input_path: Path) -> Path:
    """C0/R1~R5/C3 均直接读文本判断; 若列名是 hottopic_desc, 需要重命名为 desc。"""
    df = pd.read_excel(input_path)
    if "hottopic_desc" in df.columns and "desc" not in df.columns:
        df = df.rename(columns={"hottopic_desc": "desc"})
        prepared = input_path.parent / f".{input_path.stem}_prepared.xlsx"
        df.to_excel(prepared, index=False)
        return prepared
    return input_path


def _upload_file(api_key: str, file_path: Path) -> str:
    url = f"{DATAHUB_BASE}/api/v1/file/upload"
    with open(file_path, "rb") as f:
        resp = requests.post(
            url, params={"file_name": file_path.name},
            headers=_headers(api_key),
            files={"file": (file_path.name, f)}, timeout=120,
        )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code", -1) != 0:
        raise RuntimeError(f"上传失败: {data.get('message', data)}")
    return data["data"]["data_source_id"]


def _create_task(api_key: str, data_source_id: str, prompt_text: str,
                 model_id: str, task_name: str) -> int:
    url = f"{DATAHUB_BASE}/api/v1/task"
    payload = {
        "name": task_name,
        "data_source": {"data_source_id": data_source_id},
        "prompt": {
            "prompt_text": prompt_text,
            "prompt_run_config": {"require_llm_result_json": True},
        },
        "model": {"model_id": model_id},
    }
    resp = requests.post(
        url, headers={**_headers(api_key), "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code", -1) != 0:
        raise RuntimeError(f"建任务失败: {data.get('message', data)}")
    return data["data"]["task_id"]


def _get_task(api_key: str, task_id: int) -> dict:
    resp = requests.get(
        f"{DATAHUB_BASE}/api/v1/task/{task_id}",
        headers=_headers(api_key), timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code", -1) != 0:
        raise RuntimeError(f"查询失败: {body.get('message', body)}")
    return body["data"]


def _get_model_info(api_key: str, model_id: str) -> dict:
    resp = requests.get(f"{DATAHUB_BASE}/api/v1/model/list",
                        headers=_headers(api_key), timeout=30)
    resp.raise_for_status()
    models = resp.json().get("data", {}).get("models", [])
    for m in models:
        if m.get("model_id") == model_id:
            return {
                "platform": m.get("platform", "Unknown"),
                "input_price": float(str(m.get("input_price", 0)).lstrip("$¥￥")),
                "output_price": float(str(m.get("output_price", 0)).lstrip("$¥￥")),
            }
    return {"platform": "Unknown", "input_price": 0.0, "output_price": 0.0}


def _download_result(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)


def _poll_until_done(api_key: str, task_id: int) -> dict:
    start = time.time()
    while True:
        data = _get_task(api_key, task_id)
        status = data.get("task_status")
        elapsed = int(time.time() - start)
        print(f"[poll] task={task_id} status={status} elapsed={elapsed}s", flush=True)
        if status in DONE_STATUSES:
            return data
        if status in FAILED_STATUSES:
            raise RuntimeError(f"任务 {task_id} 终止于失败态: {status}")
        if elapsed > POLL_TIMEOUT:
            raise TimeoutError(f"任务 {task_id} 轮询超时 ({POLL_TIMEOUT}s)")
        time.sleep(POLL_INTERVAL)


def _strip_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s[:4].lower() == "json":
            s = s[4:].strip()
    return s


def _parse_dict(val):
    if pd.isna(val):
        return None
    text = str(val).strip()
    if not text:
        return None
    for cand in (text, _strip_fence(text)):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        try:
            obj = ast.literal_eval(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _parse_c3(val):
    """C3 结果结构 {"关联节点": [...]} → list[dict], 失败返回 None."""
    if pd.isna(val):
        return None
    text = str(val).strip()
    if not text:
        return None

    def _extract(obj):
        if isinstance(obj, dict) and isinstance(obj.get("关联节点"), list):
            return obj["关联节点"]
        if isinstance(obj, list):
            return obj
        return None

    for cand in (text, _strip_fence(text)):
        try:
            r = _extract(json.loads(cand))
            if r is not None:
                return r
        except Exception:
            pass
        try:
            r = _extract(ast.literal_eval(cand))
            if r is not None:
                return r
        except Exception:
            pass
    return None


def _join_or_collapse(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(set(parts)) == 1:
        return parts[0]
    return "|".join(parts)


def _c3_join(nodes, key: str) -> str:
    if not isinstance(nodes, list) or not nodes:
        return ""
    parts = [str(n.get(key, "") or "") for n in nodes if isinstance(n, dict)]
    return _join_or_collapse(parts)


def _c3_flatten(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    parsed = df["llm_result"].apply(_parse_c3)
    df["c3_parse_error"] = parsed.isna().astype(int)
    for field in C3_SUB_FIELDS:
        df[field] = parsed.apply(lambda x, k=field: _c3_join(x, k))
    return df


def _c3_timewindow(df: pd.DataFrame) -> pd.DataFrame:
    """按位置对齐算距节点天数 / 是否窗口期内。"""
    import csv

    sys.path.insert(0, str(REFERENCES_DIR))
    from generate_marketing_calendar import resolve_date  # type: ignore

    calendar: dict[str, dict] = {}
    for path in (REFERENCES_DIR / "nodes_definition.csv",
                 REFERENCES_DIR / "nodes_definition_supplement.csv"):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                calendar[row["name"].strip()] = row

    def _node_date(name: str, year: int):
        csv_name = NAME_MAP.get(name, name)
        row = calendar.get(csv_name)
        if row is None:
            return None
        valid_years = row.get("valid_years", "").strip()
        if valid_years and str(year) != valid_years:
            return None
        cached: dict = {}
        if row["calc_type"] == "lunar_derived":
            spring = calendar.get("春节")
            if spring:
                d = resolve_date(year, spring, cached)
                if d:
                    cached["春节"] = d
        return resolve_date(year, row, cached)

    def _window(name: str) -> tuple[int, int]:
        csv_name = NAME_MAP.get(name, name)
        row_def = calendar.get(csv_name, {})
        if name in NODE_WINDOW_OVERRIDE:
            return NODE_WINDOW_OVERRIDE[name]
        if row_def.get("type") in TYPE_WINDOW_OVERRIDE:
            return TYPE_WINDOW_OVERRIDE[row_def.get("type")]
        return int(row_def.get("window_before", 3) or 3), int(row_def.get("window_after", 3) or 3)

    df = df.copy()
    df["_pub"] = pd.to_datetime(df["hotpost_time"], errors="coerce").dt.date

    dists, ins = [], []
    for _, row in df.iterrows():
        names_field = row.get("提取节点")
        pub = row["_pub"]
        if pd.isna(names_field) or str(names_field).strip() == "" or pd.isna(pub):
            dists.append("")
            ins.append("")
            continue
        names = [n.strip() for n in str(names_field).split("|")]
        dparts, wparts = [], []
        for n in names:
            nd = _node_date(n, pub.year)
            if nd is None:
                dparts.append("")
                wparts.append("未知")
                continue
            d = (pub - nd).days
            wb, wa = _window(n)
            dparts.append(str(d))
            wparts.append("是" if -wb <= d <= wa else "否")
        dists.append(_join_or_collapse(dparts))
        ins.append(_join_or_collapse(wparts))
    df["距节点天数"] = dists
    df["是否窗口期内"] = ins
    return df.drop(columns=["_pub"])


def _postprocess(df: pd.DataFrame, task: str) -> tuple[pd.DataFrame, dict]:
    total = len(df)
    if task == "c3":
        df = _c3_flatten(df)
        df = _c3_timewindow(df)
        valid = int((df["c3_parse_error"] == 0).sum())
    else:
        schema = TASK_SCHEMAS[task]
        parsed = df["llm_result"].apply(_parse_dict)
        valid_mask = parsed.notna()
        valid = int(valid_mask.sum())
        for field in schema["fields"]:
            df[f"{task}_{field}"] = parsed.apply(
                lambda o, k=field: (o.get(k) if o and o.get(k) is not None else "")
            )
        df[f"{task}_parse_error"] = (~valid_mask).astype(int)

    invalid = total - valid
    rate = valid / total if total else 0.0
    status = ("pass" if rate >= VALID_THRESHOLDS["pass"]
              else "warn" if rate >= VALID_THRESHOLDS["warn"] else "fail")
    return df, {
        "total": total, "valid": valid, "invalid": invalid,
        "valid_rate": round(rate, 4), "status": status,
    }


def annotate(task: str, project_dir: str, input_file: str, prompt_file: str,
             model_id: str, run_id: int | None = None) -> dict:
    if task not in TASK_META:
        raise ValueError(f"未知 task: {task}")

    api_key = _api_key()
    meta = TASK_META[task]
    proj = _resolve_project_dir(project_dir)
    task_dir = proj / "04_标注" / meta["subdir"]
    task_dir.mkdir(parents=True, exist_ok=True)

    if run_id is None:
        run_id = _next_run_id(task_dir, f"{task}_result_raw_r*.xlsx")
    print(f"[annotate] task={task} project={proj} run_id=r{run_id}", flush=True)

    input_path = Path(input_file)
    if not input_path.is_absolute():
        input_path = proj / input_file
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    prompt_text = Path(prompt_file).read_text(encoding="utf-8")
    print(f"[annotate] prompt={prompt_file} ({len(prompt_text)} chars)", flush=True)

    upload_path = _prepare_input(input_path)
    print(f"[annotate] 上传: {upload_path}", flush=True)
    ds_id = _upload_file(api_key, upload_path)
    print(f"[annotate] data_source_id={ds_id}", flush=True)

    task_name = f"topic6_{task}_{meta['label']}_r{run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    task_id = _create_task(api_key, ds_id, prompt_text, model_id, task_name)
    print(f"[annotate] task_id={task_id}", flush=True)

    model_info = _get_model_info(api_key, model_id)
    submit_meta = {
        "task": task, "task_id": task_id, "run_id": run_id,
        "model_id": model_id, "platform": model_info["platform"],
        "input_price": model_info["input_price"],
        "output_price": model_info["output_price"],
        "currency": "USD" if model_info["platform"] in
                    {"OpenAI", "Anthropic", "Google", "Meta", "Mistral"} else "CNY",
        "prompt_file": str(prompt_file),
        "input_file": str(upload_path),
        "created_at": datetime.now().isoformat(),
    }
    (task_dir / f"{task}_submit_meta.json").write_text(
        json.dumps(submit_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    task_data = _poll_until_done(api_key, task_id)
    result_url = task_data.get("result_url")
    raw_out = task_dir / f"{task}_result_raw_r{run_id}.xlsx"
    if result_url:
        print(f"[annotate] 下载: {result_url}", flush=True)
        _download_result(result_url, raw_out)
    else:
        raise RuntimeError(f"任务 {task_id} 完成但无 result_url")

    df = pd.read_excel(raw_out)
    df_post, stats = _postprocess(df, task)
    post_out = task_dir / f"{task}_postprocess_r{run_id}.xlsx"
    tmp = post_out.with_suffix(".tmp.xlsx")
    df_post.to_excel(tmp, index=False)
    os.replace(tmp, post_out)

    total_tokens = int(task_data.get("total_tokens", 0) or 0)
    total_consume = float(task_data.get("total_consume", 0) or 0.0)
    input_tokens = int(df.get("llm_input_tokens", pd.Series(dtype=int)).fillna(0).sum())
    output_tokens = int(df.get("llm_output_tokens", pd.Series(dtype=int)).fillna(0).sum())

    completion_meta = {
        **submit_meta,
        "status": stats["status"], "total": stats["total"],
        "valid": stats["valid"], "invalid": stats["invalid"],
        "valid_rate": stats["valid_rate"],
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": total_tokens, "total_consume": total_consume,
        "raw_file": str(raw_out), "post_file": str(post_out),
        "completed_at": datetime.now().isoformat(),
    }
    (task_dir / f"{task}_completion_meta.json").write_text(
        json.dumps(completion_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[annotate] ✅ {task} 完成: {stats}", flush=True)
    print(f"[annotate] 后处理: {post_out}", flush=True)
    return completion_meta


def main() -> int:
    p = argparse.ArgumentParser(description="MA 一体化 DataHub 标注 (submit+poll+postprocess)")
    p.add_argument("--task", required=True, choices=list(TASK_META))
    p.add_argument("--project-dir", required=True)
    p.add_argument("--input", required=True, help="输入 xlsx (相对项目根或绝对路径)")
    p.add_argument("--prompt-file", required=True, help="Prompt 绝对路径")
    p.add_argument("--model-id", required=True)
    p.add_argument("--run-id", type=int, default=None)
    args = p.parse_args()

    try:
        result = annotate(args.task, args.project_dir, args.input,
                          args.prompt_file, args.model_id, args.run_id)
        print("\n=== 完成 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
