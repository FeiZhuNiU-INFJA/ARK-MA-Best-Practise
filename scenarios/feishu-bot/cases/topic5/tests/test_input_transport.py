"""Gateway 默认输入直传及旧 Files 模式兼容。"""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

CASE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(CASE), str(CASE.parents[1])]
from arkagent.ark import ArkClient
from arkagent.feishu import FeishuSender, ResourceRef
from pipeline_store import PipelineStore
from topic5_runner import Topic5Runner, Topic5RunnerError


def workbook_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return output.getvalue()


@pytest.fixture
def gateway(tmp_path):
    store = PipelineStore(str(tmp_path / "pipeline.db"))
    ark = Mock(spec=ArkClient)
    ark.get_session.return_value = {"agent": {"version": 6}}
    feishu = Mock(spec=FeishuSender)
    feishu.download_resource.return_value = workbook_bytes()
    runner = Topic5Runner(ark, feishu, store, None)
    job = store.create_job("chat", "thread", "user", "session", "full", "")
    store.mark_wait_input(job.job_id)
    yield runner, store.get_job(job.job_id), ark, feishu
    store.close()


@pytest.mark.asyncio
async def test_default_excel_preserves_original_attachment_without_files(gateway, monkeypatch):
    monkeypatch.delenv("TOPIC5_INPUT_TRANSPORT", raising=False)
    runner, job, ark, feishu = gateway
    resource = ResourceRef(type="file", file_key="file_original", file_name="活动.xlsx",
                           message_id="om_original")
    updated = await runner.attach_activity_file(
        chat_id="chat", thread_id="thread", user_open_id="user", resource=resource)
    source = runner.activity_source_payload(updated)
    assert source["transport"] == "feishu_resource"
    assert source["message_id"] == resource.message_id
    assert source["file_key"] == resource.file_key
    assert source["sha256"] == hashlib.sha256(workbook_bytes()).hexdigest()
    assert source["name"] == "活动.xlsx"
    assert source["mount_path"].endswith(".xlsx")
    feishu.download_resource.assert_called_once_with("om_original", "file_original", "file")
    ark.upload_file.assert_not_called()
    ark.add_session_file.assert_not_called()


@pytest.mark.asyncio
async def test_g1_json_roundtrip_keeps_quotes_and_unicode_without_files(gateway, monkeypatch):
    monkeypatch.delenv("TOPIC5_INPUT_TRANSPORT", raising=False)
    runner, job, ark, _ = gateway
    requirement = {"brand": "品牌“测试”", "filter": "甲|乙", "questions": ["原文", "问题"]}
    result = await runner.attach_requirement_payload(job, requirement, 1)
    assert json.loads(result) == requirement
    ark.upload_file.assert_not_called()
    ark.add_session_file.assert_not_called()


@pytest.mark.asyncio
async def test_opt_in_files_mode_keeps_existing_mount_flow(gateway, monkeypatch):
    monkeypatch.setenv("TOPIC5_INPUT_TRANSPORT", "ark_files")
    runner, job, ark, _ = gateway
    ark.upload_file.return_value = "file_ark"
    updated = await runner.attach_activity_file(
        chat_id="chat", thread_id="thread", user_open_id="user",
        resource=ResourceRef(type="file", file_key="file_original",
                             file_name="活动.xlsx", message_id="om_original"))
    assert updated.source_file_id == "file_ark"
    assert runner.activity_source_payload(updated) == {
        "name": "活动.xlsx", "mount_path": updated.source_file_mount_path}
    ark.upload_file.assert_awaited_once()
    ark.add_session_file.assert_awaited_once_with(
        "session", "file_ark", updated.source_file_mount_path)

@pytest.mark.asyncio
async def test_old_session_keeps_legacy_input_contract(gateway, monkeypatch):
    monkeypatch.delenv("TOPIC5_INPUT_TRANSPORT", raising=False)
    runner, job, ark, _ = gateway
    ark.get_session.return_value = {"agent": {"version": 5}}
    ark.upload_file.return_value = "file_legacy"
    result = await runner.attach_requirement_payload(job, {"brand": "旧任务"}, 1)
    assert result.startswith("@/mnt/session/uploads/")
    ark.upload_file.assert_awaited_once()
    ark.add_session_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_workbook_does_not_save_source_reference(gateway, monkeypatch):
    monkeypatch.delenv("TOPIC5_INPUT_TRANSPORT", raising=False)
    runner, job, ark, feishu = gateway
    feishu.download_resource.return_value = b"not an xlsx"
    with pytest.raises(Topic5RunnerError, match="xlsx"):
        await runner.attach_activity_file(
            chat_id="chat", thread_id="thread", user_open_id="user",
            resource=ResourceRef(type="file", file_key="file_original",
                                 file_name="活动.xlsx", message_id="om_original"))
    assert runner.get_job(job.job_id).source_file_id == ""
    ark.upload_file.assert_not_called()
