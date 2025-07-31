"""Market-related WebSocket resources."""
from typing import Dict, Optional, Any
from .common import WebSocketResource, ParameterizedResource, SubscribableResource, SubscribableParameterizedResource

class MarketResource:
    """Container for all market-related WebSocket resources."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the market resource container.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._all_markets = AllMarketsResource(socket)
        self._market_data = MarketDataResource(socket)
        self._market_orders = MarketOrdersResource(socket)
    
    @property
    def all_markets(self) -> 'AllMarketsResource':
        """Access the all markets data resource."""
        return self._all_markets
    
    def market_data(self, market_id: str) -> 'MarketDataSubscription':
        """Get market data for a specific market.
        
        Args:
            market_id: The ID of the market.
            
        Returns:
            A subscription object for the specified market data.
        """
        return self._market_data.for_market(market_id)
    
    def market_orders(self, market_id: str) -> 'MarketOrdersSubscription':
        """Get orders for a specific market.
        
        Args:
            market_id: The ID of the market.
            
        Returns:
            A subscription object for the specified market orders.
        """
        return self._market_orders.for_market(market_id)

class AllMarketsResource(SubscribableResource):
    """Resource for accessing all markets data."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the all markets data resource.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket)
        self.path = "/api/trading/markets/data"
    
    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to all markets data.
        
        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)
    
    def unsubscribe(self) -> None:
        """Unsubscribe from all markets data."""
        self.socket.send_unsubscribe(channel=self.path)

class MarketDataResource(SubscribableParameterizedResource):
    """Resource for accessing market data."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the market data resource.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/market/{market_id}/data")
    
    def for_market(self, market_id: str) -> 'MarketDataSubscription':
        """Create a subscription for a specific market's data.
        
        Args:
            market_id: The ID of the market.
            
        Returns:
            A subscription object for the specified market data.
        """
        return MarketDataSubscription(self.socket, market_id)

class MarketOrdersResource(SubscribableParameterizedResource):
    """Resource for accessing market orders."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the market orders resource.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/market/{market_id}/orders")
    
    def for_market(self, market_id: str) -> 'MarketOrdersSubscription':
        """Create a subscription for a specific market's orders.
        
        Args:
            market_id: The ID of the market.
            
        Returns:
            A subscription object for the specified market orders.
        """
        return MarketOrdersSubscription(self.socket, market_id)

class MarketDataSubscription:
    """Manages a subscription to market data for a specific market."""
    
    def __init__(self, socket: 'ReyaSocket', market_id: str):
        """Initialize a market data subscription.
        
        Args:
            socket: The WebSocket connection to use for this subscription.
            market_id: The ID of the market.
        """
        self.socket = socket
        self.market_id = market_id
        self.path = f"/api/trading/market/{market_id}/data"
    
    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market data.
        
        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)
    
    def unsubscribe(self) -> None:
        """Unsubscribe from market data."""
        self.socket.send_unsubscribe(channel=self.path)

class MarketOrdersSubscription:
    """Manages a subscription to market orders for a specific market."""
    
    def __init__(self, socket: 'ReyaSocket', market_id: str):
        """Initialize a market orders subscription.
        
        Args:
            socket: The WebSocket connection to use for this subscription.
            market_id: The ID of the market.
        """
        self.socket = socket
        self.market_id = market_id
        self.path = f"/api/trading/market/{market_id}/orders"
    
    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to market orders.
        
        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)
    
    def unsubscribe(self) -> None:
        """Unsubscribe from market orders."""
        self.socket.send_unsubscribe(channel=self.path)
