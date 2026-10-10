#!/usr/bin/env python3
"""Publish completed Topic6 node outputs to the Session Files directory."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

DEFAULT_PROJECTS_ROOT = Path("/workspace/Projects")
DEFAULT_OUTPUT_ROOT = Path("/mnt/session/outputs")
MANIFEST_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_project(project_dir: Path, projects_root: Path) -> Path:
    project = project_dir.resolve(strict=True)
    root = projects_root.resolve(strict=True)
    if project.parent != root:
        raise ValueError(f"project-dir must be a direct child of {root}: {project}")
    return project


def _iter_files(
    project: Path, patterns: Sequence[str], optional_patterns: Sequence[str] = ()
) -> list[Path]:
    files: dict[str, Path] = {}
    for pattern, required in [
        *((pattern, True) for pattern in patterns),
        *((pattern, False) for pattern in optional_patterns),
    ]:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError(f"include must be a project-relative path or glob: {pattern}")
        matches = list(project.glob(pattern))
        if not matches and required:
            raise FileNotFoundError(f"include matched no files: {pattern}")
        for match in matches:
            if match.is_symlink():
                raise ValueError(f"symlink outputs are not publishable: {match}")
            candidates = match.rglob("*") if match.is_dir() else (match,)
            for candidate in candidates:
                if candidate.is_symlink():
                    raise ValueError(f"symlink outputs are not publishable: {candidate}")
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                try:
                    relative = resolved.relative_to(project)
                except ValueError as error:
                    raise ValueError(f"output escapes project directory: {candidate}") from error
                files[relative.as_posix()] = resolved
    if not files:
        raise FileNotFoundError("includes contained no regular files")
    return [files[key] for key in sorted(files)]


@contextmanager
def _manifest_lock(output_project: Path) -> Iterator[None]:
    lock_key = hashlib.sha256(str(output_project).encode("utf-8")).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / f"topic6-artifacts-{lock_key}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


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


def _read_manifest(path: Path, project_name: str) -> dict:
    if not path.exists():
        return {
            "schema_version": MANIFEST_VERSION,
            "project": project_name,
            "updated_at": "",
            "publications": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != MANIFEST_VERSION
        or payload.get("project") != project_name
        or not isinstance(payload.get("publications"), list)
    ):
        raise ValueError(f"invalid artifact manifest: {path}")
    return payload


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(
    *,
    project_dir: Path,
    node: str,
    run_id: str,
    includes: Sequence[str],
    optional_includes: Sequence[str] = (),
    projects_root: Path = DEFAULT_PROJECTS_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict:
    if not node.strip():
        raise ValueError("node must not be empty")
    if not run_id.strip():
        raise ValueError("run-id must not be empty")
    project = _validated_project(project_dir, projects_root)
    sources = _iter_files(project, includes, optional_includes)
    output_project = output_root / project.name
    manifest_path = output_project / "manifest.json"
    published_at = _utc_now()

    with _manifest_lock(output_project):
        file_entries = []
        for source in sources:
            relative = source.relative_to(project)
            destination = output_project / relative
            _atomic_copy(source, destination)
            file_entries.append(
                {
                    "source": relative.as_posix(),
                    "output": f"{project.name}/{relative.as_posix()}",
                    "size": destination.stat().st_size,
                    "sha256": _sha256(destination),
                }
            )

        manifest = _read_manifest(manifest_path, project.name)
        publication = {
            "node": node.strip(),
            "run_id": run_id.strip(),
            "status": "complete",
            "published_at": published_at,
            "files": file_entries,
        }
        publications = manifest["publications"]
        publications[:] = [
            item
            for item in publications
            if not (
                isinstance(item, dict)
                and item.get("node") == publication["node"]
                and str(item.get("run_id")) == publication["run_id"]
            )
        ]
        publications.append(publication)
        manifest["updated_at"] = published_at
        _atomic_write_json(manifest_path, manifest)
    return publication


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--include", action="append", required=True)
    parser.add_argument("--optional-include", action="append", default=[])
    parser.add_argument("--projects-root", type=Path, default=DEFAULT_PROJECTS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    publication = publish(
        project_dir=args.project_dir,
        node=args.node,
        run_id=args.run_id,
        includes=args.include,
        optional_includes=args.optional_include,
        projects_root=args.projects_root,
        output_root=args.output_root,
    )
    print(json.dumps(publication, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
