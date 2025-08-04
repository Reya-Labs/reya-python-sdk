from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Union
from datetime import datetime

from ..constants.enums import TimeInForce, TpslType, UnifiedOrderType


@dataclass(frozen=True)
class OrderRequest:
    """Base class for order requests."""
    account_id: int
    market_id: int
    exchange_id: int
    is_buy: bool
    price: Union[float, str]
    size: Union[float, str]
    reduce_only: bool = False
    type: Optional[UnifiedOrderType] = None
    signature: Optional[str] = None
    nonce: Optional[int] = None
    signer_wallet: Optional[str] = None
    expires_after: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API request dictionary"""
        raise NotImplementedError("Subclasses must implement to_dict")


@dataclass(frozen=True)
class MarketOrderRequest(OrderRequest):
    """Market (IOC) order request."""

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "exchangeId": self.exchange_id,
            "isBuy": self.is_buy,
            "price": str(self.price),
            "size": str(self.size),
            "reduceOnly": self.reduce_only,
            "type": self.type.to_dict(),
            "signature": self.signature,
            "nonce": str(self.nonce),
            "signerWallet": self.signer_wallet,
            "expiresAfter": self.expires_after
        }

        return result


@dataclass(frozen=True)
class LimitOrderRequest(OrderRequest):
    """Limit (GTC) order request."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "exchangeId": self.exchange_id,
            "isBuy": self.is_buy,
            "price": str(self.price),
            "size": str(self.size),
            "reduceOnly": self.reduce_only,
            "type": self.type.to_dict(),
            "signature": self.signature,
            "nonce": str(self.nonce),
            "signerWallet": self.signer_wallet,
        }


@dataclass(frozen=True)
class TriggerOrderRequest(OrderRequest):
    """Take Profit or Stop Loss order request."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "exchangeId": self.exchange_id,
            "isBuy": self.is_buy,
            "price": str(self.price),
            "size": str(self.size),
            "reduceOnly": self.reduce_only,
            "type": self.type.to_dict(),
            "signature": self.signature,
            "nonce": str(self.nonce),
            "signerWallet": self.signer_wallet
        }


@dataclass(frozen=True)
class CancelOrderRequest:
    """Order cancellation request."""
    order_id: str
    signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "orderId": self.order_id,
            "signature": self.signature
        }


@dataclass
class OrderResponse:
    """Response for order creation or cancellation."""
    order_id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    created_at: Optional[datetime] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api_response(cls, response_data: Dict[str, Any]) -> 'OrderResponse':
        created_at = None
        if "createdAt" in response_data and response_data["createdAt"]:
            try:
                created_at = datetime.fromisoformat(
                    response_data["createdAt"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass
        return cls(
            order_id=response_data.get("orderId"),
            status=response_data.get("status"),
            message=response_data.get("message"),
            created_at=created_at,
            raw_response=response_data
        )
