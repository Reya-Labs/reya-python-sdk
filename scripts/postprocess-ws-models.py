#!/usr/bin/env python3
"""Restore schema constraints that Modelina 5.7.2 does not emit for Pydantic."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    """Replace one generator-stable fragment, failing if output changed shape."""
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {old!r} in {path}, found {count}")
    return text.replace(old, new)


def rewrite_field(lines: list[str], field_name: str, rewrite: Callable[[str], str], path: Path) -> None:
    """Rewrite one generated field declaration, failing closed on drift."""
    prefix = f"  {field_name}: "
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {field_name!r} field in {path}, found {len(matches)}")

    index = matches[0]
    updated = rewrite(lines[index])
    if updated == lines[index]:
        raise RuntimeError(f"failed to rewrite {field_name!r} in {path}")
    lines[index] = updated


def add_pattern(line: str, pattern: str) -> str:
    """Add a Pydantic regex constraint to a generated Field declaration."""
    if "Field()" in line:
        return line.replace("Field()", f"Field(pattern=r'{pattern}')", 1)
    return line.replace("Field(", f"Field(pattern=r'{pattern}', ", 1)


def require_nullable(line: str, pattern: str) -> str:
    """Keep a field nullable while requiring the key to be present."""
    if ", default=None," not in line:
        raise RuntimeError("expected generated nullable field to have default=None")
    line = add_pattern(line, pattern)
    return line.replace(", default=None,", ",", 1)


def patch_account_update_data(output_dir: Path) -> None:
    """Match AccountUpdateData's required, pattern, and extra-field contract."""
    path = output_dir / "account_update_data.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from pydantic import BaseModel, Field",
        "from pydantic import BaseModel, ConfigDict, Field",
        path,
    )
    text = replace_once(
        text,
        "class AccountUpdateData(BaseModel): ",
        'class AccountUpdateData(BaseModel):\n  model_config = ConfigDict(extra="forbid")',
        path,
    )

    lines = text.splitlines(keepends=True)
    rewrite_field(lines, "account_id", lambda line: add_pattern(line, r"^\d+$"), path)
    rewrite_field(
        lines,
        "owner",
        lambda line: add_pattern(line, r"^0x[a-fA-F0-9]{40}$"),
        path,
    )
    rewrite_field(
        lines,
        "main_account_id",
        lambda line: require_nullable(line, r"^\d+$"),
        path,
    )
    rewrite_field(
        lines,
        "spot_account_id",
        lambda line: require_nullable(line, r"^\d+$"),
        path,
    )
    path.write_text("".join(lines), encoding="utf-8")


def patch_account_update_payload(output_dir: Path) -> None:
    """Reject envelope properties disallowed by the AsyncAPI schema."""
    path = output_dir / "account_update_payload.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from pydantic import BaseModel, Field",
        "from pydantic import BaseModel, ConfigDict, Field",
        path,
    )
    text = replace_once(
        text,
        "class AccountUpdatePayload(BaseModel): ",
        'class AccountUpdatePayload(BaseModel):\n  model_config = ConfigDict(extra="forbid")',
        path,
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} OUTPUT_DIR")

    output_dir = Path(sys.argv[1])
    patch_account_update_data(output_dir)
    patch_account_update_payload(output_dir)


if __name__ == "__main__":
    main()
