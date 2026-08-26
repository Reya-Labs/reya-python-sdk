#!/usr/bin/env python3
"""Run the integration suite twice and report only the failures common to both.

Why this exists
---------------
Against shared devnet, a single run is not a reliable signal. Measured over
three comparable runs of the SAME code:

    run 1: 10 failed     run 2: 16 failed     run 3: 14 failed
    33 distinct tests failed at least once
      2 failed in ALL three runs
     28 failed in exactly ONE run

So ~85% of what a single run reports is noise: other engineers trading the
same shared accounts, a market-making bot on the book, async settlement lag,
and an intermittently flaky ws-exec transport. Chasing the failures from any
one run moves the sample rather than reducing it.

Intersecting two runs is the cheapest way to separate signal from noise -- it
needs no environment changes and no test edits. A test that fails twice in a
row is worth investigating; one that fails once usually is not.

This is a MITIGATION, not a fix. The durable fix is exclusive per-run
accounts, which removes the largest noise source outright. Until then, treat
the intersection as the real result and the symmetric difference as a
flakiness measurement worth watching: if it grows, the environment is getting
noisier.

Usage
-----
    poetry run python scripts/run_suite_twice.py
    poetry run python scripts/run_suite_twice.py -m "not localnet and not spot"

Exit code is 0 only when the intersection is empty.
"""

from __future__ import annotations

import re

# subprocess, not pytest.main(): the two runs must not share a process.
# Session-scoped fixtures, module-level env reads and the SDK's cached
# clients all persist in-process, so a second in-process run would inherit
# the first one's state -- which is exactly the contamination this script
# exists to measure. nosec B404: no shell, argv is a literal list.
import subprocess  # nosec B404
import sys

FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")


def run(pytest_args: list[str], label: str) -> tuple[set[str], str]:
    """Run pytest once; return (failed test ids, summary line)."""
    cmd = [
        "poetry",
        "run",
        "pytest",
        *pytest_args,
        "-q",
        "--no-header",
        "--tb=no",
        "-rf",
        "-p",
        "no:cacheprovider",
    ]
    print(f"\n=== {label}: {' '.join(cmd)}", flush=True)
    # nosec B603: shell=False and cmd is a literal argv list; the only
    # caller-supplied part is this script's own CLI args, already in the
    # operator's shell.
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
    out = proc.stdout + proc.stderr

    failed = set()
    for line in out.splitlines():
        m = FAILED_LINE.match(line)
        if m:
            # strip any " - <error>" suffix pytest appends
            failed.add(m.group(1).split(" - ")[0])

    summary = ""
    for line in reversed(out.splitlines()):
        if re.search(r"\d+ (passed|failed)", line):
            summary = line.strip()
            break

    print(f"    {summary}", flush=True)
    return failed, summary


def main() -> int:
    pytest_args = sys.argv[1:] or ["-m", "not localnet"]

    first, first_summary = run(pytest_args, "run 1/2")
    second, second_summary = run(pytest_args, "run 2/2")

    both = sorted(first & second)
    only_once = sorted(first ^ second)

    print("\n" + "=" * 72)
    print("  run 1: " + (first_summary or "(no summary)"))
    print("  run 2: " + (second_summary or "(no summary)"))
    print("=" * 72)

    print(f"\nFAILED IN BOTH RUNS — treat as real ({len(both)}):")
    for t in both:
        print(f"  {t}")
    if not both:
        print("  (none)")

    print(f"\nFailed in only one run — flaky/environmental ({len(only_once)}):")
    for t in only_once:
        print(f"  {t}")
    if not only_once:
        print("  (none)")

    if only_once:
        total = len(first | second)
        pct = round(100 * len(only_once) / total)
        print(
            f"\nFlakiness: {len(only_once)} of {total} distinct failures ({pct}%) "
            "did not reproduce. If this share is growing, the shared environment "
            "is getting noisier -- that is the thing to fix, not these tests."
        )

    return 1 if both else 0


if __name__ == "__main__":
    raise SystemExit(main())
