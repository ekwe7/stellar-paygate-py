"""End-to-end offline conformance matrix for stellar-paygate-py (AC-1 through AC-5).

Validates the cross-product of payment schemes (``exact``, ``upto``), fee sponsorship
modes (``sponsored``, ``unsponsored``), Stellar address architectures (``G-``, ``C-``,
``M-``), ledger validity windows, the 10 fault-injection scenarios (``S01``–``S10``),
the Hexagonal Inward Dependency Rule (``QG-1``), and FastAPI + HTTPX end-to-end
payment flows.
"""

# NOTE: ``from __future__ import annotations`` is omitted so FastAPI route
# handlers resolve ``Depends(...)`` types directly at runtime.

import ast
import json
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
import pytest
from fastapi import Depends, FastAPI

from stellar_paygate import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    ExactVerifier,
    FaultInjectionConfig,
    FeeMode,
    MeteredSettlementContext,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequirement,
    SettleResponse,
    SpendPolicyGuard,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    UptoVerifier,
    VerifierConfig,
    add_payment_middleware,
    decimal_to_atomic_units,
    decode_payment_required,
    decode_payment_response,
    encode_payment_signature,
    require_payment,
    require_upto_ceiling,
    wrap_httpx_with_paygate,
)
from stellar_paygate.fault_harness import (
    FaultInjectingSorobanRpc,
    run_conformance_scenarios,
)
from stellar_paygate.fault_harness import main as fault_harness_cli_main

CONFORMANCE_CLASSIC_PAYER = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
CONFORMANCE_CLASSIC_MERCHANT = "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
CONFORMANCE_SMART_CONTRACT = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
CONFORMANCE_MUXED_MERCHANT = "MA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGAAAAAAAAAAAAE234"
CONFORMANCE_BASE_LEDGER = 500_000


@pytest.mark.parametrize(
    "domain_core_filename",
    [
        "types.py",
        "codec.py",
        "exact.py",
    ],
)
def test_qg1_hexagonal_inward_dependency_rule(domain_core_filename: str) -> None:
    """QG-1: Verify domain core modules never import fastapi, starlette, or httpx."""
    package_directory = Path(__file__).resolve().parent.parent / "src" / "stellar_paygate"
    module_path = package_directory / domain_core_filename
    syntax_tree = ast.parse(module_path.read_text(encoding="utf-8"))
    forbidden_adapter_packages = {"fastapi", "starlette", "httpx"}

    observed_imports: set[str] = set()
    for ast_node in ast.walk(syntax_tree):
        if isinstance(ast_node, ast.Import):
            for alias_entry in ast_node.names:
                observed_imports.add(alias_entry.name.split(".")[0])
        elif isinstance(ast_node, ast.ImportFrom) and ast_node.module:
            observed_imports.add(ast_node.module.split(".")[0])

    assert observed_imports.isdisjoint(forbidden_adapter_packages), (
        f"Domain module {domain_core_filename} imported adapter package: "
        f"{observed_imports & forbidden_adapter_packages}"
    )


def test_ac1_ten_conformance_scenarios_complete_under_three_seconds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-1: All 10 scenarios (S01-S10) in stellar-paygate-sim pass offline in < 3s."""
    start_timestamp: float = time.perf_counter()

    scenario_results = run_conformance_scenarios()
    markdown_exit_code: int = fault_harness_cli_main([])
    markdown_output: str = capsys.readouterr().out

    json_exit_code: int = fault_harness_cli_main(["--json"])
    json_output: str = capsys.readouterr().out

    elapsed_seconds: float = time.perf_counter() - start_timestamp

    assert elapsed_seconds < 3.0
    assert len(scenario_results) == 10
    assert all(result.passed for result in scenario_results)
    assert markdown_exit_code == 0
    assert "10/10 scenarios passed" in markdown_output
    assert json_exit_code == 0

    parsed_json_report = json.loads(json_output)
    assert parsed_json_report["all_passed"] is True
    assert parsed_json_report["total"] == 10
    assert parsed_json_report["passed"] == 10


@pytest.mark.parametrize("scheme_option", ["exact", "upto"])
@pytest.mark.parametrize(
    ("fee_mode_option", "are_fees_sponsored"),
    [
        (FeeMode.SPONSORED, True),
        (FeeMode.UNSPONSORED, False),
    ],
)
@pytest.mark.parametrize(
    "payer_address",
    [
        CONFORMANCE_CLASSIC_PAYER,
        CONFORMANCE_SMART_CONTRACT,
        CONFORMANCE_MUXED_MERCHANT,
    ],
)
@pytest.mark.parametrize(
    "destination_address",
    [
        CONFORMANCE_CLASSIC_MERCHANT,
        CONFORMANCE_SMART_CONTRACT,
        CONFORMANCE_MUXED_MERCHANT,
    ],
)
def test_ac3_full_scheme_fee_mode_and_address_architecture_matrix(
    scheme_option: Literal["exact", "upto"],
    fee_mode_option: FeeMode,
    are_fees_sponsored: bool,
    payer_address: str,
    destination_address: str,
) -> None:
    """AC-3: Verify full cross-product of schemes, fee modes, and G-/C-/M- addresses."""
    authorized_amount_str = "1000000"
    requested_settlement_amount: int = 1_000_000 if scheme_option == "exact" else 420_000

    payment_requirement = PaymentRequirement(
        scheme=scheme_option,
        network="stellar:testnet",
        amount=authorized_amount_str,
        asset=CONFORMANCE_SMART_CONTRACT,
        pay_to=destination_address,
        extra=StellarExtraConfig(are_fees_sponsored=are_fees_sponsored),
    )
    payload_data = StellarPaymentPayloadData(
        payer=payer_address,
        network="stellar:testnet",
        ledger=CONFORMANCE_BASE_LEDGER,
        expiration_ledger=CONFORMANCE_BASE_LEDGER + 12,
        fee_mode=fee_mode_option,
        signature_expiration_ledger=CONFORMANCE_BASE_LEDGER + 12,
    )
    payment_payload = PaymentPayload(
        requirement=payment_requirement,
        payload=payload_data,
    )

    fault_rpc = FaultInjectingSorobanRpc(
        config=FaultInjectionConfig(
            allow_unsponsored_fees=True,
            override_settled_amount=requested_settlement_amount,
        ),
        base_ledger=CONFORMANCE_BASE_LEDGER,
    )

    verification_response = fault_rpc.verify_payment(payment_payload)
    assert verification_response.success is True
    assert verification_response.error_code is None

    settlement_response = fault_rpc.settle_payment(payment_payload)
    assert settlement_response.success is True
    assert settlement_response.settled_amount == str(requested_settlement_amount)
    assert settlement_response.transaction_hash is not None
    assert len(settlement_response.transaction_hash) == 64
    assert fault_rpc.submission_count == 1


@pytest.mark.parametrize(
    ("ledger_skew_value", "tolerance_ledgers", "should_pass"),
    [
        (0, 2, True),
        (2, 2, True),
        (4, 2, False),  # Legacy 2-ledger tolerance fails on +4 skew
        (4, 5, True),  # Default 5-ledger tolerance absorbs +4 skew
        (-4, 5, True),
        (6, 5, False),
    ],
)
def test_conformance_ledger_skew_tolerance_matrix(
    ledger_skew_value: int,
    tolerance_ledgers: int,
    should_pass: bool,
) -> None:
    """Verify adaptive RPC ledger-skew tolerance matrix across client/facilitator offsets."""
    fault_rpc = FaultInjectingSorobanRpc(
        config=FaultInjectionConfig(ledger_skew=ledger_skew_value),
        base_ledger=CONFORMANCE_BASE_LEDGER,
        skew_tolerance_ledgers=tolerance_ledgers,
    )
    client_observed_ledger: int = fault_rpc.get_client_latest_ledger()
    payment_requirement = PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount="1000000",
        asset=CONFORMANCE_SMART_CONTRACT,
        pay_to=CONFORMANCE_CLASSIC_MERCHANT,
    )
    payment_payload = PaymentPayload(
        requirement=payment_requirement,
        payload=StellarPaymentPayloadData(
            payer=CONFORMANCE_CLASSIC_PAYER,
            network="stellar:testnet",
            ledger=client_observed_ledger,
            expiration_ledger=client_observed_ledger + 10,
            fee_mode=FeeMode.SPONSORED,
            signature_expiration_ledger=client_observed_ledger + 10,
        ),
    )

    verification_result = fault_rpc.verify_payment(payment_payload)
    assert verification_result.success is should_pass
    if not should_pass:
        assert verification_result.error_code == PayGateErrorCode.CLOCK_SKEW_EXCEEDED


def test_ac4_spend_policy_guard_precision_and_boundary_conformance() -> None:
    """AC-4: Verify SpendPolicyGuard decimal quantization, cap breaches, and allowlists."""
    assert decimal_to_atomic_units("0.51") == 5_100_000
    with pytest.raises(ValueError, match="decimal places"):
        decimal_to_atomic_units("0.12345678")

    policy_guard = SpendPolicyGuard(
        max_per_call=1_000_000,
        max_budget=1_500_000,
        allowed_networks={"stellar:testnet"},
        allowed_assets={CONFORMANCE_SMART_CONTRACT},
        payer_address=CONFORMANCE_CLASSIC_PAYER,
        current_ledger=CONFORMANCE_BASE_LEDGER,
    )

    def make_payload(
        amount: str = "1000000",
        network: str = "stellar:testnet",
        asset: str = CONFORMANCE_SMART_CONTRACT,
    ) -> PaymentPayload:
        return PaymentPayload(
            requirement=PaymentRequirement(
                scheme="exact",
                network=network,
                amount=amount,
                asset=asset,
                pay_to=CONFORMANCE_CLASSIC_MERCHANT,
            ),
            payload=StellarPaymentPayloadData(
                payer=CONFORMANCE_CLASSIC_PAYER,
                network=network,
                ledger=CONFORMANCE_BASE_LEDGER,
                expiration_ledger=CONFORMANCE_BASE_LEDGER + 12,
                fee_mode=FeeMode.SPONSORED,
            ),
        )

    # Disallowed network
    with pytest.raises(PaygateProtocolError) as net_exc:
        policy_guard.check(make_payload(network="stellar:pubnet"))
    assert net_exc.value.code == PayGateErrorCode.UNSUPPORTED_NETWORK

    # Disallowed asset
    with pytest.raises(PaygateProtocolError) as asset_exc:
        policy_guard.check(make_payload(asset="native"))
    assert asset_exc.value.code == PayGateErrorCode.INVALID_ADDRESS

    # Per-call cap breach
    with pytest.raises(PaygateProtocolError) as cap_exc:
        policy_guard.check(make_payload(amount="1000001"))
    assert cap_exc.value.code == PayGateErrorCode.AMOUNT_MISMATCH

    # Valid spend of 1,000,000 leaves 500,000 remaining
    policy_guard.check(make_payload(amount="1000000"))
    assert policy_guard.remaining_budget == 500_000

    # Rolling budget breach (600,000 > 500,000 remaining)
    with pytest.raises(PaygateProtocolError) as budget_exc:
        policy_guard.check(make_payload(amount="600000"))
    assert budget_exc.value.code == PayGateErrorCode.EXCEEDS_MAX_UPTO


@pytest.mark.asyncio
async def test_ac2_end_to_end_fastapi_and_httpx_conformance() -> None:
    """AC-2: FastAPI exact, upto, and middleware routes complete full 402 flow with httpx."""
    fastapi_app = FastAPI()

    middleware_requirement = PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount="250000",
        asset=CONFORMANCE_SMART_CONTRACT,
        pay_to=CONFORMANCE_CLASSIC_MERCHANT,
    )
    add_payment_middleware(
        app=fastapi_app,
        requirement=middleware_requirement,
        protected_paths=["/paywall"],
        current_ledger=CONFORMANCE_BASE_LEDGER,
    )

    exact_dependency = require_payment(
        requirement=PaymentRequirement(
            scheme="exact",
            network="stellar:testnet",
            amount="500000",
            asset=CONFORMANCE_SMART_CONTRACT,
            pay_to=CONFORMANCE_MUXED_MERCHANT,
        ),
        verifier=ExactVerifier(config=VerifierConfig()),
        current_ledger=CONFORMANCE_BASE_LEDGER,
    )
    upto_dependency = require_upto_ceiling(
        requirement=PaymentRequirement(
            scheme="upto",
            network="stellar:testnet",
            amount="1000000",
            asset=CONFORMANCE_SMART_CONTRACT,
            pay_to=CONFORMANCE_CLASSIC_MERCHANT,
        ),
        verifier=UptoVerifier(config=VerifierConfig()),
        current_ledger=CONFORMANCE_BASE_LEDGER,
    )

    @fastapi_app.get("/paywall/data")
    def middleware_paid_route() -> dict[str, str]:
        return {"source": "middleware"}

    @fastapi_app.get("/exact/snapshot")
    def exact_paid_route(
        settlement: Annotated[SettleResponse, Depends(exact_dependency)],
    ) -> dict[str, str | None]:
        return {
            "settled_amount": settlement.settled_amount,
            "transaction_hash": settlement.transaction_hash,
        }

    @fastapi_app.post("/upto/tokens")
    def upto_paid_route(
        token_count: int,
        meter: Annotated[MeteredSettlementContext, Depends(upto_dependency)],
    ) -> dict[str, str | None]:
        settlement = meter.settle_usage(token_count * 1_000)
        return {
            "settled_amount": settlement.settled_amount,
            "transaction_hash": settlement.transaction_hash,
        }

    # 1. Verify raw unauthenticated client receives 402 + PAYMENT-REQUIRED
    raw_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://conformance.local",
    )
    unpaid_challenge_res = await raw_client.get("/exact/snapshot")
    assert unpaid_challenge_res.status_code == 402
    assert PAYMENT_REQUIRED_HEADER in unpaid_challenge_res.headers
    decoded_challenge = decode_payment_required(
        unpaid_challenge_res.headers[PAYMENT_REQUIRED_HEADER]
    )
    assert decoded_challenge.accepts[0].pay_to == CONFORMANCE_MUXED_MERCHANT

    # Verify manual signed payload header works
    manual_payload = PaymentPayload(
        requirement=decoded_challenge.accepts[0],
        payload=StellarPaymentPayloadData(
            payer=CONFORMANCE_SMART_CONTRACT,
            network="stellar:testnet",
            ledger=CONFORMANCE_BASE_LEDGER,
            expiration_ledger=CONFORMANCE_BASE_LEDGER + 10,
            fee_mode=FeeMode.SPONSORED,
        ),
    )
    manual_paid_res = await raw_client.get(
        "/exact/snapshot",
        headers={PAYMENT_SIGNATURE_HEADER: encode_payment_signature(manual_payload)},
    )
    assert manual_paid_res.status_code == 200
    await raw_client.aclose()

    # 2. Verify wrapped agent client completes all 3 routes automatically
    policy_guard = SpendPolicyGuard(
        max_per_call=2_000_000,
        max_budget=5_000_000,
        payer_address=CONFORMANCE_CLASSIC_PAYER,
        current_ledger=CONFORMANCE_BASE_LEDGER,
    )
    agent_client = wrap_httpx_with_paygate(
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://conformance.local",
        ),
        policy_guard,
    )

    mw_res = await agent_client.get("/paywall/data")
    assert mw_res.status_code == 200
    assert (
        decode_payment_response(mw_res.headers[PAYMENT_RESPONSE_HEADER]).settled_amount == "250000"
    )

    exact_res = await agent_client.get("/exact/snapshot")
    assert exact_res.status_code == 200
    assert (
        decode_payment_response(exact_res.headers[PAYMENT_RESPONSE_HEADER]).settled_amount
        == "500000"
    )

    upto_res = await agent_client.post("/upto/tokens?token_count=70")
    assert upto_res.status_code == 200
    assert (
        decode_payment_response(upto_res.headers[PAYMENT_RESPONSE_HEADER]).settled_amount == "70000"
    )

    assert policy_guard.consumed_budget == 1_750_000
    await agent_client.aclose()
