"""Offline tests for the PRO-657 canary configuration preflight."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.canary_preflight import (
    MAINNET_MUTATION_ACKNOWLEDGEMENT,
    SUPPORTED_ENVIRONMENTS,
    CanaryPolicy,
    CanaryProfile,
    EnvironmentIdentity,
    PreflightError,
    ProbeError,
    ProbeResult,
    build_evidence,
    load_profile,
    resolve_rpc_url,
    run_live_probes,
    validate_profile,
    write_evidence,
)
from scripts.run_canary import main

pytestmark = pytest.mark.offline

_WALLET_1 = "0x1111111111111111111111111111111111111111"
_WALLET_2 = "0x2222222222222222222222222222222222222222"


def _profile(environment: str = "devnet1") -> CanaryProfile:
    return CanaryProfile(
        name=f"test-{environment}",
        enabled=True,
        environment=environment,
        identity=SUPPORTED_ENVIRONMENTS[environment],
        release_manifest_id="candidate-2026-08-28.1",
        rpc_url_env="REYA_CANARY_RPC_URL",
        policy=CanaryPolicy(
            market_symbol="ETHRUSDPERP",
            market_id=1,
            max_quantity=Decimal("0.01"),
            max_notional=Decimal("50"),
            allowed_account_ids=(101, 102),
            allowed_wallet_addresses=(_WALLET_1, _WALLET_2),
        ),
    )


def _profile_toml(*, environment: str = "devnet1", extra: str = "") -> str:
    identity = SUPPORTED_ENVIRONMENTS[environment]
    return f"""\
name = "test-{environment}"
enabled = true
environment = "{environment}"
release_manifest_id = "candidate-2026-08-28.1"
rpc_url_env = "REYA_CANARY_RPC_URL"
{extra}
[identity]
chain_id = {identity.chain_id}
api_url = "{identity.api_url}"
read_ws_url = "{identity.read_ws_url}"
ws_exec_url = "{identity.ws_exec_url}"
orders_gateway = "{identity.orders_gateway}"
exchange_id = {identity.exchange_id}

[canary]
market_symbol = "ETHRUSDPERP"
market_id = 1
max_quantity = "0.01"
max_notional = "50"
allowed_account_ids = [101, 102]
allowed_wallet_addresses = ["{_WALLET_1}", "{_WALLET_2}"]
"""


def test_valid_profile_loads_and_passes(tmp_path: Path) -> None:
    profile_path = tmp_path / "devnet1.toml"
    profile_path.write_text(_profile_toml(), encoding="utf-8")

    profile = load_profile(profile_path)
    validate_profile(profile)

    assert profile.environment == "devnet1"
    assert profile.policy.allowed_account_ids == (101, 102)


def test_same_chain_wrong_hosts_and_gateway_fail_closed() -> None:
    expected = SUPPORTED_ENVIRONMENTS["devnet1"]
    profile = _profile()
    wrong_identity = EnvironmentIdentity(
        chain_id=expected.chain_id,
        api_url="https://api-cronos.reya.xyz/v2",
        read_ws_url="wss://websocket-testnet.reya.xyz/",
        ws_exec_url="wss://ws-exec-testnet.reya.xyz",
        orders_gateway="0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5",
        exchange_id=expected.exchange_id,
    )
    wrong_profile = replace(profile, identity=wrong_identity)

    with pytest.raises(PreflightError) as exc_info:
        validate_profile(wrong_profile)

    message = str(exc_info.value)
    assert "api_url mismatch" in message
    assert "read_ws_url mismatch" in message
    assert "ws_exec_url mismatch" in message
    assert "orders_gateway mismatch" in message


def test_disabled_placeholder_profile_is_rejected() -> None:
    profile = _profile()
    incomplete_policy = CanaryPolicy(
        market_symbol="REPLACE_WITH_DESIGNATED_MARKET",
        market_id=0,
        max_quantity=profile.policy.max_quantity * 0,
        max_notional=profile.policy.max_notional * 0,
        allowed_account_ids=(),
        allowed_wallet_addresses=(),
    )
    incomplete = replace(
        profile,
        enabled=False,
        release_manifest_id="REPLACE_WITH_PINNED_CANDIDATE_MANIFEST",
        policy=incomplete_policy,
    )

    with pytest.raises(PreflightError) as exc_info:
        validate_profile(incomplete)

    message = str(exc_info.value)
    assert "profile is disabled" in message
    assert "release_manifest_id" in message
    assert "market_id" in message
    assert "allowed_account_ids" in message


def test_mainnet_mutation_requires_exact_acknowledgement() -> None:
    profile = _profile("mainnet")

    with pytest.raises(PreflightError, match="mainnet mutation acknowledgement"):
        validate_profile(profile, mutating=True)

    validate_profile(
        profile,
        mutating=True,
        mutation_acknowledgement=MAINNET_MUTATION_ACKNOWLEDGEMENT,
    )


def test_rpc_url_is_resolved_only_from_explicit_env_name() -> None:
    profile = _profile()

    with pytest.raises(ProbeError, match="REYA_CANARY_RPC_URL is required"):
        resolve_rpc_url(profile, {})

    assert resolve_rpc_url(profile, {"REYA_CANARY_RPC_URL": "https://rpc.example/token"}) == (
        "https://rpc.example/token"
    )


class _FakeProbeTransport:
    def __init__(self) -> None:
        self.chain_result = hex(SUPPORTED_ENVIRONMENTS["devnet1"].chain_id)
        self.code_result = "0x6001600055"
        self.market_id = 1
        self.websocket_urls: list[str] = []

    async def get_json(self, _url: str, _timeout_s: float):
        return [{"symbol": "ETHRUSDPERP", "marketId": self.market_id}]

    async def post_json(self, _url: str, payload, _timeout_s: float):
        if payload["method"] == "eth_chainId":
            return {"jsonrpc": "2.0", "id": payload["id"], "result": self.chain_result}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": self.code_result}

    async def websocket_handshake(self, url: str, _timeout_s: float) -> None:
        self.websocket_urls.append(url)


@pytest.mark.asyncio
async def test_read_only_live_probes_cover_every_target_surface() -> None:
    profile = _profile()
    transport = _FakeProbeTransport()

    results = await run_live_probes(
        profile,
        rpc_url="https://rpc.example/credential-not-recorded",
        transport=transport,
    )

    assert [result.id for result in results] == [
        "rest.marketIdentity",
        "rpc.chainId",
        "rpc.ordersGatewayCode",
        "ws.readHandshake",
        "ws.execHandshake",
    ]
    assert transport.websocket_urls == [profile.identity.read_ws_url, profile.identity.ws_exec_url]


@pytest.mark.asyncio
async def test_live_probe_rejects_wrong_chain_before_gateway_and_websockets() -> None:
    profile = _profile()
    transport = _FakeProbeTransport()
    transport.chain_result = hex(profile.identity.chain_id + 1)

    with pytest.raises(ProbeError, match="RPC chain ID mismatch"):
        await run_live_probes(profile, rpc_url="https://rpc.example", transport=transport)

    assert not transport.websocket_urls


@pytest.mark.asyncio
async def test_live_probe_rejects_missing_gateway_code() -> None:
    transport = _FakeProbeTransport()
    transport.code_result = "0x"

    with pytest.raises(ProbeError, match="Orders Gateway has no deployed bytecode"):
        await run_live_probes(_profile(), rpc_url="https://rpc.example", transport=transport)


@pytest.mark.asyncio
async def test_live_probe_rejects_wrong_market_id() -> None:
    transport = _FakeProbeTransport()
    transport.market_id = 999

    with pytest.raises(ProbeError, match="REST market ID mismatch"):
        await run_live_probes(_profile(), rpc_url="https://rpc.example", transport=transport)


def test_profile_rejects_secret_fields(tmp_path: Path) -> None:
    profile_path = tmp_path / "unsafe.toml"
    profile_path.write_text(_profile_toml(extra='private_key = "do-not-store-this"\n'), encoding="utf-8")

    with pytest.raises(PreflightError, match="looks like a secret field"):
        load_profile(profile_path)


def test_profile_rejects_unknown_fields(tmp_path: Path) -> None:
    profile_path = tmp_path / "typo.toml"
    profile_path.write_text(_profile_toml().replace("api_url =", "api_urll ="), encoding="utf-8")

    with pytest.raises(PreflightError, match="profile.identity contains unknown fields: api_urll"):
        load_profile(profile_path)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_profile_rejects_non_finite_bounds(tmp_path: Path, value: str) -> None:
    profile_path = tmp_path / "non-finite.toml"
    profile_path.write_text(
        _profile_toml().replace('max_quantity = "0.01"', f'max_quantity = "{value}"'), encoding="utf-8"
    )

    with pytest.raises(PreflightError, match="max_quantity must be finite"):
        load_profile(profile_path)


def test_evidence_is_machine_readable_and_credential_free(tmp_path: Path) -> None:
    profile_path = tmp_path / "devnet1.toml"
    profile_path.write_text(_profile_toml(), encoding="utf-8")
    profile = load_profile(profile_path)
    output_path = tmp_path / "evidence" / "preflight.json"

    probes = (ProbeResult("rpc.chainId", str(profile.identity.chain_id)),)
    evidence = build_evidence(
        profile,
        profile_path,
        repo_root=Path(__file__).resolve().parents[2],
        mode="probe-live-read-only",
        probes=probes,
    )
    write_evidence(evidence, output_path)

    stored = json.loads(output_path.read_text(encoding="utf-8"))
    assert stored["result"] == "pass"
    assert stored["mode"] == "probe-live-read-only"
    assert stored["profile"]["environment"] == "devnet1"
    assert stored["sdk_git_revision"]
    assert stored["probes"] == [{"detail": str(profile.identity.chain_id), "id": "rpc.chainId"}]
    assert "credential-not-recorded" not in output_path.read_text(encoding="utf-8")
    assert "private" not in output_path.read_text(encoding="utf-8").lower()


def test_cli_preflight_writes_evidence(tmp_path: Path, capsys) -> None:
    profile_path = tmp_path / "devnet1.toml"
    profile_path.write_text(_profile_toml(), encoding="utf-8")
    output_path = tmp_path / "preflight.json"

    assert main(["--profile", str(profile_path), "--preflight-only", "--output", str(output_path)]) == 0

    assert output_path.exists()
    assert "No network requests or mutations were performed." in capsys.readouterr().out
