#!/usr/bin/env bash
# topic6 MA 资源一键创建脚本
# 依赖: curl / jq / ARK_API_KEY 环境变量
#
# 幂等策略:
#   - Environment / MemoryStore 按 name 查找,存在则复用(重建代价大且没意义)
#   - 3 个 Agent 每次都重建(prompt/skill 更新是主要驱动),同名旧 Agent 先删后建
#
# 使用:
#   ./create_all.sh                    # 幂等运行,首次全建、后续只重建 Agent
#   ./create_all.sh --update-memory    # 已存在的 MemoryStore 也强制覆盖 memories(用于磁盘 md 改动后同步)
#   ./create_all.sh --update-env       # 已存在的 Environment 也强制刷新 config(用于新增/改动沙箱环境变量)
#
# 前置: 先跑 tools/pack_skills.sh + tools/upload_skills.py,让 skill_ids.json 有值
set -euo pipefail

UPDATE_MEMORY=0
UPDATE_ENV=0
for arg in "$@"; do
  case "$arg" in
    --update-memory) UPDATE_MEMORY=1 ;;
    --update-env) UPDATE_ENV=1 ;;
    -h|--help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

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

# 带 Bearer + beta header 的 GET,失败不重试直接返回空,便于按 name 查找时"没找到"和"报错"都能兜住
ark_get() {
  local path="$1"
  curl -sS "$BASE_URL$path" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "$ARK_BETA_HEADER" 2>/dev/null || echo '{}'
}

# 幂等辅助:按 name 在指定 collection 里查找,找到返回 id 到 stdout,找不到 stdout 为空。
# 兼容 list 响应可能是 {"data":[...]} 也可能裸数组或 {"items":[...]}
find_by_name() {
  local collection="$1" name="$2"
  local body
  body=$(ark_get "/$collection?page_size=200")
  jq -r --arg n "$name" '
    (.data // .items // .) as $arr
    | if ($arr | type) == "array" then
        ($arr[] | select(.name == $n) | .id) // empty
      else empty end
  ' <<<"$body" | head -1
}

# 带 beta header 的 DELETE
ark_delete() {
  local path="$1"
  curl -sS -X DELETE "$BASE_URL$path" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    -H "$ARK_BETA_HEADER" >/dev/null || true
}

# ---- 1. Environment ----
step "1) 创建 Environment(幂等:按 name 查找,存在复用;--update-env 时原地更新 config)"
env_name=$(jq -r '.name' "$ROOT/environment.json")
ENVIRONMENT_ID=$(find_by_name "environments" "$env_name" || true)
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
if [ -n "$ENVIRONMENT_ID" ]; then
  if [ "$UPDATE_ENV" = "1" ]; then
    # 原地更新 config;新配置只对后续 Session 生效。POST /environments/{id} body={"config": ...}
    update_payload="$(jq '{config: .config}' <<<"$env_payload")"
    ark_post "/environments/$ENVIRONMENT_ID" "$update_payload" >/dev/null
    echo "已更新: $env_name -> $ENVIRONMENT_ID(--update-env,config 已刷新)"
  else
    echo "已存在: $env_name -> $ENVIRONMENT_ID(复用;如需刷新沙箱环境变量请加 --update-env)"
  fi
else
  env_resp=$(ark_post "/environments" "$env_payload")
  ENVIRONMENT_ID=$(extract_id "$env_resp")
  echo "已创建: $env_name -> $ENVIRONMENT_ID"
fi

# ---- 2. Memory Store ----
step "2) 创建 Memory Store(幂等:按 name 查找,存在复用)"
mem_name=$(jq -r '.name' "$ROOT/memory-store.json")
MEMORY_STORE_ID=$(find_by_name "memory_stores" "$mem_name" || true)
if [ -n "$MEMORY_STORE_ID" ]; then
  if [ "$UPDATE_MEMORY" = "1" ]; then
    echo "已存在: $mem_name -> $MEMORY_STORE_ID(--update-memory,将强制覆盖 memories)"
  else
    echo "已存在: $mem_name -> $MEMORY_STORE_ID(复用,跳过预置内容避免重复覆盖)"
  fi
  MEMORY_ALREADY_EXISTS=1
else
  mem_payload=$(jq '{name, description}' "$ROOT/memory-store.json")
  mem_resp=$(ark_post "/memory_stores" "$mem_payload")
  MEMORY_STORE_ID=$(extract_id "$mem_resp")
  echo "已创建: $mem_name -> $MEMORY_STORE_ID"
  MEMORY_ALREADY_EXISTS=0
fi

# 上传初始 memory 内容(首次创建时执行;复用时默认跳过避免覆盖用户后来的修改,加 --update-memory 才强制覆盖)
if [ "$MEMORY_ALREADY_EXISTS" = "0" ] || [ "$UPDATE_MEMORY" = "1" ]; then
  if [ "$UPDATE_MEMORY" = "1" ] && [ "$MEMORY_ALREADY_EXISTS" = "1" ]; then
    step "3) 覆盖 Memory 内容(--update-memory)"
  else
    step "3) 预置 Memory 内容"
  fi
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
    echo "  写入 $path"
  done
else
  step "3) 预置 Memory 内容(已存在,跳过;加 --update-memory 可强制覆盖)"
fi

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

# 幂等辅助:按 name 找到同名 Agent 就先删(方舟 name 唯一)
delete_agent_if_exists() {
  local name="$1"
  local aid
  aid=$(find_by_name "agents" "$name" || true)
  if [ -n "$aid" ]; then
    echo "  发现同名旧 Agent: $name -> $aid,先删除以便重建"
    ark_delete "/agents/$aid"
  fi
}

step "5) 创建子 Agent topic6-annotator"
delete_agent_if_exists "topic6-annotator"
annotator_payload=$(render_agent_json annotator.json annotator.system.md)
annotator_resp=$(ark_post "/agents" "$annotator_payload")
AGENT_ANNOTATOR_ID=$(extract_id "$annotator_resp")
echo "AGENT_ANNOTATOR_ID=$AGENT_ANNOTATOR_ID"

step "6) 创建子 Agent topic6-insighter"
delete_agent_if_exists "topic6-insighter"
insighter_payload=$(render_agent_json insighter.json insighter.system.md)
insighter_resp=$(ark_post "/agents" "$insighter_payload")
AGENT_INSIGHTER_ID=$(extract_id "$insighter_resp")
echo "AGENT_INSIGHTER_ID=$AGENT_INSIGHTER_ID"

# ---- 7. 创建协调器 ----
step "7) 创建协调器 topic6-coordinator"
delete_agent_if_exists "topic6-coordinator"
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

# ---- 9. 回写 gateway 侧 config.env ----
# 子 Agent 每次都"先删旧再建新",AGENT_ID 会漂移。这里把 coordinator/env/memory
# 三个 gateway 必读的 ID 同步到 ~/.arkagent/cases/topic6/config.env,免得用户
# 重跑本脚本后 gateway 还打着旧 ID(旧 Agent 已被删,调用会 404)。
CASE_ENV_PATH="${ARKAGENT_HOME:-$HOME/.arkagent}/cases/topic6/config.env"
if [ -f "$CASE_ENV_PATH" ]; then
  step "9) 回写 gateway config: $CASE_ENV_PATH"
  TOPIC6_COORDINATOR_AGENT_ID="$AGENT_COORDINATOR_ID" \
  TOPIC6_ENVIRONMENT_ID="$ENVIRONMENT_ID" \
  TOPIC6_MEMORY_STORE_ID="$MEMORY_STORE_ID" \
  CASE_ENV_PATH="$CASE_ENV_PATH" \
  python3 <<'PY'
import os
from pathlib import Path

updates = {
    "TOPIC6_COORDINATOR_AGENT_ID": os.environ["TOPIC6_COORDINATOR_AGENT_ID"],
    "TOPIC6_ENVIRONMENT_ID": os.environ["TOPIC6_ENVIRONMENT_ID"],
    "TOPIC6_MEMORY_STORE_ID": os.environ["TOPIC6_MEMORY_STORE_ID"],
}
path = Path(os.environ["CASE_ENV_PATH"])
lines = path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
out: list[str] = []
for line in lines:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        out.append(line)
        continue
    key = stripped.split("=", 1)[0].strip()
    if key in updates:
        out.append(f'{key}="{updates[key]}"')
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f'{key}="{value}"')
path.write_text("\n".join(out) + "\n", encoding="utf-8")
path.chmod(0o600)
for key, value in updates.items():
    print(f"  {key} -> {value}")
PY
  echo "已同步至 config.env(重启 gateway 后生效)"
else
  echo -e "\n⚠️  未找到 $CASE_ENV_PATH,跳过回写。"
  echo "    首次部署请手动:"
  echo "      mkdir -p \"\$(dirname $CASE_ENV_PATH)\""
  echo "      cp env.example \"$CASE_ENV_PATH\""
  echo "    然后再重跑本脚本让 ID 自动落库。"
fi
