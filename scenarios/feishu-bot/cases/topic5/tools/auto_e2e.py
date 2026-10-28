#!/usr/bin/env python3
"""Drive one Topic5 job through ordinary HITL gates for E2E verification."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent.parent
if str(CASE_DIR) not in sys.path:
    sys.path.insert(0, str(CASE_DIR))

from arkagent.ark import ArkClient, event_text  # noqa: E402
from arkagent.feishu import FeishuSender  # noqa: E402
from pipeline_store import PipelineStore, STATUS_DONE, STATUS_WAIT_HC  # noqa: E402
from topic5_hitl import (  # noqa: E402
    Topic5Hitl,
    Topic5HitlDeps,
    _decision_block_reason,
)
from topic5_intake import DEMO_REQUIREMENT_DEFAULTS  # noqa: E402
from topic5_runner import (  # noqa: E402
    Topic5Config,
    Topic5Runner,
    _event_contract_text,
    _latest_turn_events,
)

AUTO_PASS_GATES = {"G1", "G2", "N10", "G3", "G4"}
STOP_GATES = {"HC7", "USER_ESCALATE"}


def _build_runner(store: PipelineStore) -> tuple[ArkClient, Topic5Runner, Topic5Hitl]:
    ark = ArkClient(
        os.environ["ARK_API_KEY"],
        os.environ.get(
            "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
        ),
    )
    sender = FeishuSender(
        os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"]
    )
    runner = Topic5Runner(
        ark,
        sender,
        store,
        Topic5Config(
            coordinator_agent_id=os.environ["TOPIC5_COORDINATOR_AGENT_ID"],
            environment_id=os.environ["TOPIC5_ENVIRONMENT_ID"],
            memory_store_id=os.environ.get("TOPIC5_MEMORY_STORE_ID", ""),
            vault_ids=(
                (os.environ["ARK_VAULT_ID"],)
                if os.environ.get("ARK_VAULT_ID")
                else ()
            ),
        ),
        loop=asyncio.get_running_loop(),
    )
    hitl = Topic5Hitl(Topic5HitlDeps(store=store, runner=runner))
    runner.bind_card_sender(hitl)
    return ark, runner, hitl


async def _recover_hc(
    store: PipelineStore, runner: Topic5Runner, job_id: str
) -> bool:
    job = store.get_job(job_id)
    if job is None:
        raise RuntimeError(f"job not found: {job_id}")
    events = await runner._ark.list_session_events(job.ma_session_id)  # noqa: SLF001
    events = _latest_turn_events(events)
    agent_messages = [
        event_text(event)
        for event in events
        if event.get("type") == "agent.message" and event_text(event)
    ]
    contract_messages = [
        body
        for event in events
        for body in [_event_contract_text(event)]
        if body
    ]
    messages = agent_messages + contract_messages
    if not messages:
        return False
    await runner._handle_idle(job_id, messages, recovering=True)  # noqa: SLF001
    refreshed = store.get_job(job_id)
    return bool(refreshed and refreshed.status == STATUS_WAIT_HC)


async def _approve(
    store: PipelineStore,
    runner: Topic5Runner,
    hitl: Topic5Hitl,
    job_id: str,
    user_open_id: str,
) -> None:
    job = store.get_job(job_id)
    if job is None:
        raise RuntimeError(f"job not found: {job_id}")
    event = store.latest_hc_for_job(job_id, job.current_phase)
    if event is None or event.resolved_at is not None:
        raise RuntimeError(f"pending HC not found: {job.current_phase}")
    inner: dict = {
        "value": {
            "event_id": event.id,
            "job_id": job_id,
            "hc": job.current_phase,
            "decision": "pass",
        }
    }
    if job.current_phase == "G1":
        inner["form_value"] = dict(DEMO_REQUIREMENT_DEFAULTS)
    action = {
        "open_message_id": event.card_message_id,
        "operator": {"open_id": user_open_id},
        "action": inner,
    }
    await hitl.handle_card_action(action)
    stream = runner._active_streams.get(job.ma_session_id)  # noqa: SLF001
    if stream is not None:
        await stream


async def run(job_id: str, user_open_id: str) -> int:
    store = PipelineStore(os.environ.get("TOPIC5_PIPELINE_DB_PATH") or None)
    ark, runner, hitl = _build_runner(store)
    try:
        for _ in range(12):
            job = store.get_job(job_id)
            if job is None:
                raise RuntimeError(f"job not found: {job_id}")
            print(
                f"STATE job={job.job_id} status={job.status} "
                f"phase={job.current_phase}",
                flush=True,
            )
            if job.status == STATUS_DONE:
                print(f"DONE url={job.online_url}", flush=True)
                return 0
            if job.status != STATUS_WAIT_HC:
                if await _recover_hc(store, runner, job_id):
                    continue
                print(
                    f"STOP status={job.status} phase={job.current_phase} "
                    f"error={job.last_error}",
                    flush=True,
                )
                return 2
            if job.current_phase in STOP_GATES:
                print(f"STOP manual_gate={job.current_phase}", flush=True)
                return 3
            if job.current_phase not in AUTO_PASS_GATES:
                print(f"STOP unknown_gate={job.current_phase}", flush=True)
                return 4
            pending = store.latest_hc_for_job(job_id, job.current_phase)
            pending_payload = (
                pending.payload
                if pending is not None and isinstance(pending.payload, dict)
                else json.loads(pending.payload)
                if pending is not None and pending.payload
                else {}
            )
            block_reason = _decision_block_reason(
                job.current_phase, pending_payload, "pass"
            )
            if pending_payload.get("__fallback__") or block_reason:
                print(f"RECOVER gate={job.current_phase}", flush=True)
                await _recover_hc(store, runner, job_id)
                restored = store.latest_hc_for_job(job_id, job.current_phase)
                restored_payload = restored.payload if restored is not None else {}
                restored_reason = _decision_block_reason(
                    job.current_phase, restored_payload, "pass"
                )
                if restored is not None and not restored_reason:
                    continue
                raise RuntimeError(
                    f"failed to recover authoritative HC payload: "
                    f"{job.current_phase}: {restored_reason or block_reason}"
                )
            print(f"PASS gate={job.current_phase}", flush=True)
            await _approve(store, runner, hitl, job_id, user_open_id)
        print("STOP gate_limit_exceeded", flush=True)
        return 5
    finally:
        await ark.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--user-open-id", required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.job, args.user_open_id))


if __name__ == "__main__":
    raise SystemExit(main())
