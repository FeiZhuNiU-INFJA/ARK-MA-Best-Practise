#!/usr/bin/env python3
"""Select one lark-cli binary that satisfies the Miaoda skill baseline."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


MIN_VERSION = (1, 0, 95)


def candidate_paths() -> list[Path]:
    raw: list[str] = []
    override = os.environ.get("MIAODA_LARK_CLI")
    if override:
        raw.append(override)
    selected = shutil.which("lark-cli")
    if selected:
        raw.append(selected)
    which_all = subprocess.run(
        ["which", "-a", "lark-cli"], text=True, capture_output=True, check=False
    )
    raw.extend(line.strip() for line in which_all.stdout.splitlines() if line.strip())
    raw.extend(
        [
            str(Path.home() / ".npm-global" / "bin" / "lark-cli"),
            str(Path.home() / ".hermes" / "node" / "bin" / "lark-cli"),
        ]
    )
    raw.extend(glob.glob(str(Path.home() / ".nvm" / "versions" / "node" / "*" / "bin" / "lark-cli")))

    paths: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        path = Path(item).expanduser()
        key = str(path.absolute())
        if key in seen or not path.is_file() or not os.access(path, os.X_OK):
            continue
        seen.add(key)
        paths.append(path.absolute())
    return paths


def inspect(path: Path) -> dict:
    result = subprocess.run(
        [str(path), "--version"], text=True, capture_output=True, check=False
    )
    text = (result.stdout + "\n" + result.stderr).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    version = tuple(int(part) for part in match.groups()) if match else None
    return {
        "path": str(path),
        "version": ".".join(str(part) for part in version) if version else None,
        "version_tuple": version,
        "usable": result.returncode == 0 and version is not None and version >= MIN_VERSION,
    }


def main() -> int:
    inspected = [inspect(path) for path in candidate_paths()]
    usable = [item for item in inspected if item["usable"]]
    selected = max(usable, key=lambda item: item["version_tuple"]) if usable else None
    output = {
        "ok": selected is not None,
        "minimum_version": ".".join(str(part) for part in MIN_VERSION),
        "selected": (
            {"path": selected["path"], "version": selected["version"]}
            if selected
            else None
        ),
        "candidates": [
            {
                "path": item["path"],
                "version": item["version"],
                "usable": item["usable"],
            }
            for item in inspected
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if selected else 2


if __name__ == "__main__":
    sys.exit(main())
