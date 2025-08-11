from typing import Any, Optional

from dataclasses import dataclass, field
from decimal import Decimal

from sdk.reya_rest_api.constants.enums import UnifiedOrderType


@dataclass(frozen=True)
class OrderRequest:
    """Base class for order requests with all necessary fields."""

    account_id: int
    market_id: int
    exchange_id: int
    is_buy: bool
    price: Decimal
    size: Optional[Decimal]
    order_type: UnifiedOrderType
    expires_after: Optional[int]
    reduce_only: Optional[bool]
    signature: str
    nonce: int
    signer_wallet: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to API request dictionary"""
        raise NotImplementedError("Subclasses must implement to_dict")


@dataclass(frozen=True)
class LimitOrderRequest(OrderRequest):
    """Limit (IOC / GTC) order request."""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "exchangeId": self.exchange_id,
            "isBuy": self.is_buy,
            "price": str(self.price),
            "size": str(self.size),
            "reduceOnly": self.reduce_only,
            "type": self.order_type.to_dict(),
            "signature": self.signature,
            "nonce": str(self.nonce),
            "signerWallet": self.signer_wallet,
        }

        if self.expires_after is not None:
            result["expiresAfter"] = self.expires_after

        return result


@dataclass(frozen=True)
class TriggerOrderRequest(OrderRequest):
    """Take Profit (TP) or Stop Loss (SL) order request."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "exchangeId": self.exchange_id,
            "isBuy": self.is_buy,
            "price": str(self.price),
            "reduceOnly": self.reduce_only,
            "type": self.order_type.to_dict(),
            "signature": self.signature,
            "nonce": str(self.nonce),
            "signerWallet": self.signer_wallet,
        }


@dataclass
class CancelOrderRequest:
    """Order cancellation request."""

    order_id: str
    signature: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"orderId": self.order_id, "signature": self.signature}


@dataclass
class CreateOrderResponse:
    """Response for order creation."""

    success: bool
    order_id: Optional[str] = None
    transaction_hash: Optional[str] = None
    error: Optional[str] = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, response_data: dict[str, Any]) -> "CreateOrderResponse":
        return cls(
            success=bool(response_data.get("success", False)),
            order_id=response_data.get("orderId"),
            transaction_hash=response_data.get("transactionHash"),
            error=response_data.get("error"),
            raw_response=response_data,
        )


@dataclass
class CancelOrderResponse:
    """Response for order cancellation."""

    success: bool
    order_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, response_data: dict[str, Any]) -> "CancelOrderResponse":
        return cls(
            success=bool(response_data.get("success", False)),
            order_id=response_data.get("orderId"),
            error=response_data.get("error"),
            raw_response=response_data,
        )
