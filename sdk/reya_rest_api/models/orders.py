from typing import Any, Optional

from dataclasses import dataclass, field
from decimal import Decimal

from sdk.reya_rest_api.constants.enums import UnifiedOrderType


@dataclass(frozen=True)
class OrderIdentifiers:
    """Order identification fields."""

    account_id: int
    market_id: int
    exchange_id: int


@dataclass(frozen=True)
class OrderDetails:
    """Order trading details."""

    is_buy: bool
    price: Optional[Decimal]
    size: Optional[Decimal]
    order_type: UnifiedOrderType
    expires_after: Optional[int] = None
    reduce_only: Optional[bool] = None


@dataclass(frozen=True)
class OrderSignature:
    """Order signature and authentication fields."""

    signature: str
    nonce: int
    signer_wallet: str


@dataclass(frozen=True)
class OrderRequest:
    """Base class for order requests."""

    identifiers: OrderIdentifiers
    details: OrderDetails
    signature_info: OrderSignature

    def to_dict(self) -> dict[str, Any]:
        """Convert to API request dictionary"""
        raise NotImplementedError("Subclasses must implement to_dict")


@dataclass(frozen=True)
class LimitOrderRequest(OrderRequest):
    """Limit (IOC / GTC) order request."""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "accountId": self.identifiers.account_id,
            "marketId": self.identifiers.market_id,
            "exchangeId": self.identifiers.exchange_id,
            "isBuy": self.details.is_buy,
            "price": str(self.details.price),
            "size": str(self.details.size),
            "reduceOnly": self.details.reduce_only,
            "type": self.details.order_type.to_dict(),
            "signature": self.signature_info.signature,
            "nonce": str(self.signature_info.nonce),
            "signerWallet": self.signature_info.signer_wallet,
        }

        if self.details.expires_after is not None:
            result["expiresAfter"] = self.details.expires_after

        return result


@dataclass(frozen=True)
class TriggerOrderRequest(OrderRequest):
    """Take Profit (TP) or Stop Loss (SL) order request."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accountId": self.identifiers.account_id,
            "marketId": self.identifiers.market_id,
            "exchangeId": self.identifiers.exchange_id,
            "isBuy": self.details.is_buy,
            "price": str(self.details.price),
            "size": str(self.details.size),
            "reduceOnly": self.details.reduce_only,
            "type": self.details.order_type.to_dict(),
            "signature": self.signature_info.signature,
            "nonce": str(self.signature_info.nonce),
            "signerWallet": self.signature_info.signer_wallet,
        }


@dataclass(frozen=True)
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
