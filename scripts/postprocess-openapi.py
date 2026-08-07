#!/usr/bin/env python3
"""Preserve SDK compatibility constraints absent from OpenAPI Generator."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MARKET_DEPTH_METHODS = (
    "get_market_depth",
    "get_market_depth_with_http_info",
    "get_market_depth_without_preload_content",
)


def make_limit_keyword_only(lines: list[str], method_name: str, path: Path) -> None:
    """Keep the pre-existing positional timeout slot ahead of the new limit."""
    method_marker = f"    async def {method_name}("
    method_matches = [index for index, line in enumerate(lines) if line.startswith(method_marker)]
    if len(method_matches) != 1:
        raise RuntimeError(f"expected one {method_name!r} method in {path}, found {len(method_matches)}")

    start = method_matches[0]
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("    ) -> ")),
        None,
    )
    if end is None:
        raise RuntimeError(f"could not find signature end for {method_name!r} in {path}")

    signature = lines[start:end]
    limit_matches = [index for index, line in enumerate(signature) if line.startswith("        limit: ")]
    host_matches = [index for index, line in enumerate(signature) if line.startswith("        _host_index: ")]
    if len(limit_matches) != 1 or len(host_matches) != 1:
        raise RuntimeError(f"unexpected generated signature for {method_name!r} in {path}")
    if any(line.strip() == "*," for line in signature):
        raise RuntimeError(f"{method_name!r} already has keyword-only parameters in {path}")

    limit_index = start + limit_matches[0]
    host_index = start + host_matches[0]
    limit_line = lines.pop(limit_index)
    if limit_index < host_index:
        host_index -= 1
    lines.insert(host_index + 1, "        *,\n")
    lines.insert(host_index + 2, limit_line)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} OPEN_API_DIR")

    path = Path(sys.argv[1]) / "api" / "market_data_api.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for method_name in MARKET_DEPTH_METHODS:
        make_limit_keyword_only(lines, method_name, path)

    updated = "".join(lines)
    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
