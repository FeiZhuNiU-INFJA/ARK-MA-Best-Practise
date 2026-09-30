#!/usr/bin/env python3
"""Validate an existing HTML file or static directory before Miaoda publish."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


ASSET_ATTRS = {
    "audio": ("src",),
    "iframe": ("src",),
    "img": ("src", "srcset"),
    "link": ("href",),
    "script": ("src",),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
}

SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "credentials",
    "id_rsa",
    "id_ed25519",
    "service-account.json",
}

SENSITIVE_PARTS = {".aws", ".ssh", ".gnupg"}
IGNORED_PARTS = {".git", "node_modules", ".DS_Store"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_network_url(value: str) -> bool:
    value = value.strip()
    parsed = urlparse(value)
    return value.startswith("//") or parsed.scheme.lower() in {"http", "https"}


def candidates(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("data:"):
        return []
    parts = [part.strip().split()[0] for part in value.split(",") if part.strip()]
    result = []
    for item in parts:
        if not item or item.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if is_network_url(item):
            continue
        parsed = urlparse(item)
        if parsed.scheme.lower() in {"blob", "file"}:
            result.append(item)
        elif not parsed.scheme and parsed.path:
            result.append(unquote(parsed.path))
    return result


class Probe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.doctype = False
        self.network_assets: list[dict[str, str]] = []
        self.local_assets: list[dict[str, str]] = []
        self.data_assets = 0
        self.inline_handlers = 0
        self.outbound_links = 0

    def handle_decl(self, decl: str) -> None:
        if decl.lower().strip() == "doctype html":
            self.doctype = True

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self.tags[tag] += 1
        attr_map = {str(key).lower(): str(value or "") for key, value in attrs}

        self.inline_handlers += sum(1 for key in attr_map if key.startswith("on"))
        href = attr_map.get("href", "") if tag == "a" else ""
        if href and is_network_url(href):
            self.outbound_links += 1

        for key in ASSET_ATTRS.get(tag, ()):
            value = attr_map.get(key, "").strip()
            if not value:
                continue
            if value.startswith("data:"):
                self.data_assets += 1
                continue
            if is_network_url(value):
                self.network_assets.append({"tag": tag, "attribute": key, "value": value})
                continue
            for item in candidates(value):
                self.local_assets.append({"tag": tag, "attribute": key, "value": item})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an HTML file or directory for Feishu Miaoda publication."
    )
    parser.add_argument(
        "source_path",
        help="Local path or file:// URL to an HTML file or static directory",
    )
    parser.add_argument("--expect-sha256", help="Expected SHA-256 of the entry HTML")
    parser.add_argument(
        "--require-self-contained",
        action="store_true",
        help="Fail if index.html refers to local or network assets",
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        help="Optional maximum total bundle size in bytes",
    )
    return parser.parse_args()


def resolve_source(raw: str) -> Path:
    parsed = urlparse(raw)
    if parsed.scheme.lower() == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("file:// URL must refer to this local machine")
        return Path(unquote(parsed.path)).expanduser().resolve()
    if parsed.scheme:
        raise ValueError("source_path must be a local path or file:// URL")
    return Path(raw).expanduser().resolve()


def bundle_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        files.append(path)
    return sorted(files)


def safe_local_path(root: Path, value: str) -> Path | None:
    if value.startswith(("file:", "blob:")) or os.path.isabs(value):
        return None
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def is_sensitive(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    lower_parts = {part.lower() for part in relative.parts}
    name = path.name.lower()
    return (
        name in SENSITIVE_NAMES
        or name.startswith(".env.")
        or bool(lower_parts & SENSITIVE_PARTS)
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
    )


def main() -> int:
    args = parse_args()
    try:
        source = resolve_source(args.source_path)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    errors: list[str] = []
    warnings: list[str] = []

    if source.is_file():
        if source.suffix.lower() not in {".html", ".htm"}:
            errors.append("source file must end in .html or .htm")
        root = source.parent
        entry = source
        files = [source]
    elif source.is_dir():
        root = source
        entry = root / "index.html"
        files = bundle_files(root)
        if not entry.is_file():
            errors.append("directory source must contain index.html")
    else:
        result = {"ok": False, "errors": ["source_path does not exist"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    if not entry.is_file():
        result = {
            "ok": False,
            "source_path": str(source),
            "errors": errors or ["entry HTML is missing"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    entry_bytes = entry.read_bytes()
    try:
        html = entry_bytes.decode("utf-8")
    except UnicodeDecodeError:
        errors.append("entry HTML is not valid UTF-8")
        html = entry_bytes.decode("utf-8", errors="replace")

    probe = Probe()
    try:
        probe.feed(html)
    except Exception as exc:
        errors.append(f"cannot parse entry HTML: {exc}")

    entry_hash = sha256(entry)
    if args.expect_sha256 and entry_hash.lower() != args.expect_sha256.lower():
        errors.append(
            f"entry SHA-256 mismatch: expected {args.expect_sha256}, got {entry_hash}"
        )

    if not probe.doctype:
        warnings.append("entry HTML is missing <!doctype html>")
    if probe.tags["html"] != 1:
        warnings.append(f"expected one html element, found {probe.tags['html']}")
    if probe.tags["body"] != 1:
        warnings.append(f"expected one body element, found {probe.tags['body']}")

    missing_assets: list[dict[str, str]] = []
    unsafe_assets: list[dict[str, str]] = []
    for asset in probe.local_assets:
        candidate = safe_local_path(root, asset["value"])
        if candidate is None:
            unsafe_assets.append(asset)
        elif not candidate.is_file():
            missing_assets.append(asset)
    if unsafe_assets:
        errors.append(f"unsafe or unsupported local asset references: {unsafe_assets}")
    if missing_assets:
        errors.append(f"missing local assets: {missing_assets}")

    sensitive = [str(path.relative_to(root)) for path in files if is_sensitive(path, root)]
    if sensitive:
        errors.append(f"sensitive credential-like files found: {sensitive}")

    total_bytes = sum(path.stat().st_size for path in files)
    if args.max_total_bytes is not None and total_bytes > args.max_total_bytes:
        errors.append(
            f"bundle size {total_bytes} exceeds maximum {args.max_total_bytes} bytes"
        )

    if args.require_self_contained and (probe.network_assets or probe.local_assets):
        errors.append("self-contained policy violated: external asset references exist")

    if probe.network_assets:
        warnings.append(
            "network assets depend on third-party availability and policy; publication does not verify them"
        )
    if probe.data_assets:
        warnings.append(
            "embedded data: assets were found; verify final document size and browser compatibility"
        )
    if probe.inline_handlers:
        warnings.append(f"inline event handlers found: {probe.inline_handlers}")
    if probe.tags["iframe"]:
        warnings.append(f"iframe tags found: {probe.tags['iframe']}")
    if probe.tags["form"]:
        warnings.append(f"form tags found: {probe.tags['form']}")

    result = {
        "ok": not errors,
        "provider": "feishu-miaoda",
        "source_path": str(source),
        "source_kind": "file" if source.is_file() else "directory",
        "entry": {
            "path": str(entry),
            "size_bytes": len(entry_bytes),
            "sha256": entry_hash,
            "doctype_html": probe.doctype,
        },
        "bundle": {
            "file_count": len(files),
            "total_bytes": total_bytes,
            "sensitive_file_count": len(sensitive),
        },
        "html": {
            "script_count": probe.tags["script"],
            "form_count": probe.tags["form"],
            "iframe_count": probe.tags["iframe"],
            "inline_handler_count": probe.inline_handlers,
            "network_asset_count": len(probe.network_assets),
            "local_asset_count": len(probe.local_assets),
            "embedded_data_asset_count": probe.data_assets,
            "outbound_link_count": probe.outbound_links,
        },
        "missing_local_assets": missing_assets,
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
