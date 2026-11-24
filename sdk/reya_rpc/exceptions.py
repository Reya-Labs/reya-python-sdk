"""Custom exceptions for the Reya RPC SDK."""


class ReyaRpcError(Exception):
    """Base exception for Reya RPC operations."""


class InvalidChainIdError(ReyaRpcError):
    """Raised when an invalid chain ID is provided."""


class TransactionReceiptError(ReyaRpcError):
    """Raised when transaction receipt cannot be decoded or processed."""


class InvalidTokenAddressError(ReyaRpcError):
    """Raised when a caller-supplied token address is not a valid Ethereum address."""
