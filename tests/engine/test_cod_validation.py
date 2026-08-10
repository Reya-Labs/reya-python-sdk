"""
Cancel-on-disconnect (cancelAllAfter) server-side validation tests — live e2e.

The SDK's `build_cancel_all_after_payload` rejects out-of-range timeouts
locally (pinned offline in tests/validation/test_client_guards.py), so these
tests bypass the client guard and drive RAW requests through the generated
`OrderEntryApi` — each carries a REAL EIP-712 signature over the same
`timeoutMs` that goes on the wire (except the deliberate tamper case), so the
server's INPUT validation is what rejects, not signature recovery.

Mirrors the raw-request precedent in tests/spot/test_api_validation.py:
typed generated request models posted via `tester.client.orders.<op>`, with
error codes asserted on the ApiException body text.
"""

import asyncio
import re
import time

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.cancel_all_after_request import CancelAllAfterRequest
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.cod, pytest.mark.validation]


def _raw_cancel_all_after_request(
    tester: ReyaTester,
    timeout_ms: int,
    signed_timeout_ms: int | None = None,
) -> CancelAllAfterRequest:
    """Build a CancelAllAfterRequest signed over `signed_timeout_ms`
    (defaults to the wire `timeout_ms` — a fully honest request)."""
    nonce = tester.get_next_nonce()
    deadline = int(time.time()) + 60
    signature = tester.client.signature_generator.sign_cancel_all_after(
        account_id=tester.account_id,
        timeout_ms=signed_timeout_ms if signed_timeout_ms is not None else timeout_ms,
        nonce=nonce,
        deadline=deadline,
    )
    return CancelAllAfterRequest(
        accountId=tester.account_id,
        timeoutMs=timeout_ms,
        signature=signature,
        nonce=str(nonce),
        signerWallet=tester.client.signer_wallet_address,
        deadline=deadline,
    )


_RETRY_HINT_RE = re.compile(r"retry after (\d+) ms", re.IGNORECASE)


async def _cancel_all_after_paced(tester: ReyaTester, request: CancelAllAfterRequest, *, attempts: int = 6):
    """Send a raw cancelAllAfter, honouring RATE_LIMITED_ERROR retry hints.

    Admission precedes validation, so under cod-control pressure either branch
    of a validation probe can see a 429 first; a rate-limited reject is a
    non-event (no nonce burn), so resending the same signed payload is safe.
    """
    last_exc: ApiException | None = None
    for _ in range(attempts):
        try:
            return await tester.client.orders.cancel_all_after(request)
        except ApiException as exc:
            if "RATE_LIMITED_ERROR" not in str(exc):
                raise
            match = _RETRY_HINT_RE.search(str(exc))
            hint = int(match.group(1)) if match else 1_000
            last_exc = exc
            await asyncio.sleep((hint + 100) / 1000)
    raise last_exc  # type: ignore[misc]


@pytest.mark.asyncio
# Only values inside the schema envelope [0, 60000] can reach the server through
# the generated CancelAllAfterRequest model (timeoutMs is Field(le=60000, ge=0)).
# So the server-side test covers the [1, 4999] gap (schema-valid but
# business-rejected) and the {0, 5000, 60000} accepted boundary. timeoutMs > 60000
# is a client-side schema/guard rejection — pinned offline in
# tests/validation/test_client_guards.py (build_cancel_all_after_payload + the
# typed model both reject it before the wire), not reachable here.
@pytest.mark.parametrize(
    "timeout_ms,accepted",
    [
        (1, False),
        (4_999, False),
        (5_000, True),
        (60_000, True),
        (0, True),
    ],
)
async def test_timeout_validation_via_api(spot_tester: ReyaTester, timeout_ms: int, accepted: bool):
    """Server-side timeoutMs bounds: {0} ∪ [5000, 60000] accepted, everything
    else INPUT_VALIDATION_ERROR. Raw requests so the client guard can't
    pre-empt the server; every payload is signed over the timeoutMs it sends."""
    request = _raw_cancel_all_after_request(spot_tester, timeout_ms=timeout_ms)

    if not accepted:
        with pytest.raises(ApiException) as exc_info:
            await _cancel_all_after_paced(spot_tester, request)
        error_msg = str(exc_info.value)
        assert (
            "INPUT_VALIDATION_ERROR" in error_msg
        ), f"Expected INPUT_VALIDATION_ERROR for {timeout_ms}, got: {error_msg[:200]}"
        logger.info(f"✅ timeoutMs={timeout_ms} rejected with INPUT_VALIDATION_ERROR")
        return

    response = await _cancel_all_after_paced(spot_tester, request)
    try:
        assert response.timeout_ms == timeout_ms
        if timeout_ms == 0:
            assert response.trigger_at is None, f"Disarm must not echo a triggerAt: {response}"
        else:
            assert response.trigger_at is not None, f"Armed countdown must echo a triggerAt: {response}"
        logger.info(f"✅ timeoutMs={timeout_ms} accepted: {response}")
    finally:
        # Never leak an armed countdown past the test (the fixture also
        # disarms in teardown, but be explicit per arm).
        if timeout_ms != 0:
            await spot_tester.disarm_cod()


@pytest.mark.asyncio
async def test_tampered_signature_rejected(spot_tester: ReyaTester):
    """Sign timeoutMs=30000 but send timeoutMs=40000: the recovered signer
    saw different bytes than the wire payload → UNAUTHORIZED_SIGNATURE_ERROR,
    and the account must NOT end up armed."""
    request = _raw_cancel_all_after_request(spot_tester, timeout_ms=40_000, signed_timeout_ms=30_000)

    with pytest.raises(ApiException) as exc_info:
        await spot_tester.client.orders.cancel_all_after(request)
    error_msg = str(exc_info.value)
    assert "UNAUTHORIZED_SIGNATURE_ERROR" in error_msg, f"Expected UNAUTHORIZED_SIGNATURE_ERROR, got: {error_msg[:200]}"
    logger.info("✅ Tampered cancelAllAfter rejected with UNAUTHORIZED_SIGNATURE_ERROR")

    # Defensive: prove no countdown was armed by the rejected request.
    disarm = await spot_tester.disarm_cod()
    assert disarm.trigger_at is None
