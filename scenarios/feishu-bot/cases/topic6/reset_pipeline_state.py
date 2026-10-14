#!/usr/bin/env python3
"""清理 Topic6 本地任务状态，解除因中断遗留的“任务运行中”阻塞。"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


FEISHU_BOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = FEISHU_BOT_DIR / "data" / "topic6_pipeline.db"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"任务数据库路径（默认：{DEFAULT_DB_PATH}）",
    )
    parser.add_argument(
        "--include-oauth",
        action="store_true",
        help="同时删除本地 OAuth refresh token 记录；默认保留授权信息",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行清理",
    )
    return parser.parse_args()


def reset_pipeline_state(db_path: Path, *, include_oauth: bool = False) -> dict[str, int]:
    """删除本地任务状态；不触碰方舟 Session、远端资源或产物文件。"""
    path = db_path.expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"任务数据库不存在：{path}")

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        counts = {
            "jobs": connection.execute("SELECT COUNT(*) FROM pipeline_jobs").fetchone()[0],
            "hc_events": connection.execute(
                "SELECT COUNT(*) FROM pipeline_hc_events"
            ).fetchone()[0],
            "oauth": connection.execute("SELECT COUNT(*) FROM user_oauth").fetchone()[0],
        }
        with connection:
            connection.execute("DELETE FROM pipeline_jobs")
            if include_oauth:
                connection.execute("DELETE FROM user_oauth")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return counts
    finally:
        connection.close()


def main() -> int:
    args = _parse_args()
    if not args.yes:
        print(
            "未执行。该操作会清空 Topic6 本地任务与 HC 状态；"
            "请确认 Gateway 已停止后加 --yes。",
            file=sys.stderr,
        )
        return 2

    try:
        counts = reset_pipeline_state(args.db, include_oauth=args.include_oauth)
    except Exception as error:  # noqa: BLE001 - CLI 需输出可操作错误
        print(str(error), file=sys.stderr)
        return 1

    oauth_text = "已删除" if args.include_oauth else "保留"
    print(
        f"已清理 {counts['jobs']} 条任务、{counts['hc_events']} 条 HC 记录；"
        f"OAuth 记录：{oauth_text}（原有 {counts['oauth']} 条）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
