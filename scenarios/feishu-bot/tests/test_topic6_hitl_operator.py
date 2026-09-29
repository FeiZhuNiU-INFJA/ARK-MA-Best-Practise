"""topic6 HITL 卡片"操作人"回显真名的纯函数覆盖。

飞书卡片按钮事件只带 open_id(见 lark_oapi P2CardActionTrigger.operator),SDK 也没做
姓名解析,所以卡片终态想显示"张三 已通过"必须调 feishu.resolve_operator_name 反查。
这里覆盖:
- _extract_operator_open_id:兼容 lark_channel CardActionEvent(带 .operator.open_id
  + 顶层 .chat_id)与纯 dict 两种形态;
- _resolve_operator_label:反查成功走真名,失败退 open_id 后缀,完全没 open_id 落
  "未知操作人";feishu 侧抛异常时兜底不崩。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"


def _load(name: str, file: str):
    if str(CASE_DIR) not in sys.path:
        sys.path.insert(0, str(CASE_DIR))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CASE_DIR / file)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hitl_mod = _load("topic6_hitl", "topic6_hitl.py")
pipeline_store_mod = _load("pipeline_store", "pipeline_store.py")
_extract_operator_open_id = hitl_mod._extract_operator_open_id
_resolve_operator_label = hitl_mod._resolve_operator_label
_extract_action_note = hitl_mod._extract_action_note
_extract_action_value = hitl_mod._extract_action_value


class _FakeFeishu:
    """替身:模仿 FeishuSender.resolve_operator_name(open_id, chat_id=...) 契约。"""

    def __init__(self, mapping: dict[str, str], *, raises: bool = False):
        self._mapping = mapping
        self._raises = raises
        self.calls: list[tuple[str, str | None]] = []

    def resolve_operator_name(self, open_id: str, chat_id: str | None = None) -> str:
        self.calls.append((open_id, chat_id))
        if self._raises:
            raise RuntimeError("boom")
        return self._mapping.get(open_id, "")


# ---- _extract_operator_open_id ---------------------------------------------


def test_extract_open_id_from_lark_channel_event():
    # lark_channel 归一后的 CardActionEvent 结构:顶层 chat_id,operator 子对象带 open_id。
    action = SimpleNamespace(
        chat_id="oc-1",
        operator=SimpleNamespace(open_id="ou_manager"),
    )
    assert _extract_operator_open_id(action) == ("ou_manager", "oc-1")


def test_extract_open_id_from_dict_payload():
    # 纯 dict 形态(WS 事件 raw / 单测桩):同样能挖出。
    action = {"chat_id": "oc-2", "operator": {"open_id": "ou_wang"}}
    assert _extract_operator_open_id(action) == ("ou_wang", "oc-2")


def test_extract_open_id_missing_returns_empty():
    # 没有 operator / open_id:返 ("", chat_id) 由上层判空落"未知操作人"。
    assert _extract_operator_open_id(SimpleNamespace(chat_id="oc-3")) == ("", "oc-3")
    assert _extract_operator_open_id({}) == ("", "")


# ---- _resolve_operator_label -----------------------------------------------


def test_resolve_label_prefers_real_name():
    # feishu.resolve_operator_name 反查到真名 → 卡片直接用真名。
    feishu = _FakeFeishu({"ou_manager": "张经理"})
    action = SimpleNamespace(
        chat_id="oc-1", operator=SimpleNamespace(open_id="ou_manager")
    )
    assert _resolve_operator_label(action, feishu) == "张经理"
    # chat_id 传下去让 resolve_operator_name 优先走名册。
    assert feishu.calls == [("ou_manager", "oc-1")]


def test_resolve_label_falls_back_to_open_id_suffix_when_name_missing():
    # 反查拿不到真名(contact scope 没开 / API 失败):退回 open_id 后 6 位短标识。
    feishu = _FakeFeishu({})
    action = SimpleNamespace(
        chat_id="oc-1", operator=SimpleNamespace(open_id="ou_abc123def456")
    )
    assert _resolve_operator_label(action, feishu) == "操作人 ...def456"


def test_resolve_label_handles_feishu_exception():
    # feishu 侧抛异常(网络/权限)也不该冒泡,兜底成 open_id 后缀。
    feishu = _FakeFeishu({"ou_abc123def456": "王小样"}, raises=True)
    action = SimpleNamespace(
        chat_id="oc-1", operator=SimpleNamespace(open_id="ou_abc123def456")
    )
    assert _resolve_operator_label(action, feishu) == "操作人 ...def456"


def test_resolve_label_returns_unknown_when_no_open_id():
    # 事件里没 open_id(极端异常):落"未知操作人",不调 feishu。
    feishu = _FakeFeishu({"ou_x": "王小样"})
    action = SimpleNamespace(chat_id="oc-1")
    assert _resolve_operator_label(action, feishu) == "未知操作人"
    assert feishu.calls == []


def test_resolve_label_without_chat_id_still_queries_feishu():
    # 单聊场景没 chat_id:仍走 feishu(内部会跳过名册直接查 contacts)。
    feishu = _FakeFeishu({"ou_solo": "赵四"})
    action = SimpleNamespace(chat_id="", operator=SimpleNamespace(open_id="ou_solo"))
    assert _resolve_operator_label(action, feishu) == "赵四"
    # chat_id="" 传给 resolve_operator_name 时应改为 None,让底层跳过名册路径。
    assert feishu.calls == [("ou_solo", None)]


def test_resolve_label_without_feishu_client_uses_open_id_suffix():
    # feishu 为空(测试/降级路径)也能给出可用标签,不炸。
    action = SimpleNamespace(chat_id="oc-1", operator=SimpleNamespace(open_id="ou_xy1234"))
    assert _resolve_operator_label(action, feishu=None) == "操作人 ...xy1234"


def test_extracts_remark_form_submission_from_card_callback():
    action = {
        "action": {
            "name": "remark_submit",
            "form_value": '{"remark_note":"请重新执行 Phase F"}',
        }
    }

    assert _extract_action_value(action) == {"decision": "remark"}
    assert _extract_action_note(action) == "请重新执行 Phase F"


def test_hc_reuses_progress_card_instead_of_sending_new_message(tmp_path):
    store = pipeline_store_mod.PipelineStore(str(tmp_path / "topic6.db"))
    job = store.create_job(
        chat_id="chat-1",
        thread_id="",
        user_open_id="user-1",
        ma_session_id="session-1",
        mode="demo",
        project_dir="/workspace/demo",
    )
    store.set_progress_card_message_id(job.job_id, "message-progress")
    job = store.get_job(job.job_id)

    class _Feishu:
        def send_interactive_card(self, *_args, **_kwargs):
            raise AssertionError("HC must not create a second card")

    class _Runner:
        _feishu = _Feishu()

        def __init__(self):
            self.patches = []

        async def patch_job_card(self, job_id, card):
            self.patches.append((job_id, card))
            return "message-progress"

    runner = _Runner()
    hitl = hitl_mod.Topic6Hitl(hitl_mod.Topic6HitlDeps(store=store, runner=runner))
    message_id = asyncio.run(
        hitl.send_hc_card(job, "HC1", 7, {"hc": "HC1", "mode": "demo"})
    )

    assert message_id == "message-progress"
    assert len(runner.patches) == 1
    assert runner.patches[0][0] == job.job_id
    assert runner.patches[0][1]["header"]["title"]["content"].startswith("HC1")
    store.close()


def test_followup_message_resolves_pending_remark_and_resumes_job(tmp_path):
    store = pipeline_store_mod.PipelineStore(str(tmp_path / "topic6.db"))
    job = store.create_job(
        chat_id="chat-1",
        thread_id="",
        user_open_id="user-1",
        ma_session_id="session-1",
        mode="demo",
        project_dir="/workspace/demo",
    )
    event_id = store.append_hc_event(job.job_id, "HC3", {"hc": "HC3"})
    store.mark_wait_hc(job.job_id, "HC3")
    store.append_remark(event_id, hitl_mod.PENDING_REMARK_NOTE)

    class _Runner:
        _feishu = None

        def __init__(self):
            self.resumed = []

        async def resume_job(self, current_job, message):
            self.resumed.append((current_job, message))

    runner = _Runner()
    hitl = hitl_mod.Topic6Hitl(hitl_mod.Topic6HitlDeps(store=store, runner=runner))
    handled = asyncio.run(
        hitl.handle_remark_message(
            chat_id="chat-1",
            thread_id="",
            user_open_id="user-1",
            text="权限已发布，请重新执行 Phase F 飞书发布",
        )
    )

    assert handled is True
    assert runner.resumed[0][1] == (
        "HC3 remark: 权限已发布，请重新执行 Phase F 飞书发布"
    )
    resolved = store.get_hc_event(event_id)
    assert resolved.user_decision == "remark"
    assert resolved.user_note == "权限已发布，请重新执行 Phase F 飞书发布"
    assert resolved.resolved_at is not None
    store.close()
