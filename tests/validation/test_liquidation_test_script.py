"""Offline coverage for the operator-run liquidation fixture."""

from types import SimpleNamespace

from decimal import Decimal as D
from unittest.mock import AsyncMock, Mock

import pytest
from eth_abi import decode

from examples.liquidation_test import __main__ as cli
from examples.liquidation_test.readiness import OWNER, Readiness, signer_from_env
from examples.liquidation_test.sizing import positive, quote


def inputs(is_buy=True):
    return dict(
        balance=D(50),
        mark=D(2000),
        lmr_rate=D("0.03"),
        fee_rate=D("0.0003"),
        buffer=D(1),
        slippage_bps=D(5),
        tick=D("0.1"),
        step=D("0.001"),
        minimum=D("0.001"),
        levels=[{"px": "2000", "qty": "100"}],
        is_buy=is_buy,
        max_notional=D(10000),
    )


@pytest.mark.parametrize("is_buy", [True, False])
def test_maximum_lot_preserves_buffer_including_fee_and_adverse_fill(is_buy):
    data = inputs(is_buy)
    plan = quote(**data)
    qty, limit = D(plan["qty"]), D(plan["limit_px"])
    direction = 1 if is_buy else -1
    after = data["balance"] - qty * (direction * (limit - data["mark"]) + limit * data["fee_rate"])
    lmr = qty * data["mark"] * data["lmr_rate"]
    cost = data["mark"] * data["lmr_rate"] + limit * data["fee_rate"] + direction * (limit - data["mark"])
    assert after - lmr == D(plan["estimated_liquidation_delta_at_limit"])
    assert after - lmr >= data["buffer"]
    assert after - lmr - data["step"] * cost < data["buffer"]
    assert qty % data["step"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("buffer", D(0)),
        ("buffer", D(50)),
        ("max_notional", D(10)),
        ("minimum", D(10)),
        ("slippage_bps", D(-1)),
        ("fee_rate", D(-1)),
        ("levels", []),
        ("levels", [{"px": "2000", "qty": "0.001"}]),
    ],
)
def test_unachievable_plan_stops(field, value):
    data = inputs()
    data[field] = value
    with pytest.raises(ValueError):
        quote(**data)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_invalid_positive(value):
    with pytest.raises(ValueError):
        positive(value)


def test_signer_env_requires_mainnet_owner_and_redacts_invalid_key(tmp_path):
    path = tmp_path / "key.env"
    path.write_text(f"CHAIN_ID=89346162\nPERP_WALLET_ADDRESS_1={OWNER}\nPERP_PRIVATE_KEY_1=secret\n")
    with pytest.raises(ValueError, match="mainnet"):
        signer_from_env(str(path), "PERP_PRIVATE_KEY_1", OWNER)
    path.write_text(f"CHAIN_ID=1729\nPERP_WALLET_ADDRESS_1={OWNER}\nPERP_PRIVATE_KEY_1=secret\n")
    with pytest.raises(ValueError, match="value redacted") as error:
        signer_from_env(str(path), "PERP_PRIVATE_KEY_1", OWNER)
    assert "secret" not in str(error.value)


def funding_reader():
    read = Readiness.__new__(Readiness)
    read.owner = OWNER
    read.core = Mock(address="0x" + "11" * 20)
    read.rusd = Mock(address="0x" + "22" * 20)
    read.core.functions.execute.return_value._encode_transaction_data.return_value = "0x1234"
    read.assert_empty = Mock()
    read.margin = Mock(
        side_effect=lambda account: {
            "margin_balance": "112" if account == 1 else "0",
            "real_balance": "112" if account == 1 else "0",
        }
    )
    return read


def test_funding_is_one_simulated_unsigned_owner_transaction():
    read = funding_reader()
    tx = read.funding_plan(1, [2, 3], D(50))
    source, commands = read.core.functions.execute.call_args.args
    assert source == 1
    assert len(commands) == 2
    for target, (command, payload, market, exchange) in zip([2, 3], commands):
        assert (command, market, exchange) == (4, 0, 0)
        assert decode(["(uint128,address,uint256)"], payload)[0] == (target, read.rusd.address, 50_000_000)
    read.core.functions.execute.return_value.call.assert_called_once_with({"from": OWNER})
    assert tx["from"] == OWNER and tx["chainId"] == 1729 and tx["data"] == "0x1234"


@pytest.mark.parametrize(
    "source,targets,amount", [(1, [1, 2], D(1)), (1, [2, 2], D(1)), (1, [2, 3], D(57)), (1, [2, 3], D("0.0000001"))]
)
def test_funding_rejects_bad_targets_amounts(source, targets, amount):
    with pytest.raises(ValueError):
        funding_reader().funding_plan(source, targets, amount)


def test_funding_rejects_already_funded_destination():
    read = funding_reader()
    read.margin.side_effect = lambda _: {"margin_balance": "1", "real_balance": "112"}
    with pytest.raises(ValueError, match="already funded"):
        read.funding_plan(1, [2, 3], D(1))


def open_args():
    return cli.parser().parse_args(
        [
            "open",
            "--long-account",
            "2",
            "--short-account",
            "3",
            "--symbol",
            "ETHRUSDPERP",
            "--max-collateral",
            "50",
            "--max-notional",
            "10000",
            "--margin-buffer",
            "1",
            "--slippage-bps",
            "5",
        ]
    )


async def test_default_open_never_loads_key_or_executes(monkeypatch):
    monkeypatch.setattr(cli, "Readiness", Mock(return_value=SimpleNamespace(owner=OWNER)))
    monkeypatch.setattr(cli, "build_plan", Mock(return_value={}))
    signer, execute = Mock(), AsyncMock()
    monkeypatch.setattr(cli, "signer_from_env", signer)
    monkeypatch.setattr(cli, "execute_plans", execute)
    await cli.main(open_args())
    signer.assert_not_called()
    execute.assert_not_called()


def test_legacy_mark_feed_rejected():
    read = Mock(spec=Readiness)
    read.margin.return_value = {"margin_balance": "50", "real_balance": "50"}
    read.api.side_effect = [[{"symbol": "ETHRUSDPERP"}], {"throttledOraclePrice": "2000"}]
    with pytest.raises(ValueError, match="Cutover markPrice"):
        cli.build_plan(read, open_args(), 2, True)


@pytest.mark.parametrize("age_ms", [-3000, 6000])
def test_stale_or_future_feed_rejected(age_ms):
    with pytest.raises(ValueError, match="stale or future"):
        cli.fresh(cli.time.time_ns() // 1_000_000 - age_ms, 5)


@pytest.mark.parametrize("failure", ["partial", "timeout", "settlement", "none"])
async def test_no_second_leg_until_full_fill_and_settlement(monkeypatch, tmp_path, failure):
    args = open_args()
    args.journal = str(tmp_path / "batch.jsonl")
    read = SimpleNamespace(owner=OWNER)
    plans = [
        dict(
            account_id=a,
            side=s,
            symbol="ETHRUSDPERP",
            qty="1",
            limit_px="2000",
            planned_at_ms=cli.time.time_ns() // 1_000_000,
        )
        for a, s in [(2, "LONG"), (3, "SHORT")]
    ]
    monkeypatch.setattr(cli, "build_plan", lambda _, __, a, ___: plans[a - 2])
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.create_limit_order.return_value = SimpleNamespace(
        status=SimpleNamespace(value="CANCELLED" if failure == "partial" else "FILLED"),
        order_id="123",
        cum_qty="0.5" if failure == "partial" else "1",
        exec_qty=None,
    )
    if failure == "timeout":
        client.create_limit_order.side_effect = TimeoutError()
    monkeypatch.setattr(cli, "ReyaTradingClient", Mock(return_value=client))
    monkeypatch.setattr(cli, "TradingConfig", Mock())
    settled = AsyncMock(return_value={"liquidation_delta": "1"})
    if failure == "settlement":
        settled.side_effect = ValueError("unconfirmed")
    monkeypatch.setattr(cli, "wait_settled", settled)
    if failure == "none":
        await cli.execute_plans(read, args, "not-a-real-key", plans)
        assert client.create_limit_order.call_count == 2
        assert settled.call_count == 2
    else:
        with pytest.raises((ValueError, TimeoutError)):
            await cli.execute_plans(read, args, "not-a-real-key", plans)
        assert client.create_limit_order.call_count == 1
    assert "not-a-real-key" not in (tmp_path / "batch.jsonl").read_text()
    with pytest.raises(FileExistsError):
        await cli.execute_plans(read, args, "not-a-real-key", plans)


def test_plan_uses_cutover_depth_fee_bound_and_oldest_timestamp():
    read = Mock(spec=Readiness)
    read.margin.return_value = {"margin_balance": "50", "real_balance": "50"}
    now = cli.time.time_ns() // 1_000_000
    read.api.side_effect = [
        [
            {
                "symbol": "ETHRUSDPERP",
                "marketId": 1,
                "liquidationMarginParameter": "0.03",
                "tickSize": "0.1",
                "qtyStepSize": "0.001",
                "minOrderQty": "0.001",
            }
        ],
        {"markPrice": "2000", "pricesUpdatedAt": now - 1000},
        {"updatedAt": now, "asks": [{"px": "2000", "qty": "100"}]},
        [{"takerFee": "0.0001"}, {"takerFee": "0.0003"}],
    ]
    plan = cli.build_plan(read, open_args(), 2, True)
    assert plan["planned_at_ms"] == now - 1000
    assert plan["fee_rate_bound"] == "0.0003"
    assert plan["qty"] == quote(**inputs())["qty"]


async def test_settlement_requires_position_and_core_exposure(monkeypatch):
    read = Mock(spec=Readiness)
    read.margin.return_value = {"lmr": "0"}
    read.wallet.return_value = [{"accountId": 2, "symbol": "ETHRUSDPERP", "side": "B", "qty": "1"}]
    monkeypatch.setattr(cli, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 2])))
    monkeypatch.setattr(cli.asyncio, "sleep", AsyncMock())
    with pytest.raises(ValueError, match="Settlement was not confirmed"):
        await cli.wait_settled(read, {"account_id": 2, "symbol": "ETHRUSDPERP", "side": "LONG", "qty": "1"}, 1)
