#!/usr/bin/env bash
# 开源发布前体检脚本（只读）。
#
# 用法:
#   bash check.sh          # 体检当前 git 仓库将提交的内容（默认）
#   bash check.sh <dir>    # 体检指定目录（非 git 目录时退化为目录全量扫描）
#
# 检查范围 = git 将提交的文件（已跟踪 + 未跟踪且未被 .gitignore 忽略），
# 因此开发环境的 .env / *.db / .venv 会被 .gitignore 自动排除，不会误报。
# 发现敏感文件 / 明文密钥 / 缺失必需文件时以非零退出；大文件仅提示人工确认。
set -u

TARGET="${1:-.}"
cd "$TARGET" || { echo "ERROR: 无法进入目录: $TARGET" >&2; exit 2; }

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git ls-files --cached --others --exclude-standard > "$TMP"
  LIST_KIND="git 将提交的文件"
else
  find . -type f \
    -not -path './.git/*' -not -path './.venv/*' \
    -not -path '*/__pycache__/*' -not -path '*/.pytest_cache/*' > "$TMP"
  LIST_KIND="目录全量（非 git）"
fi

FAIL=0
echo "== 开源发布体检: $TARGET =="
echo "  检查范围: $LIST_KIND"

# [1] 敏感文件名
echo
echo "[1] 敏感文件（禁止开源）"
while IFS= read -r f; do
  base="$(basename "$f")"
  case "$base" in
    .env)
      echo "  [敏感] $f"; FAIL=1 ;;
    .env.*)
      case "$base" in
        .env.example|.env.template|.env.sample) ;;  # 模板安全
        *) echo "  [敏感] $f"; FAIL=1 ;;
      esac ;;
    predictions.log) ;;  # 明确公开的预测台账
    *.db|*.db-wal|*.db-shm|*.sqlite|*.sqlite3|*.pem|*.key|*.p12|*.pfx|id_rsa*|*.log)
      echo "  [敏感] $f"; FAIL=1 ;;
  esac
done < "$TMP"

# [2] 明文密钥 / 令牌 / 密码（启发式；值需含数字或为引号包裹的长串）
echo
echo "[2] 明文密钥 / 令牌 / 密码（启发式）"
KW='(api[_-]?key|secret|token|password|passwd|authorization|bearer)'
P_QUOTED="${KW}[[:space:]]*[:=][[:space:]]*[\"'][^\"']{8,}[\"']"
P_TOKEN="${KW}[[:space:]]*[:=][[:space:]]*[A-Za-z0-9_./+=-]*[0-9][A-Za-z0-9_./+=-]{7,}"
HITS=""
while IFS= read -r f; do
  m="$(grep -nIE -e "$P_QUOTED" -e "$P_TOKEN" "$f" 2>/dev/null || true)"
  [ -n "$m" ] && HITS="${HITS}${HITS:+$'\n'}${m}"
done < "$TMP"
if [ -n "$HITS" ]; then
  printf '  %s\n' "$HITS"
  FAIL=1
else
  echo "  [OK] 未发现明文密钥（空占位符不计）"
fi

# [3] 必需文件
echo
echo "[3] 必需文件"
for req in LICENSE README.md; do
  if [ -f "$req" ]; then
    echo "  [OK] $req"
  else
    echo "  [缺失] $req"
    FAIL=1
  fi
done

# [4] 大文件（仅提示，不改变退出码）
echo
echo "[4] 大文件（>1MB，人工确认）"
BIG=""
while IFS= read -r f; do
  sz="$(stat -c %s "$f" 2>/dev/null || echo 0)"
  if [ "$sz" -gt 1048576 ]; then
    BIG="${BIG}${BIG:+$'\n'}  $f ($((sz/1024))KB)"
  fi
done < "$TMP"
if [ -n "$BIG" ]; then
  printf '%s\n' "$BIG"
else
  echo "  [OK] 无大文件"
fi

echo
if [ "$FAIL" -ne 0 ]; then
  echo "== 体检未通过：请先处理上述项 =="
  exit 1
fi
echo "== 体检通过 =="
exit 0
