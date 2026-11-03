#!/usr/bin/env python3
"""Bind uploaded skills and current prompts to existing Agents, retaining their IDs.

Run after pack_skills.sh and upload_skills.py. No environment/memory/session writes.
Credentials come from the sourced case environment. --dry-run only reads Agents.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1] / "ma-resources"


def render_agent(name, ids, skill_ids):
    path = ROOT / "agents" / f"{name}.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    system_file = config.pop("system_file")
    config["system"] = (path.parent / system_file).read_text(encoding="utf-8")
    text = json.dumps(config, ensure_ascii=False)
    for key, value in skill_ids["skills"].items():
        text = text.replace("${skill_id." + key + "}", value["skill_id"])
        text = text.replace("${version." + key + "}", str(value["version"]))
    text = text.replace("${agent_id.topic5-validator}", ids["agent_validator_id"])
    if "${" in text:
        raise ValueError("Agent配置存在未解析资源占位符")
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", choices=("validator", "coordinator"))
    args = parser.parse_args()
    ids = json.loads((ROOT / "created_ids.json").read_text())
    skills = json.loads((ROOT / "skill_ids.json").read_text())
    base = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    if not base.endswith("/api/v3"):
        base += "/api/v3"
    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer " + os.environ["ARK_API_KEY"],
        "X-Ark-Beta": "agentic-2026-06-01",
    })

    def request(method, resource, payload=None):
        response = session.request(method, base + resource, json=payload, timeout=60)
        if not response.ok:
            # Do not print remote config or credential-bearing response bodies.
            raise RuntimeError(f"Agent资源更新失败: {method} {resource}, HTTP {response.status_code}")
        data = response.json()
        return data["data"] if isinstance(data.get("data"), dict) else data

    try:
        # Preflight both targets before making the first change.
        targets = []
        for name, key in (("validator", "agent_validator_id"), ("coordinator", "agent_coordinator_id")):
            if args.only and args.only != name:
                continue
            agent_id = ids[key]
            current = request("GET", f"/agents/{agent_id}")
            if current.get("name") != f"topic5-{name}":
                raise ValueError(f"资源名称与Topic5 {name}不一致")
            version = int(current["version"])
            config = render_agent(name, ids, skills)
            targets.append((name, agent_id, version, config))
        for name, agent_id, version, config in targets:
            if args.dry_run:
                print(f"{name}: id={agent_id}, current_version={version}, skills={len(config['skills'])}, dry_run")
                continue
            updated = request("POST", f"/agents/{agent_id}", {**config, "version": version})
            actual = request("GET", f"/agents/{agent_id}")
            wanted = {(s["skill_id"], str(s["version"])) for s in config["skills"]}
            mounted = {(s["skill_id"], str(s["version"])) for s in actual.get("skills") or []}
            if actual.get("id") != agent_id or int(actual["version"]) <= version or mounted != wanted:
                raise RuntimeError(f"{name}更新回读与预期资源绑定不一致")
            if actual.get("system") != config["system"]:
                raise RuntimeError(f"{name}系统提示词回读不一致")
            print(f"{name}: id={agent_id}, version={version}->{actual['version']}, skills_verified={len(wanted)}")
        if not args.dry_run:
            print("原地更新完成；Agent ID不变，新建Session使用新版本。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
