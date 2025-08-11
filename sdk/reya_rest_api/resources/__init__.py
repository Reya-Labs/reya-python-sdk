"""
Resources for the Reya Trading API.
"""

from sdk.reya_rest_api.resources.assets import AssetsResource
from sdk.reya_rest_api.resources.base import BaseResource
from sdk.reya_rest_api.resources.markets import MarketsResource
from sdk.reya_rest_api.resources.orders import OrdersResource
from sdk.reya_rest_api.resources.prices import PricesResource
from sdk.reya_rest_api.resources.wallet import WalletResource

__all__ = ["BaseResource", "OrdersResource", "WalletResource", "MarketsResource", "AssetsResource", "PricesResource"]
