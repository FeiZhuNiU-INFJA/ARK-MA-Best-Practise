#!/usr/bin/env python3
"""Update Topic5 sandbox package requirements, retaining environment ID and env.

Only dependencies change; remote credentials/configuration are neither persisted
locally nor printed. Future Sessions use the updated requirements.
"""
import argparse
import copy
import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1] / "ma-resources"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ids = json.loads((ROOT / "created_ids.json").read_text())
    environment_id = ids["environment_id"]
    desired = json.loads((ROOT / "environment.json").read_text())
    base = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    if not base.endswith("/api/v3"):
        base += "/api/v3"
    with requests.Session() as session:
        session.headers.update({"Authorization": "Bearer " + os.environ["ARK_API_KEY"],
                                "X-Ark-Beta": "agentic-2026-06-01"})
        url = base + "/environments/" + environment_id
        def request(method, **kwargs):
            response = session.request(method, url, timeout=60, **kwargs)
            if not response.ok:
                raise RuntimeError(f"环境依赖更新失败：{method}, HTTP {response.status_code}")
            data = response.json()
            return data["data"] if isinstance(data.get("data"), dict) else data
        actual = request("GET")
        if actual.get("name") != desired["name"]:
            raise ValueError("环境名称与Topic5配置不一致")
        config = copy.deepcopy(actual["config"])
        config.setdefault("packages", {})["pip"] = desired["config"]["packages"]["pip"]
        if args.dry_run:
            print(f"environment={environment_id}, packages={len(config['packages']['pip'])}, dry_run")
            return
        if config != actual["config"]:
            request("POST", json={"config": config})
        verified = request("GET")
        if verified.get("id") != environment_id or verified.get("config") != config:
            raise RuntimeError("环境更新回读与预期配置不一致")
        print(f"environment={environment_id}, packages_verified={len(config['packages']['pip'])}, ID保持不变")


if __name__ == "__main__":
    main()
