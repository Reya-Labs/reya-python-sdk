from reya_data_feed.consumer import ReyaSocket as LegacyReyaSocket

from reya_data_feed.socket import ReyaSocket
from reya_data_feed.resources.market import MarketResource
from reya_data_feed.resources.wallet import WalletResource

__all__ = ['ReyaSocket', 'LegacyReyaSocket', 'MarketResource', 'WalletResource']
