"""Unit tests for DiscoveryPricingExtension, SpendPolicyGuard, and SettlementDriftTracker.

Covers Drips Wave Issue #8:
1. ``DiscoveryPricingExtension`` serialization inside ``PaymentRequirement.extra.pricing``.
2. ``SpendPolicyGuard`` dual-threshold enforcement (``max_typical_call_atomic`` / ceiling).
3. ``SettlementDriftTracker`` rolling median calculation and >50% settlement drift detection.
"""

from __future__ import annotations

import pytest

from stellar_paygate import (
    DiscoveryPricingExtension,
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirement,
    ResourceInfo,
    SettlementDriftTracker,
    SpendPolicyGuard,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    decode_payment_required,
    encode_payment_required,
)

SAMPLE_USDC_ASSET_ADDRESS = "CCW67TSZV3SSS2HXMBQ5JFGCKJNXKZM7UQUWUZPUTHXSTZLEO7SJMI75"
SAMPLE_MERCHANT_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
SAMPLE_PAYER_ADDRESS = "GBDEVU63Y6NTHJQQZIKVTC23NWLQVP3WJ2RI2OTSJTNYOIGICST6DUXR"


def _build_upto_payment_payload(
    ceiling_amount: str,
    typical_amount: str | None = None,
    scheme: str = "upto",
) -> PaymentPayload:
    """Construct a sample PaymentPayload with optional DiscoveryPricingExtension metadata."""
    pricing_extension: DiscoveryPricingExtension | None = (
        DiscoveryPricingExtension(
            model="upto" if scheme == "upto" else "exact",
            unit="1k_tokens",
            typical_amount=typical_amount,
            note="Typical chat completion uses ~1,500 tokens",
        )
        if typical_amount is not None
        else None
    )
    requirement = PaymentRequirement(
        scheme="upto" if scheme == "upto" else "exact",
        network="stellar:testnet",
        amount=ceiling_amount,
        asset=SAMPLE_USDC_ASSET_ADDRESS,
        pay_to=SAMPLE_MERCHANT_ADDRESS,
        max_timeout_seconds=60,
        extra=StellarExtraConfig(
            are_fees_sponsored=True,
            pricing=pricing_extension,
        ),
    )
    payload_data = StellarPaymentPayloadData(
        payer=SAMPLE_PAYER_ADDRESS,
        network="stellar:testnet",
        ledger=500_000,
        expiration_ledger=500_012,
        fee_mode=FeeMode.SPONSORED,
        signature_expiration_ledger=500_012,
    )
    return PaymentPayload(
        x402_version=2,
        requirement=requirement,
        payload=payload_data,
    )


def test_discovery_pricing_extension_serialization_and_round_trip() -> None:
    """DiscoveryPricingExtension should serialize inside PaymentRequirement.extra.pricing."""
    pricing_metadata = DiscoveryPricingExtension(
        model="upto",
        unit="request",
        typical_amount="250000",
        note="Typical query cost is 0.025 USDC; ceiling covers up to 1.0 USDC",
    )
    requirement = PaymentRequirement(
        scheme="upto",
        network="stellar:testnet",
        amount="10000000",
        asset=SAMPLE_USDC_ASSET_ADDRESS,
        pay_to=SAMPLE_MERCHANT_ADDRESS,
        extra=StellarExtraConfig(
            are_fees_sponsored=True,
            pricing=pricing_metadata,
        ),
    )
    challenge = PaymentRequired(
        resource=ResourceInfo(
            url="https://api.example.com/v1/llm/stream",
            description="Metered LLM endpoint",
        ),
        accepts=[requirement],
    )

    encoded_header: str = encode_payment_required(challenge)
    decoded_challenge: PaymentRequired = decode_payment_required(encoded_header)

    decoded_pricing = decoded_challenge.accepts[0].extra.pricing
    assert decoded_pricing is not None
    assert decoded_pricing.model == "upto"
    assert decoded_pricing.unit == "request"
    assert decoded_pricing.typical_amount == "250000"
    assert decoded_pricing.note == "Typical query cost is 0.025 USDC; ceiling covers up to 1.0 USDC"

    dumped_by_alias = requirement.model_dump(by_alias=True, exclude_none=True)
    assert dumped_by_alias["extra"]["pricing"]["typicalAmount"] == "250000"


def test_spend_policy_guard_dual_threshold_validation() -> None:
    """SpendPolicyGuard should enforce both max_typical_call_atomic and max_upto_ceiling_atomic."""
    guard = SpendPolicyGuard(
        max_per_call=20_000_000,
        max_budget=100_000_000,
        max_typical_call_atomic=500_000,
        max_upto_ceiling_atomic=10_000_000,
    )

    # 1. Both typical (300_000 <= 500_000) and ceiling (5_000_000 <= 10_000_000) within limits
    valid_payload = _build_upto_payment_payload(
        ceiling_amount="5000000",
        typical_amount="300000",
    )
    guard.check(valid_payload)
    assert guard.consumed_budget == 5_000_000

    # 2. Declared typical_amount (600_000) exceeds max_typical_call_atomic (500_000)
    excessive_typical_payload = _build_upto_payment_payload(
        ceiling_amount="5000000",
        typical_amount="600000",
    )
    with pytest.raises(PaygateProtocolError) as typical_exc_info:
        guard.check(excessive_typical_payload)
    assert typical_exc_info.value.code == PayGateErrorCode.AMOUNT_MISMATCH
    assert "max_typical_call_atomic" in str(typical_exc_info.value)

    # 3. Upto ceiling (12_000_000) exceeds max_upto_ceiling_atomic (10_000_000)
    excessive_ceiling_payload = _build_upto_payment_payload(
        ceiling_amount="12000000",
        typical_amount="300000",
    )
    with pytest.raises(PaygateProtocolError) as ceiling_exc_info:
        guard.check(excessive_ceiling_payload)
    assert ceiling_exc_info.value.code == PayGateErrorCode.EXCEEDS_MAX_UPTO
    assert "max_upto_ceiling_atomic" in str(ceiling_exc_info.value)


def test_spend_policy_guard_rejects_non_positive_threshold_configs() -> None:
    """SpendPolicyGuard should reject non-positive typical or upto ceiling thresholds."""
    with pytest.raises(ValueError, match="max_typical_call_atomic"):
        SpendPolicyGuard(max_typical_call_atomic=0)

    with pytest.raises(ValueError, match="max_upto_ceiling_atomic"):
        SpendPolicyGuard(max_upto_ceiling_atomic=-100)


def test_settlement_drift_tracker_rolling_median_and_drift_detection() -> None:
    """SettlementDriftTracker should compute rolling medians and flag >50% positive drift."""
    drift_tracker = SettlementDriftTracker(drift_threshold_ratio=0.50, max_window_size=5)
    resource_url = "https://api.example.com/v1/metered-search"

    # Before any settlements are recorded
    assert drift_tracker.get_rolling_median_atomic(resource_url) is None
    assert drift_tracker.is_drifting(resource_url) is False

    # Record 3 settlements near typical (100_000): [110_000, 120_000, 150_000] -> median 120_000
    drift_tracker.record_settlement(
        resource_url, declared_typical_atomic="100000", settled_atomic="110000"
    )
    drift_tracker.record_settlement(
        resource_url, declared_typical_atomic=100_000, settled_atomic=120_000
    )
    drift_tracker.record_settlement(
        resource_url, declared_typical_atomic=100_000, settled_atomic=150_000
    )

    assert drift_tracker.get_rolling_median_atomic(resource_url) == 120_000
    assert drift_tracker.is_drifting(resource_url) is False

    # Even number of samples: [110_000, 120_000, 150_000, 180_000] -> median 135_000
    drift_tracker.record_settlement(
        resource_url, declared_typical_atomic=100_000, settled_atomic=180_000
    )
    assert drift_tracker.get_rolling_median_atomic(resource_url) == 135_000
    assert drift_tracker.is_drifting(resource_url) is False

    # Add high settlements pushing rolling median above 150_000 (>50% above 100_000)
    drift_tracker.record_settlement(
        resource_url, declared_typical_atomic=100_000, settled_atomic=190_000
    )
    drift_tracker.record_settlement(
        resource_url, declared_typical_atomic=100_000, settled_atomic=200_000
    )
    # Window of 5 is now [120_000, 150_000, 180_000, 190_000, 200_000] -> median 180_000 (> 150_000)
    assert drift_tracker.get_rolling_median_atomic(resource_url) == 180_000
    assert drift_tracker.is_drifting(resource_url) is True


def test_settlement_drift_tracker_input_validation() -> None:
    """SettlementDriftTracker should validate constructor parameters and settlement inputs."""
    with pytest.raises(ValueError, match="drift_threshold_ratio"):
        SettlementDriftTracker(drift_threshold_ratio=-0.1)

    with pytest.raises(ValueError, match="max_window_size"):
        SettlementDriftTracker(max_window_size=0)

    drift_tracker = SettlementDriftTracker()
    with pytest.raises(ValueError, match="resource_url"):
        drift_tracker.record_settlement("   ", declared_typical_atomic=100, settled_atomic=100)

    with pytest.raises(ValueError, match="declared_typical_atomic"):
        drift_tracker.record_settlement(
            "https://api.example.com/v1/query",
            declared_typical_atomic=0,
            settled_atomic=100,
        )

    with pytest.raises(ValueError, match="settled_atomic"):
        drift_tracker.record_settlement(
            "https://api.example.com/v1/query",
            declared_typical_atomic=100,
            settled_atomic="-50",
        )
