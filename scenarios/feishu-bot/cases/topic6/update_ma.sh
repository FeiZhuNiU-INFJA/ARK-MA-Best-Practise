#!/usr/bin/env bash
# Topic 6 MA 资源全量更新入口。只更新资源，不停止或重启 Gateway。
#
# ID 变化规则:
#   - 6 个 Skills:强制重新上传，ID 会变化
#   - Annotator / Insighter / Coordinator:删除同名旧 Agent 后重建，ID 会变化
#   - Environment / Memory Store:按名称原地更新，ID 不变
#   - 飞书 App / Vault:本脚本不创建或更新，ID 不变
# Coordinator 新 ID 会自动回写 config.env，因此脚本结束后必须手动重启 Gateway。
set -Eeuo pipefail

TOPIC6_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FEISHU_BOT_DIR="$(cd "$TOPIC6_DIR/../.." && pwd)"
CONFIG_FILE="${TOPIC6_CONFIG_FILE:-${ARKAGENT_HOME:-$HOME/.arkagent}/cases/topic6/config.env}"
CONDA_ENV="${TOPIC6_CONDA_ENV:-nio-ma-demo}"
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

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "缺少命令: $1" >&2
    exit 1
  }
}

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "配置文件不存在: $CONFIG_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

for name in \
  ARK_API_KEY \
  FEISHU_APP_ID \
  FEISHU_APP_SECRET \
  HOT_TOPICS_MCP_URL \
  BLUEAI_API_KEY \
  DATAHUB_ENDPOINT \
  DATAHUB_API_KEY
do
  if [[ -z "${!name:-}" ]]; then
    echo "缺少环境变量: $name（来源: $CONFIG_FILE）" >&2
    exit 1
  fi
done

for command_name in curl jq rsync zip shasum; do
  require_command "$command_name"
done

if [[ "${CONDA_DEFAULT_ENV:-}" == "$CONDA_ENV" ]]; then
  PYTHON=(python)
elif command -v conda >/dev/null 2>&1; then
  PYTHON=(conda run --no-capture-output -n "$CONDA_ENV" python)
else
  echo "当前未激活 conda 环境 '$CONDA_ENV'，且找不到 conda 命令。" >&2
  exit 1
fi

step "1/5 运行 feishu-bot 全量测试"
(
  cd "$FEISHU_BOT_DIR"
  "${PYTHON[@]}" -m pytest -q
)

step "2/5 打包全部 Topic 6 Skills"
(
  cd "$TOPIC6_DIR"
  ./tools/pack_skills.sh
)

step "3/5 强制上传全部 Topic 6 Skills"
(
  cd "$TOPIC6_DIR"
  "${PYTHON[@]}" tools/upload_skills.py --force
)

step "4/5 更新 Environment、Memory 并重建 Agents"
(
  cd "$TOPIC6_DIR"
  ./ma-resources/create_all.sh --update-env --update-memory
)

step "5/5 校验资源 ID 与 Gateway 配置"
CREATED_IDS="$TOPIC6_DIR/ma-resources/created_ids.json"
if ! jq -e 'all(.[]; type == "string" and length > 0)' "$CREATED_IDS" >/dev/null; then
  echo "资源 ID 不完整: $CREATED_IDS" >&2
  jq . "$CREATED_IDS" >&2
  exit 1
fi

for name in \
  TOPIC6_COORDINATOR_AGENT_ID \
  TOPIC6_ENVIRONMENT_ID \
  TOPIC6_MEMORY_STORE_ID
do
  if ! grep -q "^${name}=" "$CONFIG_FILE"; then
    echo "Gateway 配置缺少 $name: $CONFIG_FILE" >&2
    exit 1
  fi
done

jq . "$CREATED_IDS"
grep -E '^TOPIC6_(COORDINATOR_AGENT_ID|ENVIRONMENT_ID|MEMORY_STORE_ID)=' "$CONFIG_FILE"

cat <<EOF

Topic 6 MA 资源更新完成。
ID 变化:Skills 和 3 个 Agents 已更新为新 ID；Environment 和 Memory Store 保持原 ID。
Coordinator 新 ID 已回写 ${CONFIG_FILE}。
update_ma.sh 不会代替具体用户完成 OAuth。每位妙搭发布人首次使用前运行:
  cd "${FEISHU_BOT_DIR}"
  python cases/topic6/authorize_miaoda_user.py

Gateway 未重启。请在另一个终端手动重启:
  cd "${FEISHU_BOT_DIR}"
  python -m arkagent run --case topic6
EOF
