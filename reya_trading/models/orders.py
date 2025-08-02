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
    reduce_only: bool = False
    time_in_force: str = field(default=TimeInForce.IOC, repr=False)
    nonce: Optional[int] = None
    deadline: Optional[int] = None
    signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        size_val = str(self.size) if isinstance(self.size, float) else self.size
        price_val = str(self.price) if isinstance(self.price, float) else self.price

        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "size": size_val,
            "price": price_val,
            "reduceOnly": self.reduce_only,
            "timeInForce": self.time_in_force,
            "nonce": self.nonce,
            "deadline": self.deadline,
            "signature": self.signature
        }


@dataclass(frozen=True)
class LimitOrderRequest:
    """Limit (GTC) order request."""
    account_id: str
    market_id: str
    size: Union[float, str]  # Positive for buy, negative for sell
    price: Union[float, str]
    is_buy: bool
    reduce_only: bool = False
    type: Optional[str] = None
    time_in_force: str = field(default="GTC", repr=False)
    nonce: Optional[int] = None
    deadline: Optional[int] = None
    signature: Optional[str] = None
    signer_wallet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:

        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "isBuy": self.is_buy,
            "price": self.price,
            "size": self.size,
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
    nonce: Optional[int] = None
    deadline: Optional[int] = None
    signature: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        trigger_price_val = str(self.trigger_price) if isinstance(self.trigger_price, float) else self.trigger_price
        price_val = str(self.price) if isinstance(self.price, float) else self.price

        return {
            "accountId": self.account_id,
            "marketId": self.market_id,
            "triggerPrice": trigger_price_val,
            "price": price_val,
            "isBuy": self.is_buy,
            "triggerType": self.trigger_type,
            "nonce": self.nonce,
            "deadline": self.deadline,
            "signature": self.signature
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
