#!/usr/bin/env python3
"""feishu_bot_permissions.md 本地一致性校验（self-contained, 不联网）。

校验三件事：
  1. 文档覆盖 register_app.mjs 里声明的全部 tenant scope（防止改了注册脚本却漏更文档）。
  2. 关键权限错误码在文档里有说明。
  3. 四个官方来源 URL 都在文末列出。

用法::

    python3 check_doc.py            # 用内置默认路径
    python3 check_doc.py --doc <path> --register <path>

设计：仅依赖 Python 标准库；路径基于脚本位置自适配，换机器也能跑。
退出码 0 = 全部通过；1 = 有缺失（打印清单）。
"""
from __future__ import annotations

import argparse
import os
import re

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# common/skills/feishu-bot-perms-sync/ -> common/ -> 仓库根
_COMMON_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_COMMON_ROOT, ".."))

DEFAULT_DOC = os.path.join(_COMMON_ROOT, "docs", "feishu_bot_permissions.md")
DEFAULT_REGISTER = os.path.join(
    _REPO_ROOT, "scenarios", "feishu-bot", "node-helper", "register_app.mjs"
)

# 权限相关的关键错误码：文档应至少解释这些。
REQUIRED_ERROR_CODES = ["230027", "230002", "234004", "234009"]

# 文末应列出的官方来源 URL 片段。
REQUIRED_SOURCE_FRAGMENTS = [
    "im-v1/message/list",
    "im-v1/message/get-2",
    "im-v1/message/create",
    "add-bot-to-external-group",
]


def parse_declared_scopes(register_path: str) -> list[str]:
    """从 register_app.mjs 的 scopes.tenant:[...] 里抽出声明的权限 key。"""
    with open(register_path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"tenant\s*:\s*\[(.*?)\]", src, re.DOTALL)
    if not m:
        raise RuntimeError(f"未在 {register_path} 找到 scopes.tenant 数组")
    return re.findall(r'"([^"]+)"', m.group(1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验飞书 bot 权限文档一致性")
    parser.add_argument("--doc", default=DEFAULT_DOC, help="权限文档路径")
    parser.add_argument("--register", default=DEFAULT_REGISTER, help="register_app.mjs 路径")
    args = parser.parse_args(argv)

    if not os.path.exists(args.doc):
        print(f"[error] 找不到文档：{args.doc}")
        return 1

    with open(args.doc, encoding="utf-8") as fh:
        doc = fh.read()

    problems: list[str] = []

    # 1. scope 覆盖
    try:
        scopes = parse_declared_scopes(args.register)
    except (OSError, RuntimeError) as exc:
        print(f"[warn] 无法解析注册脚本 scope，跳过该项：{exc}")
        scopes = []
    for scope in scopes:
        if scope not in doc:
            problems.append(f"文档未覆盖已声明的 scope：{scope}")
    if scopes:
        print(f"注册脚本声明 {len(scopes)} 个 scope：{', '.join(scopes)}")

    # 2. 错误码
    for code in REQUIRED_ERROR_CODES:
        if code not in doc:
            problems.append(f"文档缺少关键错误码说明：{code}")

    # 3. 来源 URL
    for frag in REQUIRED_SOURCE_FRAGMENTS:
        if frag not in doc:
            problems.append(f"文末缺少来源链接：包含 {frag} 的 URL")

    if problems:
        print("\n发现问题：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\n[ok] 权限文档一致性校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
