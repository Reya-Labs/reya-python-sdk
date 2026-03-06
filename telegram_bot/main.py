"""
Entry point for the Reya Telegram trading bot.

Usage:
    python -m telegram_bot.main

Required environment variables:
    TELEGRAM_BOT_TOKEN      — Bot token from @BotFather
    PERP_WALLET_ADDRESS_1   — Owner wallet address
    PERP_PRIVATE_KEY_1      — Private key for signing orders
    PERP_ACCOUNT_ID_1       — Reya account ID

Optional:
    CHAIN_ID                — 1729 (mainnet, default) or 89346162 (testnet)
    REYA_API_URL            — Override the Reya REST API base URL
    ALLOWED_USER_IDS        — Comma-separated Telegram user IDs allowed to trade
                              (if unset, all users can interact with the bot)
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from sdk.reya_rest_api.config import TradingConfig
from telegram_bot.bot import build_application
from telegram_bot.trading import TradingService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("telegram_bot.main")


def _load_env() -> dict:
    """Load and validate required environment variables."""
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is required. Set it in .env or as an environment variable.")
        sys.exit(1)

    allowed_ids_raw = os.environ.get("ALLOWED_USER_IDS", "")
    allowed_user_ids: set[int] = set()
    if allowed_ids_raw.strip():
        for uid in allowed_ids_raw.split(","):
            uid = uid.strip()
            if uid:
                try:
                    allowed_user_ids.add(int(uid))
                except ValueError:
                    logger.warning("Ignoring invalid ALLOWED_USER_IDS entry: %r", uid)

    return {"token": token, "allowed_user_ids": allowed_user_ids}


async def _main() -> None:
    env = _load_env()
    token: str = env["token"]
    allowed_user_ids: set[int] = env["allowed_user_ids"]

    # Build Reya trading config from environment
    try:
        config = TradingConfig.from_env()
    except ValueError as exc:
        logger.error("Failed to load trading configuration: %s", exc)
        sys.exit(1)

    logger.info("Connecting to Reya API: %s", config.api_url)
    logger.info("Wallet: %s", config.owner_wallet_address)
    logger.info("Chain ID: %d (%s)", config.chain_id, "mainnet" if config.is_mainnet else "testnet")

    if allowed_user_ids:
        logger.info("Access restricted to user IDs: %s", allowed_user_ids)
    else:
        logger.warning("ALLOWED_USER_IDS not set — all Telegram users can interact with the bot")

    # Initialise trading service
    trading = TradingService(config=config)
    try:
        await trading.start()
    except Exception as exc:
        logger.error("Failed to start trading service: %s", exc)
        sys.exit(1)

    # Build the Telegram application
    app = build_application(token=token, trading=trading)

    # Inject access control middleware if user IDs are restricted
    if allowed_user_ids:
        _apply_access_control(app, allowed_user_ids)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        # Run polling (blocking until KeyboardInterrupt or stop signal)
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Wait until the bot is told to stop
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
    finally:
        logger.info("Shutting down…")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await trading.stop()


def _apply_access_control(app, allowed_user_ids: set[int]) -> None:
    """
    Add a pre-handler that rejects updates from users not in the allow-list.

    Uses a TypeHandler with group=-1 so it runs before any command handler.
    """
    from telegram import Update
    from telegram.ext import TypeHandler

    async def _check_user(update: Update, context) -> None:
        if not update.effective_user:
            return
        uid = update.effective_user.id
        if uid not in allowed_user_ids:
            logger.warning("Rejected update from unauthorised user %d", uid)
            if update.message:
                await update.message.reply_text("You are not authorised to use this bot.")
            raise Exception("Unauthorised")

    app.add_handler(TypeHandler(Update, _check_user), group=-1)


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")


if __name__ == "__main__":
    main()
