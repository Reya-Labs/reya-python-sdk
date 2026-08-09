#!/bin/bash

# Script to generate Python SDK from OpenAPI specs
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPECS_DIR="$ROOT_DIR/specs"
OPENAPI_GENERATOR_CLI=(npx -y @openapitools/openapi-generator-cli@2.40.1)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Generating Python SDK from OpenAPI specifications...${NC}"

# Read package version from pyproject.toml
PACKAGE_VERSION=$(grep -m1 '^version' "$ROOT_DIR/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
if [ -z "$PACKAGE_VERSION" ]; then
    echo -e "${RED}❌ Could not read version from pyproject.toml${NC}"
    exit 1
fi
echo -e "${GREEN}📦 Using package version: $PACKAGE_VERSION${NC}"

GENERATION_DIR="$(mktemp -d "$ROOT_DIR/.openapi-generation.XXXXXX")"
BACKUP_DIR="$(mktemp -d "$ROOT_DIR/.openapi-backup.XXXXXX")"
REPLACEMENT_COMPLETE=0

cleanup() {
    status=$?
    trap - EXIT

    if [ "$REPLACEMENT_COMPLETE" -ne 1 ]; then
        if [ -d "$BACKUP_DIR/open_api" ]; then
            rm -rf "$ROOT_DIR/sdk/open_api"
            mv "$BACKUP_DIR/open_api" "$ROOT_DIR/sdk/open_api"
        fi
        if [ -f "$BACKUP_DIR/sdk-init.py" ]; then
            rm -f "$ROOT_DIR/sdk/__init__.py"
            mv "$BACKUP_DIR/sdk-init.py" "$ROOT_DIR/sdk/__init__.py"
        fi
        if [ -d "$BACKUP_DIR/openapi-generator" ]; then
            rm -rf "$ROOT_DIR/.openapi-generator"
            mv "$BACKUP_DIR/openapi-generator" "$ROOT_DIR/.openapi-generator"
        fi
    fi

    rm -rf "$GENERATION_DIR" "$BACKUP_DIR"
    exit "$status"
}
trap cleanup EXIT

# Generate and validate away from the checked-out bindings. Deriving a sibling
# `reya-python-sdk` path breaks in Git worktrees whose directory has a task
# name instead of the repository name.
echo -e "${GREEN}🐍 Staging generated Python SDK for $ROOT_DIR...${NC}"

# The CLI discovers openapitools.json relative to the working directory.
# Run from the repository root so the pinned generator version is honored even
# when this script is invoked through an absolute path from another directory.
# `generate` validates the input spec by default; this script never passes
# `--skip-validate-spec`.
(
    cd "$ROOT_DIR"
    "${OPENAPI_GENERATOR_CLI[@]}" generate \
        -i "$SPECS_DIR/openapi-trading-v2.yaml" \
        -g python \
        -o "$GENERATION_DIR" \
        --skip-operation-example \
        --global-property=models,apis,modelDocs=false,modelTests=false,apiDocs=false,apiTests=false,supportingFiles=__init__.py:api_client.py:configuration.py:api_response.py:exceptions.py:rest.py \
        --additional-properties=library=asyncio,packageName=sdk.open_api,projectName=open-api,packageVersion=$PACKAGE_VERSION,packageUrl=https://github.com/reya-network/reya-python-sdk
)

test -d "$GENERATION_DIR/sdk/open_api"
test -f "$GENERATION_DIR/sdk/__init__.py"
test -d "$GENERATION_DIR/.openapi-generator"
python3 "$SCRIPT_DIR/postprocess-openapi.py" "$GENERATION_DIR/sdk/open_api"

# Replace all tracked generator outputs only after generation, spec validation,
# and post-processing succeed. The EXIT trap restores each old target if any
# replacement step fails.
mv "$ROOT_DIR/sdk/open_api" "$BACKUP_DIR/open_api"
mv "$ROOT_DIR/sdk/__init__.py" "$BACKUP_DIR/sdk-init.py"
mv "$ROOT_DIR/.openapi-generator" "$BACKUP_DIR/openapi-generator"

mv "$GENERATION_DIR/sdk/open_api" "$ROOT_DIR/sdk/open_api"
mv "$GENERATION_DIR/sdk/__init__.py" "$ROOT_DIR/sdk/__init__.py"
mv "$GENERATION_DIR/.openapi-generator" "$ROOT_DIR/.openapi-generator"
REPLACEMENT_COMPLETE=1
