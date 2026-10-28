from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from cases.topicx.control_store import ControlStore


def _task(store: ControlStore, event_id: str = "evt-1"):
    return store.create_or_get_task(
        feishu_event_id=event_id,
        chat_id="chat-1",
        root_message_id=f"root-{event_id}",
        user_open_id="ou-user",
        request_text="热点报告 full",
    )


def test_task_event_is_idempotent_and_db_is_private(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    first, created = _task(store)
    again, created_again = _task(store)
    assert created is True
    assert created_again is False
    assert again.task_id == first.task_id
    assert (tmp_path / "control.db").stat().st_mode & 0o777 == 0o600
    assert [event["event_type"] for event in store.list_events(first.task_id)] == [
        "task.created"
    ]


def test_route_commits_once_and_rejects_conflict(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    task, _ = _task(store)
    first = store.commit_route_once(task.task_id, "topic6", "full", "热点周报")
    duplicate = store.commit_route_once(
        task.task_id, "topic6", "full", "重复调用"
    )
    conflict = store.commit_route_once(
        task.task_id, "topic5", "full", "错误改路由"
    )
    assert first["accepted"] is True
    assert duplicate["accepted"] is True
    assert conflict["accepted"] is False
    assert conflict["error"] == "route_already_committed"


def test_sessions_and_job_bind_only_once(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    task, _ = _task(store)
    assert store.bind_router_session(task.task_id, "router-1")
    assert store.bind_router_session(task.task_id, "router-1")
    assert not store.bind_router_session(task.task_id, "router-2")
    assert store.bind_business_session(task.task_id, "business-1")
    assert store.bind_business_job(task.task_id, "job-1")
    assert store.get_task_by_router_session("router-1").task_id == task.task_id
    assert store.get_task_by_business_session("business-1").task_id == task.task_id


def test_topic6_acquire_is_atomic_across_connections(tmp_path):
    path = str(tmp_path / "control.db")
    seed = ControlStore(path)
    task1, _ = _task(seed, "evt-1")
    task2, _ = _task(seed, "evt-2")

    def acquire(task_id: str):
        store = ControlStore(path)
        try:
            return store.acquire_topic6(task_id)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(acquire, (task1.task_id, task2.task_id)))
    assert sum(claim.acquired for claim in claims) == 1
    owner = next(claim for claim in claims if claim.acquired)
    loser = next(claim for claim in claims if not claim.acquired)
    assert loser.owner_task_id == owner.owner_task_id


def test_topic6_token_controls_activate_and_release(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    task, _ = _task(store)
    claim = store.acquire_topic6(task.task_id)
    assert claim.acquired
    assert not store.activate_topic6(
        task.task_id, "wrong", "business-1", "job-1"
    )
    assert store.activate_topic6(
        task.task_id, claim.lease_token, "business-1", "job-1"
    )
    assert store.get_topic6_lease()["state"] == "active"
    assert not store.release_topic6_if_owner(
        task.task_id, "wrong", reason="test"
    )
    assert store.release_topic6_if_owner(
        task.task_id, claim.lease_token, reason="done"
    )
    assert store.get_topic6_lease() is None


def test_begin_stop_keeps_topic6_lease_until_confirmed(tmp_path):
    store = ControlStore(str(tmp_path / "control.db"))
    task, _ = _task(store)
    store.commit_route_once(task.task_id, "topic6", "full", "")
    assert store.claim_dispatch(task.task_id)
    assert store.transition(task.task_id, ("starting",), "running")
    claim = store.acquire_topic6(task.task_id)
    assert store.begin_stop(task.task_id, "ou-user")
    assert store.get_task(task.task_id).status == "stopping"
    assert store.get_topic6_lease()["state"] == "stopping"
    assert store.get_topic6_lease()["lease_token"] == claim.lease_token
