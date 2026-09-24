#!/usr/bin/env bash
# 把 topic6 各 skill 目录打包为 zip,校验 MA 上传约束:
#   - 单 zip < 50 MB
#   - 单文件 < 25 MB
#   - 文件数 < 500
#   - 顶层唯一 SKILL.md
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

MAX_ZIP=$((50 * 1024 * 1024))
MAX_FILE=$((25 * 1024 * 1024))
MAX_COUNT=500

check_skill_md() {
  local src="$1"
  local count
  count=$(find "$src" -maxdepth 3 -name SKILL.md | wc -l | tr -d ' ')
  if [ "$count" -ne 1 ]; then
    echo "  [错误] $src 下 SKILL.md 数量=$count,必须恰好 1 个"
    return 1
  fi
}

pack_one() {
  local src_rel="$1"
  local zip_name="$2"
  local top_name="$3"
  local src="$ROOT/$src_rel"

  [ -d "$src" ] || { echo "  [跳过] $src 不存在"; return 0; }

  echo "=== $src_rel → $zip_name ==="
  check_skill_md "$src"

  # 文件数校验(打包前)
  local file_count
  file_count=$(find "$src" -type f | wc -l | tr -d ' ')
  if [ "$file_count" -gt "$MAX_COUNT" ]; then
    echo "  [错误] 文件数=$file_count > $MAX_COUNT"; return 1
  fi

  # 单文件大小校验
  local oversize
  oversize=$(find "$src" -type f -size +25M | head -3 || true)
  if [ -n "$oversize" ]; then
    echo "  [错误] 单文件超 25MB:"; echo "$oversize"; return 1
  fi

  # 用 stage 目录构造统一顶层
  local stage
  stage=$(mktemp -d)
  # 排除常见的构建产物 / 缓存 / node_modules
  rsync -a --exclude='__pycache__' --exclude='.DS_Store' --exclude='*.pyc' \
        --exclude='node_modules' --exclude='.git' \
        "$src/" "$stage/$top_name/"

  local out="$OUT_DIR/$zip_name"
  (cd "$stage" && zip -qr "$out" "$top_name")
  rm -rf "$stage"

  # zip 大小校验
  local sz
  sz=$(stat -f%z "$out" 2>/dev/null || stat -c%s "$out")
  if [ "$sz" -gt "$MAX_ZIP" ]; then
    echo "  [错误] zip 大小=$sz > 50MB"; return 1
  fi

  shasum -a 256 "$out" | awk '{print $1}' >"$out.sha256"
  echo "  ok · size=$sz · sha256=$(cat "$out.sha256")"
}

for entry in "${SKILLS[@]}"; do
  IFS='|' read -r src_rel zip_name top_name <<<"$entry"
  pack_one "$src_rel" "$zip_name" "$top_name"
done

echo -e "\n所有 skill 打包完成 → $OUT_DIR"
ls -lh "$OUT_DIR"
