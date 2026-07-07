"""Offline checks for the repeatable PR 59 devnet smoke plan."""

from types import SimpleNamespace

import importlib

import pytest

pytestmark = pytest.mark.offline


def test_smoke_plan_tracks_pr59_manual_gap_surfaces() -> None:
    try:
        module = importlib.import_module("scripts.devnet_pr59_surface_smoke")
    except ModuleNotFoundError:
        pytest.fail("scripts.devnet_pr59_surface_smoke is missing")

    smoke_checks = module.SMOKE_CHECKS
    smoke_check_kind = module.SmokeCheckKind

    checks_by_id = {check.id: check for check in smoke_checks}

    assert {
        "rest.assetOraclePrices",
        "ws.assetOraclePrices",
        "rest.perpMarketsSummary",
        "ws.perpMarketsSummary",
        "rest.spotMarketsSummary",
        "ws.spotMarketsSummary",
        "sdk.removedAmmSurfaces",
    } <= checks_by_id.keys()

    asset_ws = checks_by_id["ws.assetOraclePrices"]
    assert asset_ws.kind is smoke_check_kind.WEBSOCKET
    assert asset_ws.target == "/v2/assetOraclePrices"
    assert "asset oracle" in asset_ws.reason.lower()

    spot_ws = checks_by_id["ws.spotMarketsSummary"]
    assert spot_ws.kind is smoke_check_kind.WEBSOCKET
    assert spot_ws.target == "/v2/spotMarkets/summary + /v2/spotMarket/{symbol}/summary"
    assert "spot market summary" in spot_ws.reason.lower()

    removed_surfaces = checks_by_id["sdk.removedAmmSurfaces"]
    assert removed_surfaces.kind is smoke_check_kind.SDK_STATIC
    assert "/v2/markets/summary" in removed_surfaces.reason
    assert "/v2/marketDefinitions" in removed_surfaces.reason


def test_smoke_cli_reports_failures_without_traceback(monkeypatch, capsys) -> None:
    module = importlib.import_module("scripts.devnet_pr59_surface_smoke")

    async def fail_smoke(*_args, **_kwargs):
        raise AssertionError("websocket channel failed")

    monkeypatch.setattr(module, "run_smoke", fail_smoke)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(list=False, timeout=1, max_age_ms=1),
    )

    assert module.main() == 1

    captured = capsys.readouterr()
    assert "FAIL AssertionError: websocket channel failed" in captured.err
    assert "Traceback" not in captured.err
