#!/usr/bin/env bash
# topic6 MA 资源一键创建脚本
# 依赖: curl / jq / ARK_API_KEY 环境变量
#
# 使用:
#   ./create_all.sh              # 全新创建
#   ./create_all.sh --update-agent  # 仅更新已存在的 Agent 定义
#
# 前置: 先跑 tools/pack_skills.sh + tools/upload_skills.py,让 skill_ids.json 有值
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BASE_URL="${ARK_BASE_URL:-https://ark.cn-beijing.volces.com}"
: "${ARK_API_KEY:?please export ARK_API_KEY}"
: "${HOT_TOPICS_MCP_URL:?please export HOT_TOPICS_MCP_URL(topic6 数据源 MCP endpoint)}"

require() { command -v "$1" >/dev/null || { echo "缺少 $1"; exit 1; }; }
require curl
require jq

step() { echo -e "\n===== $* ====="; }

# ---- 1. Environment ----
step "1) 创建 Environment"
env_payload="$(jq '.' "$ROOT/environment.json")"
env_resp=$(curl -sS --fail-with-body "$BASE_URL/api/v3/environments" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$env_payload")
ENVIRONMENT_ID=$(jq -er '.id' <<<"$env_resp")
echo "ENVIRONMENT_ID=$ENVIRONMENT_ID"

# ---- 2. Memory Store ----
step "2) 创建 Memory Store"
mem_payload=$(jq '{name, description}' "$ROOT/memory-store.json")
mem_resp=$(curl -sS --fail-with-body "$BASE_URL/api/v3/memory_stores" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$mem_payload")
MEMORY_STORE_ID=$(jq -er '.id' <<<"$mem_resp")
echo "MEMORY_STORE_ID=$MEMORY_STORE_ID"

# 上传初始 memory 内容(逐条)
step "3) 预置 Memory 内容"
jq -c '.memories[]' "$ROOT/memory-store.json" | while read -r mem; do
  path=$(jq -r '.path' <<<"$mem")
  src=$(jq -r '.source_file' <<<"$mem")
  src_abs="$ROOT/../$src"
  [ -f "$src_abs" ] || { echo "  [跳过] $src_abs 不存在"; continue; }
  content=$(jq -Rs '.' <"$src_abs")
  curl -sS --fail-with-body "$BASE_URL/api/v3/memory_stores/$MEMORY_STORE_ID/memories" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"path\": \"$path\", \"content\": $content}" >/dev/null
  echo "  预置 $path"
done

# ---- 4. 检查 skill_ids.json 是否已填 ----
step "4) 校验 skill_ids.json 已填充"
unfilled=$(jq -r '.skills | to_entries[] | select(.value == null) | .key' "$ROOT/skill_ids.json")
if [ -n "$unfilled" ]; then
  echo "以下 skill 尚未上传,请先跑 tools/pack_skills.sh + tools/upload_skills.py:"
  echo "$unfilled"
  exit 1
fi

# ---- 5. 创建子 Agent ----
render_agent_json() {
  local agent_file="$1"
  local sys_file="$2"
  local sys_content
  sys_content=$(jq -Rs '.' <"$ROOT/agents/$sys_file")
  # 用 skill_ids.json 替换 ${skill_id.xxx} / ${version.xxx};HOT_TOPICS_MCP_URL 走环境变量
  jq --arg system "$(cat "$ROOT/agents/$sys_file")" \
     --arg mcp_url "$HOT_TOPICS_MCP_URL" \
     --slurpfile ids "$ROOT/skill_ids.json" \
     '
     def resolve_placeholders:
       . as $agent |
       ($ids[0].skills) as $skills |
       ($agent | tojson) as $s |
       reduce ($skills | to_entries[]) as $e ($s;
         gsub("\\$\\{skill_id\\." + $e.key + "\\}"; $e.value.skill_id // "") |
         gsub("\\$\\{version\\." + $e.key + "\\}"; ($e.value.version // "") | tostring)
       )
       | gsub("\\$\\{HOT_TOPICS_MCP_URL\\}"; $mcp_url)
       | fromjson;
     resolve_placeholders
     | del(.system_file)
     | .system = $system
     ' "$ROOT/agents/$agent_file"
}

step "5) 创建子 Agent topic6-annotator"
annotator_payload=$(render_agent_json annotator.json annotator.system.md)
annotator_resp=$(curl -sS --fail-with-body "$BASE_URL/api/v3/agents" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$annotator_payload")
AGENT_ANNOTATOR_ID=$(jq -er '.id' <<<"$annotator_resp")
echo "AGENT_ANNOTATOR_ID=$AGENT_ANNOTATOR_ID"

step "6) 创建子 Agent topic6-insighter"
insighter_payload=$(render_agent_json insighter.json insighter.system.md)
insighter_resp=$(curl -sS --fail-with-body "$BASE_URL/api/v3/agents" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$insighter_payload")
AGENT_INSIGHTER_ID=$(jq -er '.id' <<<"$insighter_resp")
echo "AGENT_INSIGHTER_ID=$AGENT_INSIGHTER_ID"

# ---- 7. 创建协调器 ----
step "7) 创建协调器 topic6-coordinator"
coordinator_payload=$(render_agent_json coordinator.json coordinator.system.md \
  | jq --arg a "$AGENT_ANNOTATOR_ID" --arg i "$AGENT_INSIGHTER_ID" '
      (.. | strings) |= (
          gsub("\\$\\{agent_id.topic6-annotator\\}"; $a) |
          gsub("\\$\\{agent_id.topic6-insighter\\}"; $i)
      )
  ')
coordinator_resp=$(curl -sS --fail-with-body "$BASE_URL/api/v3/agents" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$coordinator_payload")
AGENT_COORDINATOR_ID=$(jq -er '.id' <<<"$coordinator_resp")
echo "AGENT_COORDINATOR_ID=$AGENT_COORDINATOR_ID"

# ---- 8. 汇总输出 ----
cat >"$ROOT/created_ids.json" <<EOF
{
  "environment_id": "$ENVIRONMENT_ID",
  "memory_store_id": "$MEMORY_STORE_ID",
  "agent_coordinator_id": "$AGENT_COORDINATOR_ID",
  "agent_annotator_id": "$AGENT_ANNOTATOR_ID",
  "agent_insighter_id": "$AGENT_INSIGHTER_ID"
}
EOF
echo -e "\n完成。ID 落盘: $ROOT/created_ids.json"
