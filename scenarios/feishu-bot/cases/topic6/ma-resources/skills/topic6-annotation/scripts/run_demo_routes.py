#!/usr/bin/env python3
"""Run resumable R1-R5 Ark batch calls for demo mode only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_config_state import update_run_config


WORKSPACE_ROOT = Path("/workspace")
SKILL_DIR = Path(__file__).resolve().parents[1]
COST_TRACKER = SKILL_DIR / "tool" / "cost-tracker" / "cost_tracker.py"
MODEL_PRICING_CNY = {
    "doubao-seed-evolving": (6.0, 30.0),
}
CHECKPOINT_VERSION = 1
DEFAULT_BATCH_SIZE = 5
DEFAULT_MAX_WORKERS = 5
DEFAULT_MAX_ATTEMPTS = 5

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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
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
    encoded_body = json.dumps(body, ensure_ascii=False).encode()
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            # A fresh client per attempt prevents a disconnected keep-alive
            # socket from poisoning subsequent retries.
            with httpx.Client(
                timeout=httpx.Timeout(timeout),
                limits=httpx.Limits(
                    max_connections=1,
                    max_keepalive_connections=0,
                ),
                http2=False,
            ) as client:
                response = client.post(
                    _api_url(base_url),
                    content=encoded_body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Connection": "close",
                    },
                )
                response.raise_for_status()
                payload = response.json()
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
        except httpx.HTTPStatusError as error:
            last_error = error
            status_code = error.response.status_code
            if status_code != 429 and not 500 <= status_code < 600:
                raise RuntimeError(
                    f"{route_name} Ark HTTP {status_code}: "
                    f"{error.response.text[:300]}"
                ) from error
        except (
            httpx.TransportError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            last_error = error
        if attempt < max_attempts - 1:
            delay = min(30.0, 2 ** attempt) + random.uniform(0.0, 0.5)
            print(
                f"[demo-routes] {route_name} attempt {attempt + 1}/"
                f"{max_attempts} failed: {last_error}; retry in {delay:.1f}s",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(
        f"{route_name} failed after {max_attempts} attempts: {last_error}"
    )


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


def _atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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


def _task_paths(project: Path, route_name: str, run_id: int) -> dict[str, Path]:
    task_dir = project / "04_标注" / ROUTES[route_name]["subdir"]
    return {
        "raw": task_dir / f"{route_name}_result_raw_r{run_id}.xlsx",
        "post": task_dir / f"{route_name}_postprocess_r{run_id}.xlsx",
        "meta": task_dir / f"{route_name}_completion_meta.json",
        "checkpoint": task_dir / f"{route_name}_demo_checkpoint_r{run_id}.json",
    }


def _cached_route(
    project: Path,
    route_name: str,
    run_id: int,
    signature: str,
) -> dict | None:
    paths = _task_paths(project, route_name, run_id)
    try:
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if (
        meta.get("run_id") != run_id
        or meta.get("run_signature") != signature
        or meta.get("status") != "pass"
        or not paths["raw"].exists()
        or not paths["post"].exists()
    ):
        return None
    return {
        "rows": int(meta.get("total") or 0),
        "output_file": str(paths["post"]),
        "input_tokens": int(meta.get("input_tokens") or 0),
        "output_tokens": int(meta.get("output_tokens") or 0),
        "cached": True,
    }


def _new_checkpoint(
    route_name: str,
    signature: str,
    model: str,
    run_id: int,
    rows: list[dict],
    batch_size: int,
    started_at: str,
) -> dict:
    return {
        "version": CHECKPOINT_VERSION,
        "route": route_name,
        "run_id": run_id,
        "run_signature": signature,
        "model_id": model,
        "batch_size": batch_size,
        "row_ids": [str(row["row_id"]) for row in rows],
        "started_at": started_at,
        "chunks": {},
    }


def _load_checkpoint(
    path: Path,
    *,
    route_name: str,
    signature: str,
    model: str,
    run_id: int,
    rows: list[dict],
    batch_size: int,
    started_at: str,
) -> dict:
    expected_row_ids = [str(row["row_id"]) for row in rows]
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        checkpoint = {}
    valid = (
        checkpoint.get("version") == CHECKPOINT_VERSION
        and checkpoint.get("route") == route_name
        and checkpoint.get("run_id") == run_id
        and checkpoint.get("run_signature") == signature
        and checkpoint.get("model_id") == model
        and checkpoint.get("batch_size") == batch_size
        and checkpoint.get("row_ids") == expected_row_ids
        and isinstance(checkpoint.get("chunks"), dict)
    )
    if not valid:
        checkpoint = _new_checkpoint(
            route_name,
            signature,
            model,
            run_id,
            rows,
            batch_size,
            started_at,
        )
        _atomic_json(checkpoint, path)
    return checkpoint


def _chunks(rows: list[dict], batch_size: int) -> list[list[dict]]:
    return [
        rows[index:index + batch_size]
        for index in range(0, len(rows), batch_size)
    ]


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


def _chunk_is_complete(chunk: object, expected_rows: list[dict]) -> bool:
    if not isinstance(chunk, dict) or not isinstance(chunk.get("normalized"), list):
        return False
    expected_ids = [str(row["row_id"]) for row in expected_rows]
    return (
        chunk.get("row_ids") == expected_ids
        and [str(row.get("row_id")) for row in chunk["normalized"]] == expected_ids
    )


def _finalize_route(
    project: Path,
    frame: pd.DataFrame,
    route_name: str,
    checkpoint: dict,
    *,
    model: str,
    run_id: int,
    signature: str,
) -> dict:
    route = ROUTES[route_name]
    chunks = checkpoint["chunks"]
    chunk_count = (
        len(frame) + checkpoint["batch_size"] - 1
    ) // checkpoint["batch_size"]
    ordered_chunks = [chunks[str(index)] for index in range(chunk_count)]
    normalized = [
        row
        for chunk in ordered_chunks
        for row in chunk["normalized"]
    ]
    input_tokens = sum(int(chunk.get("input_tokens") or 0) for chunk in ordered_chunks)
    output_tokens = sum(int(chunk.get("output_tokens") or 0) for chunk in ordered_chunks)
    paths = _task_paths(project, route_name, run_id)

    result_by_id = {row["row_id"]: row for row in normalized}
    expected_ids = frame["row_id"].astype(str).tolist()
    if list(result_by_id) != expected_ids:
        raise ValueError(f"{route_name} checkpoint row order does not match input")

    raw_frame = frame.copy()
    raw_frame["llm_result"] = raw_frame["row_id"].astype(str).map(
        lambda row_id: json.dumps(
            {
                route["label"]: result_by_id[row_id][
                    f"{route_name}_{route['label']}"
                ],
                "判断说明": result_by_id[row_id][f"{route_name}_判断说明"],
            },
            ensure_ascii=False,
        )
    )
    _atomic_excel(raw_frame, paths["raw"])
    _atomic_excel(pd.DataFrame(normalized), paths["post"])

    cost_cny = _price_cny(model, input_tokens, output_tokens)
    completed_at = datetime.now().astimezone().isoformat()
    _record_cost(
        project,
        route_name,
        model,
        run_id,
        len(normalized),
        input_tokens,
        output_tokens,
        cost_cny,
    )
    meta = {
        "task": route_name,
        "run_id": run_id,
        "model_id": model,
        "platform": "Ark",
        "backend": "ark_demo_batch",
        "run_signature": signature,
        "status": "pass",
        "total": len(normalized),
        "valid": len(normalized),
        "invalid": 0,
        "valid_rate": 1.0,
        "batch_size": checkpoint["batch_size"],
        "chunk_count": len(ordered_chunks),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "total_consume": cost_cny,
        "raw_file": str(paths["raw"]),
        "post_file": str(paths["post"]),
        "started_at": checkpoint["started_at"],
        "completed_at": completed_at,
    }
    _atomic_json(meta, paths["meta"])
    update_run_config(
        project,
        {
            route["status"]: {
                "status": "done",
                "run_id": run_id,
                "model_id": model,
                "backend": "ark_demo_batch",
                "row_count": len(normalized),
                "valid_rate": 1.0,
                "output_file": str(paths["post"]),
                "completed_at": completed_at,
            }
        },
    )
    checkpoint["status"] = "complete"
    checkpoint["completed_at"] = completed_at
    _atomic_json(checkpoint, paths["checkpoint"])
    return {
        "rows": len(normalized),
        "output_file": str(paths["post"]),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def run(
    project_dir: str,
    input_file: str,
    model: str,
    run_id: int,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_workers: int = DEFAULT_MAX_WORKERS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict:
    if batch_size < 1 or max_workers < 1 or max_attempts < 1:
        raise ValueError("batch_size, max_workers and max_attempts must be positive")
    project = resolve_project_dir(project_dir)
    source = Path(input_file)
    if not source.is_absolute():
        source = project / source
    signature = _run_signature(source, model)

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
    row_ids = [row["row_id"] for row in rows]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("demo routes input row_id must be unique")

    started_at = datetime.now().astimezone().isoformat()
    summary: dict[str, dict] = {}
    active_routes = []
    for route_name in ROUTES:
        cached = _cached_route(project, route_name, run_id, signature)
        if cached is not None:
            summary[route_name] = cached
            print(f"[demo-routes] {route_name} cache hit", flush=True)
        else:
            active_routes.append(route_name)
    if not active_routes:
        print("[demo-routes] cache hit: R1-R5 already complete", flush=True)
        return summary

    api_key = os.environ.get("ARK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("ARK_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise EnvironmentError("ARK_API_KEY/ARK_BASE_URL are required")

    update_run_config(
        project,
        {
            ROUTES[route_name]["status"]: {
                "status": "running",
                "run_id": run_id,
                "model_id": model,
                "backend": "ark_demo_batch",
                "started_at": started_at,
            }
            for route_name in active_routes
        },
    )

    route_chunks = {name: _chunks(rows, batch_size) for name in active_routes}
    checkpoints = {}
    for route_name in active_routes:
        checkpoint_path = _task_paths(project, route_name, run_id)["checkpoint"]
        checkpoints[route_name] = _load_checkpoint(
            checkpoint_path,
            route_name=route_name,
            signature=signature,
            model=model,
            run_id=run_id,
            rows=rows,
            batch_size=batch_size,
            started_at=started_at,
        )

    errors: dict[str, list[str]] = {}

    def run_route(route_name: str) -> dict:
        """Process one route serially; separate routes run concurrently."""
        route = ROUTES[route_name]
        chunks = route_chunks[route_name]
        checkpoint = checkpoints[route_name]
        for chunk_index, chunk_rows in enumerate(chunks):
            if _chunk_is_complete(
                checkpoint["chunks"].get(str(chunk_index)), chunk_rows
            ):
                print(
                    f"[demo-routes] {route_name} chunk {chunk_index + 1}/"
                    f"{len(chunks)} checkpoint hit",
                    flush=True,
                )
                continue
            call = _request_route(
                route_name,
                route,
                chunk_rows,
                model=model,
                base_url=base_url,
                api_key=api_key,
                max_attempts=max_attempts,
            )
            checkpoint["chunks"][str(chunk_index)] = {
                "row_ids": [str(row["row_id"]) for row in chunk_rows],
                "normalized": call["normalized"],
                "input_tokens": call["input_tokens"],
                "output_tokens": call["output_tokens"],
                "completed_at": datetime.now().astimezone().isoformat(),
            }
            _atomic_json(
                checkpoint,
                _task_paths(project, route_name, run_id)["checkpoint"],
            )
            print(
                f"[demo-routes] {route_name} chunk {chunk_index + 1}/"
                f"{len(chunks)} complete",
                flush=True,
            )
        result = _finalize_route(
            project,
            frame,
            route_name,
            checkpoint,
            model=model,
            run_id=run_id,
            signature=signature,
        )
        print(f"[demo-routes] {route_name} complete", flush=True)
        return result

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(active_routes))
    ) as pool:
        futures = {
            pool.submit(run_route, route_name): route_name
            for route_name in active_routes
        }
        for future in as_completed(futures):
            route_name = futures[future]
            try:
                summary[route_name] = future.result()
            except Exception as error:
                errors.setdefault(route_name, []).append(str(error))

    incomplete = [name for name in active_routes if name not in summary]
    if incomplete:
        failed_at = datetime.now().astimezone().isoformat()
        update_run_config(
            project,
            {
                ROUTES[name]["status"]: {
                    "status": "failed",
                    "run_id": run_id,
                    "backend": "ark_demo_batch",
                    "error": "; ".join(errors.get(name) or ["incomplete chunks"]),
                    "failed_at": failed_at,
                }
                for name in incomplete
            },
        )
        details = "; ".join(
            f"{name}: {', '.join(errors.get(name) or ['incomplete chunks'])}"
            for name in incomplete
        )
        raise RuntimeError(f"demo routes incomplete; rerun to resume: {details}")

    return {name: summary[name] for name in ROUTES}


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
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    args = parser.parse_args()
    try:
        result = run(
            args.project_dir,
            args.input,
            args.model,
            args.run_id,
            batch_size=args.batch_size,
            max_workers=args.max_workers,
            max_attempts=args.max_attempts,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"[demo-routes] failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
