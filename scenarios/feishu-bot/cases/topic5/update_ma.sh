#!/usr/bin/env bash
# Topic 5 MA 资源全量更新入口。只更新资源，不停止或重启 Gateway。
#
# ID 变化规则:
#   - 6 个 Skills:强制重新上传，ID 会变化
#   - Validator / Coordinator:删除同名旧 Agent 后重建，ID 会变化
#   - Environment / Memory Store:按名称原地更新，ID 不变
#   - 飞书 App / Vault:本脚本不创建或更新，ID 不变
# Coordinator 新 ID 会自动回写 config.env，因此脚本结束后必须手动重启 Gateway。
#
# 与 topic6 的差异: topic5 走飞书文档交付,无妙搭 Miaoda OAuth / artifact-sync;
# 两个 case 共用 conda env=topicX;测试跑 topic5 自带单测集。
set -Eeuo pipefail

TOPIC5_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FEISHU_BOT_DIR="$(cd "$TOPIC5_DIR/../.." && pwd)"
CONFIG_FILE="${TOPIC5_CONFIG_FILE:-${ARKAGENT_HOME:-$HOME/.arkagent}/cases/topicX/config.env}"
CONDA_ENV="${TOPIC5_CONDA_ENV:-topicX}"
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

# set -u 下安全读取环境变量:避免使用 ${!name:-}(zsh/dash 会拒绝该表达式)。
# 先 eval 取值再判空,Bash 3.2.57 / zsh / dash 均可处理。
for name in \
  ARK_API_KEY \
  FEISHU_APP_ID \
  FEISHU_APP_SECRET
do
  eval "value=\${$name:-}"
  if [[ -z "$value" ]]; then
    echo "缺少环境变量: $name（来源: $CONFIG_FILE）" >&2
    exit 1
  fi
done

# 外部数据/封面凭据缺失不阻断资源装配,但运行到对应节点会 fail-closed。
for name in \
  DATAHUB_API_KEY \
  SSJH_API_TOKEN \
  REPORT_COVER_API_KEY
do
  eval "value=\${$name:-}"
  if [[ -z "$value" ]]; then
    echo "提示: 环境变量 $name 未设置,相关节点将走降级路径" >&2
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

# 单测必须是 hermetic：本脚本已 source 生产 config.env，若直接继承凭据，
# test_handlers 的真实 DAG 契约遍历会启用 SSJH/DataHub/Ark/飞书等在线分支。
# 只清理测试子进程；父 shell 的配置保持不变，后续上传与资源创建仍使用真实凭据。
ISOLATED_TEST_ENV_KEYS=(
  ARK_API_KEY
  OPENAI_API_KEY
  DATAHUB_API_KEY
  SSJH_API_TOKEN
  FEISHU_APP_ID
  FEISHU_APP_SECRET
  FEISHU_USER_ACCESS_TOKEN
  FEISHU_DELIVERY_OWNER_OPEN_ID
  REPORT_COVER_API_KEY
  BLUEAI_API_KEY
  LARKSUITE_CLI_USER_ACCESS_TOKEN
  TOPIC5_ACTIVITY_XLSX_PATH
  TOPIC5_SESSION_UPLOADS_DIR
)

run_isolated_test_python() {
  local env_args=()
  local name
  for name in "${ISOLATED_TEST_ENV_KEYS[@]}"; do
    env_args+=(-u "$name")
  done
  env "${env_args[@]}" "${PYTHON[@]}" "$@"
}

step "1/5 运行 topic5 单测(pipeline-orchestrator + host stdlib)"
(
  cd "$TOPIC5_DIR/ma-resources/skills/pipeline-orchestrator"
  run_isolated_test_python -m unittest discover -s tests -p 'test_*.py'
)
(
  cd "$TOPIC5_DIR"
  run_isolated_test_python -m unittest discover -s tests -p 'test_*.py'
)

if [[ "${TOPIC5_TEST_ONLY:-0}" == "1" ]]; then
  echo
  echo "TOPIC5_TEST_ONLY=1：测试通过，跳过打包、上传和 MA 资源更新。"
  exit 0
fi

step "2/5 打包全部 Topic 5 Skills"
(
  cd "$TOPIC5_DIR"
  ./tools/pack_skills.sh
)

step "3/5 强制上传全部 Topic 5 Skills"
(
  cd "$TOPIC5_DIR"
  "${PYTHON[@]}" tools/upload_skills.py --force
)

step "4/5 更新 Environment、Memory 并重建 Agents"
(
  cd "$TOPIC5_DIR"
  ./ma-resources/create_all.sh --update-env --update-memory
)

step "5/5 校验资源 ID 与 Gateway 配置"
CREATED_IDS="$TOPIC5_DIR/ma-resources/created_ids.json"
if ! jq -e 'all(.[]; type == "string" and length > 0)' "$CREATED_IDS" >/dev/null; then
  echo "资源 ID 不完整: $CREATED_IDS" >&2
  jq . "$CREATED_IDS" >&2
  exit 1
fi

for name in \
  TOPIC5_COORDINATOR_AGENT_ID \
  TOPIC5_ENVIRONMENT_ID \
  TOPIC5_MEMORY_STORE_ID
do
  if ! grep -q "^${name}=" "$CONFIG_FILE"; then
    echo "Gateway 配置缺少 $name: $CONFIG_FILE" >&2
    exit 1
  fi
done

jq . "$CREATED_IDS"
grep -E '^TOPIC5_(COORDINATOR_AGENT_ID|ENVIRONMENT_ID|MEMORY_STORE_ID)=' "$CONFIG_FILE"

cat <<EOF

Topic 5 MA 资源更新完成。
ID 变化:Skills 和 2 个 Agents 已更新为新 ID；Environment 和 Memory Store 保持原 ID。
Coordinator 新 ID 已回写 ${CONFIG_FILE}。

Gateway 未重启。请在另一个终端手动重启:
  cd "${FEISHU_BOT_DIR}"
  python -m arkagent run --case topicx
EOF
