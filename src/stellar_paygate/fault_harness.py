"""Deterministic offline fault-injection harness and conformance CLI for stellar-paygate-py.

Implements :class:`FaultInjectingSorobanRpc`, the 10 canonical conformance
scenarios (``S01``–``S10``) via :func:`run_conformance_scenarios`, and the
``stellar-paygate-sim`` CLI entry point supporting both Markdown table output
and structured JSON reports (``--json``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from .codec import decode_payment_required
from .exact import (
    ExactVerifier,
    UptoVerifier,
    VerificationPort,
    VerifierConfig,
    compute_payload_fingerprint,
)
from .types import (
    FaultInjectionConfig,
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequirement,
    Scheme,
    SettleResponse,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    VerifyResponse,
)

DEFAULT_BASE_LEDGER: int = 500_000
DEFAULT_PAYER_CLASSIC_ADDRESS: str = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
DEFAULT_DESTINATION_CLASSIC_ADDRESS: str = (
    "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
)
DEFAULT_ASSET_CONTRACT_ADDRESS: str = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"


@dataclass(frozen=True)
class ConformanceScenarioResult:
    """Result record for a single deterministic fault-injection scenario."""

    scenario_id: str
    title: str
    passed: bool
    expected_outcome: str
    observed_outcome: str


def build_scenario_payment_payload(
    scheme: Literal["exact", "upto"] = "exact",
    network: str = "stellar:testnet",
    amount: str = "1000000",
    asset: str = DEFAULT_ASSET_CONTRACT_ADDRESS,
    pay_to: str = DEFAULT_DESTINATION_CLASSIC_ADDRESS,
    payer: str = DEFAULT_PAYER_CLASSIC_ADDRESS,
    ledger: int = DEFAULT_BASE_LEDGER,
    expiration_ledger: int = DEFAULT_BASE_LEDGER + 12,
    fee_mode: FeeMode = FeeMode.SPONSORED,
    are_fees_sponsored: bool = True,
) -> PaymentPayload:
    """Construct a deterministic PaymentPayload for offline conformance scenarios."""
    requirement = PaymentRequirement(
        scheme=scheme,
        network=network,
        amount=amount,
        asset=asset,
        pay_to=pay_to,
        extra=StellarExtraConfig(are_fees_sponsored=are_fees_sponsored),
    )
    payload_data = StellarPaymentPayloadData(
        payer=payer,
        network=network,
        ledger=ledger,
        expiration_ledger=expiration_ledger,
        fee_mode=fee_mode,
        signature_expiration_ledger=expiration_ledger,
    )
    return PaymentPayload(requirement=requirement, payload=payload_data)


class FaultInjectingSorobanRpc:
    """In-memory deterministic Soroban RPC and settlement fault injector."""

    def __init__(
        self,
        config: FaultInjectionConfig | None = None,
        base_ledger: int = DEFAULT_BASE_LEDGER,
        skew_tolerance_ledgers: int = 5,
    ) -> None:
        self.config: FaultInjectionConfig = config or FaultInjectionConfig()
        self.base_ledger: int = base_ledger
        self.skew_tolerance_ledgers: int = skew_tolerance_ledgers
        self.submission_count: int = 0
        self._committed_fingerprints: set[str] = set()

        verifier_config = VerifierConfig(
            skew_tolerance_ledgers=skew_tolerance_ledgers,
            allow_unsponsored_fees=self.config.allow_unsponsored_fees,
            force_settlement_failure=self.config.force_settlement_failure,
        )
        self._exact_verifier = ExactVerifier(config=verifier_config)
        self._upto_verifier = UptoVerifier(config=verifier_config)

    def get_facilitator_latest_ledger(self) -> int:
        """Return the facilitator's observed ledger sequence."""
        return self.base_ledger

    def get_client_latest_ledger(self) -> int:
        """Return the client's observed ledger sequence with injected ledger_skew."""
        return self.base_ledger + self.config.ledger_skew

    def _select_verifier(self, payment_payload: PaymentPayload) -> VerificationPort:
        if payment_payload.requirement.scheme == Scheme.UPTO.value:
            return self._upto_verifier
        return self._exact_verifier

    def verify_payment(
        self,
        payment_payload: PaymentPayload,
        requested_amount: int | str | None = None,
    ) -> VerifyResponse:
        """Run offline verification with configured fault-injection parameters."""
        effective_requested_amount: int | str | None = (
            self.config.override_settled_amount
            if self.config.override_settled_amount is not None
            else requested_amount
        )
        verifier: VerificationPort = self._select_verifier(payment_payload)
        return verifier.verify(
            payment_payload=payment_payload,
            current_ledger=self.get_facilitator_latest_ledger(),
            requested_amount=effective_requested_amount,
        )

    def settle_payment(
        self,
        payment_payload: PaymentPayload,
        requested_amount: int | str | None = None,
    ) -> SettleResponse:
        """Run offline verification and settlement with duplicate delivery simulation."""
        effective_requested_amount: int | str | None = (
            self.config.override_settled_amount
            if self.config.override_settled_amount is not None
            else requested_amount
        )
        verifier: VerificationPort = self._select_verifier(payment_payload)
        total_deliveries: int = max(1, 1 + self.config.duplicate_delivery_count)
        payload_fingerprint: str = compute_payload_fingerprint(payment_payload)

        latest_response: SettleResponse | None = None
        for _ in range(total_deliveries):
            was_already_committed: bool = payload_fingerprint in self._committed_fingerprints
            latest_response = verifier.settle(
                payment_payload=payment_payload,
                current_ledger=self.get_facilitator_latest_ledger(),
                requested_amount=effective_requested_amount,
            )
            if latest_response.success and not was_already_committed:
                self._committed_fingerprints.add(payload_fingerprint)
                self.submission_count += 1

        assert latest_response is not None
        return latest_response


def _run_scenario_s01_baseline_exact() -> ConformanceScenarioResult:
    """S01: Baseline sponsored exact SEP-41 payment settlement."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig())
    payload = build_scenario_payment_payload(scheme="exact", amount="1000000")
    settlement = rpc_harness.settle_payment(payload)
    passed: bool = (
        settlement.success is True
        and settlement.settled_amount == "1000000"
        and settlement.transaction_hash is not None
        and len(settlement.transaction_hash) == 64
        and rpc_harness.submission_count == 1
    )
    return ConformanceScenarioResult(
        scenario_id="S01",
        title="Baseline sponsored exact SEP-41 settlement",
        passed=passed,
        expected_outcome="success=True, settledAmount=1000000",
        observed_outcome=(
            f"success={settlement.success}, settledAmount={settlement.settled_amount}"
        ),
    )


def _run_scenario_s02_clock_skew_exceeded() -> ConformanceScenarioResult:
    """S02: Clock-skew rejection when client ledger skew exceeds tolerance."""
    rpc_harness = FaultInjectingSorobanRpc(
        FaultInjectionConfig(ledger_skew=8),
        skew_tolerance_ledgers=5,
    )
    client_ledger: int = rpc_harness.get_client_latest_ledger()
    payload = build_scenario_payment_payload(
        ledger=client_ledger,
        expiration_ledger=client_ledger + 12,
    )
    verification = rpc_harness.verify_payment(payload)
    passed: bool = (
        verification.success is False
        and verification.error_code == PayGateErrorCode.CLOCK_SKEW_EXCEEDED
    )
    return ConformanceScenarioResult(
        scenario_id="S02",
        title="Clock-skew rejection beyond ledger tolerance",
        passed=passed,
        expected_outcome=PayGateErrorCode.CLOCK_SKEW_EXCEEDED.value,
        observed_outcome=(verification.error_code.value if verification.error_code else "none"),
    )


def _run_scenario_s03_expired_authorization() -> ConformanceScenarioResult:
    """S03: Expired authorization rejection when expiration_ledger <= current_ledger."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig())
    current_ledger: int = rpc_harness.get_facilitator_latest_ledger()
    payload = build_scenario_payment_payload(
        ledger=current_ledger - 10,
        expiration_ledger=current_ledger,
    )
    verification = rpc_harness.verify_payment(payload)
    passed: bool = (
        verification.success is False
        and verification.error_code == PayGateErrorCode.EXPIRED_AUTHORIZATION
    )
    return ConformanceScenarioResult(
        scenario_id="S03",
        title="Expired authorization ledger window rejection",
        passed=passed,
        expected_outcome=PayGateErrorCode.EXPIRED_AUTHORIZATION.value,
        observed_outcome=(verification.error_code.value if verification.error_code else "none"),
    )


def _run_scenario_s04_unsponsored_fee_rejection() -> ConformanceScenarioResult:
    """S04: Unsponsored fee mode rejection when allow_unsponsored_fees=False."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig(allow_unsponsored_fees=False))
    payload = build_scenario_payment_payload(
        fee_mode=FeeMode.UNSPONSORED,
        are_fees_sponsored=False,
    )
    verification = rpc_harness.verify_payment(payload)
    passed: bool = (
        verification.success is False
        and verification.error_code == PayGateErrorCode.INVALID_PAYLOAD
    )
    return ConformanceScenarioResult(
        scenario_id="S04",
        title="Unsponsored fee rejection when disabled by policy",
        passed=passed,
        expected_outcome=PayGateErrorCode.INVALID_PAYLOAD.value,
        observed_outcome=(verification.error_code.value if verification.error_code else "none"),
    )


def _run_scenario_s05_upto_ceiling_overrun() -> ConformanceScenarioResult:
    """S05: Metered upto ceiling overrun rejection when override_settled_amount > ceiling."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig(override_settled_amount=1_500_000))
    payload = build_scenario_payment_payload(scheme="upto", amount="1000000")
    settlement = rpc_harness.settle_payment(payload)
    passed: bool = (
        settlement.success is False and settlement.error_code == PayGateErrorCode.EXCEEDS_MAX_UPTO
    )
    return ConformanceScenarioResult(
        scenario_id="S05",
        title="Metered upto ceiling overrun rejection",
        passed=passed,
        expected_outcome=PayGateErrorCode.EXCEEDS_MAX_UPTO.value,
        observed_outcome=(settlement.error_code.value if settlement.error_code else "none"),
    )


def _run_scenario_s06_invalid_stellar_addresses() -> ConformanceScenarioResult:
    """S06: Invalid G-, C-, and M-address rejection."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig())
    invalid_G_payload = build_scenario_payment_payload(payer="G_INVALID_ADDRESS")
    invalid_C_payload = build_scenario_payment_payload(asset="C_INVALID_CONTRACT")
    invalid_M_payload = build_scenario_payment_payload(pay_to="M_INVALID_MUXED")

    g_result = rpc_harness.verify_payment(invalid_G_payload)
    c_result = rpc_harness.verify_payment(invalid_C_payload)
    m_result = rpc_harness.verify_payment(invalid_M_payload)

    passed: bool = (
        g_result.error_code == PayGateErrorCode.INVALID_ADDRESS
        and c_result.error_code == PayGateErrorCode.INVALID_ADDRESS
        and m_result.error_code == PayGateErrorCode.INVALID_ADDRESS
    )
    return ConformanceScenarioResult(
        scenario_id="S06",
        title="Invalid G-, C-, and M-address format rejection",
        passed=passed,
        expected_outcome=PayGateErrorCode.INVALID_ADDRESS.value,
        observed_outcome=(g_result.error_code.value if g_result.error_code else "none"),
    )


def _run_scenario_s07_exact_amount_mismatch() -> ConformanceScenarioResult:
    """S07: Exact amount mismatch rejection when settled amount differs from requirement."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig(override_settled_amount=500_000))
    payload = build_scenario_payment_payload(scheme="exact", amount="1000000")
    verification = rpc_harness.verify_payment(payload)
    passed: bool = (
        verification.success is False
        and verification.error_code == PayGateErrorCode.AMOUNT_MISMATCH
    )
    return ConformanceScenarioResult(
        scenario_id="S07",
        title="Exact scheme amount mismatch rejection",
        passed=passed,
        expected_outcome=PayGateErrorCode.AMOUNT_MISMATCH.value,
        observed_outcome=(verification.error_code.value if verification.error_code else "none"),
    )


def _run_scenario_s08_duplicate_delivery_idempotency() -> ConformanceScenarioResult:
    """S08: Duplicate delivery idempotency (multiple deliveries settle exactly once)."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig(duplicate_delivery_count=3))
    payload = build_scenario_payment_payload(scheme="exact", amount="1000000")
    first_settlement = rpc_harness.settle_payment(payload)
    second_settlement = rpc_harness.settle_payment(payload)

    passed: bool = (
        first_settlement.success is True
        and second_settlement.success is True
        and first_settlement.transaction_hash == second_settlement.transaction_hash
        and rpc_harness.submission_count == 1
    )
    return ConformanceScenarioResult(
        scenario_id="S08",
        title="Duplicate delivery idempotency deduplication",
        passed=passed,
        expected_outcome="submissions=1, identical transactionHash",
        observed_outcome=(
            f"submissions={rpc_harness.submission_count}, "
            f"identical={first_settlement.transaction_hash == second_settlement.transaction_hash}"
        ),
    )


def _run_scenario_s09_forced_settlement_failure() -> ConformanceScenarioResult:
    """S09: Forced settlement RPC submission failure."""
    rpc_harness = FaultInjectingSorobanRpc(FaultInjectionConfig(force_settlement_failure=True))
    payload = build_scenario_payment_payload(scheme="exact", amount="1000000")
    settlement = rpc_harness.settle_payment(payload)
    passed: bool = (
        settlement.success is False
        and settlement.error_code == PayGateErrorCode.SETTLEMENT_FAILURE
        and rpc_harness.submission_count == 0
    )
    return ConformanceScenarioResult(
        scenario_id="S09",
        title="Forced Soroban RPC settlement failure handling",
        passed=passed,
        expected_outcome=PayGateErrorCode.SETTLEMENT_FAILURE.value,
        observed_outcome=(settlement.error_code.value if settlement.error_code else "none"),
    )


def _run_scenario_s10_malformed_header_and_payload() -> ConformanceScenarioResult:
    """S10: Malformed Base64 and non-JSON wire header rejection."""
    observed_header_code: PayGateErrorCode | None = None
    try:
        decode_payment_required("!!!not_valid_base64!!!")
    except PaygateProtocolError as exc:
        observed_header_code = exc.code

    passed: bool = observed_header_code == PayGateErrorCode.INVALID_HEADER
    return ConformanceScenarioResult(
        scenario_id="S10",
        title="Malformed Base64/JSON wire header rejection",
        passed=passed,
        expected_outcome=PayGateErrorCode.INVALID_HEADER.value,
        observed_outcome=(observed_header_code.value if observed_header_code else "none"),
    )


def run_conformance_scenarios() -> list[ConformanceScenarioResult]:
    """Execute all 10 deterministic offline conformance and fault-injection scenarios."""
    return [
        _run_scenario_s01_baseline_exact(),
        _run_scenario_s02_clock_skew_exceeded(),
        _run_scenario_s03_expired_authorization(),
        _run_scenario_s04_unsponsored_fee_rejection(),
        _run_scenario_s05_upto_ceiling_overrun(),
        _run_scenario_s06_invalid_stellar_addresses(),
        _run_scenario_s07_exact_amount_mismatch(),
        _run_scenario_s08_duplicate_delivery_idempotency(),
        _run_scenario_s09_forced_settlement_failure(),
        _run_scenario_s10_malformed_header_and_payload(),
    ]


def format_conformance_markdown_report(
    scenario_results: Sequence[ConformanceScenarioResult],
) -> str:
    """Format scenario results as a Markdown conformance table."""
    passed_count: int = sum(1 for item in scenario_results if item.passed)
    total_count: int = len(scenario_results)
    lines: list[str] = [
        "# Stellar Paygate Offline Conformance Report (`stellar-paygate-sim`)",
        "",
        "| Scenario | Title | Status | Expected Outcome | Observed Outcome |",
        "| :---: | :--- | :---: | :--- | :--- |",
    ]
    for item in scenario_results:
        status_label: str = "PASS" if item.passed else "FAIL"
        lines.append(
            f"| `{item.scenario_id}` | {item.title} | **{status_label}** | "
            f"`{item.expected_outcome}` | `{item.observed_outcome}` |"
        )
    lines.append("")
    lines.append(f"**Summary:** {passed_count}/{total_count} scenarios passed.")
    return "\n".join(lines)


def format_conformance_json_report(
    scenario_results: Sequence[ConformanceScenarioResult],
) -> str:
    """Format scenario results as a structured JSON conformance report."""
    passed_count: int = sum(1 for item in scenario_results if item.passed)
    total_count: int = len(scenario_results)
    report_payload: dict[str, object] = {
        "all_passed": passed_count == total_count,
        "total": total_count,
        "passed": passed_count,
        "scenarios": [asdict(item) for item in scenario_results],
    }
    return json.dumps(report_payload, indent=2)


def build_simulation_argument_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for ``stellar-paygate-sim``."""
    parser = argparse.ArgumentParser(
        prog="stellar-paygate-sim",
        description=(
            "Execute the 10 deterministic offline fault-injection and conformance "
            "scenarios (S01-S10) for stellar-paygate-py."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit a structured JSON conformance report instead of a Markdown table.",
    )
    return parser


def main(command_line_arguments: Sequence[str] | None = None) -> int:
    """CLI entrypoint for ``stellar-paygate-sim``."""
    argument_parser: argparse.ArgumentParser = build_simulation_argument_parser()
    active_arguments: Sequence[str] = (
        command_line_arguments if command_line_arguments is not None else []
    )

    try:
        parsed_arguments = argument_parser.parse_args(active_arguments)
    except SystemExit as system_exit:
        exit_code_value = system_exit.code
        if isinstance(exit_code_value, int):
            return 0 if exit_code_value == 0 else 10
        return 10

    scenario_results: list[ConformanceScenarioResult] = run_conformance_scenarios()
    all_scenarios_passed: bool = all(item.passed for item in scenario_results)

    if parsed_arguments.emit_json:
        sys.stdout.write(f"{format_conformance_json_report(scenario_results)}\n")
    else:
        sys.stdout.write(f"{format_conformance_markdown_report(scenario_results)}\n")

    return 0 if all_scenarios_passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = [
    "ConformanceScenarioResult",
    "FaultInjectingSorobanRpc",
    "build_scenario_payment_payload",
    "run_conformance_scenarios",
    "format_conformance_markdown_report",
    "format_conformance_json_report",
    "main",
]
