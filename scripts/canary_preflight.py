"""Fail-closed configuration preflight for the Perp OB migration canary."""

from __future__ import annotations

from typing import Any

import hashlib
import json
import re
import subprocess  # nosec B404
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10/3.11 compatibility for mypy/dev tooling
    import tomli as tomllib


MAINNET_MUTATION_ACKNOWLEDGEMENT = "PRO-657-MAINNET-MUTATION-APPROVED"

_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SECRET_KEY_PARTS = ("private_key", "privatekey", "secret", "mnemonic")
_PROFILE_FIELDS = {"name", "enabled", "environment", "release_manifest_id", "identity", "canary"}
_IDENTITY_FIELDS = {"chain_id", "api_url", "read_ws_url", "ws_exec_url", "orders_gateway", "exchange_id"}
_CANARY_FIELDS = {
    "market_symbol",
    "market_id",
    "max_quantity",
    "max_notional",
    "allowed_account_ids",
    "allowed_wallet_addresses",
}


class PreflightError(ValueError):
    """Raised when a canary profile is unsafe or incomplete."""


@dataclass(frozen=True)
class EnvironmentIdentity:
    """Immutable identity of a supported Reya deployment."""

    chain_id: int
    api_url: str
    read_ws_url: str
    ws_exec_url: str
    orders_gateway: str
    exchange_id: int


@dataclass(frozen=True)
class CanaryPolicy:
    """Bounded mutation policy for one designated canary run."""

    market_symbol: str
    market_id: int
    max_quantity: Decimal
    max_notional: Decimal
    allowed_account_ids: tuple[int, ...]
    allowed_wallet_addresses: tuple[str, ...]


@dataclass(frozen=True)
class CanaryProfile:
    """Environment identity and mutation policy loaded from an explicit file."""

    name: str
    enabled: bool
    environment: str
    identity: EnvironmentIdentity
    release_manifest_id: str
    policy: CanaryPolicy


SUPPORTED_ENVIRONMENTS: Mapping[str, EnvironmentIdentity] = {
    "devnet1": EnvironmentIdentity(
        chain_id=89346162,
        api_url="https://api-devnet.reya-cronos.network/v2",
        read_ws_url="wss://websocket-devnet.reya-cronos.network/",
        ws_exec_url="wss://ws-exec-devnet.reya-cronos.network",
        orders_gateway="0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D",
        exchange_id=1,
    ),
    "mainnet": EnvironmentIdentity(
        chain_id=1729,
        api_url="https://api.reya.xyz/v2",
        read_ws_url="wss://ws.reya.xyz/",
        ws_exec_url="wss://ws-exec.reya.xyz",
        orders_gateway="0xfc8c96be87da63cecddbf54abfa7b13ee8044739",
        exchange_id=2,
    ),
}


def _reject_secret_fields(value: Any, path: str = "profile") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise PreflightError(
                    f"{path}.{key} looks like a secret field; keep credentials in the operator environment"
                )
            _reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")


def _reject_unknown_fields(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise PreflightError(f"{path} contains unknown fields: {', '.join(unknown)}")


def _required_string(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise PreflightError(f"{name} must be a string")
    return value.strip()


def _required_int(data: Mapping[str, Any], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreflightError(f"{name} must be an integer")
    return value


def _required_decimal(data: Mapping[str, Any], name: str) -> Decimal:
    value = data.get(name)
    if not isinstance(value, str):
        raise PreflightError(f"{name} must be a quoted decimal string")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise PreflightError(f"{name} must be a valid decimal string") from error
    if not decimal_value.is_finite():
        raise PreflightError(f"{name} must be finite")
    return decimal_value


def _int_tuple(data: Mapping[str, Any], name: str) -> tuple[int, ...]:
    value = data.get(name)
    if not isinstance(value, list):
        raise PreflightError(f"{name} must be an array of integers")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise PreflightError(f"{name} must contain only integers")
    return tuple(value)


def _string_tuple(data: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = data.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PreflightError(f"{name} must be an array of strings")
    return tuple(item.strip() for item in value)


def load_profile(path: Path) -> CanaryProfile:
    """Load a TOML profile without consulting `.env` or process credentials."""
    try:
        with path.open("rb") as profile_file:
            raw = tomllib.load(profile_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PreflightError(f"cannot load profile {path}: {error}") from error

    _reject_secret_fields(raw)
    _reject_unknown_fields(raw, _PROFILE_FIELDS, "profile")
    identity_data = raw.get("identity")
    policy_data = raw.get("canary")
    if not isinstance(identity_data, Mapping):
        raise PreflightError("profile.identity table is required")
    if not isinstance(policy_data, Mapping):
        raise PreflightError("profile.canary table is required")
    _reject_unknown_fields(identity_data, _IDENTITY_FIELDS, "profile.identity")
    _reject_unknown_fields(policy_data, _CANARY_FIELDS, "profile.canary")

    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise PreflightError("enabled must be a boolean")

    identity = EnvironmentIdentity(
        chain_id=_required_int(identity_data, "chain_id"),
        api_url=_required_string(identity_data, "api_url"),
        read_ws_url=_required_string(identity_data, "read_ws_url"),
        ws_exec_url=_required_string(identity_data, "ws_exec_url"),
        orders_gateway=_required_string(identity_data, "orders_gateway"),
        exchange_id=_required_int(identity_data, "exchange_id"),
    )
    policy = CanaryPolicy(
        market_symbol=_required_string(policy_data, "market_symbol"),
        market_id=_required_int(policy_data, "market_id"),
        max_quantity=_required_decimal(policy_data, "max_quantity"),
        max_notional=_required_decimal(policy_data, "max_notional"),
        allowed_account_ids=_int_tuple(policy_data, "allowed_account_ids"),
        allowed_wallet_addresses=_string_tuple(policy_data, "allowed_wallet_addresses"),
    )
    return CanaryProfile(
        name=_required_string(raw, "name"),
        enabled=enabled,
        environment=_required_string(raw, "environment").lower(),
        identity=identity,
        release_manifest_id=_required_string(raw, "release_manifest_id"),
        policy=policy,
    )


def _canonical_endpoint(value: str, label: str) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise PreflightError(f"{label} must be an absolute HTTP(S) or WS(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PreflightError(f"{label} must not contain credentials, a query, or a fragment")
    return (parsed.scheme.lower(), parsed.hostname.lower(), parsed.port, parsed.path.rstrip("/"))


def _identity_errors(actual: EnvironmentIdentity, expected: EnvironmentIdentity) -> list[str]:
    errors: list[str] = []
    if actual.chain_id != expected.chain_id:
        errors.append(f"chain_id mismatch: expected {expected.chain_id}, got {actual.chain_id}")
    for field_name in ("api_url", "read_ws_url", "ws_exec_url"):
        actual_url = getattr(actual, field_name)
        expected_url = getattr(expected, field_name)
        try:
            actual_endpoint = _canonical_endpoint(actual_url, field_name)
            expected_endpoint = _canonical_endpoint(expected_url, field_name)
            if actual_endpoint != expected_endpoint:
                errors.append(f"{field_name} mismatch: expected {expected_url}, got {actual_url}")
        except PreflightError as error:
            errors.append(str(error))
    if not _ADDRESS_PATTERN.fullmatch(actual.orders_gateway):
        errors.append("orders_gateway must be a 20-byte 0x-prefixed address")
    elif actual.orders_gateway.lower() != expected.orders_gateway.lower():
        errors.append(
            f"orders_gateway mismatch: expected {expected.orders_gateway.lower()}, got {actual.orders_gateway.lower()}"
        )
    if actual.exchange_id != expected.exchange_id:
        errors.append(f"exchange_id mismatch: expected {expected.exchange_id}, got {actual.exchange_id}")
    return errors


def validate_profile(
    profile: CanaryProfile,
    *,
    mutating: bool = False,
    mutation_acknowledgement: str | None = None,
) -> None:
    """Validate identity, bounds, allowlists, and the mainnet mutation gate."""
    errors: list[str] = []
    expected = SUPPORTED_ENVIRONMENTS.get(profile.environment)
    if expected is None:
        errors.append(
            f"unsupported environment {profile.environment!r}; expected one of {', '.join(SUPPORTED_ENVIRONMENTS)}"
        )
    else:
        errors.extend(_identity_errors(profile.identity, expected))

    if not profile.enabled:
        errors.append("profile is disabled; copy the template to a local profile and set enabled=true")
    if not profile.name:
        errors.append("name must not be empty")
    if not profile.release_manifest_id or profile.release_manifest_id.startswith("REPLACE_"):
        errors.append("release_manifest_id must pin the exact candidate manifest")
    if not profile.policy.market_symbol or profile.policy.market_symbol.startswith("REPLACE_"):
        errors.append("canary.market_symbol must name the designated market")
    if profile.policy.market_id <= 0:
        errors.append("canary.market_id must be greater than zero")
    if profile.policy.max_quantity <= 0:
        errors.append("canary.max_quantity must be greater than zero")
    if profile.policy.max_notional <= 0:
        errors.append("canary.max_notional must be greater than zero")
    if not profile.policy.allowed_account_ids:
        errors.append("canary.allowed_account_ids must not be empty")
    elif any(account_id <= 0 for account_id in profile.policy.allowed_account_ids):
        errors.append("canary.allowed_account_ids must contain only positive IDs")
    if len(set(profile.policy.allowed_account_ids)) != len(profile.policy.allowed_account_ids):
        errors.append("canary.allowed_account_ids must not contain duplicates")
    if not profile.policy.allowed_wallet_addresses:
        errors.append("canary.allowed_wallet_addresses must not be empty")
    else:
        invalid_addresses = [
            address for address in profile.policy.allowed_wallet_addresses if not _ADDRESS_PATTERN.fullmatch(address)
        ]
        if invalid_addresses:
            errors.append("canary.allowed_wallet_addresses must contain only 20-byte 0x-prefixed addresses")
        normalized_addresses = [address.lower() for address in profile.policy.allowed_wallet_addresses]
        if len(set(normalized_addresses)) != len(normalized_addresses):
            errors.append("canary.allowed_wallet_addresses must not contain duplicates")

    if mutating and profile.environment == "mainnet":
        if mutation_acknowledgement != MAINNET_MUTATION_ACKNOWLEDGEMENT:
            errors.append(
                "mainnet mutation acknowledgement missing or incorrect; "
                f"expected {MAINNET_MUTATION_ACKNOWLEDGEMENT!r}"
            )

    if errors:
        raise PreflightError("canary preflight failed:\n- " + "\n- ".join(errors))


def _git_revision(repo_root: Path) -> str:
    try:
        result = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell, repository cwd
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreflightError(f"cannot resolve SDK git revision: {error}") from error
    return result.stdout.strip()


def build_evidence(profile: CanaryProfile, profile_path: Path, *, repo_root: Path) -> dict[str, Any]:
    """Build a credential-free, machine-readable preflight evidence record."""
    try:
        profile_sha256 = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    except OSError as error:
        raise PreflightError(f"cannot hash profile {profile_path}: {error}") from error
    identity = asdict(profile.identity)
    policy = asdict(profile.policy)
    policy["max_quantity"] = str(profile.policy.max_quantity)
    policy["max_notional"] = str(profile.policy.max_notional)
    policy["allowed_account_ids"] = list(profile.policy.allowed_account_ids)
    policy["allowed_wallet_addresses"] = list(profile.policy.allowed_wallet_addresses)
    return {
        "schema_version": 1,
        "result": "pass",
        "mode": "preflight-only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sdk_git_revision": _git_revision(repo_root),
        "profile": {
            "name": profile.name,
            "environment": profile.environment,
            "release_manifest_id": profile.release_manifest_id,
            "sha256": profile_sha256,
        },
        "identity": identity,
        "canary": policy,
    }


def write_evidence(evidence: Mapping[str, Any], output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise PreflightError(f"cannot write evidence {output_path}: {error}") from error


def default_evidence_path(profile: CanaryProfile, *, repo_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return repo_root / "artifacts" / "canary" / f"{timestamp}-{profile.environment}" / "preflight.json"
