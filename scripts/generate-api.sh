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

# Generate directly into the checked-out repository. Deriving a sibling
# `reya-python-sdk` path breaks in Git worktrees whose directory has a task
# name instead of the repository name.
echo -e "${GREEN}🐍 Generating Python SDK in $ROOT_DIR...${NC}"
rm -rf "$ROOT_DIR/sdk/open_api"

# The CLI discovers openapitools.json relative to the working directory.
# Run from the repository root so the pinned generator version is honored even
# when this script is invoked through an absolute path from another directory.
(
    cd "$ROOT_DIR"
    "${OPENAPI_GENERATOR_CLI[@]}" generate \
        -i "$SPECS_DIR/openapi-trading-v2.yaml" \
        -g python \
        -o "$ROOT_DIR" \
        --skip-operation-example \
        --global-property=models,apis,modelDocs=false,modelTests=false,apiDocs=false,apiTests=false,supportingFiles=__init__.py:api_client.py:configuration.py:api_response.py:exceptions.py:rest.py \
        --additional-properties=library=asyncio,packageName=sdk.open_api,projectName=open-api,packageVersion=$PACKAGE_VERSION,packageUrl=https://github.com/reya-network/reya-python-sdk
)
