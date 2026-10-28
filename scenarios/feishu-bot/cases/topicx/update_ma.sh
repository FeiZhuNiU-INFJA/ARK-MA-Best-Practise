#!/usr/bin/env bash
# Topicx 统一资源更新入口。复用 Topic5/Topic6 各自资源定义，并更新轻量 Router。
set -Eeuo pipefail

TOPICX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FEISHU_BOT_DIR="$(cd "$TOPICX_DIR/../.." && pwd)"
CONFIG_FILE="${TOPICX_CONFIG_FILE:-${ARKAGENT_HOME:-$HOME/.arkagent}/cases/topicX/config.env}"
CONDA_ENV="${TOPICX_CONDA_ENV:-topicX}"
CURRENT_STEP="初始化"

on_error() {
  local exit_code=$?
  echo >&2
  echo "[失败] ${CURRENT_STEP}（exit=${exit_code}）" >&2
  exit "$exit_code"
}
trap on_error ERR

step() {
  CURRENT_STEP="$1"
  echo
  echo "===== $1 ====="
}

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "配置文件不存在: $CONFIG_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

for name in ARK_API_KEY FEISHU_APP_ID FEISHU_APP_SECRET; do
  eval "value=\${$name:-}"
  if [[ -z "$value" ]]; then
    echo "缺少环境变量: $name（来源: $CONFIG_FILE）" >&2
    exit 1
  fi
done

step "1/5 更新 Topic5 业务资源"
TOPIC5_CONFIG_FILE="$CONFIG_FILE" \
TOPIC5_CONDA_ENV="$CONDA_ENV" \
  "$TOPICX_DIR/../topic5/update_ma.sh"

step "2/5 更新 Topic6 业务资源并清理旧 Insighter"
TOPIC6_CONFIG_FILE="$CONFIG_FILE" \
TOPIC6_CONDA_ENV="$CONDA_ENV" \
  "$TOPICX_DIR/../topic6/update_ma.sh"

# 两个业务脚本已完成 Topic5、Topic6 和 feishu-bot 全量测试。
step "3/5 创建或原地更新 Router Environment 与 Agent"
(
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
  cd "$TOPICX_DIR"
  ./ma-resources/create_all.sh
)

step "4/5 校验统一配置"
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a
for name in \
  TOPICX_ROUTER_AGENT_ID \
  TOPICX_ROUTER_ENVIRONMENT_ID \
  TOPIC5_COORDINATOR_AGENT_ID \
  TOPIC5_ENVIRONMENT_ID \
  TOPIC5_MEMORY_STORE_ID \
  TOPIC6_COORDINATOR_AGENT_ID \
  TOPIC6_ENVIRONMENT_ID \
  TOPIC6_MEMORY_STORE_ID
do
  eval "value=\${$name:-}"
  if [[ -z "$value" || "$value" == *'${'* ]]; then
    echo "统一配置缺失或仍含占位符: $name" >&2
    exit 1
  fi
done

PYTHONPATH="$FEISHU_BOT_DIR" conda run --no-capture-output -n "$CONDA_ENV" \
  python -c 'from cases.topicx.config import load_case_config; load_case_config()'

step "5/5 检查 Router 资源与脚本"
bash -n "$TOPICX_DIR/update_ma.sh" "$TOPICX_DIR/ma-resources/create_all.sh"
jq empty \
  "$TOPICX_DIR/ma-resources/environment.json" \
  "$TOPICX_DIR/ma-resources/agents/router.json"
if grep -RInE '(cli_|ou_|sk-|tvly-)[A-Za-z0-9_-]{8,}' \
  "$TOPICX_DIR/ma-resources" "$TOPICX_DIR/README.md" \
  "$TOPICX_DIR/TOPICX_DEBUG_QUICKSTART.md"; then
  echo "Topicx 入库资源疑似包含真实敏感凭据或身份 ID" >&2
  exit 1
fi

cat <<EOF

Topicx 统一资源更新完成。
Router 与 Topic5/Topic6 资源 ID 已写入 ${CONFIG_FILE}。

Gateway 未重启。请在另一个终端手动启动:
  cd "${FEISHU_BOT_DIR}"
  conda run --no-capture-output -n "${CONDA_ENV}" python -m arkagent run --case topicx
EOF
