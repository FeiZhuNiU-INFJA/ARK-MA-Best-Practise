from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"


def _load(module_name: str, file_name: str):
    if str(CASE_DIR) not in sys.path:
        sys.path.insert(0, str(CASE_DIR))
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, CASE_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


artifact_sync = _load("artifact_sync", "artifact_sync.py")
Topic6ArtifactSync = artifact_sync.Topic6ArtifactSync


class _FakeArk:
    def __init__(self, files: list[dict], content: dict[str, bytes]):
        self.files = files
        self.content = content
        self.downloads: list[str] = []

    async def list_agent_files(self, session_id: str):
        assert session_id == "session-1"
        return self.files

    async def download_file(self, url: str):
        self.downloads.append(url)
        return self.content[url]


async def test_sync_downloads_once_and_materializes_manifest_paths(tmp_path: Path):
    artifact = b"xlsx-content"
    digest = hashlib.sha256(artifact).hexdigest()
    manifest = {
        "schema_version": 1,
        "project": "W40",
        "updated_at": "2026-10-10T00:00:00Z",
        "publications": [
            {
                "node": "merge",
                "run_id": "1",
                "status": "complete",
                "published_at": "2026-10-10T00:00:00Z",
                "files": [
                    {
                        "source": "05_合并/wide_table_demo_r1.xlsx",
                        "output": "W40/05_合并/wide_table_demo_r1.xlsx",
                        "size": len(artifact),
                        "sha256": digest,
                    }
                ],
            }
        ],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode()
    files = [
        {
            "id": "file-artifact",
            "filename": "wide_table_demo_r1.xlsx",
            "size": len(artifact),
            "status": "active",
            "download_url": "https://files/artifact",
        },
        {
            "id": "file-manifest",
            "filename": "manifest.json",
            "size": len(manifest_bytes),
            "status": "active",
            "download_url": "https://files/manifest",
        },
    ]
    ark = _FakeArk(
        files,
        {
            "https://files/artifact": artifact,
            "https://files/manifest": manifest_bytes,
        },
    )
    sync = Topic6ArtifactSync(
        ark,
        tmp_path,
        lambda _job_id: SimpleNamespace(status="running"),
    )

    assert await sync.sync_once("job-1", "session-1") == 2
    assert await sync.sync_once("job-1", "session-1") == 0

    job_root = tmp_path / "job-1"
    assert (
        job_root / "W40/05_合并/wide_table_demo_r1.xlsx"
    ).read_bytes() == artifact
    assert json.loads(
        (job_root / "W40/manifest.json").read_text(encoding="utf-8")
    )["project"] == "W40"
    index = json.loads((job_root / "index.json").read_text(encoding="utf-8"))
    assert set(index["files"]) == {"file-artifact", "file-manifest"}
    assert len(ark.downloads) == 2


async def test_sync_ignores_files_without_download_url(tmp_path: Path):
    ark = _FakeArk(
        [{"id": "processing", "filename": "pending.xlsx", "status": "processing"}],
        {},
    )
    sync = Topic6ArtifactSync(ark, tmp_path, lambda _job_id: None)

    assert await sync.sync_once("job-1", "session-1") == 0
    index = json.loads(
        (tmp_path / "job-1/index.json").read_text(encoding="utf-8")
    )
    assert index["files"] == {}
