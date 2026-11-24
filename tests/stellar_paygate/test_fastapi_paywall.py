"""In-process FastAPI and httpx.ASGITransport integration tests for fastapi_ext."""

# NOTE: ``from __future__ import annotations`` is intentionally omitted so
# FastAPI route handlers resolve concrete ``Depends(...)`` types at runtime.

from collections.abc import AsyncIterator
from typing import Annotated, Literal

import httpx
import pytest
from fastapi import Depends, FastAPI, Request, Response
from fastapi.testclient import TestClient

from stellar_paygate.client import SpendPolicyGuard, wrap_httpx_with_paygate
from stellar_paygate.codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    decode_payment_required,
    decode_payment_response,
    encode_payment_signature,
)
from stellar_paygate.exact import UptoVerifier, VerifierConfig
from stellar_paygate.fastapi_ext import (
    MeteredSettlementContext,
    PayGateStreamingUptoResponse,
    add_payment_middleware,
    require_payment,
    require_upto_ceiling,
)
from stellar_paygate.types import (
    FeeMode,
    PayGateErrorCode,
    PaymentPayload,
    PaymentRequirement,
    SettleResponse,
    StellarPaymentPayloadData,
)

TEST_ASSET_CONTRACT = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
TEST_MERCHANT_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
TEST_PAYER_ADDRESS = "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"


def make_route_requirement(
    scheme: Literal["exact", "upto"] = "exact",
    amount: str = "1000000",
    network: str = "stellar:testnet",
    asset: str = TEST_ASSET_CONTRACT,
    pay_to: str = TEST_MERCHANT_ADDRESS,
) -> PaymentRequirement:
    """Build a standard PaymentRequirement for FastAPI route tests."""
    return PaymentRequirement(
        scheme=scheme,
        network=network,
        amount=amount,
        asset=asset,
        pay_to=pay_to,
    )


def make_signed_payload(
    requirement: PaymentRequirement,
    payer: str = TEST_PAYER_ADDRESS,
    network: str | None = None,
    ledger: int = 500_000,
    expiration_ledger: int = 500_012,
) -> PaymentPayload:
    """Build a PaymentPayload matching a given requirement."""
    payload_data = StellarPaymentPayloadData(
        payer=payer,
        network=network if network is not None else requirement.network,
        ledger=ledger,
        expiration_ledger=expiration_ledger,
        fee_mode=FeeMode.SPONSORED,
        signature_expiration_ledger=expiration_ledger,
    )
    return PaymentPayload(requirement=requirement, payload=payload_data)


def create_demo_fastapi_app(
    middleware_requirement: PaymentRequirement | None = None,
    protected_paths: list[str] | None = None,
    verifier_config: VerifierConfig | None = None,
) -> FastAPI:
    """Create a FastAPI app exercising middleware, require_payment, and require_upto_ceiling."""
    app = FastAPI()

    if middleware_requirement is not None or protected_paths is not None:
        add_payment_middleware(
            app=app,
            requirement=middleware_requirement,
            protected_paths=protected_paths,
            verifier_config=verifier_config,
            current_ledger=500_000,
        )

    exact_route_requirement = make_route_requirement(scheme="exact", amount="1000000")
    upto_route_requirement = make_route_requirement(scheme="upto", amount="2000000")

    exact_dependency = require_payment(
        requirement=exact_route_requirement,
        verifier_config=verifier_config,
        current_ledger=500_000,
    )
    upto_dependency = require_upto_ceiling(
        requirement=upto_route_requirement,
        verifier_config=verifier_config,
        current_ledger=500_000,
    )

    @app.get("/health")
    def health_endpoint() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/v1/report")
    def paid_exact_report(
        settlement: Annotated[SettleResponse, Depends(exact_dependency)],
    ) -> dict[str, str | None]:
        return {
            "report": "Q4 Stellar Network Metrics",
            "payer": settlement.payer,
            "tx_hash": settlement.transaction_hash,
            "settled_amount": settlement.settled_amount,
        }

    @app.post("/v1/inference")
    def paid_upto_inference(
        requested_tokens: int,
        meter: Annotated[MeteredSettlementContext, Depends(upto_dependency)],
    ) -> dict[str, str | int | None]:
        actual_cost_stroops: int = requested_tokens * 1_000
        settlement = meter.settle_usage(actual_cost_stroops)
        return {
            "tokens_generated": requested_tokens,
            "settled_amount": settlement.settled_amount,
            "tx_hash": settlement.transaction_hash,
        }

    @app.get("/middleware/protected")
    def middleware_protected_endpoint(request: Request) -> dict[str, str | None]:
        settlement: SettleResponse | None = getattr(request.state, "payment_settlement", None)
        return {
            "resource": "middleware_data",
            "settled_amount": settlement.settled_amount if settlement else None,
        }

    @app.get("/public/info")
    def public_info_endpoint() -> dict[str, str]:
        return {"public": "open"}

    return app


def test_require_payment_missing_malformed_and_mismatched_headers() -> None:
    """Verify require_payment returns 402 with PAYMENT-REQUIRED on invalid requests."""
    app = create_demo_fastapi_app()
    client = TestClient(app)

    # 1. Missing PAYMENT-SIGNATURE header -> 402 + PAYMENT-REQUIRED
    missing_response = client.get("/v1/report")
    assert missing_response.status_code == 402
    assert PAYMENT_REQUIRED_HEADER in missing_response.headers
    decoded_challenge = decode_payment_required(missing_response.headers[PAYMENT_REQUIRED_HEADER])
    assert decoded_challenge.accepts[0].amount == "1000000"

    # 2. Malformed Base64 PAYMENT-SIGNATURE header -> 402
    malformed_response = client.get(
        "/v1/report",
        headers={PAYMENT_SIGNATURE_HEADER: "!!!not_base64!!!"},
    )
    assert malformed_response.status_code == 402
    assert malformed_response.json()["detail"]["error"] == PayGateErrorCode.INVALID_HEADER.value

    # 3. Mismatched scheme (upto sent to exact route) -> 402 UNSUPPORTED_SCHEME
    wrong_scheme_payload = make_signed_payload(
        make_route_requirement(scheme="upto", amount="1000000")
    )
    wrong_scheme_response = client.get(
        "/v1/report",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(wrong_scheme_payload)},
    )
    assert wrong_scheme_response.status_code == 402
    assert (
        wrong_scheme_response.json()["detail"]["error"] == PayGateErrorCode.UNSUPPORTED_SCHEME.value
    )

    # 4. Mismatched network -> 402 UNSUPPORTED_NETWORK
    wrong_network_payload = make_signed_payload(make_route_requirement(network="stellar:pubnet"))
    wrong_network_response = client.get(
        "/v1/report",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(wrong_network_payload)},
    )
    assert wrong_network_response.status_code == 402
    assert (
        wrong_network_response.json()["detail"]["error"]
        == PayGateErrorCode.UNSUPPORTED_NETWORK.value
    )

    # 5. Mismatched asset -> 402 INVALID_ADDRESS
    wrong_asset_payload = make_signed_payload(make_route_requirement(asset="native"))
    wrong_asset_response = client.get(
        "/v1/report",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(wrong_asset_payload)},
    )
    assert wrong_asset_response.status_code == 402
    assert wrong_asset_response.json()["detail"]["error"] == PayGateErrorCode.INVALID_ADDRESS.value

    # 6. Mismatched payTo destination -> 402 INVALID_ADDRESS
    wrong_payto_payload = make_signed_payload(make_route_requirement(pay_to=TEST_PAYER_ADDRESS))
    wrong_payto_response = client.get(
        "/v1/report",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(wrong_payto_payload)},
    )
    assert wrong_payto_response.status_code == 402
    assert wrong_payto_response.json()["detail"]["error"] == PayGateErrorCode.INVALID_ADDRESS.value

    # 7. Insufficient amount (500,000 sent when route requires 1,000,000) -> 402 AMOUNT_MISMATCH
    insufficient_payload = make_signed_payload(make_route_requirement(amount="500000"))
    insufficient_response = client.get(
        "/v1/report",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(insufficient_payload)},
    )
    assert insufficient_response.status_code == 402
    assert insufficient_response.json()["detail"]["error"] == PayGateErrorCode.AMOUNT_MISMATCH.value


def test_require_upto_ceiling_metered_billing_and_error_paths() -> None:
    """Verify require_upto_ceiling settles exact usage and rejects over-ceiling or expired auth."""
    app = create_demo_fastapi_app()
    client = TestClient(app)

    # 1. Missing signature header -> 402
    missing_res = client.post("/v1/inference?requested_tokens=250")
    assert missing_res.status_code == 402
    assert PAYMENT_REQUIRED_HEADER in missing_res.headers

    # 2. Malformed signature header -> 402
    malformed_res = client.post(
        "/v1/inference?requested_tokens=250",
        headers={PAYMENT_SIGNATURE_HEADER: "%%%invalid%%%"},
    )
    assert malformed_res.status_code == 402

    # 3. Route mismatch (exact sent to upto route) -> 402
    wrong_scheme_payload = make_signed_payload(
        make_route_requirement(scheme="exact", amount="2000000")
    )
    mismatch_res = client.post(
        "/v1/inference?requested_tokens=250",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(wrong_scheme_payload)},
    )
    assert mismatch_res.status_code == 402

    # 4. Signed ceiling lower than route's required ceiling (1,000,000 < 2,000,000) -> 402
    low_ceiling_payload = make_signed_payload(
        make_route_requirement(scheme="upto", amount="1000000")
    )
    low_ceiling_res = client.post(
        "/v1/inference?requested_tokens=250",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(low_ceiling_payload)},
    )
    assert low_ceiling_res.status_code == 402
    assert low_ceiling_res.json()["detail"]["error"] == PayGateErrorCode.AMOUNT_MISMATCH.value

    # 5. Expired authorization in pre-verification -> 402 EXPIRED_AUTHORIZATION
    expired_upto_payload = make_signed_payload(
        make_route_requirement(scheme="upto", amount="2000000"),
        ledger=499_990,
        expiration_ledger=500_000,
    )
    expired_res = client.post(
        "/v1/inference?requested_tokens=250",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(expired_upto_payload)},
    )
    assert expired_res.status_code == 402
    assert expired_res.json()["detail"]["error"] == PayGateErrorCode.EXPIRED_AUTHORIZATION.value

    # 6. Valid ceiling (2,000,000) with partial token usage (350 * 1,000 = 350,000 stroops)
    valid_upto_payload = make_signed_payload(
        make_route_requirement(scheme="upto", amount="2000000"),
        ledger=500_000,
        expiration_ledger=500_012,
    )
    success_res = client.post(
        "/v1/inference?requested_tokens=350",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(valid_upto_payload)},
    )
    assert success_res.status_code == 200
    assert success_res.json()["settled_amount"] == "350000"
    assert PAYMENT_RESPONSE_HEADER in success_res.headers
    decoded_receipt = decode_payment_response(success_res.headers[PAYMENT_RESPONSE_HEADER])
    assert decoded_receipt.settled_amount == "350000"

    # 7. Over-ceiling post-execution usage (2,500 * 1,000 = 2,500,000 > 2,000,000) -> 402
    over_ceiling_payload = make_signed_payload(
        make_route_requirement(scheme="upto", amount="2000000"),
        ledger=500_001,
        expiration_ledger=500_013,
    )
    over_ceiling_res = client.post(
        "/v1/inference?requested_tokens=2500",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(over_ceiling_payload)},
    )
    assert over_ceiling_res.status_code == 402
    assert over_ceiling_res.json()["detail"]["error"] == PayGateErrorCode.EXCEEDS_MAX_UPTO.value


def test_add_payment_middleware_protected_and_unprotected_paths() -> None:
    """Verify add_payment_middleware protects configured paths while allowing /health & public."""
    middleware_requirement = make_route_requirement(scheme="exact", amount="500000")
    app = create_demo_fastapi_app(
        middleware_requirement=middleware_requirement,
        protected_paths=["/middleware"],
    )
    client = TestClient(app)

    # 1. /health and /public/info pass through without payment
    assert client.get("/health").status_code == 200
    assert client.get("/public/info").status_code == 200

    # 2. /middleware/protected without header returns 402 + PAYMENT-REQUIRED
    unpaid_res = client.get("/middleware/protected")
    assert unpaid_res.status_code == 402
    assert PAYMENT_REQUIRED_HEADER in unpaid_res.headers

    # 3. /middleware/protected with malformed header returns 402
    malformed_res = client.get(
        "/middleware/protected",
        headers={PAYMENT_SIGNATURE_HEADER: "invalid_b64!"},
    )
    assert malformed_res.status_code == 402

    # 4. /middleware/protected with wrong network returns 402
    wrong_net_payload = make_signed_payload(
        make_route_requirement(amount="500000", network="stellar:pubnet")
    )
    wrong_net_res = client.get(
        "/middleware/protected",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(wrong_net_payload)},
    )
    assert wrong_net_res.status_code == 402

    # 5. /middleware/protected with expired payload returns 402
    expired_payload = make_signed_payload(
        middleware_requirement,
        ledger=499_990,
        expiration_ledger=500_000,
    )
    expired_res = client.get(
        "/middleware/protected",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(expired_payload)},
    )
    assert expired_res.status_code == 402

    # 6. /middleware/protected with valid payment settles and returns 200 + PAYMENT-RESPONSE
    valid_payload = make_signed_payload(middleware_requirement)
    paid_res = client.get(
        "/middleware/protected",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(valid_payload)},
    )
    assert paid_res.status_code == 200
    assert paid_res.json() == {
        "resource": "middleware_data",
        "settled_amount": "500000",
    }
    assert PAYMENT_RESPONSE_HEADER in paid_res.headers


def test_add_payment_middleware_without_fixed_requirement_validates_present_headers() -> None:
    """Verify middleware with requirement=None passes unpaid requests but verifies signed ones."""
    app = FastAPI()
    add_payment_middleware(app, current_ledger=500_000)

    @app.get("/flexible")
    def flexible_route() -> dict[str, str]:
        return {"mode": "flexible"}

    client = TestClient(app)

    # Unpaid passes through when requirement=None
    assert client.get("/flexible").status_code == 200

    # Malformed signature when requirement=None returns 400
    bad_sig_res = client.get(
        "/flexible",
        headers={PAYMENT_SIGNATURE_HEADER: "not_base64!!!"},
    )
    assert bad_sig_res.status_code == 400

    # Expired signature when requirement=None returns 402
    expired_payload = make_signed_payload(
        make_route_requirement(),
        ledger=499_990,
        expiration_ledger=500_000,
    )
    expired_res = client.get(
        "/flexible",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(expired_payload)},
    )
    assert expired_res.status_code == 402

    # Valid signature settles and attaches PAYMENT-RESPONSE
    valid_payload = make_signed_payload(make_route_requirement(amount="250000"))
    valid_res = client.get(
        "/flexible",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(valid_payload)},
    )
    assert valid_res.status_code == 200
    assert PAYMENT_RESPONSE_HEADER in valid_res.headers


@pytest.mark.asyncio
async def test_end_to_end_asgi_httpx_auto_pay_exact_and_upto_flows() -> None:
    """Verify wrap_httpx_with_paygate completes 402 -> sign -> 200 OK against FastAPI in-memory."""
    app = create_demo_fastapi_app(
        middleware_requirement=make_route_requirement(scheme="exact", amount="500000"),
        protected_paths=["/middleware"],
    )
    policy_guard = SpendPolicyGuard(
        max_per_call=3_000_000,
        max_budget=10_000_000,
        payer_address=TEST_PAYER_ADDRESS,
        current_ledger=500_000,
    )
    asgi_transport = httpx.ASGITransport(app=app)
    async_client = wrap_httpx_with_paygate(
        httpx.AsyncClient(transport=asgi_transport, base_url="http://paygate.local"),
        policy_guard,
    )

    # 1. Exact route via require_payment dependency (1,000,000 stroops)
    exact_response = await async_client.get("/v1/report")
    assert exact_response.status_code == 200
    exact_json = exact_response.json()
    assert exact_json["payer"] == TEST_PAYER_ADDRESS
    assert exact_json["settled_amount"] == "1000000"
    assert PAYMENT_RESPONSE_HEADER in exact_response.headers
    exact_receipt = decode_payment_response(exact_response.headers[PAYMENT_RESPONSE_HEADER])
    assert exact_receipt.success is True
    assert exact_receipt.settled_amount == "1000000"

    # 2. Upto route via require_upto_ceiling (authorizes 2,000,000 ceiling, settles 450,000)
    upto_response = await async_client.post("/v1/inference?requested_tokens=450")
    assert upto_response.status_code == 200
    upto_json = upto_response.json()
    assert upto_json["tokens_generated"] == 450
    assert upto_json["settled_amount"] == "450000"
    assert PAYMENT_RESPONSE_HEADER in upto_response.headers
    upto_receipt = decode_payment_response(upto_response.headers[PAYMENT_RESPONSE_HEADER])
    assert upto_receipt.success is True
    assert upto_receipt.settled_amount == "450000"

    # 3. Middleware-protected route (500,000 stroops)
    middleware_response = await async_client.get("/middleware/protected")
    assert middleware_response.status_code == 200
    assert middleware_response.json()["settled_amount"] == "500000"
    assert PAYMENT_RESPONSE_HEADER in middleware_response.headers

    # Total budget consumed on client after upto rebate reconciliation:
    # 1,000,000 (exact) + 450,000 (settled from 2,000,000 upto ceiling) + 500,000 = 1,950,000
    assert policy_guard.consumed_budget == 1_950_000
    await async_client.aclose()


@pytest.mark.asyncio
async def test_sse_streaming_upto_response_normal_cutoff_and_disconnect() -> None:
    """Verify PayGateStreamingUptoResponse normal completion, ceiling cutoff, and partial close."""
    app = FastAPI()
    streaming_requirement = make_route_requirement(scheme="upto", amount="500000")
    upto_stream_dependency = require_upto_ceiling(
        requirement=streaming_requirement,
        current_ledger=500_000,
    )

    last_created_stream_response: PayGateStreamingUptoResponse | None = None

    @app.get("/v1/stream-async")
    def stream_async_tokens(
        total_chunks: int,
        cost_per_chunk: int,
        meter: Annotated[MeteredSettlementContext, Depends(upto_stream_dependency)],
    ) -> PayGateStreamingUptoResponse:
        nonlocal last_created_stream_response

        async def async_token_generator() -> AsyncIterator[str | bytes]:
            for index in range(1, total_chunks + 1):
                if index == 1:
                    yield f"token-{index}".encode()
                else:
                    yield f"token-{index}"

        last_created_stream_response = PayGateStreamingUptoResponse(
            chunk_stream=async_token_generator(),
            meter=meter,
            atomic_cost_per_chunk=cost_per_chunk,
        )
        return last_created_stream_response

    @app.get("/v1/stream-sync")
    def stream_sync_tokens(
        total_chunks: int,
        cost_per_chunk: int,
        meter: Annotated[MeteredSettlementContext, Depends(upto_stream_dependency)],
    ) -> PayGateStreamingUptoResponse:
        sync_chunks: list[str | bytes] = [
            b"sync-1" if idx == 1 else f"sync-{idx}" for idx in range(1, total_chunks + 1)
        ]
        return PayGateStreamingUptoResponse(
            chunk_stream=sync_chunks,
            meter=meter,
            atomic_cost_per_chunk=cost_per_chunk,
        )

    # Validate non-positive atomic_cost_per_chunk raises ValueError
    dummy_meter = MeteredSettlementContext(
        payment_payload=make_signed_payload(streaming_requirement),
        requirement=streaming_requirement,
        response=Response(),
        verifier=UptoVerifier(),
    )
    with pytest.raises(ValueError, match="positive integer"):
        PayGateStreamingUptoResponse(
            chunk_stream=["tok"],
            meter=dummy_meter,
            atomic_cost_per_chunk=0,
        )

    policy_guard = SpendPolicyGuard(
        max_per_call=1_000_000,
        max_budget=5_000_000,
        payer_address=TEST_PAYER_ADDRESS,
        current_ledger=500_000,
    )
    async_client = wrap_httpx_with_paygate(
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://paygate-stream.local",
        ),
        policy_guard,
    )

    # 1. Normal completion below ceiling (3 chunks * 100,000 = 300,000 <= 500,000 ceiling)
    normal_res = await async_client.get("/v1/stream-async?total_chunks=3&cost_per_chunk=100000")
    assert normal_res.status_code == 200
    assert "text/event-stream" in normal_res.headers["content-type"]
    normal_body = normal_res.text
    assert "data: token-1" in normal_body
    assert "data: token-2" in normal_body
    assert "data: token-3" in normal_body
    assert "event: paygate-ceiling-reached" not in normal_body
    assert "event: paygate-receipt" in normal_body
    assert '"settledAmount":"300000"' in normal_body
    assert last_created_stream_response is not None
    assert last_created_stream_response.accumulated_cost_stroops == 300_000
    assert last_created_stream_response.ceiling_cutoff_triggered is False

    # 2. Automatic ceiling cutoff (10 * 150,000 vs 500,000 ceiling -> halts at 3 chunks = 450,000)
    # Advance client ledger by 1 so the second distinct streaming call signs a new ledger window
    policy_guard.current_ledger = 500_001
    cutoff_res = await async_client.get("/v1/stream-async?total_chunks=10&cost_per_chunk=150000")
    assert cutoff_res.status_code == 200
    cutoff_body = cutoff_res.text
    assert "data: token-1" in cutoff_body
    assert "data: token-2" in cutoff_body
    assert "data: token-3" in cutoff_body
    assert "data: token-4" not in cutoff_body
    assert "event: paygate-ceiling-reached" in cutoff_body
    assert "event: paygate-receipt" in cutoff_body
    assert '"settledAmount":"450000"' in cutoff_body
    assert last_created_stream_response.accumulated_cost_stroops == 450_000
    assert last_created_stream_response.delivered_chunk_count == 3
    assert last_created_stream_response.ceiling_cutoff_triggered is True

    # 3. Synchronous iterable chunk stream (2 chunks * 120,000 = 240,000)
    policy_guard.current_ledger = 500_002
    sync_stream_res = await async_client.get("/v1/stream-sync?total_chunks=2&cost_per_chunk=120000")
    assert sync_stream_res.status_code == 200
    assert "data: sync-1" in sync_stream_res.text
    assert "data: sync-2" in sync_stream_res.text
    assert '"settledAmount":"240000"' in sync_stream_res.text

    # 4. Mid-stream partial disconnect settles only the delivered chunks in finally block
    partial_payload = make_signed_payload(
        streaming_requirement,
        ledger=500_004,
        expiration_ledger=500_016,
    )
    partial_meter = MeteredSettlementContext(
        payment_payload=partial_payload,
        requirement=streaming_requirement,
        response=Response(),
        verifier=UptoVerifier(),
        current_ledger=500_000,
    )
    partial_stream = PayGateStreamingUptoResponse(
        chunk_stream=["part-1", "part-2", "part-3", "part-4"],
        meter=partial_meter,
        atomic_cost_per_chunk=100_000,
    )
    stream_generator = partial_stream._meter_and_format_sse_stream(
        ["part-1", "part-2", "part-3", "part-4"]
    )
    first_frame = await stream_generator.__anext__()
    second_frame = await stream_generator.__anext__()
    assert "part-1" in first_frame
    assert "part-2" in second_frame
    # Close generator mid-stream after 2 chunks (200,000 stroops)
    if hasattr(stream_generator, "aclose"):
        await stream_generator.aclose()
    assert partial_meter.last_settlement is not None
    assert partial_meter.last_settlement.settled_amount == "200000"

    await async_client.aclose()
