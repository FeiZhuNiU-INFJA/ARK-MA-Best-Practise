from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "cases/topic6/ma-resources/skills/topic6-annotation/scripts"
    / "publish_node_artifacts.py"
)


def _load_script():
    name = "topic6_publish_node_artifacts"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


publisher = _load_script()


def test_publish_preserves_project_paths_and_updates_manifest(tmp_path: Path):
    projects_root = tmp_path / "Projects"
    project = projects_root / "W40"
    output_root = tmp_path / "outputs"
    source = project / "04_标注/C0/result.xlsx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first")

    first = publisher.publish(
        project_dir=project,
        node="c0",
        run_id="1",
        includes=["04_标注/C0/*.xlsx"],
        projects_root=projects_root,
        output_root=output_root,
    )

    copied = output_root / "W40/04_标注/C0/result.xlsx"
    assert copied.read_bytes() == b"first"
    assert first["status"] == "complete"
    assert first["files"][0]["output"] == "W40/04_标注/C0/result.xlsx"

    source.write_bytes(b"second")
    publisher.publish(
        project_dir=project,
        node="c0",
        run_id="1",
        includes=["04_标注/C0/result.xlsx"],
        projects_root=projects_root,
        output_root=output_root,
    )

    manifest = json.loads(
        (output_root / "W40/manifest.json").read_text(encoding="utf-8")
    )
    assert copied.read_bytes() == b"second"
    assert len(manifest["publications"]) == 1
    assert manifest["publications"][0]["files"][0]["size"] == 6


def test_publish_directory_and_second_node_append_publications(tmp_path: Path):
    projects_root = tmp_path / "Projects"
    project = projects_root / "W40"
    output_root = tmp_path / "outputs"
    (project / "06_洞察/v1").mkdir(parents=True)
    (project / "06_洞察/v1/e1.md").write_text("e1", encoding="utf-8")
    (project / "06_洞察/v1/e2.md").write_text("e2", encoding="utf-8")

    publisher.publish(
        project_dir=project,
        node="insight",
        run_id="1",
        includes=["06_洞察/v1"],
        projects_root=projects_root,
        output_root=output_root,
    )
    publisher.publish(
        project_dir=project,
        node="report",
        run_id="1",
        includes=["06_洞察/v1/e1.md"],
        projects_root=projects_root,
        output_root=output_root,
    )

    manifest = json.loads(
        (output_root / "W40/manifest.json").read_text(encoding="utf-8")
    )
    assert [item["node"] for item in manifest["publications"]] == [
        "insight",
        "report",
    ]
    assert len(manifest["publications"][0]["files"]) == 2


def test_publish_rejects_escape_and_missing_patterns(tmp_path: Path):
    projects_root = tmp_path / "Projects"
    project = projects_root / "W40"
    project.mkdir(parents=True)

    with pytest.raises(ValueError, match="project-relative"):
        publisher.publish(
            project_dir=project,
            node="bad",
            run_id="1",
            includes=["../secret"],
            projects_root=projects_root,
            output_root=tmp_path / "outputs",
        )

    with pytest.raises(FileNotFoundError, match="matched no files"):
        publisher.publish(
            project_dir=project,
            node="missing",
            run_id="1",
            includes=["missing/*.json"],
            projects_root=projects_root,
            output_root=tmp_path / "outputs",
        )
