"""
Telegram bot command handlers for Reya trading.

Each handler corresponds to a bot command and delegates to TradingService
for SDK operations and to formatters for message construction.
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from telegram_bot import formatters
from telegram_bot.trading import TradingService

logger = logging.getLogger("telegram_bot.bot")

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

HELP_TEXT = """
*Reya Trading Bot — Commands*

*Market Data*
`/prices` — All market prices
`/price <SYMBOL>` — Price for one symbol
`/markets` — 24h market summaries
`/symbols` — List all available symbols

*Account*
`/accounts` — Your account IDs
`/balance` — Account balances
`/positions` — Open positions
`/orders` — Open orders
`/history` — Recent trade history

*Trading — Perp / Spot IOC (fill-or-kill)*
`/buy <SYMBOL> <QTY> <PRICE>` — Limit buy (IOC)
`/sell <SYMBOL> <QTY> <PRICE>` — Limit sell (IOC)

*Trading — Perp GTC (resting limit order)*
`/buygtc <SYMBOL> <QTY> <PRICE>` — Limit buy (GTC)
`/sellgtc <SYMBOL> <QTY> <PRICE>` — Limit sell (GTC)

*Trigger Orders — Perp only*
`/sl <SYMBOL> <buy|sell> <TRIGGER_PRICE>` — Stop loss
`/tp <SYMBOL> <buy|sell> <TRIGGER_PRICE>` — Take profit

*Order Management*
`/cancel <ORDER_ID>` — Cancel an order

*Examples*
`/price ETHRUSDPERP`
`/buy ETHRUSDPERP 0.01 2000`
`/sellgtc BTCRUSDPERP 0.001 70000`
`/sl ETHRUSDPERP sell 1500`
`/tp ETHRUSDPERP sell 5000`
`/cancel 12345`
"""


async def _reply(update: Update, text: str) -> None:
    """Send a Markdown reply, falling back to plain text on parse errors."""
    try:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(text)


def _get_trading(context: ContextTypes.DEFAULT_TYPE) -> TradingService:
    return context.bot_data["trading"]


# ---------------------------------------------------------------------------
# General commands
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        "*Welcome to the Reya Trading Bot!*\n\n"
        "Trade perpetuals and spot on the Reya exchange directly from Telegram.\n\n"
        + HELP_TEXT,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT)


# ---------------------------------------------------------------------------
# Market data commands
# ---------------------------------------------------------------------------


async def cmd_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        prices = await trading.get_prices()
        await _reply(update, formatters.fmt_prices(prices))
    except Exception as exc:
        logger.exception("Error fetching prices")
        await _reply(update, f"Failed to fetch prices: {exc}")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Usage: `/price <SYMBOL>`\nExample: `/price ETHRUSDPERP`")
        return

    symbol = context.args[0].upper()
    trading = _get_trading(context)
    try:
        price = await trading.get_price(symbol)
        await _reply(update, formatters.fmt_single_price(symbol, price))
    except Exception as exc:
        logger.exception("Error fetching price for %s", symbol)
        await _reply(update, f"Failed to fetch price for `{symbol}`: {exc}")


async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        summaries = await trading.get_market_summaries()
        await _reply(update, formatters.fmt_market_summaries(summaries))
    except Exception as exc:
        logger.exception("Error fetching market summaries")
        await _reply(update, f"Failed to fetch market summaries: {exc}")


async def cmd_symbols(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        perp_defs = await trading.get_market_definitions()
        spot_defs = await trading.get_spot_market_definitions()
        lines = ["*Available Symbols*\n", "*Perpetuals:*"]
        for m in perp_defs:
            lines.append(f"  `{m.symbol}`")
        lines.append("\n*Spot:*")
        for m in spot_defs:
            lines.append(f"  `{m.symbol}`")
        await _reply(update, "\n".join(lines))
    except Exception as exc:
        logger.exception("Error fetching symbols")
        await _reply(update, f"Failed to fetch symbols: {exc}")


# ---------------------------------------------------------------------------
# Account / wallet commands
# ---------------------------------------------------------------------------


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        accounts = await trading.get_accounts()
        await _reply(update, formatters.fmt_accounts(accounts))
    except Exception as exc:
        logger.exception("Error fetching accounts")
        await _reply(update, f"Failed to fetch accounts: {exc}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        balances = await trading.get_account_balances()
        await _reply(update, formatters.fmt_account_balances(balances))
    except Exception as exc:
        logger.exception("Error fetching balances")
        await _reply(update, f"Failed to fetch balances: {exc}")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        positions = await trading.get_positions()
        await _reply(update, formatters.fmt_positions(positions))
    except Exception as exc:
        logger.exception("Error fetching positions")
        await _reply(update, f"Failed to fetch positions: {exc}")


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        orders = await trading.get_open_orders()
        await _reply(update, formatters.fmt_open_orders(orders))
    except Exception as exc:
        logger.exception("Error fetching orders")
        await _reply(update, f"Failed to fetch open orders: {exc}")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trading = _get_trading(context)
    try:
        executions = await trading.get_perp_executions()
        await _reply(update, formatters.fmt_executions(executions))
    except Exception as exc:
        logger.exception("Error fetching trade history")
        await _reply(update, f"Failed to fetch trade history: {exc}")


# ---------------------------------------------------------------------------
# Trading commands — IOC
# ---------------------------------------------------------------------------


def _parse_order_args(args: list[str]) -> tuple[str, str, str]:
    """Parse and validate <SYMBOL> <QTY> <PRICE> args. Returns (symbol, qty, price)."""
    if len(args) < 3:
        raise ValueError("Usage: `<SYMBOL> <QTY> <PRICE>`")
    symbol = args[0].upper()
    qty = args[1]
    price = args[2]
    float(qty)   # validate numeric
    float(price)  # validate numeric
    return symbol, qty, price


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        symbol, qty, price = _parse_order_args(context.args or [])
    except ValueError as exc:
        await _reply(update, f"Invalid arguments. {exc}\nExample: `/buy ETHRUSDPERP 0.01 2000`")
        return

    trading = _get_trading(context)
    await _reply(update, f"Submitting IOC limit buy: {qty} {symbol} @ {price}...")
    try:
        response = await trading.buy_ioc(symbol, qty, price)
        await _reply(update, formatters.fmt_order_created("IOC Buy", response))
    except Exception as exc:
        logger.exception("Error submitting buy order")
        await _reply(update, f"Buy order failed: {exc}")


async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        symbol, qty, price = _parse_order_args(context.args or [])
    except ValueError as exc:
        await _reply(update, f"Invalid arguments. {exc}\nExample: `/sell ETHRUSDPERP 0.01 4000`")
        return

    trading = _get_trading(context)
    await _reply(update, f"Submitting IOC limit sell: {qty} {symbol} @ {price}...")
    try:
        response = await trading.sell_ioc(symbol, qty, price)
        await _reply(update, formatters.fmt_order_created("IOC Sell", response))
    except Exception as exc:
        logger.exception("Error submitting sell order")
        await _reply(update, f"Sell order failed: {exc}")


# ---------------------------------------------------------------------------
# Trading commands — GTC
# ---------------------------------------------------------------------------


async def cmd_buygtc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        symbol, qty, price = _parse_order_args(context.args or [])
    except ValueError as exc:
        await _reply(update, f"Invalid arguments. {exc}\nExample: `/buygtc ETHRUSDPERP 0.01 1800`")
        return

    trading = _get_trading(context)
    await _reply(update, f"Submitting GTC limit buy: {qty} {symbol} @ {price}...")
    try:
        response = await trading.buy_gtc(symbol, qty, price)
        await _reply(update, formatters.fmt_order_created("GTC Buy", response))
    except Exception as exc:
        logger.exception("Error submitting GTC buy order")
        await _reply(update, f"GTC buy order failed: {exc}")


async def cmd_sellgtc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        symbol, qty, price = _parse_order_args(context.args or [])
    except ValueError as exc:
        await _reply(update, f"Invalid arguments. {exc}\nExample: `/sellgtc ETHRUSDPERP 0.01 4200`")
        return

    trading = _get_trading(context)
    await _reply(update, f"Submitting GTC limit sell: {qty} {symbol} @ {price}...")
    try:
        response = await trading.sell_gtc(symbol, qty, price)
        await _reply(update, formatters.fmt_order_created("GTC Sell", response))
    except Exception as exc:
        logger.exception("Error submitting GTC sell order")
        await _reply(update, f"GTC sell order failed: {exc}")


# ---------------------------------------------------------------------------
# Trigger order commands
# ---------------------------------------------------------------------------


def _parse_trigger_args(args: list[str]) -> tuple[str, bool, str]:
    """Parse <SYMBOL> <buy|sell> <TRIGGER_PRICE>. Returns (symbol, is_buy, trigger_px)."""
    if len(args) < 3:
        raise ValueError("Usage: `<SYMBOL> <buy|sell> <TRIGGER_PRICE>`")
    symbol = args[0].upper()
    side = args[1].lower()
    if side not in ("buy", "sell"):
        raise ValueError("Side must be `buy` or `sell`.")
    is_buy = side == "buy"
    trigger_px = args[2]
    float(trigger_px)  # validate numeric
    return symbol, is_buy, trigger_px


async def cmd_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        symbol, is_buy, trigger_px = _parse_trigger_args(context.args or [])
    except ValueError as exc:
        await _reply(
            update,
            f"Invalid arguments. {exc}\n"
            "Example: `/sl ETHRUSDPERP sell 1500`  (SL for long position)\n"
            "Example: `/sl ETHRUSDPERP buy 9000`   (SL for short position)",
        )
        return

    trading = _get_trading(context)
    side_str = "buy" if is_buy else "sell"
    await _reply(update, f"Submitting stop loss: {side_str} {symbol} trigger @ {trigger_px}...")
    try:
        response = await trading.stop_loss(symbol, is_buy, trigger_px)
        await _reply(update, formatters.fmt_order_created("Stop Loss", response))
    except Exception as exc:
        logger.exception("Error submitting stop loss")
        await _reply(update, f"Stop loss failed: {exc}")


async def cmd_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        symbol, is_buy, trigger_px = _parse_trigger_args(context.args or [])
    except ValueError as exc:
        await _reply(
            update,
            f"Invalid arguments. {exc}\n"
            "Example: `/tp ETHRUSDPERP sell 5000`  (TP for long position)\n"
            "Example: `/tp ETHRUSDPERP buy 1500`   (TP for short position)",
        )
        return

    trading = _get_trading(context)
    side_str = "buy" if is_buy else "sell"
    await _reply(update, f"Submitting take profit: {side_str} {symbol} trigger @ {trigger_px}...")
    try:
        response = await trading.take_profit(symbol, is_buy, trigger_px)
        await _reply(update, formatters.fmt_order_created("Take Profit", response))
    except Exception as exc:
        logger.exception("Error submitting take profit")
        await _reply(update, f"Take profit failed: {exc}")


# ---------------------------------------------------------------------------
# Order management
# ---------------------------------------------------------------------------


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _reply(update, "Usage: `/cancel <ORDER_ID>`\nExample: `/cancel 12345`")
        return

    order_id = context.args[0]
    trading = _get_trading(context)
    await _reply(update, f"Cancelling order `{order_id}`...")
    try:
        response = await trading.cancel_order(order_id)
        await _reply(update, formatters.fmt_order_cancelled(response))
    except Exception as exc:
        logger.exception("Error cancelling order %s", order_id)
        await _reply(update, f"Cancel failed: {exc}")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def build_application(token: str, trading: TradingService) -> Application:
    """
    Construct and return the Telegram Application with all handlers registered.

    Args:
        token: Telegram bot token from BotFather.
        trading: Initialised TradingService instance.

    Returns:
        A configured Application ready to run.
    """
    app = Application.builder().token(token).build()

    # Store trading service in bot_data so handlers can access it
    app.bot_data["trading"] = trading

    # Register all command handlers
    handlers = [
        ("start", cmd_start),
        ("help", cmd_help),
        # Market data
        ("prices", cmd_prices),
        ("price", cmd_price),
        ("markets", cmd_markets),
        ("symbols", cmd_symbols),
        # Account
        ("accounts", cmd_accounts),
        ("balance", cmd_balance),
        ("positions", cmd_positions),
        ("orders", cmd_orders),
        ("history", cmd_history),
        # Trading — IOC
        ("buy", cmd_buy),
        ("sell", cmd_sell),
        # Trading — GTC
        ("buygtc", cmd_buygtc),
        ("sellgtc", cmd_sellgtc),
        # Trigger orders
        ("sl", cmd_sl),
        ("tp", cmd_tp),
        # Order management
        ("cancel", cmd_cancel),
    ]

    for command, handler in handlers:
        app.add_handler(CommandHandler(command, handler))

    return app
