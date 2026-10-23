# Strategy examples

Small programs that combine market data, order entry and protection the way a trading bot does.
Try them on devnet1 first.

| File | What it does | Needs a signer |
|---|---|---|
| [`mark_feed.py`](./mark_feed.py) | A reconnecting, staleness-aware mark-price feed for one perp market. | No |
| [`price_cross_entry.py`](./price_cross_entry.py) | Enters once when the mark crosses a level, then arms a stop-loss on the whole position. | Yes: the client loads it from `.env`. A dry run signs and sends nothing. |

## The mark and how fast this reacts

Reya's mark price is recalculated once a second. Reya's own stop-loss and take-profit orders fire on that
mark, and these examples watch the same mark on the market-summary channel
(`/v2/perpMarket/{symbol}/summary`). Nothing that watches the mark can act on a price touched and
reversed within one second. A program also adds its own network time to the API. For a move shorter than
that, rest a limit order on the book.

A mark older than 10 seconds counts as stale, the same limit Reya's own triggers use. A crossing that
happens while the feed is stale or reconnecting is reported, never fired late.

## `mark_feed.py`

```bash
python -m examples.strategies.mark_feed ETHRUSDPERP
```

- Prints each new mark and its age.
- The SDK reconnects and re-subscribes after a `1012`/`1013` close. If it runs out of reconnect attempts,
  the feed starts it again.
- A slow consumer drops the oldest marks, never the newest.

## `price_cross_entry.py`

```bash
python -m examples.strategies.price_cross_entry --symbol ETHRUSDPERP --direction down --level 2492 \
    --side sell --notional 30 --stop-px 2560          # dry run; add --live to send
```

1. **Waits for a crossing.** It fires when the previous fresh mark was on the other side of `--level` and
   the new one is at or past it. Touching the level counts. If the level is already passed at start, it
   waits until the mark moves 0.5% back first.
2. **Sends one IOC limit order** at the mark plus or minus `--bound-bps` (default 25), rounded toward the
   mark to the market's tick. The size is `--notional` at that limit price, rounded down to the market's
   quantity step. It says the real size first: $30 of SOL at 108.15 becomes 0.27 SOL (about $29.20), and
   0.28 would be about $30.28.
3. **Waits until the fill shows in the position**, then arms a `STOP_LOSS` that protects the whole
   position, with an IOC child that may fill up to 1% beyond the trigger.

Without `--live` it prints both orders and sends nothing.

It never resends an entry whose outcome is unclear. A timeout, a dropped connection or
`ORDER_OUTCOME_UNKNOWN_ERROR` stops it with the `clientOrderId` to reconcile. If the stop can't be armed,
it says **POSITION UNPROTECTED** so you can arm one by hand.

Configure it like the other examples, from `.env`:
- `PERP_ACCOUNT_ID_1`: your account ID;
- `PERP_WALLET_ADDRESS_1`: your owner wallet;
- `PERP_PRIVATE_KEY_1`: the signer you approved on the API Keys page;
- the environment values from `.env.example`, plus `REYA_DEX_ID=1` on devnet1 (the exchange ID devnet1 registers).

Use a dedicated account, so the strategy's position and its stop are its own.

**Rate limits.** One run uses two create operations, the entry and the stop. The Standard tier allows 60 a
minute with a burst of 5 per account.

**Going further:**
- **Repeat:** after a firing, wait until the mark moves back past a band before arming again.
- **Take-profit:** add a `TAKE_PROFIT`; it forms an OCO pair with the stop.
- **Pairs:** give each leg its own entry and its own stop. A stop fires on its own market's mark, so a
  rule like "close both legs when ETH hits X" needs your program to send the second leg's close.
