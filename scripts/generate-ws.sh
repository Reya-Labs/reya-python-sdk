#!/usr/bin/env bash
set -euo pipefail

# WS-only generator: fixes anonymous models without touching the shared schema files used by OpenAPI.
# Plus: sanitizes Modelina's Python output where it sometimes emits default=''value'' (invalid Python).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SPEC_SRC="$REPO_ROOT/specs/asyncapi-trading-v2.yaml"
TRADING_JSON_SRC="$REPO_ROOT/specs/trading-schemas.json"
OUT_DIR="$REPO_ROOT/sdk/async_api"

ASYNCAPI_CLI="npx -y @asyncapi/cli"
MODELINA_CLI="npx -y @asyncapi/modelina-cli"

# --- Ensure jq is installed ---
if ! command -v jq >/dev/null 2>&1; then
  echo "⚠️  jq not found. Attempting to install..."
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y jq
    elif command -v yum >/dev/null 2>&1; then
      sudo yum install -y jq
    else
      echo "❌ Could not auto-install jq (unsupported package manager). Please install jq manually."
      exit 1
    fi
  elif [[ "$OSTYPE" == "darwin"* ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install jq
    else
      echo "❌ Homebrew not found. Please install jq manually (brew install jq)."
      exit 1
    fi
  else
    echo "❌ Unsupported OS. Please install jq manually from https://stedolan.github.io/jq/"
    exit 1
  fi
fi

# --- Portable in-place sed wrapper (macOS vs GNU sed) ---
sed_inplace() {
  # Usage: sed_inplace 's/old/new/g' file1 file2 ...
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -E -i '' "$1" "${@:2}"
  else
    sed -r -i "$1" "${@:2}"
  fi
}

# --- Work in a temp dir ---
TMP_DIR="$(mktemp -d -t reya-ws-XXXXXX)"
BUNDLED="$TMP_DIR/asyncapi-bundled.yaml"

echo "🧭 Temp dir: $TMP_DIR"
echo "🧹 Cleaning output: $OUT_DIR"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

# 1) Copy specs into temp workspace
cp "$SPEC_SRC" "$TMP_DIR/asyncapi.yaml"
cp "$TRADING_JSON_SRC" "$TMP_DIR/trading-schemas.json"

# 2) Patch temp trading-schemas.json with stable names
echo "🧪 Patching temp trading-schemas.json with titles/ids (WS-only)"
jq '
  .definitions |= with_entries(
    .value += {
      "title": (.value.title // .key),
      "$id":    (.value["$id"] // ("#" + .key)),
      "x-parser-schema-id": (.value["x-parser-schema-id"] // .key)
    }
  )
' "$TMP_DIR/trading-schemas.json" > "$TMP_DIR/trading-schemas.tmp.json"
mv "$TMP_DIR/trading-schemas.tmp.json" "$TMP_DIR/trading-schemas.json"

# 3) Bundle using AsyncAPI CLI (from temp dir so $refs resolve)
echo "📦 Bundling AsyncAPI"
pushd "$TMP_DIR" >/dev/null
$ASYNCAPI_CLI bundle "./asyncapi.yaml" --output "$BUNDLED"
popd >/dev/null

# 4) Generate Python models (Pydantic v2)
echo "🐍 Generating Python (Pydantic v2) models -> $OUT_DIR"
$MODELINA_CLI generate python "$BUNDLED" \
  --output "$OUT_DIR" \
  --pyDantic \
  --packageName="sdk.async_api"

touch "$OUT_DIR/__init__.py"

# 5) Sanitize Modelina Python output (fix default=''value'')
#    Only touch Field(... default=...) occurrences; keep triple-quoted descriptions intact.
echo "🧼 Sanitizing generated Python: fixing default=''value'' → default='value'"
# Portable way to collect Python files into array (works on both macOS and Linux)
PY_FILES=()
while IFS= read -r -d '' file; do
  PY_FILES+=("$file")
done < <(find "$OUT_DIR" -type f -name "*.py" -print0)

if ((${#PY_FILES[@]})); then
  # Regex (extended):
  #   - Capture prefix up to and including "default =" with flexible spacing:
  #       (Field[^)]*default[[:space:]]*=[[:space:]]*)
  #   - Match doubled single quotes around the value:
  #       ''([^']*)''
  #   - Replace with single quotes: \1'\2'
  sed_inplace "s/(Field[^)]*default[[:space:]]*=[[:space:]]*)''([^']*)''/\\1'\\2'/g" "${PY_FILES[@]}"
fi

echo "✅ Done. Models at: $OUT_DIR"
find "$OUT_DIR" -type f -name "*.py" | sort

# 6) Cleanup
rm -rf "$TMP_DIR"
