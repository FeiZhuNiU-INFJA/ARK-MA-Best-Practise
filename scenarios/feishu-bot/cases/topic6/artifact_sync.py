"""Incrementally synchronize Topic6 Session output files to local disk."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from pipeline_store import STATUS_RUNNING, STATUS_WAIT_HC, PipelineJob

log = logging.getLogger("arkagent.topic6.artifacts")
ACTIVE_STATUSES = {STATUS_RUNNING, STATUS_WAIT_HC}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_component(value: str, fallback: str) -> str:
    text = Path(value).name.strip()
    if text in {"", ".", ".."}:
        return fallback
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_int(value: object) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


class Topic6ArtifactSync:
    def __init__(
        self,
        ark,
        sync_root: Path,
        get_job: Callable[[str], Optional[PipelineJob]],
        poll_interval_sec: float = 60.0,
    ) -> None:
        self._ark = ark
        self._sync_root = sync_root
        self._get_job = get_job
        self._poll_interval_sec = poll_interval_sec
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, job: PipelineJob, loop: asyncio.AbstractEventLoop) -> None:
        self.stop(job.job_id)
        task = loop.create_task(self._run(job.job_id, job.ma_session_id))
        self._tasks[job.job_id] = task
        task.add_done_callback(
            lambda completed, job_id=job.job_id: self._done(job_id, completed)
        )

    def stop(self, job_id: str) -> None:
        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _done(self, job_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("artifact sync stopped unexpectedly job=%s: %s", job_id, error)

    async def _run(self, job_id: str, session_id: str) -> None:
        while True:
            try:
                await self.sync_once(job_id, session_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - polling retries transient failures
                log.warning(
                    "artifact sync failed job=%s session=%s: %s",
                    job_id,
                    session_id,
                    error,
                )
            job = self._get_job(job_id)
            if job is None or job.status not in ACTIVE_STATUSES:
                return
            await asyncio.sleep(self._poll_interval_sec)

    async def sync_once(self, job_id: str, session_id: str) -> int:
        job_root = self._sync_root / _safe_component(job_id, "unknown-job")
        objects_root = job_root / ".objects"
        index_path = job_root / "index.json"
        index = self._read_index(index_path, job_id, session_id)
        downloaded = 0

        for item in await self._ark.list_agent_files(session_id):
            file_id = str(item.get("id") or "").strip()
            download_url = str(item.get("download_url") or "").strip()
            if not file_id or not download_url:
                continue
            fingerprint = {
                "size": _optional_int(item.get("size")),
                "updated_at": str(item.get("updated_at") or ""),
            }
            previous = index["files"].get(file_id)
            if (
                isinstance(previous, dict)
                and previous.get("fingerprint") == fingerprint
                and (job_root / str(previous.get("object_path") or "")).is_file()
            ):
                continue

            filename = str(item.get("filename") or item.get("name") or file_id)
            safe_name = _safe_component(filename, "artifact")
            object_name = f"{hashlib.sha256(file_id.encode()).hexdigest()[:16]}__{safe_name}"
            object_path = objects_root / object_name
            content = await self._ark.download_file(download_url)
            _atomic_write(object_path, content)
            index["files"][file_id] = {
                "file_id": file_id,
                "filename": filename,
                "status": str(item.get("status") or ""),
                "fingerprint": fingerprint,
                "object_path": object_path.relative_to(job_root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "synced_at": _utc_now(),
            }
            downloaded += 1
            log.info(
                "artifact synced job=%s file=%s path=%s",
                job_id,
                filename,
                object_path,
            )

        self._materialize_manifests(job_root, index)
        index["updated_at"] = _utc_now()
        _atomic_write(
            index_path,
            (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return downloaded

    @staticmethod
    def _read_index(path: Path, job_id: str, session_id: str) -> dict:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                log.warning("ignoring invalid artifact index: %s", path)
        return {
            "schema_version": 1,
            "job_id": job_id,
            "session_id": session_id,
            "updated_at": "",
            "files": {},
        }

    @staticmethod
    def _materialize_manifests(job_root: Path, index: dict) -> None:
        files = [item for item in index["files"].values() if isinstance(item, dict)]
        manifests = [
            item
            for item in files
            if Path(str(item.get("filename") or "")).name == "manifest.json"
        ]
        for manifest_item in manifests:
            manifest_object = job_root / str(manifest_item["object_path"])
            try:
                manifest = json.loads(manifest_object.read_text(encoding="utf-8"))
                project = _safe_component(str(manifest["project"]), "")
                publications = manifest["publications"]
                if not project or not isinstance(publications, list):
                    continue
            except (KeyError, OSError, json.JSONDecodeError, TypeError):
                continue

            _atomic_copy(manifest_object, job_root / project / "manifest.json")
            for publication in publications:
                if not isinstance(publication, dict):
                    continue
                for artifact in publication.get("files", []):
                    if not isinstance(artifact, dict):
                        continue
                    output = PurePosixPath(str(artifact.get("output") or ""))
                    if (
                        output.is_absolute()
                        or not output.parts
                        or output.parts[0] != project
                        or ".." in output.parts
                    ):
                        continue
                    expected_size = artifact.get("size")
                    expected_hash = str(artifact.get("sha256") or "")
                    basename = PurePosixPath(str(artifact.get("source") or "")).name
                    size_matches = [
                        candidate
                        for candidate in files
                        if (
                            expected_size is None
                            or candidate.get("fingerprint", {}).get("size")
                            == expected_size
                        )
                    ]
                    named_matches = [
                        candidate
                        for candidate in size_matches
                        if Path(str(candidate.get("filename") or "")).name == basename
                    ]
                    candidates = named_matches or size_matches
                    matched = next(
                        (
                            candidate
                            for candidate in candidates
                            if candidate.get("sha256") == expected_hash
                            or _sha256(job_root / str(candidate["object_path"]))
                            == expected_hash
                        ),
                        None,
                    )
                    if matched is None:
                        continue
                    _atomic_copy(
                        job_root / str(matched["object_path"]),
                        job_root.joinpath(*output.parts),
                    )
