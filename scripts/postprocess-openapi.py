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

UNKNOWN_MEMBER = "UNKNOWN"

# Enums whose vocabulary the SERVER owns and can widen without an SDK release.
# A response carrying a member this SDK has never heard of parses as UNKNOWN
# instead of raising, so one unfamiliar value cannot cost the caller the whole
# frame. The order-entry vocabularies (OrderType, TimeInForce) are deliberately
# absent: a request the client cannot encode must keep failing loudly.
OPEN_VOCABULARY_ENUMS = (
    "account_type",
    "cancel_reason",
    "execution_type",
    "order_status",
    "request_error_code",
    "server_error_code",
    "tier_type",
)


def class_name_for(module: str) -> str:
    return "".join(part.title() for part in module.split("_"))


def add_unknown_fallback(models_dir: Path, module: str) -> None:
    """Give a server-extended enum an UNKNOWN member and the hook that lands on it."""
    path = models_dir / f"{module}.py"
    class_name = class_name_for(module)
    text = path.read_text(encoding="utf-8")
    if f"    {UNKNOWN_MEMBER} = " in text:
        raise RuntimeError(f"{class_name} already declares {UNKNOWN_MEMBER} in {path}")

    anchor = (
        "    @classmethod\n"
        "    def from_json(cls, json_str: str) -> Self:\n"
        f'        """Create an instance of {class_name} from a JSON string"""\n'
        "        return cls(json.loads(json_str))\n"
    )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"expected one from_json block in {path}, found {count}")

    replacement = (
        f"    {UNKNOWN_MEMBER} = '{UNKNOWN_MEMBER}'\n"
        "\n"
        f"{anchor}"
        "\n"
        "    @classmethod\n"
        "    def _missing_(cls, value: object) -> Self:\n"
        f'        """Resolve a member added by the server since this SDK was generated."""\n'
        f"        return cls.{UNKNOWN_MEMBER}\n"
    )
    updated = text.replace(anchor, replacement)
    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8")


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

    open_api_dir = Path(sys.argv[1])
    path = open_api_dir / "api" / "market_data_api.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for method_name in MARKET_DEPTH_METHODS:
        make_limit_keyword_only(lines, method_name, path)

    updated = "".join(lines)
    ast.parse(updated, filename=str(path))
    path.write_text(updated, encoding="utf-8")

    for module in OPEN_VOCABULARY_ENUMS:
        add_unknown_fallback(open_api_dir / "models", module)


if __name__ == "__main__":
    main()
