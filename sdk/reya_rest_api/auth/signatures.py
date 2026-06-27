"""
Signature generation utilities for Reya Trading API authentication.

Implements EIP-712 signing for the unified spot+perp Order envelope, plus
matching-engine-layer OrderCancel, MassCancel, and CancelAllAfter envelopes.
See specs/docs/eip712.md for the canonical typehash strings and field
semantics.
"""

from decimal import Decimal
from enum import IntEnum

from eth_account import Account

from sdk.reya_rest_api.config import TradingConfig


class OrderTypeInt(IntEnum):
    """On-chain `OrderDetails.orderType` values. Mirrors the API string enum
    but encodes the uint8 expected by the EIP-712 typed data."""

    LIMIT = 0
    STOP_LOSS = 1
    TAKE_PROFIT = 2


class TimeInForceInt(IntEnum):
    """On-chain `OrderDetails.timeInForce` values."""

    GTC = 0
    IOC = 1
    GTT = 2


# EIP-712 `OrderDetails` member list. Field order MUST match the on-chain
# OrderDetails typehash byte-for-byte (the struct hash is order-sensitive).
# `postOnly` sits immediately after `reduceOnly` (14 fields). The golden-vector
# test in tests/parity/test_order_details_golden.py pins the resulting struct
# hash to the canonical on-chain value, so accidental drift is caught.
_ORDER_DETAILS_TYPE: list[dict[str, str]] = [
    {"name": "accountId", "type": "uint128"},
    {"name": "marketId", "type": "uint128"},
    {"name": "exchangeId", "type": "uint128"},
    {"name": "orderType", "type": "uint8"},
    {"name": "quantity", "type": "int256"},
    {"name": "limitPrice", "type": "uint256"},
    {"name": "triggerPrice", "type": "uint256"},
    {"name": "timeInForce", "type": "uint8"},
    {"name": "clientOrderId", "type": "uint64"},
    {"name": "reduceOnly", "type": "bool"},
    {"name": "postOnly", "type": "bool"},
    {"name": "expiresAfter", "type": "uint256"},
    {"name": "signer", "type": "address"},
    {"name": "nonce", "type": "uint256"},
]


class SignatureGenerator:
    """Generate EIP-712 signatures for Reya Trading API requests."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self._private_key = config.private_key
        self._chain_id = config.chain_id

        if not self._private_key:
            raise ValueError("Private key is required for signing")

        self._signer_wallet_address: str = str(Account.from_key(self._private_key).address)

    @property
    def signer_wallet_address(self) -> str:
        return self._signer_wallet_address

    @staticmethod
    def _scale_e18(value) -> int:
        """Scale a decimal/string/int/float to an E18 integer."""
        return int(Decimal(str(value)) * (10**18))

    @property
    def _domain(self) -> dict:
        """EIP-712 domain shared by Order, OrderCancel, MassCancel.

        Note: chainId is intentionally absent from the domain — it travels in
        the envelope as `verifyingChainId` so signatures stay portable across
        forks where the domain separator would diverge."""
        return {
            "name": "Reya",
            "version": "1",
            "verifyingContract": self.config.default_orders_gateway_address,
        }

    def sign_order(
        self,
        account_id: int,
        market_id: int,
        exchange_id: int,
        order_type: int,
        is_buy: bool,
        qty: Decimal,
        limit_price: Decimal,
        trigger_price: Decimal,
        time_in_force: int,
        client_order_id: int,
        reduce_only: bool,
        expires_after: int,
        nonce: int,
        deadline: int,
        post_only: bool = False,
    ) -> str:
        """Sign an Order envelope per docs/eip712.md.

        Reconstructs the signed `OrderDetails.quantity` (int256) from
        `is_buy` + unsigned `qty` as `is_buy ? +qty : -qty`. A negative `qty`
        would silently flip the trade direction, so reject it up front.
        """
        if qty < 0:
            raise ValueError(f"sign_order requires unsigned qty (got {qty}); use is_buy to set direction")
        signed_qty = qty if is_buy else -qty

        types = {
            "Order": [
                {"name": "verifyingChainId", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "order", "type": "OrderDetails"},
            ],
            "OrderDetails": _ORDER_DETAILS_TYPE,
        }

        message = {
            "verifyingChainId": self._chain_id,
            "deadline": deadline,
            "order": {
                "accountId": account_id,
                "marketId": market_id,
                "exchangeId": exchange_id,
                "orderType": order_type,
                "quantity": self._scale_e18(signed_qty),
                "limitPrice": self._scale_e18(limit_price),
                "triggerPrice": self._scale_e18(trigger_price),
                "timeInForce": time_in_force,
                "clientOrderId": client_order_id,
                "reduceOnly": reduce_only,
                "postOnly": post_only,
                "expiresAfter": expires_after,
                "signer": self._signer_wallet_address,
                "nonce": nonce,
            },
        }

        signed_message = Account.sign_typed_data(self._private_key, self._domain, types, message)
        return _to_hex_signature(signed_message.signature.hex())

    def sign_cancel_order(
        self,
        account_id: int,
        market_id: int,
        order_id: int,
        client_order_id: int,
        nonce: int,
        deadline: int,
    ) -> str:
        """Sign an OrderCancel envelope (matching-engine layer).

        Works for both spot and perp markets. `order_id` and `client_order_id`
        are mutually exclusive on the API; pass 0 for the unused field.
        """
        types = {
            "OrderCancel": [
                {"name": "verifyingChainId", "type": "uint64"},
                {"name": "deadline", "type": "uint64"},
                {"name": "cancel", "type": "OrderCancelDetails"},
            ],
            "OrderCancelDetails": [
                {"name": "accountId", "type": "uint64"},
                {"name": "marketId", "type": "uint64"},
                {"name": "orderId", "type": "uint64"},
                {"name": "clOrdId", "type": "uint64"},
                {"name": "nonce", "type": "uint64"},
            ],
        }

        message = {
            "verifyingChainId": self._chain_id,
            "deadline": deadline,
            "cancel": {
                "accountId": account_id,
                "marketId": market_id,
                "orderId": order_id,
                "clOrdId": client_order_id,
                "nonce": nonce,
            },
        }

        signed_message = Account.sign_typed_data(self._private_key, self._domain, types, message)
        return _to_hex_signature(signed_message.signature.hex())

    def sign_mass_cancel(
        self,
        account_id: int,
        market_id: int,
        nonce: int,
        deadline: int,
    ) -> str:
        """Sign a MassCancel envelope (matching-engine layer).

        Works for both spot and perp markets. Pass `market_id=0` to cancel
        across all markets (the API treats omitted `symbol` as wildcard)."""
        types = {
            "MassCancel": [
                {"name": "verifyingChainId", "type": "uint64"},
                {"name": "deadline", "type": "uint64"},
                {"name": "massCancel", "type": "MassCancelDetails"},
            ],
            "MassCancelDetails": [
                {"name": "accountId", "type": "uint64"},
                {"name": "marketId", "type": "uint64"},
                {"name": "nonce", "type": "uint64"},
            ],
        }

        message = {
            "verifyingChainId": self._chain_id,
            "deadline": deadline,
            "massCancel": {
                "accountId": account_id,
                "marketId": market_id,
                "nonce": nonce,
            },
        }

        signed_message = Account.sign_typed_data(self._private_key, self._domain, types, message)
        return _to_hex_signature(signed_message.signature.hex())

    def sign_cancel_all_after(
        self,
        account_id: int,
        timeout_ms: int,
        nonce: int,
        deadline: int,
    ) -> str:
        """Sign a CancelAllAfter envelope (matching-engine layer).

        Arms/refreshes (`timeout_ms` in [5000, 60000]) or disarms
        (`timeout_ms=0`) the account-wide dead-man's-switch countdown."""
        types = {
            "CancelAllAfter": [
                {"name": "verifyingChainId", "type": "uint64"},
                {"name": "deadline", "type": "uint64"},
                {"name": "cancelAllAfter", "type": "CancelAllAfterDetails"},
            ],
            "CancelAllAfterDetails": [
                {"name": "accountId", "type": "uint64"},
                {"name": "timeoutMs", "type": "uint64"},
                {"name": "nonce", "type": "uint64"},
            ],
        }

        message = {
            "verifyingChainId": self._chain_id,
            "deadline": deadline,
            "cancelAllAfter": {
                "accountId": account_id,
                "timeoutMs": timeout_ms,
                "nonce": nonce,
            },
        }

        signed_message = Account.sign_typed_data(self._private_key, self._domain, types, message)
        return _to_hex_signature(signed_message.signature.hex())


def _to_hex_signature(sig_hex: str) -> str:
    """Normalize an eth_account signature hex to a 0x-prefixed string."""
    return sig_hex if sig_hex.startswith("0x") else f"0x{sig_hex}"
