#!/usr/bin/env python3
"""Run the Perp OB migration canary preflight without mutating the target."""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path

from scripts.canary_preflight import (
    PreflightError,
    build_evidence,
    default_evidence_path,
    load_profile,
    validate_profile,
    write_evidence,
)


def parse_args(argv: Sequence[str] | None = None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", required=True, type=Path, help="Explicit TOML profile; implicit .env loading is forbidden"
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate configuration and write evidence without constructing a client or sending network requests",
    )
    parser.add_argument(
        "--output", type=Path, help="Evidence JSON path (default: artifacts/canary/<run>/preflight.json)"
    )
    args = parser.parse_args(argv)
    if not args.preflight_only:
        parser.error("only --preflight-only is implemented; no mutation path exists yet")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    profile_path = args.profile.expanduser().resolve()
    try:
        profile = load_profile(profile_path)
        validate_profile(profile, mutating=False)
        evidence = build_evidence(profile, profile_path, repo_root=repo_root)
        output_path = (
            args.output.expanduser().resolve() if args.output else default_evidence_path(profile, repo_root=repo_root)
        )
        write_evidence(evidence, output_path)
    except PreflightError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(f"PASS: {profile.name} ({profile.environment}) configuration preflight")
    print(f"Evidence: {output_path}")
    print("No network requests or mutations were performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
