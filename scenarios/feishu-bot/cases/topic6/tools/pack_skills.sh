#!/usr/bin/env bash
# 把 topic6 各 skill 目录打包为 zip,校验 MA 上传约束:
#   - 上传的 zip <= 30 MiB
#   - 解压后单文件 <= 30 MiB、总大小 <= 120 MiB
#   - 文件数 <= 500
#   - 所有文件位于同一顶层目录,且其下直接包含唯一 SKILL.md
#
# 产物: tools/out/*.zip + tools/out/*.zip.sha256
#
# 使用: ./pack_skills.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/out"
mkdir -p "$OUT_DIR"

# MA 版 skill 目录 → zip 名 → 打包时的顶层目录名(zip 里 SKILL.md 需在顶层直接子级)
# 迁移完成后统一从 ma-resources/skills/ 打包,不再从 assets_from_customer 引用
SKILLS=(
  "ma-resources/skills/topic6-fetch-normalize|topic6-fetch-normalize.zip|topic6-fetch-normalize"
  "ma-resources/skills/topic6-annotation|topic6-annotation.zip|topic6-annotation"
  "ma-resources/skills/topic6-insight|topic6-insight.zip|topic6-insight"
  "ma-resources/skills/topic6-event-registry|topic6-event-registry.zip|topic6-event-registry"
  "ma-resources/skills/topic6-web-report|topic6-web-report.zip|topic6-web-report"
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

  # 用 stage 目录构造统一顶层
  local stage
  stage=$(mktemp -d)
  # 排除常见的构建产物 / 缓存 / node_modules
  rsync -a --exclude='__pycache__' --exclude='.DS_Store' --exclude='*.pyc' \
        --exclude='node_modules' --exclude='.git' \
        "$src/" "$stage/$top_name/"

  local staged_root="$stage/$top_name"
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
