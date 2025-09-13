#!/usr/bin/env bash
set -euo pipefail

# Modern AsyncAPI 3.0.0 WebSocket Python generator using Modelina CLI
# Generates Pydantic v2 models with custom serializer/validator methods for additional properties

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SPEC_SRC="$REPO_ROOT/specs/asyncapi-trading-v2.yaml"
TRADING_JSON_SRC="$REPO_ROOT/specs/trading-schemas.json"
OUT_DIR="$REPO_ROOT/sdk/async_api"

MODELINA_CLI="npx -y @asyncapi/modelina-cli@5.7.2"

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

# --- Work in a temp dir ---
TMP_DIR="$(mktemp -d -t reya-ws-XXXXXX)"
BUNDLED="$TMP_DIR/asyncapi-bundled.yaml"

echo "🧭 Temp dir: $TMP_DIR"
echo "🧹 Cleaning output directory: $OUT_DIR"
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

# 3) Generate Python Pydantic v2 models directly from AsyncAPI spec (skip bundling)
echo "🐍 Generating Python Pydantic v2 models directly from AsyncAPI spec -> $OUT_DIR"
cd "$TMP_DIR"
$MODELINA_CLI generate python "asyncapi.yaml" \
  --output "$OUT_DIR" \
  --pyDantic \
  --packageName="sdk.async_api"
cd "$REPO_ROOT"

# Create __init__.py for proper Python package structure
touch "$OUT_DIR/__init__.py"

# --- Portable in-place sed wrapper (macOS vs GNU sed) ---
sed_inplace() {
  # Usage: sed_inplace 's/old/new/g' file1 file2 ...
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -E -i '' "$1" "${@:2}"
  else
    sed -r -i "$1" "${@:2}"
  fi
}

# Sanitize Modelina Python output (let Modelina handle custom methods automatically)
echo "🧼 Sanitizing generated Python code"
# Portable way to collect Python files into array (works on both macOS and Linux)
PY_FILES=()
while IFS= read -r -d '' file; do
  PY_FILES+=("$file")
done < <(find "$OUT_DIR" -type f -name "*.py" -print0)

if ((${#PY_FILES[@]})); then
  # Fix doubled single quotes in Field default values: default=''value'' → default='value'
  sed_inplace "s/(Field[^)]*default[[:space:]]*=[[:space:]]*)''([^']*)''/\\1'\\2'/g" "${PY_FILES[@]}"
  
  # Fix any potential double-quoted empty strings in Field defaults
  sed_inplace 's/(Field[^)]*default[[:space:]]*=[[:space:]]*)""/\\1None/g' "${PY_FILES[@]}"
fi

echo "✅ Done. Pydantic v2 models with automatic @model_serializer/@model_validator methods generated at: $OUT_DIR"
echo "📁 Generated files:"
find "$OUT_DIR" -type f -name "*.py" | sort

# Cleanup
rm -rf "$TMP_DIR"
