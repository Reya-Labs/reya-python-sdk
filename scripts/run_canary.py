#!/usr/bin/env python3
"""Run the Perp OB migration canary preflight without mutating the target."""

from __future__ import annotations

import asyncio
import os
import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path

from scripts.canary_preflight import (
    PreflightError,
    build_evidence,
    default_evidence_path,
    load_profile,
    resolve_rpc_url,
    run_live_probes,
    validate_profile,
    write_evidence,
)


def parse_args(argv: Sequence[str] | None = None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, type=Path, help="Explicit TOML profile; implicit .env loading is forbidden"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate configuration and write evidence without constructing a client or sending network requests",
    )
    mode.add_argument(
        "--probe-live-read-only",
        action="store_true",
        help="Run bounded REST/RPC/WebSocket identity probes without credentials or mutations",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-probe timeout in seconds")
    parser.add_argument(
        "--output", type=Path, help="Evidence JSON path (default: artifacts/canary/<run>/preflight.json)"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    profile_path = args.profile.expanduser().resolve()
    try:
        profile = load_profile(profile_path)
        validate_profile(profile, mutating=False)
        if args.probe_live_read_only:
            rpc_url = resolve_rpc_url(profile, os.environ)
            probes = asyncio.run(run_live_probes(profile, rpc_url=rpc_url, timeout_s=args.timeout))
            evidence_mode = "probe-live-read-only"
        else:
            probes = ()
            evidence_mode = "preflight-only"
        evidence = build_evidence(
            profile,
            profile_path,
            repo_root=repo_root,
            mode=evidence_mode,
            probes=probes,
        )
        output_path = (
            args.output.expanduser().resolve() if args.output else default_evidence_path(profile, repo_root=repo_root)
        )
        write_evidence(evidence, output_path)
    except PreflightError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"PASS: {profile.name} ({profile.environment}) configuration preflight")
    for probe in probes:
        print(f"PASS {probe.id}: {probe.detail}")
    print(f"Evidence: {output_path}")
    if args.preflight_only:
        print("No network requests or mutations were performed.")
    else:
        print("Only read-only target probes were performed; no credentials or mutations were used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
