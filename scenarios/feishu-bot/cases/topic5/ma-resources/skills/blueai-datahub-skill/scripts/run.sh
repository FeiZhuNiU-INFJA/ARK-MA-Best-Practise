#!/bin/bash
# DataHub API SDK runner - 使用 skill 目录内的虚拟环境
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$SKILL_DIR/.venv/bin/python3"

# 保存用户的原始工作目录，供 Python 脚本使用
export DATAHUB_ORIGINAL_CWD="$(pwd)"

# 检查虚拟环境是否存在
if [ ! -f "$VENV_PYTHON" ]; then
    echo "初始化虚拟环境..."
    python3 -m venv "$SKILL_DIR/.venv"
    "$SKILL_DIR/.venv/bin/pip" install -q -r "$SKILL_DIR/requirements.txt"
fi

# SKILL_DIR 支持 `from sdk import ...`；sdk 子目录支持 `from client import ...`
export PYTHONPATH="$SKILL_DIR:$SKILL_DIR/sdk:${PYTHONPATH:-}"

cd "$SKILL_DIR"
exec "$VENV_PYTHON" "$@"
