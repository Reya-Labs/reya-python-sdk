#!/usr/bin/env bash
set -euo pipefail

# WS-only generator: fixes anonymous models without touching the shared schema files used by OpenAPI.

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
  --packageName="reya_ws_models"

touch "$OUT_DIR/__init__.py"

echo "✅ Done. Models at: $OUT_DIR"
find "$OUT_DIR" -type f -name "*.py" | sort

# 5) Cleanup
rm -rf "$TMP_DIR"
