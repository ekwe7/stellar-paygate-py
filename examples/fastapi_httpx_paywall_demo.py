"""Offline runnable demonstration of FastAPI HTTP 402 paywalls and HTTPX auto-pay client.

Run directly from the repository root:
    python examples/fastapi_httpx_paywall_demo.py
"""

# NOTE: ``from __future__ import annotations`` is omitted so FastAPI's runtime
# dependency injector resolves ``Depends(...)`` types directly.

import asyncio
import sys
from pathlib import Path
from typing import Annotated

PROJECT_SOURCE_DIRECTORY = Path(__file__).resolve().parent.parent / "src"
if str(PROJECT_SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_SOURCE_DIRECTORY))

import httpx  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402

from stellar_paygate import (  # noqa: E402
    PAYMENT_RESPONSE_HEADER,
    MeteredSettlementContext,
    PaymentRequirement,
    SettleResponse,
    SpendPolicyGuard,
    atomic_units_to_decimal,
    decode_payment_response,
    require_payment,
    require_upto_ceiling,
    wrap_httpx_with_paygate,
)

DEMO_USDC_CONTRACT = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
DEMO_MERCHANT_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
DEMO_AGENT_ADDRESS = "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
DEMO_CURRENT_LEDGER = 500_000


def build_demo_paywalled_fastapi_app() -> FastAPI:
    """Create an in-memory FastAPI service with fixed-price (exact) and metered (upto) routes."""
    application = FastAPI(title="Stellar Paygate Demo Service")

    exact_report_requirement = PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount="1000000",  # 0.1000000 USDC
        asset=DEMO_USDC_CONTRACT,
        pay_to=DEMO_MERCHANT_ADDRESS,
    )
    upto_inference_requirement = PaymentRequirement(
        scheme="upto",
        network="stellar:testnet",
        amount="2000000",  # 0.2000000 USDC ceiling
        asset=DEMO_USDC_CONTRACT,
        pay_to=DEMO_MERCHANT_ADDRESS,
    )

    exact_payment_dependency = require_payment(
        requirement=exact_report_requirement,
        current_ledger=DEMO_CURRENT_LEDGER,
    )
    upto_ceiling_dependency = require_upto_ceiling(
        requirement=upto_inference_requirement,
        current_ledger=DEMO_CURRENT_LEDGER,
    )

    @application.get("/v1/market-report")
    def get_paid_market_report(
        settlement: Annotated[SettleResponse, Depends(exact_payment_dependency)],
    ) -> dict[str, str | None]:
        return {
            "title": "Stellar DeFi Liquidity Snapshot",
            "payer": settlement.payer,
            "settled_amount": settlement.settled_amount,
            "transaction_hash": settlement.transaction_hash,
        }

    @application.post("/v1/llm-complete")
    def generate_metered_completion(
        output_tokens: int,
        meter: Annotated[MeteredSettlementContext, Depends(upto_ceiling_dependency)],
    ) -> dict[str, str | int | None]:
        cost_per_token_stroops: int = 2_000
        actual_cost_stroops: int = output_tokens * cost_per_token_stroops
        settlement = meter.settle_usage(actual_cost_stroops)
        return {
            "output_tokens": output_tokens,
            "settled_amount": settlement.settled_amount,
            "transaction_hash": settlement.transaction_hash,
        }

    return application


async def run_fastapi_httpx_paywall_demo() -> int:
    """Run an end-to-end offline HTTP 402 paywall and agent auto-payment demonstration."""
    fastapi_app = build_demo_paywalled_fastapi_app()

    policy_guard = SpendPolicyGuard(
        max_per_call=3_000_000,
        max_budget=10_000_000,
        allowed_networks={"stellar:testnet"},
        allowed_assets={DEMO_USDC_CONTRACT},
        payer_address=DEMO_AGENT_ADDRESS,
        current_ledger=DEMO_CURRENT_LEDGER,
    )

    asgi_transport = httpx.ASGITransport(app=fastapi_app)
    async_http_client = wrap_httpx_with_paygate(
        httpx.AsyncClient(transport=asgi_transport, base_url="http://paygate-demo.local"),
        policy_guard,
    )

    try:
        # 1. Fixed-price exact endpoint (1,000,000 stroops / 0.1000000 token)
        report_response = await async_http_client.get("/v1/market-report")
        assert report_response.status_code == 200
        report_receipt = decode_payment_response(report_response.headers[PAYMENT_RESPONSE_HEADER])
        print(
            f"[1] Exact Route 200 OK | Settled: "
            f"{atomic_units_to_decimal(int(report_receipt.settled_amount or '0'))} "
            f"({report_receipt.settled_amount} stroops) | "
            f"Tx: {(report_receipt.transaction_hash or '')[:16]}..."
        )
        assert report_receipt.settled_amount == "1000000"

        # 2. Metered upto endpoint (authorizes 2,000,000 ceiling, settles 300 tokens = 600,000)
        inference_response = await async_http_client.post("/v1/llm-complete?output_tokens=300")
        assert inference_response.status_code == 200
        inference_receipt = decode_payment_response(
            inference_response.headers[PAYMENT_RESPONSE_HEADER]
        )
        print(
            f"[2] Upto Route 200 OK  | Settled: "
            f"{atomic_units_to_decimal(int(inference_receipt.settled_amount or '0'))} "
            f"({inference_receipt.settled_amount} stroops) | "
            f"Tx: {(inference_receipt.transaction_hash or '')[:16]}..."
        )
        assert inference_receipt.settled_amount == "600000"

        print(
            f"[3] Agent Rolling Budget | Consumed: {policy_guard.consumed_budget} stroops | "
            f"Remaining: {policy_guard.remaining_budget} stroops"
        )
        assert policy_guard.consumed_budget == 3_000_000
    finally:
        await async_http_client.aclose()

    print("FastAPI + HTTPX 402 paywall demonstration completed offline successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_fastapi_httpx_paywall_demo()))
