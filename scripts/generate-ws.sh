#!/usr/bin/env bash
set -euo pipefail

# Modern AsyncAPI 3.0.0 WebSocket Python generator using Modelina CLI
# Generates Pydantic v2 models with custom serializer/validator methods for
# additional properties.
#
# Generates TWO output packages:
#   sdk/async_api/      from specs/asyncapi-trading-v2.yaml (read-side WS)
#   sdk/async_exec_api/ from specs/asyncapi-exec-v2.yaml    (ws-exec order entry)
#
# Both specs `$ref` into the same `specs/trading-schemas.json`, which is
# patched once with stable titles/ids in the shared temp dir.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TRADING_SPEC_SRC="$REPO_ROOT/specs/asyncapi-trading-v2.yaml"
EXEC_SPEC_SRC="$REPO_ROOT/specs/asyncapi-exec-v2.yaml"
TRADING_JSON_SRC="$REPO_ROOT/specs/trading-schemas.json"

TRADING_OUT_DIR="$REPO_ROOT/sdk/async_api"
EXEC_OUT_DIR="$REPO_ROOT/sdk/async_exec_api"

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

# --- Portable in-place sed wrapper (macOS vs GNU sed) ---
sed_inplace() {
  # Usage: sed_inplace 's/old/new/g' file1 file2 ...
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -E -i '' "$1" "${@:2}"
  else
    sed -r -i "$1" "${@:2}"
  fi
}

# --- Generate one AsyncAPI package ---
# Args: spec_src out_dir package_name
generate_async_api() {
  local SPEC_SRC="$1"
  local OUT_DIR="$2"
  local PACKAGE_NAME="$3"

  echo ""
  echo "▶️  Generating $PACKAGE_NAME from $(basename "$SPEC_SRC")"
  echo "🧹 Cleaning output directory: $OUT_DIR"
  rm -rf "$OUT_DIR"
  mkdir -p "$OUT_DIR"

  # Stage the spec into the shared temp dir (overwriting the previous pass's copy)
  cp "$SPEC_SRC" "$TMP_DIR/asyncapi.yaml"

  echo "🐍 Generating Python Pydantic v2 models -> $OUT_DIR"
  (
    cd "$TMP_DIR"
    $MODELINA_CLI generate python "asyncapi.yaml" \
      --output "$OUT_DIR" \
      --pyDantic \
      --packageName="$PACKAGE_NAME"
  )

  # Create __init__.py for proper Python package structure
  touch "$OUT_DIR/__init__.py"

  # Sanitize Modelina Python output (let Modelina handle custom methods automatically)
  echo "🧼 Sanitizing generated Python code"
  local PY_FILES=()
  while IFS= read -r -d '' file; do
    PY_FILES+=("$file")
  done < <(find "$OUT_DIR" -type f -name "*.py" -print0)

  if ((${#PY_FILES[@]})); then
    # Fix doubled single quotes in Field default values: default=''value'' → default='value'
    sed_inplace "s/(Field[^)]*default[[:space:]]*=[[:space:]]*)''([^']*)''/\\1'\\2'/g" "${PY_FILES[@]}"

    # Fix any potential double-quoted empty strings in Field defaults
    sed_inplace 's/(Field[^)]*default[[:space:]]*=[[:space:]]*)""/\\1None/g' "${PY_FILES[@]}"
  fi

  echo "✅ $PACKAGE_NAME generated"
}

# --- Shared temp workspace (patched trading-schemas.json lives here) ---
TMP_DIR="$(mktemp -d -t reya-ws-XXXXXX)"
echo "🧭 Temp dir: $TMP_DIR"

# Stage the shared schema definitions once, patch them with stable titles/ids.
# Both AsyncAPI specs `$ref` into ./trading-schemas.json relative to the cwd,
# which is $TMP_DIR during each Modelina invocation.
cp "$TRADING_JSON_SRC" "$TMP_DIR/trading-schemas.json"
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

# --- Pass 1: trading WebSocket (read-side) ---
generate_async_api "$TRADING_SPEC_SRC" "$TRADING_OUT_DIR" "sdk.async_api"

# --- Pass 2: ws-exec (order entry) ---
if [[ -f "$EXEC_SPEC_SRC" ]]; then
  generate_async_api "$EXEC_SPEC_SRC" "$EXEC_OUT_DIR" "sdk.async_exec_api"
else
  echo "⚠️  Skipping ws-exec codegen — $EXEC_SPEC_SRC not present (specs submodule may be on an older pointer)."
fi

echo ""
echo "✅ All AsyncAPI packages regenerated."
echo "📁 Generated files:"
find "$TRADING_OUT_DIR" -type f -name "*.py" | sort
if [[ -d "$EXEC_OUT_DIR" ]]; then
  echo "--- ws-exec ---"
  find "$EXEC_OUT_DIR" -type f -name "*.py" | sort
fi

# Cleanup
rm -rf "$TMP_DIR"
