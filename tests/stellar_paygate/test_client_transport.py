"""Unit and mock-transport tests for SpendPolicyGuard and HTTPX 402 transports."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import httpx
import pytest

from stellar_paygate.client import (
    PaygateAsyncTransport,
    PaygateSyncTransport,
    SpendPolicyGuard,
    atomic_units_to_decimal,
    compute_request_fingerprint,
    decimal_to_atomic_units,
    parse_challenge_atomic_amount,
    wrap_httpx_with_paygate,
)
from stellar_paygate.codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    decode_payment_signature,
    encode_payment_required,
    encode_payment_response,
)
from stellar_paygate.types import (
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirement,
    ResourceInfo,
    SettlementResponse,
    StellarExtraConfig,
    StellarPaymentPayloadData,
)

SAMPLE_ASSET_CONTRACT = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
SAMPLE_MERCHANT_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
SAMPLE_PAYER_ADDRESS = "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"


def build_test_payment_requirement(
    amount: str = "1000000",
    scheme: Literal["exact", "upto"] = "exact",
    network: str = "stellar:testnet",
    asset: str = SAMPLE_ASSET_CONTRACT,
    pay_to: str = SAMPLE_MERCHANT_ADDRESS,
    are_fees_sponsored: bool = True,
) -> PaymentRequirement:
    """Create a sample PaymentRequirement for client tests."""
    return PaymentRequirement(
        scheme=scheme,
        network=network,
        amount=amount,
        asset=asset,
        pay_to=pay_to,
        extra=StellarExtraConfig(are_fees_sponsored=are_fees_sponsored),
    )


def build_test_payment_payload(
    amount: str = "1000000",
    network: str = "stellar:testnet",
    asset: str = SAMPLE_ASSET_CONTRACT,
    ledger: int = 500_000,
    expiration_ledger: int = 500_012,
) -> PaymentPayload:
    """Create a sample PaymentPayload for SpendPolicyGuard tests."""
    requirement = build_test_payment_requirement(
        amount=amount,
        network=network,
        asset=asset,
    )
    payload_data = StellarPaymentPayloadData(
        payer=SAMPLE_PAYER_ADDRESS,
        network=network,
        ledger=ledger,
        expiration_ledger=expiration_ledger,
        fee_mode=FeeMode.SPONSORED,
        signature_expiration_ledger=expiration_ledger,
    )
    return PaymentPayload(requirement=requirement, payload=payload_data)


def build_encoded_payment_required_header(
    amount: str = "1000000",
    network: str = "stellar:testnet",
    asset: str = SAMPLE_ASSET_CONTRACT,
    are_fees_sponsored: bool = True,
) -> str:
    """Create a Base64-encoded PAYMENT-REQUIRED header string."""
    challenge_model = PaymentRequired(
        resource=ResourceInfo(
            url="https://api.example.com/v1/report",
            description="Paid analytics report",
        ),
        accepts=[
            build_test_payment_requirement(
                amount=amount,
                network=network,
                asset=asset,
                are_fees_sponsored=are_fees_sponsored,
            )
        ],
    )
    return encode_payment_required(challenge_model)


@pytest.mark.parametrize(
    ("human_input", "expected_atomic_units", "expected_formatted_decimal"),
    [
        ("0.51", 5_100_000, "0.5100000"),
        ("1", 10_000_000, "1.0000000"),
        (5, 50_000_000, "5.0000000"),
        (Decimal("0.0000001"), 1, "0.0000001"),
        ("123456.7890123", 1_234_567_890_123, "123456.7890123"),
    ],
)
def test_decimal_and_atomic_unit_conversions_zero_float_drift(
    human_input: str | Decimal | int,
    expected_atomic_units: int,
    expected_formatted_decimal: str,
) -> None:
    """Verify exact Decimal-to-atomic-unit quantization and reverse formatting."""
    converted_atomic: int = decimal_to_atomic_units(human_input)
    assert converted_atomic == expected_atomic_units
    assert atomic_units_to_decimal(converted_atomic) == expected_formatted_decimal


@pytest.mark.parametrize(
    "invalid_human_amount",
    [
        "",
        "   ",
        "not_a_number",
        "0",
        "-1.5",
        0,
        -10,
        True,
        "0.12345678",  # 8 decimal places (sub-stroop dust)
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_decimal_to_atomic_units_rejects_invalid_and_sub_stroop_inputs(
    invalid_human_amount: str | Decimal | int,
) -> None:
    """Verify decimal_to_atomic_units raises ValueError on invalid or >7 decimal inputs."""
    with pytest.raises(ValueError):
        decimal_to_atomic_units(invalid_human_amount)


def test_atomic_units_to_decimal_rejects_negative_or_boolean() -> None:
    """Verify atomic_units_to_decimal rejects negative integers and booleans."""
    assert atomic_units_to_decimal(0) == "0.0000000"
    with pytest.raises(ValueError):
        atomic_units_to_decimal(-1)
    with pytest.raises(ValueError):
        atomic_units_to_decimal(True)


@pytest.mark.parametrize(
    "invalid_challenge_amount",
    [
        "",
        "   ",
        "0",
        "-500",
        "10.5",
        "abc",
        0,
        -1,
        True,
    ],
)
def test_parse_challenge_atomic_amount_rejects_invalid_values(
    invalid_challenge_amount: str | int,
) -> None:
    """Verify parse_challenge_atomic_amount raises PaygateProtocolError on invalid input."""
    with pytest.raises(PaygateProtocolError) as exc_info:
        parse_challenge_atomic_amount(invalid_challenge_amount)
    assert exc_info.value.code == PayGateErrorCode.AMOUNT_MISMATCH


def test_spend_policy_guard_initialization_validation() -> None:
    """Verify SpendPolicyGuard rejects non-positive caps and budgets."""
    with pytest.raises(ValueError, match="max_per_call"):
        SpendPolicyGuard(max_per_call=0)
    with pytest.raises(ValueError, match="max_budget"):
        SpendPolicyGuard(max_budget=-100)


def test_spend_policy_guard_boundary_math_and_rolling_budget() -> None:
    """Verify exact per-call cap, +1 stroop over cap, and rolling budget exhaustion."""
    policy_guard = SpendPolicyGuard(
        max_per_call=1_000_000,
        max_budget=2_500_000,
        allowed_networks={"stellar:testnet"},
        allowed_assets={SAMPLE_ASSET_CONTRACT},
    )
    assert policy_guard.remaining_budget == 2_500_000

    # 1. Exact per-call cap (1,000,000 stroops) succeeds
    exact_cap_payload = build_test_payment_payload(amount="1000000")
    policy_guard.check(exact_cap_payload)
    assert policy_guard.consumed_budget == 1_000_000
    assert policy_guard.remaining_budget == 1_500_000

    # 2. +1 stroop over per-call cap (1,000,001 stroops) raises PaygateProtocolError
    over_cap_payload = build_test_payment_payload(amount="1000001")
    with pytest.raises(PaygateProtocolError) as per_call_exc:
        policy_guard.check(over_cap_payload)
    assert per_call_exc.value.code == PayGateErrorCode.AMOUNT_MISMATCH
    assert policy_guard.consumed_budget == 1_000_000

    # 3. Second valid call of 1,000,000 stroops brings consumed_budget to 2,000,000
    policy_guard.check(exact_cap_payload)
    assert policy_guard.consumed_budget == 2_000_000
    assert policy_guard.remaining_budget == 500_000

    # 4. Call of 500,001 stroops (within per-call cap, but +1 over remaining budget) fails
    over_budget_payload = build_test_payment_payload(amount="500001")
    with pytest.raises(PaygateProtocolError) as budget_exc:
        policy_guard.check(over_budget_payload)
    assert budget_exc.value.code == PayGateErrorCode.EXCEEDS_MAX_UPTO
    assert policy_guard.consumed_budget == 2_000_000

    # 5. Exact remaining budget (500,000 stroops) succeeds and exhausts budget
    exact_remaining_payload = build_test_payment_payload(amount="500000")
    policy_guard.check(exact_remaining_payload)
    assert policy_guard.consumed_budget == 2_500_000
    assert policy_guard.remaining_budget == 0


def test_spend_policy_guard_network_and_asset_allowlists() -> None:
    """Verify disallowed networks and assets are rejected before deducting budget."""
    policy_guard = SpendPolicyGuard(
        max_per_call=5_000_000,
        max_budget=10_000_000,
        allowed_networks={"stellar:testnet"},
        allowed_assets={SAMPLE_ASSET_CONTRACT},
    )

    untrusted_network_payload = build_test_payment_payload(network="stellar:pubnet")
    with pytest.raises(PaygateProtocolError) as network_exc:
        policy_guard.check(untrusted_network_payload)
    assert network_exc.value.code == PayGateErrorCode.UNSUPPORTED_NETWORK
    assert policy_guard.consumed_budget == 0

    untrusted_asset_payload = build_test_payment_payload(asset="native")
    with pytest.raises(PaygateProtocolError) as asset_exc:
        policy_guard.check(untrusted_asset_payload)
    assert asset_exc.value.code == PayGateErrorCode.INVALID_ADDRESS
    assert policy_guard.consumed_budget == 0


def test_spend_policy_guard_inflight_idempotency_cache_and_rollback() -> None:
    """Verify in-flight request cache prevents double deduction and supports rollback."""
    policy_guard = SpendPolicyGuard(
        max_per_call=2_000_000,
        max_budget=5_000_000,
        current_ledger=500_000,
    )
    request_fingerprint: str = compute_request_fingerprint(
        http_method="GET",
        request_url="https://api.example.com/v1/report",
    )
    payment_payload = build_test_payment_payload(
        amount="1500000",
        ledger=500_000,
        expiration_ledger=500_010,
    )

    # First check reserves 1,500,000 and caches payload
    policy_guard.check(payment_payload, request_fingerprint=request_fingerprint)
    assert policy_guard.consumed_budget == 1_500_000
    assert policy_guard.get_cached_payload(request_fingerprint) == payment_payload

    # Second check with the same unexpired request_fingerprint does NOT double-deduct
    policy_guard.check(payment_payload, request_fingerprint=request_fingerprint)
    assert policy_guard.consumed_budget == 1_500_000

    # Rolling back reservation by fingerprint restores budget and clears cache
    policy_guard.release_reservation(request_fingerprint=request_fingerprint)
    assert policy_guard.consumed_budget == 0
    assert policy_guard.get_cached_payload(request_fingerprint) is None

    # Direct atomic amount release also works and validates non-negative input
    policy_guard.check(payment_payload)
    assert policy_guard.consumed_budget == 1_500_000
    policy_guard.release_reservation(atomic_amount=500_000)
    assert policy_guard.consumed_budget == 1_000_000

    with pytest.raises(ValueError, match="non-negative"):
        policy_guard.release_reservation(atomic_amount=-10)


def test_spend_policy_guard_expired_cache_eviction_and_challenge_selection() -> None:
    """Verify expired cached payloads are evicted and challenge builder selects matching option."""
    policy_guard = SpendPolicyGuard(
        max_per_call=2_000_000,
        max_budget=5_000_000,
        allowed_networks={"stellar:testnet"},
        allowed_assets={SAMPLE_ASSET_CONTRACT},
        current_ledger=500_000,
    )
    request_fingerprint = "test_fingerprint_expiry"
    payment_payload = build_test_payment_payload(
        amount="1000000",
        ledger=500_000,
        expiration_ledger=500_005,
    )
    policy_guard.check(payment_payload, request_fingerprint=request_fingerprint)
    assert policy_guard.consumed_budget == 1_000_000

    # Advance ledger past expiration_ledger
    policy_guard.current_ledger = 500_005
    assert policy_guard.get_cached_payload(request_fingerprint) is None

    # Empty accepts list in PaymentRequired raises INVALID_PAYLOAD
    empty_challenge = PaymentRequired(
        resource=ResourceInfo(url="https://api.example.com/v1/empty"),
        accepts=[],
    )
    with pytest.raises(PaygateProtocolError) as empty_exc:
        policy_guard.build_payment_payload_for_challenge(empty_challenge)
    assert empty_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD


def test_sync_httpx_transport_402_challenge_auto_retry_flow() -> None:
    """Verify synchronous httpx.Client automatically answers 402 and retries with signature."""
    call_counter: int = 0

    def mock_server_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_counter
        call_counter += 1

        if PAYMENT_SIGNATURE_HEADER not in request.headers:
            encoded_challenge = build_encoded_payment_required_header(amount="750000")
            return httpx.Response(
                status_code=402,
                headers={PAYMENT_REQUIRED_HEADER: encoded_challenge},
                json={"detail": "Payment required"},
            )

        decoded_payload = decode_payment_signature(request.headers[PAYMENT_SIGNATURE_HEADER])
        assert decoded_payload.requirement.amount == "750000"
        assert decoded_payload.payload.payer == SAMPLE_PAYER_ADDRESS

        settlement_receipt = SettlementResponse(
            success=True,
            scheme="exact",
            network="stellar:testnet",
            payer=decoded_payload.payload.payer,
            transaction_hash="A" * 64,
            settled_amount="750000",
        )
        return httpx.Response(
            status_code=200,
            headers={PAYMENT_RESPONSE_HEADER: encode_payment_response(settlement_receipt)},
            json={"data": "unlocked_premium_content"},
        )

    policy_guard = SpendPolicyGuard(
        max_per_call=1_000_000,
        max_budget=3_000_000,
        payer_address=SAMPLE_PAYER_ADDRESS,
    )
    http_client = httpx.Client(transport=httpx.MockTransport(mock_server_handler))
    wrapped_client = wrap_httpx_with_paygate(http_client, policy_guard)

    assert wrapped_client is http_client
    # Calling wrap_httpx_with_paygate a second time is idempotent
    assert wrap_httpx_with_paygate(http_client, policy_guard) is http_client

    response = wrapped_client.get("https://api.example.com/v1/report")
    assert response.status_code == 200
    assert response.json() == {"data": "unlocked_premium_content"}
    assert PAYMENT_RESPONSE_HEADER in response.headers
    assert call_counter == 2
    assert policy_guard.consumed_budget == 750_000
    wrapped_client.close()


def test_sync_httpx_transport_passthrough_and_error_rollback() -> None:
    """Verify non-402 responses pass through and transport failures roll back budget."""
    attempt_number: int = 0

    def failing_on_retry_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_number
        attempt_number += 1

        if request.url.path == "/free":
            return httpx.Response(status_code=200, json={"free": True})

        if request.url.path == "/402-no-header":
            return httpx.Response(status_code=402, json={"error": "missing header"})

        if PAYMENT_SIGNATURE_HEADER not in request.headers:
            return httpx.Response(
                status_code=402,
                headers={
                    PAYMENT_REQUIRED_HEADER: build_encoded_payment_required_header(
                        amount="500000",
                        are_fees_sponsored=False,
                    )
                },
            )

        raise httpx.ConnectError("Simulated connection drop during paid retry")

    policy_guard = SpendPolicyGuard(max_per_call=1_000_000, max_budget=2_000_000)
    http_client = wrap_httpx_with_paygate(
        httpx.Client(transport=httpx.MockTransport(failing_on_retry_handler)),
        policy_guard,
    )

    free_response = http_client.get("https://api.example.com/free")
    assert free_response.status_code == 200
    assert policy_guard.consumed_budget == 0

    bare_402_response = http_client.get("https://api.example.com/402-no-header")
    assert bare_402_response.status_code == 402
    assert policy_guard.consumed_budget == 0

    with pytest.raises(httpx.ConnectError):
        http_client.get("https://api.example.com/paid-drop")

    # Budget reservation must be rolled back automatically after ConnectError
    assert policy_guard.consumed_budget == 0
    http_client.close()


@pytest.mark.asyncio
async def test_async_httpx_transport_402_challenge_auto_retry_and_rollback() -> None:
    """Verify asynchronous httpx.AsyncClient handles 402 challenges and transport rollbacks."""
    async_call_count: int = 0

    async def async_mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal async_call_count
        async_call_count += 1

        if request.url.path == "/ok":
            return httpx.Response(status_code=200, json={"ok": True})

        if request.url.path == "/bare-402":
            return httpx.Response(status_code=402)

        if request.url.path == "/drop-on-retry":
            if PAYMENT_SIGNATURE_HEADER not in request.headers:
                return httpx.Response(
                    status_code=402,
                    headers={
                        PAYMENT_REQUIRED_HEADER: build_encoded_payment_required_header(
                            amount="400000"
                        )
                    },
                )
            raise httpx.ReadTimeout("Simulated async timeout on retry")

        if PAYMENT_SIGNATURE_HEADER not in request.headers:
            return httpx.Response(
                status_code=402,
                headers={
                    PAYMENT_REQUIRED_HEADER: build_encoded_payment_required_header(amount="600000")
                },
            )

        if request.url.path == "/still-402":
            return httpx.Response(status_code=402, json={"error": "rejected"})

        return httpx.Response(status_code=200, json={"async_paid": True})

    policy_guard = SpendPolicyGuard(max_per_call=1_000_000, max_budget=3_000_000)
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(async_mock_handler))
    wrapped_async_client = wrap_httpx_with_paygate(async_client, policy_guard)
    assert wrapped_async_client is async_client
    assert wrap_httpx_with_paygate(async_client, policy_guard) is async_client

    ok_response = await wrapped_async_client.get("https://api.example.com/ok")
    assert ok_response.status_code == 200

    bare_402 = await wrapped_async_client.get("https://api.example.com/bare-402")
    assert bare_402.status_code == 402

    paid_response = await wrapped_async_client.post(
        "https://api.example.com/v1/inference",
        json={"prompt": "hello"},
    )
    assert paid_response.status_code == 200
    assert paid_response.json() == {"async_paid": True}
    assert policy_guard.consumed_budget == 600_000

    # If the server returns 402 even when PAYMENT-SIGNATURE is already present, do not loop
    still_402 = await wrapped_async_client.get("https://api.example.com/still-402")
    assert still_402.status_code == 402
    assert policy_guard.consumed_budget == 1_200_000

    # Async transport error rolls back reservation
    with pytest.raises(httpx.ReadTimeout):
        await wrapped_async_client.get("https://api.example.com/drop-on-retry")
    assert policy_guard.consumed_budget == 1_200_000

    await wrapped_async_client.aclose()


def test_sync_transport_stops_if_retry_returns_402_and_cached_fingerprint_reuse() -> None:
    """Verify sync transport does not infinite-loop if retry returns 402 and reuses cache."""

    def always_402_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=402,
            headers={
                PAYMENT_REQUIRED_HEADER: build_encoded_payment_required_header(amount="300000")
            },
        )

    policy_guard = SpendPolicyGuard(max_per_call=1_000_000, max_budget=2_000_000)
    sync_transport = PaygateSyncTransport(
        inner_transport=httpx.MockTransport(always_402_handler),
        guard=policy_guard,
    )
    http_client = httpx.Client(transport=sync_transport)

    first_402 = http_client.get("https://api.example.com/always-402")
    assert first_402.status_code == 402
    assert policy_guard.consumed_budget == 300_000

    # Repeating the identical request reuses the in-flight cache without deducting again
    second_402 = http_client.get("https://api.example.com/always-402")
    assert second_402.status_code == 402
    assert policy_guard.consumed_budget == 300_000
    assert isinstance(sync_transport, PaygateSyncTransport)
    assert PaygateAsyncTransport is not None
    http_client.close()
