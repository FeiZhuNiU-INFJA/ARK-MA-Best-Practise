#!/usr/bin/env python3
"""上传 topic6 skill zip 到方舟 SkillHub。

使用:
    export ARK_API_KEY=...
    python upload_skills.py                # 全部上传(hash 变化才重传)
    python upload_skills.py --force        # 强制全部重传
    python upload_skills.py --only c2      # 只上传指定 key

产物:
    - 上传成功后追加/更新 ma-resources/skill_ids.json
    - 每条记录形如 {skill_id, version, uploaded_at, source_sha256}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path(__file__).resolve().parent / "out"
SKILL_IDS_FILE = ROOT / "ma-resources" / "skill_ids.json"
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com")

SKILLS = [
    ("topic6-fetch-normalize", "topic6-fetch-normalize.zip", "topic6 · Phase A+B 取数标准化"),
    ("topic6-annotation", "topic6-annotation.zip", "topic6 · C0/R1~R5/C3 标注与 DataHub 轮询"),
    ("topic6-insight", "topic6-insight.zip", "topic6 · E1~E4 分版块洞察"),
    ("topic6-event-registry", "topic6-event-registry.zip", "topic6 · C2 事件合并 (blueai-canonical-event-registry v2.1.0)"),
    ("topic6-web-report", "topic6-web-report.zip", "topic6 · Phase G 网页生成 (v1.1.0)"),
]


def _load_state() -> dict:
    if SKILL_IDS_FILE.exists():
        return json.loads(SKILL_IDS_FILE.read_text(encoding="utf-8"))
    return {"note": "", "skills": {}}


def _save_state(state: dict) -> None:
    SKILL_IDS_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_sha(zip_path: Path) -> str:
    sha_file = zip_path.with_suffix(zip_path.suffix + ".sha256")
    if not sha_file.exists():
        raise FileNotFoundError(f"{sha_file} 不存在,请先跑 pack_skills.sh")
    return sha_file.read_text(encoding="utf-8").strip()


def upload(key: str, zip_name: str, display_title: str, force: bool = False) -> None:
    zip_path = OUT_DIR / zip_name
    if not zip_path.exists():
        print(f"  [跳过] {zip_path} 不存在")
        return

    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("请设置 ARK_API_KEY")

    sha = _read_sha(zip_path)
    state = _load_state()
    existing = state["skills"].get(key)
    if existing and not force and existing.get("source_sha256") == sha:
        print(f"  [跳过] {key} 内容未变 (sha256={sha[:12]}...)")
        return

    print(f"=== 上传 {key} ({display_title}) ===")
    with zip_path.open("rb") as fh:
        resp = requests.post(
            f"{BASE_URL}/api/v3/skills",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"files": (zip_name, fh, "application/zip")},
            data={"display_title": display_title},
            timeout=300,
        )
    if not resp.ok:
        raise RuntimeError(f"上传失败 status={resp.status_code} body={resp.text[:500]}")

    payload = resp.json()
    state["skills"][key] = {
        "skill_id": payload["id"],
        "version": payload.get("latest_version", "1"),
        "uploaded_at": int(time.time()),
        "source_sha256": sha,
        "display_title": display_title,
    }
    _save_state(state)
    print(f"  ok · skill_id={payload['id']} · version={payload.get('latest_version')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重传")
    ap.add_argument("--only", help="只上传指定 key")
    args = ap.parse_args()

    for key, zip_name, title in SKILLS:
        if args.only and args.only != key:
            continue
        try:
            upload(key, zip_name, title, force=args.force)
        except Exception as exc:
            print(f"  [失败] {key}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
