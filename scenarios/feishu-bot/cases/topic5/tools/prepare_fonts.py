#!/usr/bin/env python3
"""Topic5 字体统一准备：仓库外下载缓存、SHA-256 校验、临时部署目录注入。"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[5]
FONTS = (
    ("blueai-yuqing-echarts-skill-new", "SourceHanSansSC-Regular.otf",
     "https://cdn.jsdelivr.net/gh/adobe-fonts/source-han-sans@2.004R/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf",
     "84bbd4ace91d327b3ad1a581c688196278a4e41308520176f419180064e4af2b"),
    ("blueai-yuqing-echarts-skill-new", "SourceHanSansSC-Bold.otf",
     "https://cdn.jsdelivr.net/gh/adobe-fonts/source-han-sans@2.004R/OTF/SimplifiedChinese/SourceHanSansSC-Bold.otf",
     "3baae4d7da5af78133c2db849814e1c0ca962e490f681714922cc499c6aeb355"),
    ("wordcloud-insight", "NotoSansCJK-Bold.ttc",
     "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/OTC/NotoSansCJK-Bold.ttc",
     "faa5f3656a78b2e2d450d27fe8382c778bc2b6bb5ea29c986664a6a435056ceb"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def outside_repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError(f"字体缓存和临时注入目录必须位于仓库外：{resolved}")
    return resolved


def prepare(skill: str | None = None, stage_root: Path | None = None) -> None:
    cache = outside_repo(Path(os.environ.get(
        "TOPIC5_FONT_CACHE_DIR", str(Path.home() / ".cache/topic5-fonts"))))
    stage = outside_repo(stage_root) if stage_root is not None else None
    if stage is not None and skill is None:
        raise ValueError("--stage-root 必须同时指定 --skill，目标为该 Skill 的临时根目录")
    cache.mkdir(parents=True, exist_ok=True)
    for owner, name, url, expected in FONTS:
        if skill is not None and owner != skill:
            continue
        cached = cache / name
        if not cached.is_file() or sha256(cached) != expected:
            print(f"下载 {name}", flush=True)
            fd, download_name = tempfile.mkstemp(prefix=".font-download-", dir=cache)
            os.close(fd)
            download = Path(download_name)
            try:
                subprocess.run([
                    "curl", "--fail", "--location", "--retry", "2",
                    "--connect-timeout", "20", "--max-time", "180",
                    "--output", str(download), url,
                ], check=True, timeout=600)
                if sha256(download) != expected:
                    raise ValueError(f"{name} SHA-256 不匹配，停止准备字体")
                download.replace(cached)
            finally:
                download.unlink(missing_ok=True)
        if stage is not None:
            destination = outside_repo(stage / "assets/fonts" / name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, destination)
            if sha256(destination) != expected:
                raise ValueError(f"{name} 注入后的 SHA-256 不匹配")
        print(f"字体校验通过：{name} ({owner})", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", choices=sorted({row[0] for row in FONTS}))
    parser.add_argument("--stage-root", type=Path,
                        help="仓库外的临时 Skill 根目录；省略则仅准备缓存")
    args = parser.parse_args()
    prepare(args.skill, args.stage_root)


if __name__ == "__main__":
    main()
