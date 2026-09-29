#!/usr/bin/env python3
"""Run the complete Topic6 C2 cross-platform event archive.

The cross-platform path is:
  platform 00 in parallel -> x0 -> merged 01..07 -> x2 -> x3 -> x4

Each completed stage is recorded in c2_status.json. Re-running the command
resumes from the first incomplete stage; the underlying LLM scripts also keep
their content-addressed caches.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from relay import Relay  # noqa: E402

ANNOTATION_SCRIPT_DIR = (
    SCRIPT_DIR.parent.parent / "topic6-annotation" / "scripts"
)
sys.path.insert(0, str(ANNOTATION_SCRIPT_DIR))
from run_config_state import update_run_config  # noqa: E402

PLATFORMS = {
    "weibo": "微博",
    "douyin": "抖音",
    "bilibili": "B站",
    "zhihu": "知乎",
}
RUNNER_SCHEMA_VERSION = 1
DEMO_FAST_SYSTEM = """你是社媒热点事件归并器。给定少量热点标题，把描述同一现实事件的记录归入同一事件簇。

规则：
1. 每个输入 row_id 必须恰好返回一次，不得增删或修改 row_id。
2. 同一人物/品牌不代表同一事件；只有核心动作、对象和时间语境一致才合并。
3. 一级事件名使用简洁、可读的中文事实短语，不写平台名、热度和评价。
4. 信息不足时宁可保持独立，不要过度合并。
5. 只输出 JSON 对象，不要 markdown：
{"results":[{"row_id":"原样返回","一级事件名":"事件簇名"}]}
"""


def resolve_project_dir(project_dir: str | Path) -> Path:
    path = Path(project_dir)
    return path if path.is_absolute() else Path("/workspace") / path


def build_stage_plan(
    run_dir: Path,
    input_csv: Path,
    chat_model: str,
    embedding_model: str,
    *,
    x2_verdict: str = "sonnet",
) -> list[tuple[str, list[list[str]]]]:
    py = sys.executable
    script = lambda name: str(SCRIPT_DIR / name)
    platform_00 = [
        [
            py,
            script("00_clean_titles.py"),
            "--input",
            str(input_csv),
            "--run-dir",
            str(run_dir / platform),
            "--platform",
            label,
            "--heat-col",
            "heat",
            "--model",
            chat_model,
            "--concurrency",
            "4",
        ]
        for platform, label in PLATFORMS.items()
    ]
    merged = run_dir / "merged"
    stages: list[tuple[str, list[list[str]]]] = [
        ("00_platforms", platform_00),
        (
            "x0_merge",
            [[
                py,
                script("x0_merge_platforms.py"),
                "--out-dir",
                str(merged),
                "--from",
                *(str(run_dir / platform) for platform in PLATFORMS),
            ]],
        ),
        (
            "01_eventness",
            [[py, script("01_eventness.py"), "--run-dir", str(merged),
              "--model", chat_model, "--batch-size", "60", "--concurrency", "4"]],
        ),
        (
            "02_frames",
            [[py, script("02_extract_frames.py"), "--run-dir", str(merged),
              "--model", chat_model, "--batch-size", "40", "--concurrency", "4"]],
        ),
        (
            "03_entities",
            [[py, script("03_normalize_entities.py"), "--run-dir", str(merged),
              "--model", chat_model, "--batch-size", "60"]],
        ),
        (
            "04_embeddings",
            [[py, script("04_build_embeddings.py"), "--run-dir", str(merged),
              "--model", embedding_model, "--concurrency", "8"]],
        ),
        (
            "05_recall",
            [[py, script("05_recall_candidates.py"), "--run-dir", str(merged),
              "--top-k", "60"]],
        ),
        (
            "06_blocks",
            [[py, script("06_build_blocks.py"), "--run-dir", str(merged),
              "--cap", "60"]],
        ),
        (
            "07_archive",
            [[py, script("07_block_archive.py"), "--run-dir", str(merged),
              "--model", chat_model, "--concurrency", "7"]],
        ),
    ]
    x2_command = [
        py,
        script("x2_confidence_filter.py"),
        "--run-dir",
        str(merged),
        "--verdict",
        x2_verdict,
    ]
    if x2_verdict != "off":
        x2_command.extend(["--model", chat_model])
    stages.extend([
        ("x2_confidence", [x2_command]),
        (
            "x3_review",
            [[py, script("x3_review_bidirectional.py"), "--run-dir", str(merged),
              "--model", chat_model]],
        ),
        (
            "x4_detail",
            [[py, script("x4_detail_table.py"), "--run-dir", str(merged),
              "--source", str(input_csv)]],
        ),
    ])
    return stages


def _atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _load_status(path: Path) -> dict:
    if not path.exists():
        return {"status": "pending", "completed_stages": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "pending", "completed_stages": []}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_signature(
    input_sha256: str,
    chat_model: str,
    embedding_model: str,
    x2_verdict: str,
) -> str:
    payload = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "input_sha256": input_sha256,
        "chat_model": chat_model,
        "embedding_model": embedding_model,
        "x2_verdict": x2_verdict,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _reset_stage_outputs(run_dir: Path, result_path: Path) -> None:
    for name in (*PLATFORMS, "merged", "logs"):
        path = run_dir / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    result_path.unlink(missing_ok=True)


def _prepare_input(source: Path, destination: Path) -> int:
    if not source.exists():
        raise FileNotFoundError(f"C2 input not found: {source}")
    frame = (
        pd.read_excel(source)
        if source.suffix.lower() in {".xlsx", ".xls"}
        else pd.read_csv(source)
    )
    aliases = {
        "record_id": ("record_id", "row_id"),
        "title": ("title", "热点标题"),
        "platform": ("platform", "平台"),
        "heat": ("heat", "heat_score", "标准化热度分", "hot_index"),
    }
    selected: dict[str, pd.Series] = {}
    for target, candidates in aliases.items():
        source_col = next((name for name in candidates if name in frame.columns), None)
        if source_col is None:
            raise ValueError(
                f"C2 input missing {target}; candidates={candidates}, "
                f"actual={list(frame.columns)}"
            )
        selected[target] = frame[source_col]
    output = pd.DataFrame(selected)
    if output["record_id"].astype(str).duplicated().any():
        raise ValueError("C2 input record_id must be unique")
    unknown = sorted(set(output["platform"].dropna()) - set(PLATFORMS.values()))
    if unknown:
        raise ValueError(f"C2 input contains unknown platforms: {unknown}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp.csv")
    output.to_csv(temp, index=False, encoding="utf-8-sig")
    os.replace(temp, destination)
    return len(output)


def _run_command(command: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{datetime.now().astimezone().isoformat()}] {' '.join(command)}\n")
        log.flush()
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)


def _run_stage(stage: str, commands: list[list[str]], log_dir: Path) -> None:
    if len(commands) == 1:
        _run_command(commands[0], log_dir / f"{stage}.log")
        return
    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        futures = {
            pool.submit(
                _run_command,
                command,
                log_dir / f"{stage}_{platform}.log",
            ): platform
            for command, platform in zip(commands, PLATFORMS)
        }
        for future in as_completed(futures):
            future.result()


def _write_result(run_dir: Path, destination: Path, expected_rows: int) -> None:
    source = run_dir / "merged" / "out" / "热点明细_含事件归属.csv"
    if not source.exists():
        raise FileNotFoundError(f"C2 x4 output not found: {source}")
    frame = pd.read_csv(source)
    required = {"record_id", "一级事件名"}
    if not required.issubset(frame.columns):
        raise ValueError(f"C2 x4 output missing columns: {sorted(required - set(frame.columns))}")
    if len(frame) != expected_rows:
        raise ValueError(f"C2 output rows {len(frame)} != input rows {expected_rows}")
    result = frame[["record_id", "一级事件名"]].rename(
        columns={"record_id": "row_id"}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp.xlsx")
    result.to_excel(temp, index=False)
    os.replace(temp, destination)


def _normalize_demo_fast_results(
    records: list[dict], payload: dict
) -> list[dict]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("C2 demo fast output missing results list")
    expected = [str(record["record_id"]) for record in records]
    by_id: dict[str, str] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        row_id = str(item.get("row_id") or "")
        event_name = str(item.get("一级事件名") or "").strip()
        if row_id in by_id:
            raise ValueError(f"C2 demo fast duplicate row_id: {row_id}")
        if row_id in expected and event_name:
            by_id[row_id] = event_name
    missing = [row_id for row_id in expected if row_id not in by_id]
    if missing:
        raise ValueError(f"C2 demo fast missing row_ids: {missing}")
    return [
        {"row_id": row_id, "一级事件名": by_id[row_id]}
        for row_id in expected
    ]


def _run_fast_demo(args: argparse.Namespace) -> Path:
    project = resolve_project_dir(args.project_dir)
    source = Path(args.input) if args.input else (
        project / "04_标注" / "_可用子集"
        / f"usable_subset_{args.mode}_r{args.run_id}.xlsx"
    )
    run_dir = project / "04_标注" / "C2_事件归档" / "c2_run"
    status_path = run_dir / "c2_status.json"
    result_path = (
        project / "04_标注" / "C2_事件归档"
        / f"c2_event_result_r{args.run_id}.xlsx"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    input_csv = run_dir / "input.csv"
    rows = _prepare_input(source, input_csv)
    frame = pd.read_csv(input_csv)
    records = [
        {
            "record_id": str(row["record_id"]),
            "platform": "" if pd.isna(row["platform"]) else str(row["platform"]),
            "title": "" if pd.isna(row["title"]) else str(row["title"]),
        }
        for _, row in frame.iterrows()
    ]
    signature = _run_signature(
        _file_sha256(input_csv),
        args.chat_model,
        "demo-fast-no-embedding",
        "demo-fast-v1",
    )
    status = _load_status(status_path)
    if (
        status.get("status") == "done"
        and status.get("run_signature") == signature
        and result_path.exists()
    ):
        print(f"[c2] demo fast cache hit: {result_path}", flush=True)
        return result_path

    started_at = datetime.now().astimezone().isoformat()
    status = {
        "status": "running",
        "current_stage": "demo_fast_cluster",
        "mode": "demo",
        "strategy": "single_llm_cluster",
        "run_id": args.run_id,
        "input_file": str(source),
        "input_rows": rows,
        "run_signature": signature,
        "chat_model": args.chat_model,
        "started_at": started_at,
        "updated_at": started_at,
    }
    _atomic_json(status_path, status)
    update_run_config(
        project,
        {
            "c2_cluster": {
                "status": "running",
                "stage": "demo_fast_cluster",
                "strategy": "single_llm_cluster",
                "input_file": str(source),
                "row_count": rows,
                "run_id": args.run_id,
            }
        },
    )
    try:
        relay = Relay(args.chat_model, 6000, DEMO_FAST_SYSTEM)
        prompt = json.dumps({"records": records}, ensure_ascii=False)
        last_error: Exception | None = None
        normalized: list[dict] = []
        for attempt in range(3):
            try:
                payload = relay.call_json(prompt, attempts=1)
                normalized = _normalize_demo_fast_results(records, payload)
                break
            except (RuntimeError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if not normalized:
            raise RuntimeError(
                f"C2 demo fast failed after 3 attempts: {last_error}"
            )
        result = pd.DataFrame(normalized)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temp = result_path.with_name(
            f".{result_path.stem}.{os.getpid()}.tmp.xlsx"
        )
        result.to_excel(temp, index=False)
        os.replace(temp, result_path)

        completed_at = datetime.now().astimezone().isoformat()
        status.update(
            {
                "status": "done",
                "current_stage": "done",
                "output_file": str(result_path),
                "input_tokens": relay.usage["input_tokens"],
                "output_tokens": relay.usage["output_tokens"],
                "cost_usd": relay.cost(),
                "completed_at": completed_at,
                "updated_at": completed_at,
            }
        )
        _atomic_json(status_path, status)
        update_run_config(
            project,
            {
                "c2_cluster": {
                    "status": "done",
                    "stage": "done",
                    "strategy": "single_llm_cluster",
                    "run_id": args.run_id,
                    "row_count": rows,
                    "output_file": str(result_path),
                    "chat_model": args.chat_model,
                    "completed_at": completed_at,
                }
            },
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return result_path
    except Exception as error:
        failed_at = datetime.now().astimezone().isoformat()
        status.update(
            {
                "status": "failed",
                "error": str(error),
                "failed_at": failed_at,
                "updated_at": failed_at,
            }
        )
        _atomic_json(status_path, status)
        update_run_config(
            project,
            {
                "c2_cluster": {
                    "status": "failed",
                    "stage": "demo_fast_cluster",
                    "strategy": "single_llm_cluster",
                    "error": str(error),
                    "failed_at": failed_at,
                }
            },
        )
        raise


def _run_unlocked(args: argparse.Namespace) -> Path:
    project = resolve_project_dir(args.project_dir)
    source = Path(args.input) if args.input else (
        project / "04_标注" / "_可用子集"
        / f"usable_subset_{args.mode}_r{args.run_id}.xlsx"
    )
    run_dir = project / "04_标注" / "C2_事件归档" / "c2_run"
    input_csv = run_dir / "input.csv"
    status_path = run_dir / "c2_status.json"
    result_path = (
        project / "04_标注" / "C2_事件归档"
        / f"c2_event_result_r{args.run_id}.xlsx"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = _prepare_input(source, input_csv)
    input_sha256 = _file_sha256(input_csv)
    run_signature = _run_signature(
        input_sha256,
        args.chat_model,
        args.embedding_model,
        args.x2_verdict,
    )
    status = _load_status(status_path)
    if status.get("run_signature") != run_signature:
        _reset_stage_outputs(run_dir, result_path)
        status = {"status": "pending", "completed_stages": []}
    completed = set(status.get("completed_stages") or [])
    status.update({
        "status": "running",
        "mode": args.mode,
        "run_id": args.run_id,
        "input_file": str(source),
        "input_rows": rows,
        "input_sha256": input_sha256,
        "run_signature": run_signature,
        "chat_model": args.chat_model,
        "embedding_model": args.embedding_model,
        "updated_at": datetime.now().astimezone().isoformat(),
    })
    _atomic_json(status_path, status)
    update_run_config(
        project,
        {"c2_cluster": {
            "status": "running",
            "stage": status.get("current_stage", "prepare"),
            "input_file": str(source),
            "row_count": rows,
            "run_id": args.run_id,
        }},
    )

    try:
        for stage, commands in build_stage_plan(
            run_dir,
            input_csv,
            args.chat_model,
            args.embedding_model,
            x2_verdict=args.x2_verdict,
        ):
            if stage in completed:
                print(f"[c2] skip completed stage: {stage}", flush=True)
                continue
            print(f"[c2] start: {stage}", flush=True)
            status["current_stage"] = stage
            status["updated_at"] = datetime.now().astimezone().isoformat()
            _atomic_json(status_path, status)
            update_run_config(
                project,
                {"c2_cluster": {"status": "running", "stage": stage}},
            )
            _run_stage(stage, commands, run_dir / "logs")
            completed.add(stage)
            status["completed_stages"] = sorted(completed)
            _atomic_json(status_path, status)

        _write_result(run_dir, result_path, rows)
        status.update({
            "status": "done",
            "current_stage": "done",
            "output_file": str(result_path),
            "completed_at": datetime.now().astimezone().isoformat(),
        })
        _atomic_json(status_path, status)
        update_run_config(
            project,
            {"c2_cluster": {
                "status": "done",
                "stage": "done",
                "run_id": args.run_id,
                "row_count": rows,
                "output_file": str(result_path),
                "chat_model": args.chat_model,
                "embedding_model": args.embedding_model,
                "completed_at": status["completed_at"],
            }},
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return result_path
    except Exception as error:
        status.update({
            "status": "failed",
            "error": str(error),
            "failed_at": datetime.now().astimezone().isoformat(),
        })
        _atomic_json(status_path, status)
        update_run_config(
            project,
            {"c2_cluster": {
                "status": "failed",
                "stage": status.get("current_stage"),
                "error": str(error),
                "failed_at": status["failed_at"],
            }},
        )
        raise


def run(args: argparse.Namespace) -> Path:
    if getattr(args, "demo_fast", False) and args.mode != "demo":
        raise ValueError("--demo-fast is only valid with --mode demo")
    project = resolve_project_dir(args.project_dir)
    lock_path = project / "04_标注" / "C2_事件归档" / ".c2_runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another C2 runner is already active") from error
        try:
            if args.mode == "demo" and getattr(args, "demo_fast", False):
                return _run_fast_demo(args)
            return _run_unlocked(args)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--mode", choices=["test", "demo", "full"], required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--input")
    parser.add_argument(
        "--chat-model",
        default=os.environ.get("C2_CHAT_MODEL_ID") or "doubao-seed-evolving",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("EMBEDDING_MODEL_ID")
        or "doubao-embedding-vision-251215",
    )
    parser.add_argument(
        "--x2-verdict",
        choices=["off", "opus", "sonnet"],
        default="sonnet",
    )
    parser.add_argument(
        "--demo-fast",
        action="store_true",
        help="demo only: replace the production multi-stage pipeline with one batch clustering call",
    )
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except Exception as error:
        print(f"[c2] failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
