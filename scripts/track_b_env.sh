#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ADDRESS_ENV="${TRACK_B_ADDRESS_ENV:-${SDK_ROOT}/../offchain/e2e/out/compose/basic-addresses.env}"

if [[ $# -eq 0 ]]; then
  echo "usage: scripts/track_b_env.sh <command> [args...]" >&2
  exit 64
fi

if [[ ! -f "${ADDRESS_ENV}" ]]; then
  if [[ "${TRACK_B_ENV_REQUIRED:-false}" == "true" ]]; then
    echo "Track B address env not found at ${ADDRESS_ENV}" >&2
    exit 1
  fi
  exec "$@"
fi

set -a
# shellcheck disable=SC1090
source "${ADDRESS_ENV}"
set +a

force_export() {
  local key="$1"
  local value="${2:-}"
  export "${key}=${value}"
}

force_export CHAIN_ID "31337"
force_export ORDERBOOK_PERP_ASSET "ETH"
force_export REYA_API_URL "http://127.0.0.1:3000/v2"
force_export REYA_WS_URL "ws://127.0.0.1:8082"
force_export REYA_WS_EXEC_URL "ws://127.0.0.1:8080"
force_export REYA_DEX_ID "1"
force_export REYA_ORDERS_GATEWAY "${ORDERS_GATEWAY_PROXY_ADDRESS:-}"

force_export ACCOUNT_ID "${LOCAL_PERP_ACCOUNT_ID:-}"
force_export PRIVATE_KEY "${LOCAL_SDK_PRIVATE_KEY:-}"
force_export WALLET_ADDRESS "${LOCAL_SDK_WALLET_ADDRESS:-}"
force_export OWNER_WALLET_ADDRESS "${LOCAL_SDK_WALLET_ADDRESS:-}"

force_export PERP_ACCOUNT_ID_1 "${LOCAL_PERP_ACCOUNT_ID:-}"
force_export PERP_PRIVATE_KEY_1 "${LOCAL_SDK_PRIVATE_KEY:-}"
force_export PERP_WALLET_ADDRESS_1 "${LOCAL_SDK_WALLET_ADDRESS:-}"
force_export PERP_ACCOUNT_ID_2 "${LOCAL_PERP_COUNTERPARTY_ACCOUNT_ID:-}"
force_export PERP_PRIVATE_KEY_2 "${LOCAL_SDK_COUNTERPARTY_PRIVATE_KEY:-}"
force_export PERP_WALLET_ADDRESS_2 "${LOCAL_SDK_COUNTERPARTY_WALLET_ADDRESS:-}"

force_export SPOT_ACCOUNT_ID_1 "${LOCAL_SPOT_ACCOUNT_ID:-}"
force_export SPOT_PRIVATE_KEY_1 "${LOCAL_SDK_PRIVATE_KEY:-}"
force_export SPOT_WALLET_ADDRESS_1 "${LOCAL_SDK_WALLET_ADDRESS:-}"
force_export SPOT_ACCOUNT_ID_2 "${LOCAL_SPOT_COUNTERPARTY_ACCOUNT_ID:-}"
force_export SPOT_PRIVATE_KEY_2 "${LOCAL_SDK_COUNTERPARTY_PRIVATE_KEY:-}"
force_export SPOT_WALLET_ADDRESS_2 "${LOCAL_SDK_COUNTERPARTY_WALLET_ADDRESS:-}"

missing=()
for key in \
  CHAIN_ID \
  REYA_API_URL \
  REYA_WS_URL \
  REYA_WS_EXEC_URL \
  REYA_ORDERS_GATEWAY \
  PERP_ACCOUNT_ID_1 \
  PERP_PRIVATE_KEY_1 \
  PERP_WALLET_ADDRESS_1 \
  PERP_ACCOUNT_ID_2 \
  PERP_PRIVATE_KEY_2 \
  PERP_WALLET_ADDRESS_2 \
  SPOT_ACCOUNT_ID_1 \
  SPOT_PRIVATE_KEY_1 \
  SPOT_WALLET_ADDRESS_1 \
  SPOT_ACCOUNT_ID_2 \
  SPOT_PRIVATE_KEY_2 \
  SPOT_WALLET_ADDRESS_2; do
  if [[ -z "${!key:-}" ]]; then
    missing+=("${key}")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "Track B local SDK env is incomplete: ${missing[*]}" >&2
  echo "Address env: ${ADDRESS_ENV}" >&2
  exit 1
fi

exec "$@"
