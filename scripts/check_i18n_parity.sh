#!/usr/bin/env bash
# Verify i18n parity between the en and zh-CN locale files.
#
# This script checks that every leaf key in `en.json` has a corresponding
# leaf key in `zh-CN.json` and vice versa. Mismatches usually mean a PR
# added an English string but forgot to translate it, or deleted a string
# from one locale but not the other.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCALES_DIR="$REPO_ROOT/frontend/src/i18n/locales"

EN_FILE="$LOCALES_DIR/en.json"
ZH_FILE="$LOCALES_DIR/zh-CN.json"

if [ ! -f "$EN_FILE" ]; then
    echo "Missing $EN_FILE" >&2
    exit 1
fi
if [ ! -f "$ZH_FILE" ]; then
    echo "Missing $ZH_FILE" >&2
    exit 1
fi

# Flatten a JSON file's nested keys to one key per line.
flatten() {
    python3 - "$1" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

def walk(prefix, node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            walk(prefix + [str(key)], value, out)
    else:
        out.append(".".join(prefix))

out = []
walk([], data, out)
for key in sorted(out):
    print(key)
PY
}

tmp_en="$(mktemp)"
tmp_zh="$(mktemp)"
trap 'rm -f "$tmp_en" "$tmp_zh"' EXIT

flatten "$EN_FILE" >"$tmp_en"
flatten "$ZH_FILE" >"$tmp_zh"

if diff -u "$tmp_en" "$tmp_zh" >/dev/null; then
    keys_en="$(wc -l <"$tmp_en")"
    keys_zh="$(wc -l <"$tmp_zh")"
    echo "i18n parity OK ($keys_en keys in en.json, $keys_zh in zh-CN.json)"
    exit 0
fi

echo "i18n parity FAILED — see diff between en.json and zh-CN.json leaf keys:" >&2
diff -u "$tmp_en" "$tmp_zh" >&2 || true
echo >&2
echo "Missing in zh-CN (present in en):" >&2
comm -23 "$tmp_en" "$tmp_zh" >&2 || true
echo >&2
echo "Missing in en (present in zh-CN):" >&2
comm -13 "$tmp_en" "$tmp_zh" >&2 || true
exit 1
