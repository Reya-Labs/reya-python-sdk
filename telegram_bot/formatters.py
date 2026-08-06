"""
Message formatting utilities for the Telegram trading bot.

All functions return Markdown-formatted strings suitable for sending via Telegram.
"""

from typing import Any


def _oracle_price_usd(raw: Any) -> str:
    """Convert a raw oracle price (18-decimal integer string) to a USD string."""
    try:
        return f"${float(raw) / 1e18:,.4f}"
    except (TypeError, ValueError):
        return str(raw)


def fmt_prices(prices: list) -> str:
    if not prices:
        return "No price data available."

    lines = ["*Market Prices*\n"]
    for p in prices:
        oracle = _oracle_price_usd(p.oracle_price) if p.oracle_price else "N/A"
        lines.append(f"`{p.symbol}`: {oracle}")
    return "\n".join(lines)


def fmt_single_price(symbol: str, price) -> str:
    if price is None:
        return f"No price data found for `{symbol}`."

    oracle = _oracle_price_usd(price.oracle_price) if price.oracle_price else "N/A"
    return f"*{symbol}*\nOracle price: {oracle}"


def fmt_accounts(accounts: list) -> str:
    if not accounts:
        return "No accounts found for this wallet."

    lines = ["*Accounts*\n"]
    for i, acc in enumerate(accounts, 1):
        lines.append(f"*#{i}* — Account ID: `{acc.id}`")
        if hasattr(acc, "type") and acc.type:
            lines.append(f"  Type: {acc.type}")
    return "\n".join(lines)


def fmt_account_balances(balances: list) -> str:
    if not balances:
        return "No account balances found."

    lines = ["*Account Balances*\n"]
    for b in balances:
        account_id = getattr(b, "account_id", "N/A")
        token = getattr(b, "token_symbol", "N/A")
        balance_raw = getattr(b, "balance", None)
        balance_str = f"{float(balance_raw) / 1e18:,.6f}" if balance_raw else "N/A"
        lines.append(f"Account `{account_id}` — {token}: `{balance_str}`")
    return "\n".join(lines)


def fmt_positions(positions: list) -> str:
    if not positions:
        return "No open positions."

    lines = ["*Open Positions*\n"]
    for pos in positions:
        symbol = getattr(pos, "symbol", "N/A")
        side = "Long" if getattr(pos, "is_long", True) else "Short"
        size_raw = getattr(pos, "size", None)
        size_str = f"{float(size_raw) / 1e18:,.6f}" if size_raw else "N/A"
        entry_raw = getattr(pos, "avg_entry_price", None)
        entry_str = _oracle_price_usd(entry_raw) if entry_raw else "N/A"
        pnl_raw = getattr(pos, "unrealized_pnl", None)
        pnl_str = f"{float(pnl_raw) / 1e18:,.4f}" if pnl_raw else "N/A"
        lines.append(
            f"`{symbol}` — {side} {size_str}\n"
            f"  Entry: {entry_str}  |  uPnL: {pnl_str} rUSD"
        )
    return "\n".join(lines)


def fmt_open_orders(orders: list) -> str:
    if not orders:
        return "No open orders."

    lines = ["*Open Orders*\n"]
    for o in orders:
        order_id = getattr(o, "order_id", "N/A")
        symbol = getattr(o, "symbol", "N/A")
        side = "Buy" if getattr(o, "is_buy", True) else "Sell"
        qty_raw = getattr(o, "qty", None)
        qty_str = f"{float(qty_raw) / 1e18:,.6f}" if qty_raw else "N/A"
        px_raw = getattr(o, "limit_px", None)
        px_str = _oracle_price_usd(px_raw) if px_raw else "N/A"
        order_type = getattr(o, "order_type", "")
        tif = getattr(o, "time_in_force", "")
        status = getattr(o, "status", "")
        lines.append(
            f"ID `{order_id}` — {side} {qty_str} `{symbol}` @ {px_str}\n"
            f"  Type: {order_type} {tif}  |  Status: {status}"
        )
    return "\n".join(lines)


def fmt_order_created(order_type: str, response) -> str:
    order_id = getattr(response, "order_id", "N/A")
    status = getattr(response, "status", "N/A")
    return (
        f"*{order_type} order submitted*\n"
        f"Order ID: `{order_id}`\n"
        f"Status: `{status}`"
    )


def fmt_order_cancelled(response) -> str:
    order_id = getattr(response, "order_id", "N/A")
    status = getattr(response, "status", "N/A")
    return f"*Order cancelled*\nOrder ID: `{order_id}`\nStatus: `{status}`"


def fmt_executions(executions) -> str:
    data = getattr(executions, "data", [])
    if not data:
        return "No trade history found."

    lines = ["*Recent Trades*\n"]
    for ex in data[:10]:  # Show last 10
        symbol = getattr(ex, "symbol", "N/A")
        side = "Buy" if getattr(ex, "is_buy", True) else "Sell"
        qty_raw = getattr(ex, "qty", None)
        qty_str = f"{float(qty_raw) / 1e18:,.6f}" if qty_raw else "N/A"
        px_raw = getattr(ex, "fill_px", None)
        px_str = _oracle_price_usd(px_raw) if px_raw else "N/A"
        lines.append(f"{side} {qty_str} `{symbol}` @ {px_str}")
    if len(data) > 10:
        lines.append(f"_...and {len(data) - 10} more_")
    return "\n".join(lines)


def fmt_market_summaries(summaries: list) -> str:
    if not summaries:
        return "No market summary data available."

    lines = ["*Market Summaries*\n"]
    for s in summaries:
        symbol = getattr(s, "symbol", "N/A")
        last_raw = getattr(s, "last_price", None)
        last_str = _oracle_price_usd(last_raw) if last_raw else "N/A"
        volume_raw = getattr(s, "volume_24h", None)
        volume_str = f"{float(volume_raw) / 1e18:,.2f}" if volume_raw else "N/A"
        lines.append(f"`{symbol}` — Last: {last_str}  Vol 24h: {volume_str}")
    return "\n".join(lines)
