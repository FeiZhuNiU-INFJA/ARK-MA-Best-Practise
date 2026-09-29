#!/usr/bin/env python3
"""Run R1-R5 as five concurrent Ark batch calls for demo mode only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_config_state import update_run_config


WORKSPACE_ROOT = Path("/workspace")
SKILL_DIR = Path(__file__).resolve().parents[1]
COST_TRACKER = SKILL_DIR / "tool" / "cost-tracker" / "cost_tracker.py"
MODEL_PRICING_CNY = {
    "doubao-seed-evolving": (6.0, 30.0),
}

ROUTES = {
    "r1": {
        "subdir": "R1_平台借势",
        "prompt": "prompts/R1_平台借势/v2.md",
        "label": "是否平台玩法",
        "status": "r1_platform",
    },
    "r2": {
        "subdir": "R2_商业合作",
        "prompt": "prompts/R2_商业合作/v1.md",
        "label": "是否商业合作",
        "status": "r2_commercial",
    },
    "r3": {
        "subdir": "R3_风险预警",
        "prompt": "prompts/R3_风险预警/v1.md",
        "label": "是否风险预警",
        "status": "r3_risk",
    },
    "r4": {
        "subdir": "R4_创意借鉴",
        "prompt": "prompts/R4_创意借鉴/v2.md",
        "label": "是否营销发现",
        "status": "r4_creative",
    },
    "r5": {
        "subdir": "R5_消费者行为",
        "prompt": "prompts/R5_消费者行为/v2.md",
        "label": "是否消费者行为",
        "status": "r5_consumer",
    },
}


def resolve_project_dir(project_dir: str | Path) -> Path:
    path = Path(project_dir)
    return path if path.is_absolute() else WORKSPACE_ROOT / path


def _api_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


def _batch_system_prompt(route: dict) -> str:
    source = (SKILL_DIR / route["prompt"]).read_text(encoding="utf-8")
    source = source.split("## 五、输入", 1)[0].rstrip()
    return (
        source
        + "\n\n## Demo 批量模式\n"
        + "以下批量包装规则覆盖上文的单条输入和单行 JSON 包装要求，"
        + "但判定标准、字段含义和 40 字说明限制不变。"
        + "对每个 row_id 独立判断，必须原样返回全部 row_id，不得增删、合并或改名。"
    )


def _batch_user_prompt(route: dict, rows: list[dict]) -> str:
    label = route["label"]
    payload = {
        "records": rows,
        "required_output": {
            "results": [
                {
                    "row_id": "原样返回输入 row_id",
                    label: "是|否",
                    "判断说明": "单句，40字内",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _request_route(
    route_name: str,
    route: dict,
    rows: list[dict],
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int = 600,
) -> dict:
    body = {
        "model": model,
        "max_tokens": max(2000, len(rows) * 160),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _batch_system_prompt(route)},
            {"role": "user", "content": _batch_user_prompt(route, rows)},
        ],
    }
    request = urllib.request.Request(
        _api_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            choices = payload.get("choices") or []
            if not choices:
                raise ValueError("Ark response has no choices")
            choice = choices[0]
            if choice.get("finish_reason") == "length":
                raise ValueError("Ark output was truncated")
            content = (choice.get("message") or {}).get("content") or ""
            parsed = _parse_json_object(content)
            normalized = _normalize_results(
                route_name, rows, parsed.get("results")
            )
            usage = payload.get("usage") or {}
            return {
                "route": route_name,
                "normalized": normalized,
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            }
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{route_name} failed after 3 attempts: {last_error}")


def _normalize_results(route_name: str, rows: list[dict], results: object) -> list[dict]:
    if not isinstance(results, list):
        raise ValueError(f"{route_name} results must be a list")
    route = ROUTES[route_name]
    label = route["label"]
    expected = [str(row["row_id"]) for row in rows]
    by_id: dict[str, dict] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("row_id") or "")
        verdict = str(item.get(label) or "").strip()
        reason = str(item.get("判断说明") or "").strip()
        if row_id in by_id:
            raise ValueError(f"{route_name} duplicate row_id: {row_id}")
        if row_id in expected and verdict in {"是", "否"} and reason:
            by_id[row_id] = {
                "row_id": row_id,
                f"{route_name}_{label}": verdict,
                f"{route_name}_判断说明": reason,
                f"{route_name}_parse_error": 0,
            }
    missing = [row_id for row_id in expected if row_id not in by_id]
    if missing:
        raise ValueError(f"{route_name} missing or invalid row_ids: {missing}")
    return [by_id[row_id] for row_id in expected]


def _atomic_excel(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}.{os.getpid()}.tmp.xlsx")
    frame.to_excel(temp, index=False)
    os.replace(temp, path)


def _price_cny(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = next(
        (price for key, price in MODEL_PRICING_CNY.items() if key in model),
        (0.0, 0.0),
    )
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def _run_signature(source: Path, model: str) -> str:
    digest = hashlib.sha256()
    digest.update(source.read_bytes())
    digest.update(model.encode())
    for route in ROUTES.values():
        digest.update((SKILL_DIR / route["prompt"]).read_bytes())
    return digest.hexdigest()


def _cached_summary(project: Path, run_id: int, signature: str) -> dict | None:
    summary = {}
    for route_name, route in ROUTES.items():
        task_dir = project / "04_标注" / route["subdir"]
        meta_path = task_dir / f"{route_name}_completion_meta.json"
        post_path = task_dir / f"{route_name}_postprocess_r{run_id}.xlsx"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if (
            meta.get("run_signature") != signature
            or meta.get("status") != "pass"
            or not post_path.exists()
        ):
            return None
        summary[route_name] = {
            "rows": int(meta.get("total") or 0),
            "output_file": str(post_path),
            "input_tokens": int(meta.get("input_tokens") or 0),
            "output_tokens": int(meta.get("output_tokens") or 0),
            "cached": True,
        }
    return summary


def _record_cost(
    project: Path,
    route_name: str,
    model: str,
    run_id: int,
    row_count: int,
    input_tokens: int,
    output_tokens: int,
    cost_cny: float,
) -> None:
    if not COST_TRACKER.exists():
        return
    subprocess.run(
        [
            sys.executable,
            str(COST_TRACKER),
            "--project-dir",
            str(project),
            "append",
            "--phase",
            "C",
            "--task",
            f"{route_name}_demo_fast",
            "--round",
            str(run_id),
            "--mode",
            "demo",
            "--model-id",
            model,
            "--platform",
            "Ark",
            "--input-tokens",
            str(input_tokens),
            "--output-tokens",
            str(output_tokens),
            "--raw-cost",
            str(cost_cny),
            "--currency",
            "CNY",
            "--row-count",
            str(row_count),
        ],
        check=True,
    )


def run(project_dir: str, input_file: str, model: str, run_id: int) -> dict:
    project = resolve_project_dir(project_dir)
    source = Path(input_file)
    if not source.is_absolute():
        source = project / source
    signature = _run_signature(source, model)
    cached = _cached_summary(project, run_id, signature)
    if cached is not None:
        print("[demo-routes] cache hit: R1-R5 already complete", flush=True)
        return cached

    frame = pd.read_excel(source)
    desc_col = "desc" if "desc" in frame.columns else "hottopic_desc"
    required = {"row_id", "platform", "title", desc_col}
    if not required.issubset(frame.columns):
        raise ValueError(f"demo routes input missing columns: {sorted(required - set(frame.columns))}")
    rows = [
        {
            "row_id": str(row["row_id"]),
            "平台": "" if pd.isna(row["platform"]) else str(row["platform"]),
            "标题": "" if pd.isna(row["title"]) else str(row["title"]),
            "描述": "" if pd.isna(row[desc_col]) else str(row[desc_col]),
        }
        for _, row in frame.iterrows()
    ]
    if not rows:
        raise ValueError("demo routes input is empty")

    api_key = os.environ.get("ARK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("ARK_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise EnvironmentError("ARK_API_KEY/ARK_BASE_URL are required")

    started_at = datetime.now().astimezone().isoformat()
    update_run_config(
        project,
        {
            route["status"]: {
                "status": "running",
                "run_id": run_id,
                "model_id": model,
                "backend": "ark_demo_batch",
                "started_at": started_at,
            }
            for route in ROUTES.values()
        },
    )

    calls: dict[str, dict] = {}
    try:
        with ThreadPoolExecutor(max_workers=len(ROUTES)) as pool:
            futures = {
                pool.submit(
                    _request_route,
                    route_name,
                    route,
                    rows,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                ): route_name
                for route_name, route in ROUTES.items()
            }
            for future in as_completed(futures):
                route_name = futures[future]
                calls[route_name] = future.result()
                print(f"[demo-routes] {route_name} model call complete", flush=True)

        summary = {}
        for route_name, route in ROUTES.items():
            call = calls[route_name]
            normalized_frame = pd.DataFrame(call["normalized"])
            task_dir = project / "04_标注" / route["subdir"]
            raw_path = task_dir / f"{route_name}_result_raw_r{run_id}.xlsx"
            post_path = task_dir / f"{route_name}_postprocess_r{run_id}.xlsx"
            completion_path = task_dir / f"{route_name}_completion_meta.json"

            raw_frame = frame.copy()
            result_by_id = {
                row["row_id"]: row for row in call["normalized"]
            }
            raw_frame["llm_result"] = raw_frame["row_id"].astype(str).map(
                lambda row_id: json.dumps(
                    {
                        route["label"]: result_by_id[row_id][f"{route_name}_{route['label']}"],
                        "判断说明": result_by_id[row_id][f"{route_name}_判断说明"],
                    },
                    ensure_ascii=False,
                )
            )
            _atomic_excel(raw_frame, raw_path)
            _atomic_excel(normalized_frame, post_path)

            cost_cny = _price_cny(
                model, call["input_tokens"], call["output_tokens"]
            )
            completed_at = datetime.now().astimezone().isoformat()
            meta = {
                "task": route_name,
                "run_id": run_id,
                "model_id": model,
                "platform": "Ark",
                "backend": "ark_demo_batch",
                "run_signature": signature,
                "status": "pass",
                "total": len(rows),
                "valid": len(rows),
                "invalid": 0,
                "valid_rate": 1.0,
                "input_tokens": call["input_tokens"],
                "output_tokens": call["output_tokens"],
                "total_tokens": call["input_tokens"] + call["output_tokens"],
                "total_consume": cost_cny,
                "raw_file": str(raw_path),
                "post_file": str(post_path),
                "started_at": started_at,
                "completed_at": completed_at,
            }
            completion_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            update_run_config(
                project,
                {
                    route["status"]: {
                        "status": "done",
                        "run_id": run_id,
                        "model_id": model,
                        "backend": "ark_demo_batch",
                        "row_count": len(rows),
                        "valid_rate": 1.0,
                        "output_file": str(post_path),
                        "completed_at": completed_at,
                    }
                },
            )
            _record_cost(
                project,
                route_name,
                model,
                run_id,
                len(rows),
                call["input_tokens"],
                call["output_tokens"],
                cost_cny,
            )
            summary[route_name] = {
                "rows": len(rows),
                "output_file": str(post_path),
                "input_tokens": call["input_tokens"],
                "output_tokens": call["output_tokens"],
            }
        return summary
    except Exception as error:
        failed_at = datetime.now().astimezone().isoformat()
        update_run_config(
            project,
            {
                ROUTES[name]["status"]: {
                    "status": "failed",
                    "run_id": run_id,
                    "backend": "ark_demo_batch",
                    "error": str(error),
                    "failed_at": failed_at,
                }
                for name in ROUTES
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument(
        "--model",
        default=os.environ.get("DEMO_CHAT_MODEL_ID")
        or os.environ.get("C2_CHAT_MODEL_ID")
        or "doubao-seed-evolving",
    )
    args = parser.parse_args()
    try:
        result = run(args.project_dir, args.input, args.model, args.run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"[demo-routes] failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
