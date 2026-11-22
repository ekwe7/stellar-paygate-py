"""Additional unit tests ensuring comprehensive test coverage across SDK stubs."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stellar_paygate.client import SpendPolicyGuard, wrap_httpx_with_paygate
from stellar_paygate.exact import settle_payload, verify_payload
from stellar_paygate.fastapi_ext import add_payment_middleware
from stellar_paygate.fault_harness import FaultInjectingSorobanRpc
from stellar_paygate.fault_harness import main as fault_harness_main
from stellar_paygate.types import (
    PaymentPayload,
    PaymentRequirement,
    SettleResponse,
    StellarPaymentPayloadData,
    VerifyResponse,
)


def create_sample_payment_payload() -> PaymentPayload:
    """Create a sample payment payload for verification and settlement tests."""
    payment_requirement: PaymentRequirement = PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount="5000",
        asset="native",
        pay_to="GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT",
    )
    payload_data: StellarPaymentPayloadData = StellarPaymentPayloadData(
        payer="GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT",
        network="stellar:testnet",
        ledger=100000,
        expiration_ledger=100100,
        fee_mode="sponsored",
    )
    return PaymentPayload(
        requirement=payment_requirement,
        payload=payload_data,
    )


def test_spend_policy_guard_initialization_and_check() -> None:
    """Verify SpendPolicyGuard initializes properly and allows checking payload."""
    policy_guard: SpendPolicyGuard = SpendPolicyGuard(
        max_per_call=2_000_000,
        max_budget=20_000_000,
    )
    assert policy_guard.max_per_call == 2_000_000
    assert policy_guard.max_budget == 20_000_000

    sample_payload: PaymentPayload = create_sample_payment_payload()
    policy_guard.check(sample_payload)


def test_wrap_httpx_with_paygate() -> None:
    """Verify wrap_httpx_with_paygate returns client instance."""
    policy_guard: SpendPolicyGuard = SpendPolicyGuard()
    http_client: httpx.Client = httpx.Client()
    wrapped_client: httpx.Client = wrap_httpx_with_paygate(http_client, policy_guard)
    assert wrapped_client is http_client
    http_client.close()


def test_exact_verify_and_settle_payload() -> None:
    """Verify exact payload verification and settlement return valid responses."""
    sample_payload: PaymentPayload = create_sample_payment_payload()

    verification_response: VerifyResponse = verify_payload(sample_payload)
    assert verification_response.success is True
    assert verification_response.scheme == "exact"
    assert verification_response.payer == sample_payload.payload.payer

    settlement_response: SettleResponse = settle_payload(sample_payload)
    assert settlement_response.success is True
    assert settlement_response.scheme == "exact"
    assert settlement_response.settled_amount == "5000"
    assert settlement_response.transaction_hash is not None


def test_fault_harness_components() -> None:
    """Verify fault harness stub class and entrypoint."""
    fault_rpc_instance: FaultInjectingSorobanRpc = FaultInjectingSorobanRpc()
    assert isinstance(fault_rpc_instance, FaultInjectingSorobanRpc)

    exit_code_result: int = fault_harness_main()
    assert exit_code_result == 0


def test_fastapi_payment_middleware() -> None:
    """Verify FastAPI payment middleware registers and passes requests through."""
    fastapi_application: FastAPI = FastAPI()
    add_payment_middleware(fastapi_application)

    @fastapi_application.get("/health")
    def health_check_endpoint() -> dict[str, str]:
        return {"status": "operational"}

    test_client: TestClient = TestClient(fastapi_application)
    http_response = test_client.get("/health")
    assert http_response.status_code == 200
    assert http_response.json() == {"status": "operational"}
