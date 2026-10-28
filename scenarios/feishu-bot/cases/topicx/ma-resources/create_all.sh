#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BASE="${ARK_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"
BASE="${BASE%/}"
[[ "$BASE" == */api/v3 ]] || BASE="$BASE/api/v3"
ARK_BETA_HEADER="X-Ark-Beta: agentic-2026-06-01"
: "${ARK_API_KEY:?please export ARK_API_KEY}"
command -v curl >/dev/null
command -v jq >/dev/null

api() {
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sS --fail-with-body -X "$method" "$BASE$path" \
      -H "Authorization: Bearer $ARK_API_KEY" \
      -H "$ARK_BETA_HEADER" \
      -H "Content-Type: application/json" \
      -d "$body"
  else
    curl -sS --fail-with-body -X "$method" "$BASE$path" \
      -H "Authorization: Bearer $ARK_API_KEY" \
      -H "$ARK_BETA_HEADER"
  fi
}

find_id() {
  local collection="$1" name="$2"
  api GET "/$collection?page_size=200" |
    jq -r --arg name "$name" '(.data // .items // [])[] | select(.name == $name) | .id' |
    head -1
}

ENV_NAME="$(jq -r .name "$ROOT/environment.json")"
ENVIRONMENT_ID="${TOPICX_ROUTER_ENVIRONMENT_ID:-}"
if [[ -z "$ENVIRONMENT_ID" ]]; then
  ENVIRONMENT_ID="$(find_id environments "$ENV_NAME")"
fi
if [[ -z "$ENVIRONMENT_ID" ]]; then
  ENVIRONMENT_ID="$(api POST /environments "$(jq -c . "$ROOT/environment.json")" | jq -er '.data.id // .id')"
else
  api POST "/environments/$ENVIRONMENT_ID" \
    "$(jq -c '{config: .config}' "$ROOT/environment.json")" >/dev/null
fi

ROUTER_JSON="$ROOT/agents/router.json"
ROUTER_NAME="$(jq -r .name "$ROUTER_JSON")"
MODEL_ID="${TOPICX_ROUTER_MODEL_ID:-doubao-seed-2-1-lite-ultra-vision-260920}"
SYSTEM="$(jq -Rs . <"$ROOT/agents/router.system.md")"
PAYLOAD="$(jq -c --arg model "$MODEL_ID" --argjson system "$SYSTEM" \
  '.model.id = $model | .system = $system | del(.system_file)' "$ROUTER_JSON")"
AGENT_ID="${TOPICX_ROUTER_AGENT_ID:-}"
if [[ -z "$AGENT_ID" ]]; then
  AGENT_ID="$(find_id agents "$ROUTER_NAME")"
fi
if [[ -n "$AGENT_ID" ]]; then
  VERSION="$(api GET "/agents/$AGENT_ID" | jq -er '.data.version // .version')"
  api POST "/agents/$AGENT_ID" "$(jq -c --argjson version "$VERSION" '. + {version: $version}' <<<"$PAYLOAD")" >/dev/null
else
  AGENT_ID="$(api POST /agents "$PAYLOAD" | jq -er '.data.id // .id')"
fi

printf 'TOPICX_ROUTER_ENVIRONMENT_ID=%s\nTOPICX_ROUTER_AGENT_ID=%s\n' \
  "$ENVIRONMENT_ID" "$AGENT_ID"

cat >"$ROOT/created_ids.json" <<EOF
{
  "router_environment_id": "$ENVIRONMENT_ID",
  "router_agent_id": "$AGENT_ID"
}
EOF

CASE_ENV_PATH="${ARKAGENT_HOME:-$HOME/.arkagent}/cases/topicX/config.env"
if [[ -f "$CASE_ENV_PATH" ]]; then
  TOPICX_ROUTER_ENVIRONMENT_ID="$ENVIRONMENT_ID" \
  TOPICX_ROUTER_AGENT_ID="$AGENT_ID" \
  CASE_ENV_PATH="$CASE_ENV_PATH" \
  python3 <<'PY'
import os
from pathlib import Path

updates = {
    "TOPICX_ROUTER_ENVIRONMENT_ID": os.environ["TOPICX_ROUTER_ENVIRONMENT_ID"],
    "TOPICX_ROUTER_AGENT_ID": os.environ["TOPICX_ROUTER_AGENT_ID"],
}
path = Path(os.environ["CASE_ENV_PATH"])
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
output = []
for line in lines:
    stripped = line.lstrip()
    key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
    if stripped and not stripped.startswith("#") and key in updates:
        output.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        output.append(line)
for key, value in updates.items():
    if key not in seen:
        output.append(f"{key}={value}")
path.write_text("\n".join(output) + "\n", encoding="utf-8")
PY
  chmod 600 "$CASE_ENV_PATH"
  echo "Router ID 已回写: $CASE_ENV_PATH"
else
  echo "配置文件不存在，未回写 Router ID: $CASE_ENV_PATH" >&2
fi
