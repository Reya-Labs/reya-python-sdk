"""Wallet-related WebSocket resources."""
from typing import Dict, Optional, Any
from .common import WebSocketResource, ParameterizedResource, SubscribableResource, SubscribableParameterizedResource

class WalletResource:
    """Container for all wallet-related WebSocket resources."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the wallet resource container.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        self.socket = socket
        self._positions = WalletPositionsResource(socket)
        self._orders = WalletOrdersResource(socket)
        self._balances = WalletBalancesResource(socket)
    
    def positions(self, address: str) -> 'WalletPositionsSubscription':
        """Get positions for a specific wallet address.
        
        Args:
            address: The wallet address.
            
        Returns:
            A subscription object for the wallet positions.
        """
        return self._positions.for_wallet(address)
    
    def orders(self, address: str) -> 'WalletOrdersSubscription':
        """Get orders for a specific wallet address.
        
        Args:
            address: The wallet address.
            
        Returns:
            A subscription object for the wallet orders.
        """
        return self._orders.for_wallet(address)
    
    def balances(self, address: str) -> 'WalletBalancesSubscription':
        """Get account balances for a specific wallet address.
        
        Args:
            address: The wallet address.
            
        Returns:
            A subscription object for the wallet balances.
        """
        return self._balances.for_wallet(address)

class WalletPositionsResource(SubscribableParameterizedResource):
    """Resource for accessing wallet positions."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the wallet positions resource.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/positions")
    
    def for_wallet(self, address: str) -> 'WalletPositionsSubscription':
        """Create a subscription for a specific wallet's positions.
        
        Args:
            address: The wallet address.
            
        Returns:
            A subscription object for the wallet positions.
        """
        return WalletPositionsSubscription(self.socket, address)

class WalletOrdersResource(SubscribableParameterizedResource):
    """Resource for accessing wallet orders."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the wallet orders resource.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/orders")
    
    def for_wallet(self, address: str) -> 'WalletOrdersSubscription':
        """Create a subscription for a specific wallet's orders.
        
        Args:
            address: The wallet address.
            
        Returns:
            A subscription object for the wallet orders.
        """
        return WalletOrdersSubscription(self.socket, address)

class WalletBalancesResource(SubscribableParameterizedResource):
    """Resource for accessing wallet account balances."""
    
    def __init__(self, socket: 'ReyaSocket'):
        """Initialize the wallet balances resource.
        
        Args:
            socket: The WebSocket connection to use for this resource.
        """
        super().__init__(socket, "/api/trading/wallet/{address}/accounts/balances")
    
    def for_wallet(self, address: str) -> 'WalletBalancesSubscription':
        """Create a subscription for a specific wallet's account balances.
        
        Args:
            address: The wallet address.
            
        Returns:
            A subscription object for the wallet balances.
        """
        return WalletBalancesSubscription(self.socket, address)

class WalletPositionsSubscription:
    """Manages a subscription to positions for a specific wallet."""
    
    def __init__(self, socket: 'ReyaSocket', address: str):
        """Initialize a wallet positions subscription.
        
        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/positions"
    
    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet positions.
        
        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)
    
    def unsubscribe(self) -> None:
        """Unsubscribe from wallet positions."""
        self.socket.send_unsubscribe(channel=self.path)

class WalletOrdersSubscription:
    """Manages a subscription to orders for a specific wallet."""
    
    def __init__(self, socket: 'ReyaSocket', address: str):
        """Initialize a wallet orders subscription.
        
        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/orders"
    
    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet orders.
        
        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)
    
    def unsubscribe(self) -> None:
        """Unsubscribe from wallet orders."""
        self.socket.send_unsubscribe(channel=self.path)

class WalletBalancesSubscription:
    """Manages a subscription to account balances for a specific wallet."""
    
    def __init__(self, socket: 'ReyaSocket', address: str):
        """Initialize a wallet balances subscription.
        
        Args:
            socket: The WebSocket connection to use for this subscription.
            address: The wallet address.
        """
        self.socket = socket
        self.address = address
        self.path = f"/api/trading/wallet/{address}/accounts/balances"
    
    def subscribe(self, batched: bool = False) -> None:
        """Subscribe to wallet account balances.
        
        Args:
            batched: Whether to receive updates in batches.
        """
        self.socket.send_subscribe(channel=self.path, batched=batched)
    
    def unsubscribe(self) -> None:
        """Unsubscribe from wallet account balances."""
        self.socket.send_unsubscribe(channel=self.path)
