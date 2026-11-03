"""飞书附件直读：校验完整性、缓存幂等及 G2 引用契约。"""
import hashlib
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import excel_source
import hc_bridge


def source(data=b"original excel bytes"):
    return {
        "name": "活动.xlsx", "mount_path": "activity.xlsx",
        "transport": "feishu_resource", "message_id": "om_test_message",
        "file_key": "file_v3_original", "sha256": hashlib.sha256(data).hexdigest(),
    }


def test_g2_keeps_download_identity():
    expected = source()
    assert hc_bridge._parse_source_file(json.dumps({"source_file": expected})) == expected


def test_download_and_resume_reuses_verified_cache(tmp_path, monkeypatch):
    data = b"original excel bytes"
    monkeypatch.delenv("TOPIC5_ACTIVITY_XLSX_PATH", raising=False)
    monkeypatch.setenv("TOPIC5_SESSION_UPLOADS_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_USER_ACCESS_TOKEN", "test-only-token")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(data)) as download:
        first = excel_source.resolve_activity_xlsx({"source_file": source(data)})
        second = excel_source.resolve_activity_xlsx({"source_file": source(data)})
    assert first == second == tmp_path / "activity.xlsx"
    assert first.read_bytes() == data
    download.assert_called_once()
    request = download.call_args.args[0]
    assert "/messages/om_test_message/resources/file_v3_original?type=file" in request.full_url
    assert download.call_args.kwargs["timeout"] == 60


def test_corrupt_cache_is_redownloaded(tmp_path, monkeypatch):
    data = b"original excel bytes"
    monkeypatch.delenv("TOPIC5_ACTIVITY_XLSX_PATH", raising=False)
    monkeypatch.setenv("TOPIC5_SESSION_UPLOADS_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_USER_ACCESS_TOKEN", "test-only-token")
    (tmp_path / "activity.xlsx").write_bytes(b"corrupted cache")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(data)) as download:
        result = excel_source.resolve_activity_xlsx({"source_file": source(data)})
    assert result.read_bytes() == data
    download.assert_called_once()


@pytest.mark.parametrize("body", [b"wrong attachment", b""])
def test_hash_mismatch_never_publishes_cache(tmp_path, monkeypatch, body):
    monkeypatch.delenv("TOPIC5_ACTIVITY_XLSX_PATH", raising=False)
    monkeypatch.setenv("TOPIC5_SESSION_UPLOADS_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_USER_ACCESS_TOKEN", "test-only-token")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
        with pytest.raises(ValueError, match="SHA256"):
            excel_source.resolve_activity_xlsx({"source_file": source()})
    assert list(tmp_path.iterdir()) == []


def test_oversize_stops_download_and_removes_temporary_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TOPIC5_ACTIVITY_XLSX_PATH", raising=False)
    monkeypatch.setenv("TOPIC5_SESSION_UPLOADS_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_USER_ACCESS_TOKEN", "test-only-token")
    monkeypatch.setattr(excel_source, "_MAX_FILE_BYTES", 4)
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"12345")):
        with pytest.raises(ValueError, match="50 MiB"):
            excel_source.resolve_activity_xlsx({"source_file": source(b"12345")})
    assert list(tmp_path.iterdir()) == []


def test_http_failure_is_not_reported_as_missing_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TOPIC5_ACTIVITY_XLSX_PATH", raising=False)
    monkeypatch.setenv("TOPIC5_SESSION_UPLOADS_DIR", str(tmp_path))
    monkeypatch.setenv("FEISHU_USER_ACCESS_TOKEN", "test-only-token")
    error = urllib.error.HTTPError("https://open.feishu.cn", 400, "error", {}, None)
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(ValueError, match="excel_source:.*HTTP 400"):
            excel_source.resolve_activity_xlsx({"source_file": source()})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", [
    ("sha256", "invalid"), ("file_key", "../another"), ("message_id", "missing"),
    ("transport", "unknown"), ("mount_path", "../activity.xlsx"),
])
def test_invalid_sources_rejected_before_network(field, value):
    reference = {**source(), field: value}
    with patch("urllib.request.urlopen") as download:
        with pytest.raises(ValueError, match="excel_source:"):
            excel_source.resolve_activity_xlsx({"source_file": reference})
    download.assert_not_called()


def test_legacy_mount_still_resolves_without_download(monkeypatch):
    monkeypatch.delenv("TOPIC5_ACTIVITY_XLSX_PATH", raising=False)
    monkeypatch.delenv("TOPIC5_SESSION_UPLOADS_DIR", raising=False)
    with patch("urllib.request.urlopen") as download:
        result = excel_source.resolve_activity_xlsx({
            "source_file": {"name": "旧任务.xlsx", "mount_path": "legacy.xlsx"}})
    assert result == Path("/mnt/session/uploads/legacy.xlsx")
    download.assert_not_called()
