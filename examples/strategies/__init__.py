"""
Strategy-shaped examples that combine market data, order entry and protection.

- `mark_feed.py`: a reconnecting, staleness-aware mark-price feed for one perp market
- `price_cross_entry.py`: enter once when the mark crosses a level, then arm a
  whole-position stop-loss
"""
