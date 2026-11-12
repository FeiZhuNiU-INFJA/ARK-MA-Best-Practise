#!/usr/bin/env bash
# 用当前 Python 环境创建或原地更新 Seedance 资源；传递 --dry-run / --check / --config。
set -euo pipefail
SEEDANCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FEISHU_BOT_DIR="$(cd "$SEEDANCE_DIR/../.." && pwd)"
PYTHON_BIN="${SEEDANCE_PYTHON:-python}"
export PYTHONPATH="$FEISHU_BOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m cases.seedance.resources "$@"
