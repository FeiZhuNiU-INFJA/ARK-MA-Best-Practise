#!/usr/bin/env bash
# 把 topic5 各 skill 目录打包为 zip,校验 MA 上传约束:
#   - 上传的 zip <= 30 MiB
#   - 解压后单文件 <= 30 MiB、总大小 <= 120 MiB
#   - 文件数 <= 500
#   - 所有文件位于同一顶层目录,且其下直接包含唯一 SKILL.md
#
# 产物: tools/out/*.zip + tools/out/*.zip.sha256
#
# 使用: ./pack_skills.sh
#
# 硬约束: 统一从 ma-resources/skills/ 读取源码,与客户原始目录完全解耦。
# 排除 node_modules(echarts 依赖仅在 MA 沙箱 setup_script 安装)、__pycache__、Outputs、
# .env/config.json(凭据不入包)。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/out"
mkdir -p "$OUT_DIR"

# MA 版 skill 目录 → zip 名 → 打包时的顶层目录名(zip 里 SKILL.md 需在顶层直接子级)
SKILLS=(
  "ma-resources/skills/pipeline-orchestrator|pipeline-orchestrator.zip|pipeline-orchestrator"
  "ma-resources/skills/SSJH-Social-Stat|SSJH-Social-Stat.zip|ssjh-social-stat"
  "ma-resources/skills/blueai-datahub-skill|blueai-datahub-skill.zip|blueai-datahub-skill"
  "ma-resources/skills/blueai-yuqing-echarts-skill-new|blueai-yuqing-echarts-skill-new.zip|blueai-yuqing-echarts-skill-new"
  "ma-resources/skills/report-cover-skill|report-cover-skill.zip|report-cover-skill"
  "ma-resources/skills/topic5-validator|topic5-validator.zip|topic5-validator"
)

MAX_UPLOAD=$((30 * 1024 * 1024))
MAX_FILE=$((30 * 1024 * 1024))
MAX_UNPACKED=$((120 * 1024 * 1024))
MAX_COUNT=500

check_skill_md() {
  local src="$1"
  local count
  count=$(find "$src" -type f -name SKILL.md | wc -l | tr -d ' ')
  if [ "$count" -ne 1 ]; then
    echo "  [错误] $src 下 SKILL.md 数量=$count,必须恰好 1 个"
    return 1
  fi
  if [ ! -f "$src/SKILL.md" ]; then
    echo "  [错误] SKILL.md 必须位于统一顶层目录的直接子级: $src/SKILL.md"
    return 1
  fi
}

file_size() {
  stat -f%z "$1" 2>/dev/null || stat -c%s "$1"
}

pack_one() {
  local src_rel="$1"
  local zip_name="$2"
  local top_name="$3"
  local src="$ROOT/$src_rel"

  [ -d "$src" ] || { echo "  [跳过] $src 不存在"; return 0; }

  echo "=== $src_rel → $zip_name ==="

  local skill_name
  skill_name="$(awk '/^name:[[:space:]]*/ { sub(/^name:[[:space:]]*/, ""); print; exit }' "$src/SKILL.md")"
  if [[ -z "$skill_name" ]]; then
    skill_name="$(awk '/^#[[:space:]]+/ { sub(/^#[[:space:]]+/, ""); print; exit }' "$src/SKILL.md")"
  fi
  if [[ ! "$skill_name" =~ ^[a-z0-9-]{1,64}$ ]]; then
    echo "  [错误] SKILL.md name 不合法: '$skill_name'（必须匹配 ^[a-z0-9-]{1,64}\$）" >&2
    return 1
  fi

  if [[ "$src_rel" == "ma-resources/skills/blueai-yuqing-echarts-skill-new" ]]; then
    local font_dir="$src/assets/fonts"
    local font_name
    for font_name in SourceHanSansSC-Regular.otf SourceHanSansSC-Bold.otf; do
      if [[ ! -s "$font_dir/$font_name" ]]; then
        echo "  [错误] 缺少 ECharts 中文字体: $font_dir/$font_name" >&2
        echo "  请按 $font_dir/README.md 准备字体后重试。" >&2
        return 1
      fi
    done
  fi

  # 用 stage 目录构造统一顶层
  local stage
  stage=$(mktemp -d)
  # 排除常见的构建产物 / 缓存 / node_modules / 凭据 / 本地产出
  rsync -a --exclude='__pycache__' --exclude='.DS_Store' --exclude='*.pyc' \
        --exclude='node_modules' --exclude='.git' \
        --exclude='.env' --exclude='config.json' --exclude='Outputs' \
        "$src/" "$stage/$top_name/"

  local staged_root="$stage/$top_name"
  if [[ "$top_name" == "pipeline-orchestrator" ]]; then
    local canonical_dag="$ROOT/ma-resources/io/contracts/pipeline.v0.2.yaml"
    if [[ ! -s "$canonical_dag" ]]; then
      echo "  [错误] 权威 DAG 不存在或为空: $canonical_dag" >&2
      rm -rf "$stage"
      return 1
    fi
    mkdir -p "$staged_root/contracts"
    cp "$canonical_dag" "$staged_root/contracts/pipeline.v0.2.yaml"
    cmp -s "$canonical_dag" "$staged_root/contracts/pipeline.v0.2.yaml" || {
      echo "  [错误] 打包 DAG 与权威源不一致" >&2
      rm -rf "$stage"
      return 1
    }
    mkdir -p "$staged_root/runtime_resources/agents"
    cp "$ROOT/ma-resources/agents/coordinator.json" \
       "$ROOT/ma-resources/agents/coordinator.system.md" \
       "$ROOT/ma-resources/agents/validator.json" \
       "$ROOT/ma-resources/agents/validator.system.md" \
       "$staged_root/runtime_resources/agents/"
    cp "$ROOT/ma-resources/environment.json" \
       "$staged_root/runtime_resources/environment.json"
  fi
  check_skill_md "$staged_root"

  local file_count
  file_count=$(find "$staged_root" -type f | wc -l | tr -d ' ')
  if [ "$file_count" -gt "$MAX_COUNT" ]; then
    echo "  [错误] 文件数=$file_count > $MAX_COUNT"; rm -rf "$stage"; return 1
  fi

  local unpacked_size=0 size path
  local oversize=()
  while IFS= read -r -d '' path; do
    size=$(file_size "$path")
    unpacked_size=$((unpacked_size + size))
    if [ "$size" -gt "$MAX_FILE" ] && [ "${#oversize[@]}" -lt 3 ]; then
      oversize+=("$path")
    fi
  done < <(find "$staged_root" -type f -print0)
  if [ "${#oversize[@]}" -gt 0 ]; then
    printf '  [错误] 单文件超过 30 MiB:\n  %s\n' "${oversize[@]}"
    rm -rf "$stage"
    return 1
  fi
  if [ "$unpacked_size" -gt "$MAX_UNPACKED" ]; then
    echo "  [错误] 解压后文件总大小=$unpacked_size > 120 MiB"
    rm -rf "$stage"
    return 1
  fi

  local out="$OUT_DIR/$zip_name"
  # zip 默认会更新已有归档，目录改名/文件删除后旧 entry 会残留；必须原子重建。
  rm -f "$out" "$out.sha256"
  (cd "$stage" && zip -qr "$out" "$top_name")
  rm -rf "$stage"

  # zip 大小校验
  local sz
  sz=$(stat -f%z "$out" 2>/dev/null || stat -c%s "$out")
  if [ "$sz" -gt "$MAX_UPLOAD" ]; then
    echo "  [错误] zip 大小=$sz > 30 MiB"; return 1
  fi

  shasum -a 256 "$out" | awk '{print $1}' >"$out.sha256"
  echo "  ok · zip=$sz bytes · unpacked=$unpacked_size bytes · files=$file_count · sha256=$(cat "$out.sha256")"
}

for entry in "${SKILLS[@]}"; do
  IFS='|' read -r src_rel zip_name top_name <<<"$entry"
  pack_one "$src_rel" "$zip_name" "$top_name"
done

echo -e "\n所有 skill 打包完成 → $OUT_DIR"
ls -lh "$OUT_DIR"
