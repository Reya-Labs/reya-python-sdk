"""Exercise version-gate shell snippets used by the release workflow offline."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.offline
WORKFLOW = (Path(__file__).parents[2] / ".github/workflows/version-consistency.yml").read_text()


def test_review_and_stable_tags_extract_the_same_version(tmp_path: Path) -> None:
    start = WORKFLOW.index('          if [[ ! "$SPECS_TAG" =~')
    end = WORKFLOW.index('          echo "SPECS_VERSION_PREFIX:', start)
    script = dedent(WORKFLOW[start:end]) + '\nprintf "%s" "$SPECS_VERSION_PREFIX"\n'
    for tag in ["3.6.3", "v3.6.3", "3.6.3-rwa-launch.3"]:
        result = subprocess.run(
            ["bash", "-eu", "-c", script],
            env={**os.environ, "SPECS_TAG": tag, "GITHUB_ENV": str(tmp_path / "env")},
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == "3.6.3"
    for tag in ["main", "3.6", "3.6.3.4", "3.6.3-", "3.6.3;exit 0"]:
        result = subprocess.run(
            ["bash", "-eu", "-c", script],
            env={**os.environ, "SPECS_TAG": tag},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0


@pytest.mark.parametrize(
    "version,changed,prefix,accepted",
    [
        ("3.6.3.0", "true", "3.6.3", True),
        ("3.6.1.1", "false", "3.6.1", True),
        ("3.6.3.0", "false", "3.6.3", False),
        ("3.6.3.0", "true", "3.6.2", False),
        ("3.6.3.1", "true", "3.6.3", False),
        ("3.6.0.0", "true", "3.6.0", False),
    ],
)
def test_version_jump_requires_exact_new_spec_alignment(
    version: str, changed: str, prefix: str, accepted: bool
) -> None:
    section = WORKFLOW.split("      - name: Validate version progression (for manual changes)\n", 1)[1]
    script = dedent(section.split("        run: |\n", 1)[1].split("      - name:", 1)[0])
    result = subprocess.run(
        ["bash", "-eu", "-c", script],
        env={
            **os.environ,
            "BASE_SDK_VERSION": "3.6.1.0",
            "SDK_VERSION": version,
            "SDK_VERSION_PREFIX": ".".join(version.split(".")[:3]),
            "SPECS_TAG_CHANGED": changed,
            "SPECS_VERSION_PREFIX": prefix,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
