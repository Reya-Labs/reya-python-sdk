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

# Check if OpenAPI Generator CLI is installed
if ! command -v openapi-generator-cli &> /dev/null; then
    echo -e "${YELLOW}⚠️  OpenAPI Generator CLI not found. Installing...${NC}"
    npm install -g @openapitools/openapi-generator-cli
fi


# Generate Python SDK directly to Python SDK repo
# Calculate parent directory of the project (one level up from ROOT_DIR)
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
PYTHON_SDK_REPO="$PARENT_DIR/reya-python-sdk"
if [ -d "$PYTHON_SDK_REPO" ]; then
    echo -e "${GREEN}🐍 Generating Python SDK to dedicated repo...${NC}"
    # Create the target directory structure first
    rm -rf "$PYTHON_SDK_REPO/sdk/open_api"
    
    # Generate the SDK directly into the target directory
    openapi-generator-cli generate \
        -i "$SPECS_DIR/openapi-trading-v2.yaml" \
        -g python \
        -o "$PYTHON_SDK_REPO" \
        --skip-operation-example \
        --global-property=models,apis,modelDocs=false,modelTests=false,apiDocs=false,apiTests=false,supportingFiles=__init__.py:api_client.py:configuration.py:api_response.py:exceptions.py:rest.py \
        --additional-properties=library=asyncio,packageName=sdk.open_api,projectName=open-api,packageVersion=2.0.0,packageUrl=https://github.com/reya-network/reya-python-sdk

    
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Python SDK generation failed${NC}"
        exit 1
    fi
  
else
    echo -e "${YELLOW}⚠️  Python SDK repo not found at $PYTHON_SDK_REPO${NC}"
fi
