#!/bin/bash

# Script to generate Python SDK from OpenAPI specs
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SPECS_DIR="$ROOT_DIR/specs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Generating Python SDK from OpenAPI specifications...${NC}"

# Read package version from pyproject.toml
PACKAGE_VERSION=$(grep -m1 '^version' "$ROOT_DIR/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
if [ -z "$PACKAGE_VERSION" ]; then
    echo -e "${RED}❌ Could not read version from pyproject.toml${NC}"
    exit 1
fi
echo -e "${GREEN}📦 Using package version: $PACKAGE_VERSION${NC}"

# Check if OpenAPI Generator CLI is installed
if ! command -v openapi-generator-cli &> /dev/null; then
    echo -e "${YELLOW}⚠️  OpenAPI Generator CLI not found. Installing...${NC}"
    npm install -g @openapitools/openapi-generator-cli
fi


# Generate directly into the checked-out repository. Deriving a sibling
# `reya-python-sdk` path breaks in Git worktrees whose directory has a task
# name instead of the repository name.
echo -e "${GREEN}🐍 Generating Python SDK in $ROOT_DIR...${NC}"
rm -rf "$ROOT_DIR/sdk/open_api"

openapi-generator-cli generate \
    -i "$SPECS_DIR/openapi-trading-v2.yaml" \
    -g python \
    -o "$ROOT_DIR" \
    --skip-operation-example \
    --global-property=models,apis,modelDocs=false,modelTests=false,apiDocs=false,apiTests=false,supportingFiles=__init__.py:api_client.py:configuration.py:api_response.py:exceptions.py:rest.py \
    --additional-properties=library=asyncio,packageName=sdk.open_api,projectName=open-api,packageVersion=$PACKAGE_VERSION,packageUrl=https://github.com/reya-network/reya-python-sdk
