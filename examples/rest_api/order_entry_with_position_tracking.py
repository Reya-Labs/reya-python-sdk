#!/usr/bin/env python3
"""
Order entry test with position tracking and websocket trade confirmation.

This enhanced example demonstrates:
- Real-time price fetching for cross-market order placement
- IOC, GTC, and SLTP order testing with position tracking
- WebSocket trade confirmation before proceeding to next test
- Position size and price impact monitoring

Requirements:
- PRIVATE_KEY: Your Ethereum private key
- ACCOUNT_ID: Your Reya account ID  
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- API_URL: The API URL (optional, defaults based on chain ID)
- WALLET_ADDRESS: Your wallet address for position tracking
"""
import os
import time
import json
import logging
import asyncio
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from decimal import Decimal
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.constants.enums import LimitOrderType, Limit, TimeInForce
from sdk.reya_websocket import ReyaSocket

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("reya.order_entry_tracking")

class OrderTracker:
    """Tracks orders and their trade confirmations via WebSocket."""
    
    def __init__(self, client: ReyaTradingClient):
        self.client = client
        self.wallet_address = client.wallet_address
        self.account_id = int(client.config.account_id)
        self.confirmed_trades = []  # list of confirmed trades
        self.websocket = None
        self.current_prices = {}
        self.positions_before = {}
        self.positions_after = {}
        
    async def setup_websocket(self):
        """Set up WebSocket connection for trade monitoring."""
        ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
        
        self.websocket = ReyaSocket(
            url=ws_url,
            on_open=self._on_websocket_open,
            on_message=self._on_websocket_message,
        )
        
        await self.websocket.async_connect()
        logger.info("WebSocket connected for trade monitoring")
        
    def _on_websocket_open(self, ws):
        """Handle WebSocket connection open."""
        logger.info("WebSocket opened, subscribing to trade feeds")
        
        ws.wallet.trades(self.wallet_address).subscribe()

        
    def _on_websocket_message(self, ws, message):
        """Handle WebSocket messages for trade confirmations."""
        message_type = message.get("type")
        
        if message_type == "subscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"✅ Subscribed to {channel}")
            
        elif message_type == "channel_data":
            channel = message.get("channel", "unknown")

            if "trades" in channel:
                self.confirmed_trades.append(
                    message['contents']['result']
                )
                
        elif message_type == "ping":
            ws.send(json.dumps({"type": "pong"}))

    async def get_current_prices(self) -> Dict[str, Any]:
        """Fetch current market prices."""
        prices = await self.client.prices.get_prices()
        self.current_prices = prices
        
        # Extract market 1 price (usually ETH)
        market_price = None
        for key, price_data in prices.items():
            if "ETHUSDMARK" in key.upper():
                if "oraclePrice" in price_data:
                    # Convert from wei (18 decimals)
                    oracle_price_wei = price_data["oraclePrice"]
                    market_price = float(oracle_price_wei) / 10**18
                    break
                    
        if market_price:
            logger.info(f"💰 Current market price for Market 1 (ETH): ${market_price:.2f}")
            return {"market_1_price": market_price, "all_prices": prices}
        else:
            logger.warning("Could not find ETH price, using fallback")
            return {"market_1_price": 3000.0, "all_prices": prices}  # Realistic ETH fallback
            
    async def get_positions(self) -> Dict[str, Any]:
        """Get current positions."""
        positions_response = await self.client.get_positions()
        positions = positions_response.get("data", positions_response) if isinstance(positions_response, dict) else (positions_response or [])
        
        position_summary = {}
        for pos in positions:
            if isinstance(pos, dict):
                market_id = pos.get("market_id") or pos.get("marketId")
                size = pos.get("size") or pos.get("base")
                if market_id and size:
                    position_summary[f"market_{market_id}"] = {
                        "size": float(size),
                        "raw": pos
                    }
                    
        logger.info(f"📊 Current positions: {position_summary}")
        return position_summary
            
    async def wait_for_trade_confirmation(self, order_details: dict, timeout: int = 30) -> bool:
        """Wait for trade confirmation via WebSocket."""
        logger.info(f"⏳ Waiting for trade confirmation for {order_details['type']} {order_details['side']} order...")

        
        start_time = time.time()
        initial_confirmed_count = len(self.confirmed_trades)
        
        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            if len(self.confirmed_trades) > initial_confirmed_count:
                # Check if the new trade matches our order
                latest_trade = self.confirmed_trades[-1]
                outcome = True
                if abs(Decimal(latest_trade["executed_base"])/Decimal(10**18)) != Decimal(order_details["size"]):
                    logger.error(f"❌ Trade size mismatch: {abs(Decimal(latest_trade['executed_base']))} vs. {order_details['size']}")
                    outcome = False
                if Decimal(latest_trade["executed_base"])>0 != order_details["is_buy"]:
                    logger.error(f"❌ Trade direction mismatch: {latest_trade['price']>0} vs. {order_details['is_buy']}")
                    outcome = False
                if latest_trade["price"] != order_details["price"]:
                    logger.error(f"❌ Trade price mismatch: {latest_trade['price']} vs. {order_details['price']}")
                    outcome = False
                if latest_trade["market_id"] != order_details["market_id"]:
                    logger.error(f"❌ Trade market ID mismatch: {latest_trade['market_id']} vs. {order_details['market_id']}")
                return outcome

            await asyncio.sleep(0.5)

        logger.error(f"️ ❌ Trade confirmation timed out for {order_details['type']} {order_details['side']} order")
        return False
        
    async def create_ioc_order(self, market_id: int, is_buy: bool, market_price: str, size: str) -> Optional[dict]:
        """Create IOC order with cross-market pricing."""
        # Calculate limit price with ±10 offset from market price for cross execution
        price_offset = 10.0
        if is_buy:
            limit_price = market_price + price_offset  # Buy above market for immediate fill
        else:
            limit_price = market_price - price_offset  # Sell below market for immediate fill
            
        order_type = LimitOrderType(limit=Limit(time_in_force=TimeInForce.IOC))
        
        side_text = "BUY" if is_buy else "SELL" 
        logger.info(f"📤 Creating IOC {side_text} order: size={size}, limit_price=${limit_price:.2f} (market: ${market_price:.2f})")
        
        response = await self.client.create_limit_order(
            market_id=market_id,
            is_buy=is_buy,
            price=str(limit_price),
            size=size,
            order_type=order_type,
            reduce_only=False
        )
        
        order_details = {
            "type": "IOC",
            "side": side_text,
            "market_id": market_id,
            "is_buy": is_buy,
            "price": limit_price,
            "size": size
        }
        
        if hasattr(response, 'raw_response') and response.raw_response.get('success', False):
            tx_hash = response.raw_response.get('transactionHash')
            if tx_hash:
                logger.info(f"✅ IOC {side_text} order created with ID: {tx_hash}")
                return order_details
                
        logger.error(f"❌ IOC {side_text} order failed: {response}")
        raise ValueError(f"IOC {side_text} order failed: {response}")
            
    async def create_gtc_order(self, market_id: int, is_buy: bool, market_price: str, size: str) -> Optional[dict]:
        """Create GTC order with crossed pricing for immediate execution."""
        # Use crossed pricing so GTC orders also execute immediately
        price_offset = 20.0  # Larger offset than IOC for more aggressive crossing
        if is_buy:
            limit_price = market_price + price_offset  # Buy above market for immediate fill
        else:
            limit_price = market_price - price_offset  # Sell below market for immediate fill
            
        order_type = LimitOrderType(limit=Limit(time_in_force=TimeInForce.GTC))
        
        side_text = "BUY" if is_buy else "SELL"
        logger.info(f"📤 Creating GTC {side_text} order: size={size}, limit_price=${limit_price:.2f} (market: ${market_price:.2f})")
        
        response = await self.client.create_limit_order(
            market_id=market_id,
            is_buy=is_buy,
            price=str(limit_price),
            size=size,
            order_type=order_type,
            reduce_only=False
        )
        
        order_details = {
            "type": "GTC",
            "side": side_text,
            "market_id": market_id,
            "is_buy": is_buy,
            "price": limit_price,
            "size": size
        }
        
        if hasattr(response, 'raw_response') and response.raw_response.get('success', False):
            order_id = response.raw_response.get('orderId')
            if order_id:
                logger.info(f"✅ GTC {side_text} order created with ID: {order_id}")
                return order_details
                
        logger.error(f"❌ GTC {side_text} order failed: {response}")
        raise Exception(f"❌ GTC {side_text} order failed: {response}")
            
    async def create_stop_loss_order(self, market_id: int, is_buy: bool, market_price: float) -> Optional[dict]:
        """Create Stop Loss order with crossed trigger for immediate execution."""
        # Set trigger prices that are already crossed so they execute immediately
        if is_buy:
            # Stop loss for short position - set trigger below current market so it triggers immediately
            trigger_price = market_price - 50.0
            limit_price = market_price + 50.0
            # TODO: if we set trigger and limit price both to -50 here, we will get the infinite UnacceptablePrice issue in the CO bot...
        else:
            # Stop loss for long position - set trigger above current market so it triggers immediately
            trigger_price = market_price + 50.0
            limit_price = market_price - 50.0
            
        side_text = "BUY" if is_buy else "SELL"
        logger.info(f"📤 Creating Stop Loss {side_text} order: trigger_price=${trigger_price:.2f} (market: ${market_price:.2f})")
        
        response = await self.client.create_stop_loss_order(
            market_id=market_id,
            is_buy=is_buy,
            trigger_price=str(trigger_price),
            price=str(limit_price),
        )
        
        order_details = {
            "type": "SL",
            "side": side_text,
            "market_id": market_id,
            "is_buy": is_buy,
            "price": trigger_price,
            "trigger_price": trigger_price,
        }

        if hasattr(response, 'raw_response') and response.raw_response.get('success', False):
            order_id = response.raw_response.get('orderId')
            if order_id:
                logger.info(f"✅ Stop Loss {side_text} order created with ID: {order_id}")
                return order_details
                
        logger.error(f"❌ Stop Loss {side_text} order failed: {response}")
        raise ValueError(f"❌ Stop Loss {side_text} order failed: {response}")
            
    async def create_take_profit_order(self, market_id: int, is_buy: bool, market_price: str) -> Optional[dict]:
        """Create Take Profit order with crossed trigger for immediate execution."""
        # Set trigger prices that are already crossed so they execute immediately
        if is_buy:
            # Take profit for short position - set trigger above current market so it triggers immediately
            trigger_price = market_price + 30.0
        else:
            # Take profit for long position - set trigger below current market so it triggers immediately
            trigger_price = market_price - 30.0
            
        side_text = "BUY" if is_buy else "SELL"
        logger.info(f"📤 Creating Take Profit {side_text} order: trigger_price=${trigger_price:.2f} (market: ${market_price:.2f})")
        
        response = await self.client.create_take_profit_order(
            market_id=market_id,
            is_buy=is_buy,
            trigger_price=str(trigger_price),
            price=str(trigger_price),
        )
        
        order_details = {
            "type": "TP",
            "side": side_text,
            "market_id": market_id,
            "is_buy": is_buy,
            "price": trigger_price,
            "trigger_price": trigger_price,
        }
        
        if hasattr(response, 'raw_response') and response.raw_response.get('success', False):
            order_id = response.raw_response.get('orderId')
            if order_id:
                logger.info(f"✅ Take Profit {side_text} order created with ID: {order_id}")
                return order_details
                
        logger.error(f"❌ Take Profit {side_text} order failed: {response}")
        raise ValueError(f"❌ Take Profit {side_text} order failed: {response}")

    async def establish_long_position(self, market_id: int, market_price: str, size: str) -> bool:
        """Establish a long position for TPSL testing."""
        logger.info(f"📈 Establishing long position: size={size}")

        position_order = await self.create_ioc_order(
            market_id=market_id,
            is_buy=True,  # Buy to go long
            market_price=market_price,
            size=size
        )

        if position_order:
            confirmed = await self.wait_for_trade_confirmation(position_order)
            if confirmed:
                logger.info(f"✅ Long position established successfully")
                return True

        logger.error(f"❌ Failed to establish long position")
        return True

    async def establish_short_position(self, market_id: int, market_price: str, size: str) -> bool:
        """Establish a short position for TPSL testing."""
        logger.info(f"📉 Establishing short position: size={size}")

        position_order = await self.create_ioc_order(
            market_id=market_id,
            is_buy=False,  # Sell to go short
            market_price=market_price,
            size=size
        )

        if position_order:
            confirmed = await self.wait_for_trade_confirmation(position_order)
            if confirmed:
                logger.info(f"✅ Short position established successfully")
                return True

        logger.error(f"❌ Failed to establish short position")
        return True


def print_separator(title: str):
    """Print a section separator."""
    logger.info("=" * 60)
    logger.info(f" {title} ")
    logger.info("=" * 60)


async def test_order_flow():
    logger.info("🚀 Starting order testing with position tracking...")
    
    # Load environment variables
    load_dotenv()
    
    # Verify required environment variables
    required_vars = ['PRIVATE_KEY', 'ACCOUNT_ID', 'WALLET_ADDRESS']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        return
        
    # Create client and tracker
    client = ReyaTradingClient()
    tracker = OrderTracker(client)
    
    logger.info(f"✅ Client initialized")
    logger.info(f"   Account ID: {client.config.account_id}")
    logger.info(f"   Chain ID: {client.config.chain_id}")
    logger.info(f"   Wallet: {client.wallet_address}")
    
    # Setup WebSocket for trade monitoring
    await tracker.setup_websocket()

    # Wait a moment for WebSocket to establish
    await asyncio.sleep(2)
    
    print_separator("FETCHING CURRENT MARKET DATA")
    
    # Get current prices
    price_data = await tracker.get_current_prices()
    market_price = price_data["market_1_price"]
    
    # Get initial positions  
    tracker.positions_before = await tracker.get_positions()
    
    """print_separator("TESTING IOC ORDERS WITH CROSS-MARKET PRICING")
    
    # Test IOC Buy Order
    buy_order_details = await tracker.create_ioc_order(
        market_id=1,
        is_buy=True, 
        market_price=market_price,
        size="0.01"
    )
    
    if buy_order_details:
        confirmed = await tracker.wait_for_trade_confirmation(buy_order_details)
        if not confirmed:
            logger.warning("IOC Buy order may not have filled immediately")

    # Small delay between orders
    await asyncio.sleep(2)
    
    # Test IOC Sell Order
    sell_order_details = await tracker.create_ioc_order(
        market_id=1,
        is_buy=False,
        market_price=market_price, 
        size="0.011"
    )
    
    if sell_order_details:
        confirmed = await tracker.wait_for_trade_confirmation(sell_order_details)
        if not confirmed:
            logger.warning("IOC Sell order may not have filled immediately")
            
    print_separator("TESTING GTC ORDERS WITH CROSSED PRICING")
    
    # Test GTC orders (these should fill immediately with crossed pricing)
    gtc_buy_details = await tracker.create_gtc_order(
        market_id=1,
        is_buy=True,
        market_price=market_price,
        size="0.012"
    )
    
    if gtc_buy_details:
        confirmed = await tracker.wait_for_trade_confirmation(gtc_buy_details)
        if not confirmed:
            logger.warning("GTC Buy order did not fill as expected")

    await asyncio.sleep(2)

    gtc_sell_details = await tracker.create_gtc_order(
        market_id=1, 
        is_buy=False,
        market_price=market_price,
        size="0.013"
    )
    
    if gtc_sell_details:
        confirmed = await tracker.wait_for_trade_confirmation(gtc_sell_details)
        if not confirmed:
            logger.warning("GTC Sell order did not fill as expected")"""
    
    print_separator("ESTABLISHING POSITIONS FOR TPSL TESTING")

    # First establish a long position for testing SL/TP orders
    long_established = await tracker.establish_long_position(
        market_id=1,
        market_price=market_price,
        size="0.014"
    )
    
    if long_established:
        await asyncio.sleep(1)

        print_separator("TESTING STOP LOSS & TAKE PROFIT FOR LONG POSITION")

        # Test Stop Loss for long position (sell when price drops)
        sl_long_details = await tracker.create_stop_loss_order(
            market_id=1,
            is_buy=False,  # Sell to close long position
            market_price=market_price
        )

        sl_long_details["size"] = "0.014"

        if sl_long_details:
            confirmed = await tracker.wait_for_trade_confirmation(sl_long_details)
            if not confirmed:
                logger.warning("Stop Loss for long position did not fill as expected")

        await asyncio.sleep(2)

    long_established = await tracker.establish_long_position(
        market_id=1,
        market_price=market_price,
        size="0.015"
    )

    if long_established:
        # Test Take Profit for long position (sell when price rises)
        tp_long_details = await tracker.create_take_profit_order(
            market_id=1,
            is_buy=False,  # Sell to close long position
            market_price=market_price
        )

        tp_long_details["size"] = "0.015"

        if tp_long_details:
            confirmed = await tracker.wait_for_trade_confirmation(tp_long_details)
            if not confirmed:
                logger.warning("Take Profit for long position did not fill as expected")

    await asyncio.sleep(2)

    # Establish a short position for testing SL/TP orders
    short_established = await tracker.establish_short_position(
        market_id=1,
        market_price=market_price,
        size="0.016"
    )

    if short_established:
        await asyncio.sleep(1)

        print_separator("TESTING STOP LOSS & TAKE PROFIT FOR SHORT POSITION")

        # Test Stop Loss for short position (buy when price rises)
        sl_short_details = await tracker.create_stop_loss_order(
            market_id=1,
            is_buy=True,  # Buy to close short position
            market_price=market_price
        )

        sl_short_details["size"] = "0.016"

        if sl_short_details:
            confirmed = await tracker.wait_for_trade_confirmation(sl_short_details)
            if not confirmed:
                logger.warning("Stop Loss for short position did not fill as expected")

        await asyncio.sleep(2)

    short_established = await tracker.establish_short_position(
        market_id=1,
        market_price=market_price,
        size="0.017"
    )

    if short_established:
        tp_short_details = await tracker.create_take_profit_order(
            market_id=1,
            is_buy=True,  # Buy to close short position
            market_price=market_price
        )

        tp_short_details["size"] = "0.017"

        if tp_short_details:
            confirmed = await tracker.wait_for_trade_confirmation(tp_short_details)
            if not confirmed:
                logger.warning("Take Profit for short position did not fill as expected")

    # Wait a moment for any final trades to arrive
    await asyncio.sleep(3)
    
    print_separator("POSITION TRACKING RESULTS")
    
    # Get final positions
    tracker.positions_after = await tracker.get_positions()
    
    # Compare positions
    logger.info("📊 Position Changes:")
    
    for market_key in set(list(tracker.positions_before.keys()) + list(tracker.positions_after.keys())):
        before_size = tracker.positions_before.get(market_key, {}).get("size", 0.0)
        after_size = tracker.positions_after.get(market_key, {}).get("size", 0.0)
        change = after_size - before_size
        
        if abs(change) > 0.0001:  # Only show meaningful changes
            logger.info(f"   {market_key}: {before_size:.6f} → {after_size:.6f} (change: {change:+.6f})")
            
    # Summary of confirmed trades
    logger.info(f"🎯 Trade Confirmations: {len(tracker.confirmed_trades)} confirmed trades")
    
    for confirmed_trade in tracker.confirmed_trades:
        order_details = confirmed_trade["order_details"]
        logger.info(f"   ✅ {order_details.get('type', 'Unknown')} {order_details.get('side', 'Unknown')} order: FILLED")
        
    print_separator("TESTING COMPLETE")
    
    logger.info("🎉 Order testing completed!")
    logger.info("📊 Review position changes and trade confirmations above")
    logger.info("💡 All orders used crossed pricing and should have filled immediately")
    logger.info("📝 Check WebSocket feed for real-time trade confirmations")
    logger.info("📝 Check WebSocket feed for real-time trade confirmations")

    # Close WebSocket connection
    if tracker.websocket:
        # Give a moment for any final messages
        await asyncio.sleep(1)

"""
NOTE: Currently, if an SLTP order is submitted before the CO bot is aware of a new position being created, 
the SLTP order will be cancelled, and the only way for the user to be aware of what is happening to the order is to listen
to openOrder updates. Therefore, when sending an IOC and an SLTP back to back, one will have to wait a few seconds to send
 the SLTP, otherwise it will always be automatically cancelled."""

if __name__ == "__main__":
    asyncio.run(test_order_flow())