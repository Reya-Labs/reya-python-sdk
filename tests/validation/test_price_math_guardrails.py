import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

TESTS_ROOT = Path(__file__).parents[1]
EXCLUDED_RELATIVE_PATHS = {
    Path("validation/test_price_math_guardrails.py"),
    Path("helpers/price_helpers.py"),
}

FORBIDDEN_PATTERNS = {
    "raw oracle Decimal for order prices": re.compile(r"Decimal\(\s*str\(\s*[^)\n]*\.oracle_price\s*\)\s*\)"),
    "percentage-improved live best bid": re.compile(r"best_external_bid[^\n]*\*\s*1\.001"),
    "percentage-improved live best ask": re.compile(r"best_external_ask[^\n]*\*\s*0\.999"),
    "hard-coded ws-exec spot self-match prices": re.compile(
        r"ask_px\s*=\s*[\"']2[\"']\s*\n\s*bid_px\s*=\s*[\"']1[\"']"
    ),
}


def test_live_tests_do_not_reintroduce_known_unsafe_price_math():
    violations: list[str] = []

    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel_path = path.relative_to(TESTS_ROOT)
        if rel_path in EXCLUDED_RELATIVE_PATHS or rel_path.parts[0] in {"helpers", "validation"}:
            continue

        source = path.read_text()
        for name, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(source):
                violations.append(f"{rel_path}: {name}")

    assert not violations, "Use tests.helpers.price_helpers instead:\n" + "\n".join(violations)
