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
_RAW_BASE="${ARK_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"
_RAW_BASE="${_RAW_BASE%/}"
if [[ "$_RAW_BASE" != */api/v3 ]]; then
  BASE_URL="${_RAW_BASE}/api/v3"
else
  BASE_URL="$_RAW_BASE"
fi
# 方舟 MA 属 agentic beta 面,skills/environments/agents/sessions/memory_stores 都要求这个 header,不带就 404
ARK_BETA_HEADER="X-Ark-Beta: agentic-2026-06-01"
: "${ARK_API_KEY:?please export ARK_API_KEY}"
: "${HOT_TOPICS_MCP_URL:?please export HOT_TOPICS_MCP_URL(topic6 数据源 MCP endpoint)}"

require() { command -v "$1" >/dev/null || { echo "缺少 $1"; exit 1; }; }
require curl
require jq

step() { echo -e "\n===== $* ====="; }

# 统一封装:带 Bearer + beta header + Content-Type 的 POST。
# 失败时把 http 状态和 response body 都打到 stderr,便于定位 400 InvalidParameter 这类问题。
ark_post() {
  local path="$1" body="$2"
  local tmp; tmp=$(mktemp)
  local code
  code=$(curl -sS -o "$tmp" -w "%{http_code}" "$BASE_URL$path" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "$ARK_BETA_HEADER" \
    -H "Content-Type: application/json" \
    -d "$body")
  if [ "$code" -ge 400 ]; then
    echo "  [HTTP $code] $BASE_URL$path" >&2
    cat "$tmp" >&2
    echo >&2
    rm -f "$tmp"
    return 1
  fi
  cat "$tmp"
  rm -f "$tmp"
}

# 方舟响应有时包 data 壳、有时裸露顶层,统一用 .data.id // .id 兼容
extract_id() { jq -er '.data.id // .id' <<<"$1"; }

# ---- 1. Environment ----
step "1) 创建 Environment"
# environment.json 里 config.env 用 ${VAR} 字面占位,先展开一次。
# 方舟不做二次插值,不展开就把 "${DATAHUB_ENDPOINT}" 死字符串灌进沙箱。
# macOS 默认不带 envsubst,改用 jq 遍历字符串值做 ${VAR} 替换(未 export 的变量替成空串,与 envsubst 行为一致)。
env_payload="$(
  jq '
    def subst:
      if type == "string" then
        gsub("\\$\\{(?<name>[A-Z_][A-Z0-9_]*)\\}"; env[.name] // "")
      elif type == "object" then with_entries(.value |= subst)
      elif type == "array"  then map(subst)
      else . end;
    subst
  ' "$ROOT/environment.json"
)"
env_resp=$(ark_post "/environments" "$env_payload")
ENVIRONMENT_ID=$(extract_id "$env_resp")
echo "ENVIRONMENT_ID=$ENVIRONMENT_ID"

# ---- 2. Memory Store ----
step "2) 创建 Memory Store"
mem_payload=$(jq '{name, description}' "$ROOT/memory-store.json")
mem_resp=$(ark_post "/memory_stores" "$mem_payload")
MEMORY_STORE_ID=$(extract_id "$mem_resp")
echo "MEMORY_STORE_ID=$MEMORY_STORE_ID"

# 上传初始 memory 内容(逐条)
step "3) 预置 Memory 内容"
jq -c '.memories[]' "$ROOT/memory-store.json" | while read -r mem; do
  path=$(jq -r '.path' <<<"$mem")
  # 方舟要求 memory path 必须以 / 开头,漏斜杠会 400 InvalidParameter
  [[ "$path" == /* ]] || path="/$path"
  src=$(jq -r '.source_file' <<<"$mem")
  src_abs="$ROOT/../$src"
  [ -f "$src_abs" ] || { echo "  [跳过] $src_abs 不存在"; continue; }
  content=$(jq -Rs '.' <"$src_abs")
  ark_post "/memory_stores/$MEMORY_STORE_ID/memories" \
    "{\"path\": \"$path\", \"content\": $content}" >/dev/null
  echo "  预置 $path"
done

# ---- 4. 检查 skill_ids.json 是否已填 ----
step "4) 校验 skill_ids.json 已填充"
# 该文件已入 .gitignore(每人/每环境独立),缺就从 .example 复制骨架,提示先跑 upload_skills.py
if [ ! -f "$ROOT/skill_ids.json" ]; then
  if [ -f "$ROOT/skill_ids.json.example" ]; then
    cp "$ROOT/skill_ids.json.example" "$ROOT/skill_ids.json"
    echo "  从 skill_ids.json.example 生成 skill_ids.json 骨架"
  else
    echo "缺少 skill_ids.json 且没有 example 模板,无法继续"
    exit 1
  fi
fi
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
annotator_resp=$(ark_post "/agents" "$annotator_payload")
AGENT_ANNOTATOR_ID=$(extract_id "$annotator_resp")
echo "AGENT_ANNOTATOR_ID=$AGENT_ANNOTATOR_ID"

step "6) 创建子 Agent topic6-insighter"
insighter_payload=$(render_agent_json insighter.json insighter.system.md)
insighter_resp=$(ark_post "/agents" "$insighter_payload")
AGENT_INSIGHTER_ID=$(extract_id "$insighter_resp")
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
coordinator_resp=$(ark_post "/agents" "$coordinator_payload")
AGENT_COORDINATOR_ID=$(extract_id "$coordinator_resp")
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
