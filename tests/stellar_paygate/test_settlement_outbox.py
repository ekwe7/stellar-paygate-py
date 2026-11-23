"""Unit and concurrency tests for SettlementOutboxManager in stellar_paygate.exact.

Covers Drips Wave Issue #9:
1. Per-fingerprint ``asyncio.Lock`` coalescing across 20 concurrent ``settle_async`` requests.
2. Multi-poll ``VERIFIED -> SUBMITTED -> PENDING -> SETTLED`` state transitions and history.
3. Dead-Letter Queue (``DLQ``) transitions on exhausted polling or unrecoverable RPC errors.
"""

from __future__ import annotations

import asyncio

import pytest

from stellar_paygate import (
    FeeMode,
    OutboxEntry,
    OutboxSettlementState,
    PayGateErrorCode,
    PaymentPayload,
    PaymentRequirement,
    SettlementOutboxManager,
    SettleResponse,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    VerifierConfig,
    compute_payload_fingerprint,
)

SAMPLE_USDC_ASSET_ADDRESS = "CCW67TSZV3SSS2HXMBQ5JFGCKJNXKZM7UQUWUZPUTHXSTZLEO7SJMI75"
SAMPLE_MERCHANT_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
SAMPLE_PAYER_ADDRESS = "GBDEVU63Y6NTHJQQZIKVTC23NWLQVP3WJ2RI2OTSJTNYOIGICST6DUXR"


def _create_sample_payment_payload(
    amount_atomic: str = "1000000",
    ledger_number: int = 500_000,
) -> PaymentPayload:
    """Create a deterministic valid PaymentPayload for outbox tests."""
    requirement = PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount=amount_atomic,
        asset=SAMPLE_USDC_ASSET_ADDRESS,
        pay_to=SAMPLE_MERCHANT_ADDRESS,
        max_timeout_seconds=60,
        extra=StellarExtraConfig(are_fees_sponsored=True),
    )
    payload_data = StellarPaymentPayloadData(
        payer=SAMPLE_PAYER_ADDRESS,
        network="stellar:testnet",
        ledger=ledger_number,
        expiration_ledger=ledger_number + 12,
        fee_mode=FeeMode.SPONSORED,
        signature_expiration_ledger=ledger_number + 12,
    )
    return PaymentPayload(
        x402_version=2,
        requirement=requirement,
        payload=payload_data,
    )


@pytest.mark.asyncio
async def test_outbox_coalesces_20_concurrent_settlement_requests() -> None:
    """20 concurrent settle_async calls for the same payload must execute 1 RPC submission."""
    payment_payload = _create_sample_payment_payload(amount_atomic="2500000")
    payload_fingerprint: str = compute_payload_fingerprint(payment_payload)
    callback_invocations: int = 0

    async def delayed_settled_callback(
        submitted_payload: PaymentPayload,
        attempt_number: int,
    ) -> OutboxSettlementState:
        nonlocal callback_invocations
        assert submitted_payload == payment_payload
        assert attempt_number == 1
        callback_invocations += 1
        await asyncio.sleep(0.01)
        return OutboxSettlementState.SETTLED

    outbox_manager = SettlementOutboxManager(
        max_poll_attempts=3,
        rpc_submit_callback=delayed_settled_callback,
    )

    concurrent_results: list[SettleResponse] = await asyncio.gather(
        *[outbox_manager.settle_async(payment_payload) for _ in range(20)]
    )

    assert outbox_manager.submission_count == 1
    assert callback_invocations == 1
    assert len(concurrent_results) == 20
    first_response: SettleResponse = concurrent_results[0]
    assert first_response.success is True
    assert first_response.settled_amount == "2500000"
    for response_item in concurrent_results:
        assert response_item == first_response

    recorded_entry: OutboxEntry | None = outbox_manager.get_entry(payload_fingerprint)
    assert recorded_entry is not None
    assert recorded_entry.state == OutboxSettlementState.SETTLED
    assert recorded_entry.attempts == 1
    assert recorded_entry.history == [
        OutboxSettlementState.VERIFIED,
        OutboxSettlementState.SUBMITTED,
        OutboxSettlementState.SETTLED,
    ]
    assert outbox_manager.dead_letter_queue == []


@pytest.mark.asyncio
async def test_outbox_multi_poll_pending_to_settled_state_history() -> None:
    """Simulated PENDING -> PENDING -> SETTLED polling must record full state history."""
    payment_payload = _create_sample_payment_payload(amount_atomic="5000000")
    payload_fingerprint: str = compute_payload_fingerprint(payment_payload)

    def multi_poll_rpc_callback(
        _submitted_payload: PaymentPayload,
        attempt_number: int,
    ) -> OutboxSettlementState:
        if attempt_number < 3:
            return OutboxSettlementState.PENDING
        return OutboxSettlementState.SETTLED

    outbox_manager = SettlementOutboxManager(
        max_poll_attempts=3,
        initial_backoff_seconds=0.001,
        rpc_submit_callback=multi_poll_rpc_callback,
    )

    settlement_response: SettleResponse = await outbox_manager.settle_async(payment_payload)
    assert settlement_response.success is True
    assert settlement_response.settled_amount == "5000000"

    recorded_entry: OutboxEntry | None = outbox_manager.get_entry(payload_fingerprint)
    assert recorded_entry is not None
    assert recorded_entry.state == OutboxSettlementState.SETTLED
    assert recorded_entry.attempts == 3
    assert recorded_entry.history == [
        OutboxSettlementState.VERIFIED,
        OutboxSettlementState.SUBMITTED,
        OutboxSettlementState.PENDING,
        OutboxSettlementState.SETTLED,
    ]


@pytest.mark.asyncio
async def test_outbox_transitions_to_dlq_on_exhausted_pending_or_rpc_exception() -> None:
    """Exhausted PENDING polls or unrecoverable RPC errors must transition to DLQ."""
    pending_payload = _create_sample_payment_payload(amount_atomic="1000000", ledger_number=500_000)
    pending_fingerprint: str = compute_payload_fingerprint(pending_payload)

    always_pending_outbox = SettlementOutboxManager(
        max_poll_attempts=2,
        initial_backoff_seconds=0.0,
        rpc_submit_callback=lambda _payload, _attempt: OutboxSettlementState.PENDING,
    )

    pending_response: SettleResponse = await always_pending_outbox.settle_async(pending_payload)
    assert pending_response.success is False
    assert pending_response.error_code == PayGateErrorCode.SETTLEMENT_FAILURE

    pending_entry: OutboxEntry | None = always_pending_outbox.get_entry(pending_fingerprint)
    assert pending_entry is not None
    assert pending_entry.state == OutboxSettlementState.DLQ
    assert pending_entry.attempts == 2
    assert pending_entry.history == [
        OutboxSettlementState.VERIFIED,
        OutboxSettlementState.SUBMITTED,
        OutboxSettlementState.PENDING,
        OutboxSettlementState.DLQ,
    ]
    assert always_pending_outbox.dead_letter_queue == [pending_entry]

    # Transient exception that exhausts retries also lands in DLQ
    raising_payload = _create_sample_payment_payload(amount_atomic="2000000", ledger_number=500_100)

    def raising_rpc_callback(_payload: PaymentPayload, _attempt: int) -> OutboxSettlementState:
        raise RuntimeError("Soroban RPC node connection reset")

    raising_outbox = SettlementOutboxManager(
        max_poll_attempts=2,
        initial_backoff_seconds=0.001,
        rpc_submit_callback=raising_rpc_callback,
    )
    raising_response: SettleResponse = await raising_outbox.settle_async(raising_payload)
    assert raising_response.success is False
    assert raising_response.error_code == PayGateErrorCode.SETTLEMENT_FAILURE
    assert "Soroban RPC node connection reset" in (raising_response.error_reason or "")
    assert len(raising_outbox.dead_letter_queue) == 1
    assert raising_outbox.dead_letter_queue[0].state == OutboxSettlementState.DLQ


@pytest.mark.asyncio
async def test_outbox_default_direct_settlement_and_validation_edges() -> None:
    """SettlementOutboxManager should support direct settlement and validate config arguments."""
    with pytest.raises(ValueError, match="max_poll_attempts"):
        SettlementOutboxManager(max_poll_attempts=0)

    with pytest.raises(ValueError, match="initial_backoff_seconds"):
        SettlementOutboxManager(initial_backoff_seconds=-0.5)

    # Default direct settlement (no custom callback)
    direct_payload = _create_sample_payment_payload(amount_atomic="750000")
    direct_outbox = SettlementOutboxManager()
    direct_response = await direct_outbox.settle_async(direct_payload)
    assert direct_response.success is True
    assert direct_response.settled_amount == "750000"

    # Forced failure via VerifierConfig
    failing_outbox = SettlementOutboxManager(
        config=VerifierConfig(force_settlement_failure=True),
    )
    failing_response = await failing_outbox.settle_async(direct_payload)
    assert failing_response.success is False
    assert len(failing_outbox.dead_letter_queue) == 1

    # Pre-verification failure (expired ledger) transitions straight to DLQ
    expired_payload = _create_sample_payment_payload(amount_atomic="750000", ledger_number=100_000)
    expired_outbox = SettlementOutboxManager()
    expired_response = await expired_outbox.settle_async(expired_payload, current_ledger=200_000)
    assert expired_response.success is False
    assert expired_response.error_code == PayGateErrorCode.EXPIRED_AUTHORIZATION
    assert len(expired_outbox.dead_letter_queue) == 1
