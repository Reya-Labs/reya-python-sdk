from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Union
from datetime import datetime

from ..constants.enums import TimeInForce, TpslType


@dataclass(frozen=True)
class OrderRequest:
    """Base class for order requests."""
    account_id: int
    market_id: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API request dictionary"""
        raise NotImplementedError("Subclasses must implement to_dict")


@dataclass(frozen=True)
class MarketOrderRequest(OrderRequest):
    """Market (IOC) order request."""
    size: Union[float, str]  # Positive for buy, negative for sell
    price: Union[float, str]  # Limit price
    is_buy: bool
    reduce_only: bool = False
    nonce: Optional[int] = None
    signature: Optional[str] = None
    signer_wallet: Optional[str] = None
    expires_after: int = None

    def to_dict(self) -> Dict[str, Any]:
        size_val = str(self.size) if isinstance(self.size, float) else self.size
        price_val = str(self.price) if isinstance(self.price, float) else self.price

        result = {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "isBuy": self.is_buy,
            "price": price_val,
            "size": size_val,
            "reduceOnly": self.reduce_only,
            "type": {
                "limit": {
                    "timeInForce": "IOC"
                }
            },
            "signature": self.signature,
            "nonce": self.nonce,
            "signerWallet": self.signer_wallet,
            "expiresAfter": self.expires_after
        }

        return result


@dataclass(frozen=True)
class LimitOrderRequest:
    """Limit (GTC) order request."""
    account_id: int
    market_id: int
    size: Union[float, str]  # Positive for buy, negative for sell
    price: Union[float, str]
    is_buy: bool
    reduce_only: bool = False
    type: Optional[Dict[str, Any]] = None
    nonce: Optional[int] = None
    signature: Optional[str] = None
    signer_wallet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "isBuy": self.is_buy,
            "price": str(self.price),
            "size": str(self.size),
            "reduceOnly": self.reduce_only,
            "type": self.type,
            "signature": self.signature,
            "nonce": self.nonce,
            "signerWallet": self.signer_wallet,
        }


@dataclass(frozen=True)
class TriggerOrderRequest(OrderRequest):
    """Take Profit or Stop Loss order request."""
    trigger_price: Union[float, str]
    price: Union[float, str]
    is_buy: bool
    trigger_type: str  # TP or SL
    size: Union[float, str] = ""  # Usually 0 for SL/TP orders
    reduce_only: bool = False
    nonce: Optional[int] = None
    signature: Optional[str] = None
    signer_wallet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        trigger_price_val = str(self.trigger_price) if isinstance(self.trigger_price, float) else self.trigger_price
        price_val = str(self.price) if isinstance(self.price, float) else self.price

        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "isBuy": self.is_buy,
            "price": price_val,
            "size": str(self.size),
            "reduceOnly": self.reduce_only,
            "type": {
                "trigger": {
                    "triggerPx": trigger_price_val,
                    "tpsl": self.trigger_type
                }
            },
            "signature": self.signature,
            "nonce": self.nonce,
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
