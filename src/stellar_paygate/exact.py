"""exact.py placeholder
Provides verification and settlement engine stubs.
"""

from __future__ import annotations

from .types import (
    PaymentPayload,
    SettleResponse,
    VerifyResponse,
)



def verify_payload(payload: PaymentPayload) -> VerifyResponse:
    """Placeholder verification that always returns valid.
    Real implementation will perform scheme checks, ledger skew, signatures, etc.
    """
    return VerifyResponse(
        is_valid=True,
        invalid_reason=None,
        invalid_detail=None,
        payer=payload.payload.payer,
        authorized_amount=payload.accepted.amount,
        diagnostics=None,
    )


def settle_payload(payload: PaymentPayload) -> SettleResponse:
    """Placeholder settlement that always succeeds.
    Returns a dummy transaction hash.
    """
    return SettleResponse(
        success=True,
        error_reason=None,
        error_detail=None,
        payer=payload.payload.payer,
        transaction="DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF",
        network=payload.accepted.network,
        settled_amount=payload.accepted.amount,
        idempotent_replay=False,
    )
