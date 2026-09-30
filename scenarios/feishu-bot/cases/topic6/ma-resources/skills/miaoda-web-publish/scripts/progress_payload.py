#!/usr/bin/env python3
"""Build a safe, idempotent Feishu progress message for Miaoda publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from urllib.parse import parse_qsl, unquote, urlparse


STAGES = {
    "preflight_passed": "HTML 预检通过",
    "app_ready": "妙搭应用已就绪",
    "source_pushed": "源码已推送",
    "release_started": "妙搭发布已启动",
    "release_finished": "妙搭发布成功",
    "release_failed": "妙搭发布失败",
    "authorization_required": "等待飞书授权",
}


SENSITIVE_QUERY_PARTS = {"token", "access_token", "secret", "code", "device_code", "key", "password"}

STAGE_REQUIREMENTS = {
    "preflight_passed": ("source_sha256",),
    "app_ready": ("app_id",),
    "source_pushed": ("app_id", "commit_id"),
    "release_started": ("app_id", "release_id"),
    "release_finished": ("app_id", "release_id", "online_url"),
    "release_failed": ("error_category",),
    "authorization_required": (),
}


def safe_id(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", value):
        raise ValueError(f"{field} contains unsupported characters")
    return value


def validate_fields(args: argparse.Namespace) -> None:
    safe_id(args.run_id, "run_id")
    if args.app_id and not re.fullmatch(r"app_[a-z0-9]{6,64}", args.app_id):
        raise ValueError("app_id has an invalid format")
    if args.release_id and not re.fullmatch(r"(?:[0-9]{6,32}|release_[a-z0-9]{6,64})", args.release_id):
        raise ValueError("release_id has an invalid format")
    if args.commit_id and not re.fullmatch(r"[0-9a-fA-F]{7,64}", args.commit_id):
        raise ValueError("commit_id must be a Git hash")
    if args.source_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", args.source_sha256):
        raise ValueError("source_sha256 must be 64 hexadecimal characters")

    missing = [name for name in STAGE_REQUIREMENTS[args.stage] if not getattr(args, name)]
    if missing:
        raise ValueError(f"stage {args.stage} requires: {', '.join(missing)}")

    if args.online_url:
        parsed = urlparse(args.online_url)
        host = (parsed.hostname or "").lower()
        allowed_host = host.endswith(".feishu.cn") or host.endswith(".larksuite.com")
        decoded_url = unquote(args.online_url).lower()
        if parsed.scheme != "https" or not host or not allowed_host:
            raise ValueError("online_url must use https on a Feishu/Lark domain")
        if any(char.isspace() for char in decoded_url) or any(
            marker in decoded_url for marker in ("<", ">", "`", "@all")
        ):
            raise ValueError("online_url contains unsafe message markup")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("online_url must not contain userinfo or a fragment")
        query_names = {name.lower() for name, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if any(any(part in name for part in SENSITIVE_QUERY_PARTS) for name in query_names):
            raise ValueError("online_url contains a sensitive query parameter")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage", required=True, choices=sorted(STAGES))
    parser.add_argument("--app-id")
    parser.add_argument("--release-id")
    parser.add_argument("--commit-id")
    parser.add_argument("--source-sha256")
    parser.add_argument("--online-url")
    parser.add_argument(
        "--error-category",
        choices=["auth", "validation", "git", "release", "timeout", "unknown"],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_fields(args)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    run_id = args.run_id
    app_id = args.app_id
    release_id = args.release_id
    commit_id = args.commit_id
    source_hash = args.source_sha256

    lines = [f"#### 妙搭发布进度 · {STAGES[args.stage]}", "", f"- 批次：`{run_id}`"]
    if source_hash:
        lines.append(f"- 源文件：`{source_hash[:12]}`")
    if app_id:
        lines.append(f"- App ID：`{app_id}`")
    if commit_id:
        lines.append(f"- Commit：`{commit_id[:12]}`")
    if release_id:
        lines.append(f"- Release ID：`{release_id}`")
    if args.online_url:
        lines.append(f"- 访问链接：{args.online_url}")
    if args.error_category:
        lines.append(f"- 错误类别：`{args.error_category}`")

    key_stage = args.stage.replace("_", "-")
    run_digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    idempotency_key = f"mdp-{run_digest}-{key_stage}"[:50]
    output = {
        "ok": True,
        "stage": args.stage,
        "idempotency_key": idempotency_key,
        "markdown": "\n".join(lines),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
