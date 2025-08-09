"""Custom exceptions for the Reya RPC SDK."""


class ReyaRpcError(Exception):
    """Base exception for Reya RPC operations."""
    pass


class InvalidChainIdError(ReyaRpcError):
    """Raised when an invalid chain ID is provided."""
    pass


class NetworkConfigurationError(ReyaRpcError):
    """Raised when network configuration is missing or invalid."""
    pass


class TransactionReceiptError(ReyaRpcError):
    """Raised when transaction receipt cannot be decoded or processed."""
    pass


class BridgeFeeExceededError(ReyaRpcError):
    """Raised when bridge fee exceeds the specified limit."""
    pass