#!/usr/bin/env python3
"""Preview or send one confirmed Feishu progress message via lark-cli API."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--chat-id")
    target.add_argument("--user-id")
    parser.add_argument("--identity", choices=["user", "bot"], required=True)
    parser.add_argument(
        "--confirmed",
        action="store_true",
        help="Assert that recipient, identity, and v1 message template were approved",
    )
    parser.add_argument("--send", action="store_true", help="Send; otherwise API dry-run only")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--app-id")
    parser.add_argument("--release-id")
    parser.add_argument("--commit-id")
    parser.add_argument("--source-sha256")
    parser.add_argument("--online-url")
    parser.add_argument("--error-category")
    return parser.parse_args()


def run_json(args: list[str]) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    return result, payload if isinstance(payload, dict) else {}


def find_key(value, key: str):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_key(child, key)
            if found is not None:
                return found
    return None


def main() -> int:
    args = parse_args()
    if not args.confirmed:
        print(
            json.dumps(
                {
                    "ok": False,
                    "delivery_status": "blocked",
                    "error": "recipient, identity, and template confirmation is required",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload_args = [
        sys.executable,
        str(HERE / "progress_payload.py"),
        "--run-id",
        args.run_id,
        "--stage",
        args.stage,
    ]
    for flag, value in (
        ("--app-id", args.app_id),
        ("--release-id", args.release_id),
        ("--commit-id", args.commit_id),
        ("--source-sha256", args.source_sha256),
        ("--online-url", args.online_url),
        ("--error-category", args.error_category),
    ):
        if value:
            payload_args.extend([flag, value])
    payload_result, payload = run_json(payload_args)
    if payload_result.returncode != 0 or payload.get("ok") is not True:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    resolver_result, resolver = run_json([sys.executable, str(HERE / "resolve_lark_cli.py")])
    selected = resolver.get("selected")
    if resolver_result.returncode != 0 or not isinstance(selected, dict):
        print(
            json.dumps(
                {"ok": False, "delivery_status": "blocked", "error": "compatible lark-cli not found"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    target_flag = "--chat-id" if args.chat_id else "--user-id"
    target_value = args.chat_id or args.user_id
    cli_args = [
        selected["path"],
        "im",
        "+messages-send",
        "--as",
        args.identity,
        target_flag,
        target_value,
        "--markdown",
        payload["markdown"],
        "--idempotency-key",
        payload["idempotency_key"],
        "--format",
        "json",
    ]
    if not args.send:
        cli_args.append("--dry-run")

    send_result, response = run_json(cli_args)
    success = send_result.returncode == 0 and response.get("ok") is not False
    output = {
        "ok": success,
        "delivery_status": ("sent" if args.send else "previewed") if success else "failed",
        "stage": args.stage,
        "identity": args.identity,
        "target_type": "chat" if args.chat_id else "user",
        "idempotency_key": payload["idempotency_key"],
        "message_id": find_key(response, "message_id") if args.send and success else None,
    }
    if not success:
        output["error"] = "Feishu message API call failed; inspect CLI error locally"
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
