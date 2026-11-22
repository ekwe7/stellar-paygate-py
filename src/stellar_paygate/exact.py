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
        success=True,
        scheme=payload.requirement.scheme,
        network=payload.requirement.network,
        payer=payload.payload.payer,
    )


def settle_payload(payload: PaymentPayload) -> SettleResponse:
    """Placeholder settlement that always succeeds.
    Returns a dummy transaction hash.
    """
    return SettleResponse(
        success=True,
        scheme=payload.requirement.scheme,
        network=payload.requirement.network,
        payer=payload.payload.payer,
        transaction_hash="DEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF",
        settled_amount=payload.requirement.amount,
    )
