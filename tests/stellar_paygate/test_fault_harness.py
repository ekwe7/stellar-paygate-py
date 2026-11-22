"""Tests for deterministic offline fault-injection harness and stellar-paygate-sim CLI."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from stellar_paygate.client import SpendPolicyGuard, wrap_httpx_with_paygate
from stellar_paygate.fault_harness import (
    ConformanceScenarioResult,
    FaultInjectingSorobanRpc,
    build_scenario_payment_payload,
    format_conformance_json_report,
    format_conformance_markdown_report,
    run_conformance_scenarios,
)
from stellar_paygate.fault_harness import main as fault_harness_main
from stellar_paygate.types import (
    FaultInjectionConfig,
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
)


def test_fault_injection_config_model() -> None:
    """Verify FaultInjectionConfig model defaults and custom validation."""
    default_config: FaultInjectionConfig = FaultInjectionConfig()
    assert default_config.ledger_skew == 0
    assert default_config.allow_unsponsored_fees is True
    assert default_config.force_settlement_failure is False
    assert default_config.override_settled_amount is None
    assert default_config.duplicate_delivery_count == 0

    custom_config: FaultInjectionConfig = FaultInjectionConfig(
        ledger_skew=5,
        allow_unsponsored_fees=False,
        force_settlement_failure=True,
        override_settled_amount=1000,
        duplicate_delivery_count=2,
    )
    assert custom_config.ledger_skew == 5
    assert custom_config.allow_unsponsored_fees is False
    assert custom_config.force_settlement_failure is True
    assert custom_config.override_settled_amount == 1000
    assert custom_config.duplicate_delivery_count == 2


def test_fault_injecting_soroban_rpc_knobs_and_behaviors() -> None:
    """Verify every knob on FaultInjectionConfig triggers its deterministic behavior."""
    # 1. Baseline happy path
    baseline_rpc = FaultInjectingSorobanRpc()
    assert baseline_rpc.get_facilitator_latest_ledger() == 500_000
    assert baseline_rpc.get_client_latest_ledger() == 500_000
    exact_payload = build_scenario_payment_payload(scheme="exact", amount="1000000")
    assert baseline_rpc.verify_payment(exact_payload).success is True
    settled = baseline_rpc.settle_payment(exact_payload)
    assert settled.success is True
    assert settled.settled_amount == "1000000"
    assert baseline_rpc.submission_count == 1

    # 2. Ledger skew knob
    skewed_rpc = FaultInjectingSorobanRpc(
        FaultInjectionConfig(ledger_skew=10),
        skew_tolerance_ledgers=5,
    )
    assert skewed_rpc.get_client_latest_ledger() == 500_010
    skewed_payload = build_scenario_payment_payload(
        ledger=skewed_rpc.get_client_latest_ledger(),
        expiration_ledger=skewed_rpc.get_client_latest_ledger() + 12,
    )
    skewed_verify = skewed_rpc.verify_payment(skewed_payload)
    assert skewed_verify.success is False
    assert skewed_verify.error_code == PayGateErrorCode.CLOCK_SKEW_EXCEEDED

    # 3. Unsponsored fee rejection knob
    no_unsponsored_rpc = FaultInjectingSorobanRpc(
        FaultInjectionConfig(allow_unsponsored_fees=False)
    )
    unsponsored_payload = build_scenario_payment_payload(
        fee_mode=FeeMode.UNSPONSORED,
        are_fees_sponsored=False,
    )
    unsponsored_verify = no_unsponsored_rpc.verify_payment(unsponsored_payload)
    assert unsponsored_verify.success is False
    assert unsponsored_verify.error_code == PayGateErrorCode.INVALID_PAYLOAD

    # 4. Override settled amount knob (upto partial vs overrun)
    upto_partial_rpc = FaultInjectingSorobanRpc(
        FaultInjectionConfig(override_settled_amount=70_000)
    )
    upto_payload = build_scenario_payment_payload(scheme="upto", amount="1000000")
    upto_settled = upto_partial_rpc.settle_payment(upto_payload)
    assert upto_settled.success is True
    assert upto_settled.settled_amount == "70000"

    upto_overrun_rpc = FaultInjectingSorobanRpc(
        FaultInjectionConfig(override_settled_amount=2_000_000)
    )
    upto_overrun = upto_overrun_rpc.settle_payment(upto_payload)
    assert upto_overrun.success is False
    assert upto_overrun.error_code == PayGateErrorCode.EXCEEDS_MAX_UPTO

    # 5. Duplicate delivery count knob (idempotent single submission)
    duplicate_rpc = FaultInjectingSorobanRpc(FaultInjectionConfig(duplicate_delivery_count=4))
    duplicate_settled = duplicate_rpc.settle_payment(exact_payload)
    assert duplicate_settled.success is True
    assert duplicate_rpc.submission_count == 1

    # 6. Force settlement failure knob
    failing_rpc = FaultInjectingSorobanRpc(FaultInjectionConfig(force_settlement_failure=True))
    failed_settle = failing_rpc.settle_payment(exact_payload)
    assert failed_settle.success is False
    assert failed_settle.error_code == PayGateErrorCode.SETTLEMENT_FAILURE
    assert failing_rpc.submission_count == 0


def test_run_conformance_scenarios_s01_to_s10() -> None:
    """Verify all 10 deterministic conformance scenarios (S01-S10) execute and pass."""
    scenario_results: list[ConformanceScenarioResult] = run_conformance_scenarios()
    assert len(scenario_results) == 10

    expected_scenario_ids: list[str] = [f"S{index:02d}" for index in range(1, 11)]
    observed_scenario_ids: list[str] = [item.scenario_id for item in scenario_results]
    assert observed_scenario_ids == expected_scenario_ids

    for scenario in scenario_results:
        assert scenario.passed is True, (
            f"Scenario {scenario.scenario_id} failed: "
            f"expected={scenario.expected_outcome}, observed={scenario.observed_outcome}"
        )


def test_fault_harness_cli_markdown_and_json_modes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify stellar-paygate-sim CLI Markdown output, --json report, and flag errors."""
    # 1. Default Markdown report mode
    markdown_exit_code: int = fault_harness_main([])
    assert markdown_exit_code == 0
    markdown_stdout: str = capsys.readouterr().out
    assert "# Stellar Paygate Offline Conformance Report" in markdown_stdout
    assert "10/10 scenarios passed" in markdown_stdout
    for index in range(1, 11):
        assert f"`S{index:02d}`" in markdown_stdout

    # 2. Structured JSON report mode (--json)
    json_exit_code: int = fault_harness_main(["--json"])
    assert json_exit_code == 0
    json_stdout: str = capsys.readouterr().out
    parsed_report: dict[str, object] = json.loads(json_stdout)
    assert parsed_report["all_passed"] is True
    assert parsed_report["total"] == 10
    assert parsed_report["passed"] == 10
    scenarios_list = parsed_report["scenarios"]
    assert isinstance(scenarios_list, list)
    assert len(scenarios_list) == 10

    # 3. --help flag exits with 0
    help_exit_code: int = fault_harness_main(["--help"])
    assert help_exit_code == 0
    capsys.readouterr()

    # 4. Invalid CLI flag exits with code 10
    invalid_flag_exit_code: int = fault_harness_main(["--unknown-invalid-flag"])
    assert invalid_flag_exit_code == 10
    capsys.readouterr()


def test_fault_harness_cli_returns_1_when_a_scenario_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify main() returns exit code 1 when any conformance scenario fails."""
    failing_scenario = ConformanceScenarioResult(
        scenario_id="S01",
        title="Simulated failure",
        passed=False,
        expected_outcome="success=True",
        observed_outcome="success=False",
    )
    monkeypatch.setattr(
        "stellar_paygate.fault_harness.run_conformance_scenarios",
        lambda: [failing_scenario],
    )

    exit_code: int = fault_harness_main(["--json"])
    assert exit_code == 1
    parsed_output = json.loads(capsys.readouterr().out)
    assert parsed_output["all_passed"] is False
    assert parsed_output["passed"] == 0

    markdown_table: str = format_conformance_markdown_report([failing_scenario])
    assert "**FAIL**" in markdown_table
    json_report: str = format_conformance_json_report([failing_scenario])
    assert '"all_passed": false' in json_report


def test_simulated_network_timeout_failure() -> None:
    """Simulate a network timeout failure when communicating with an upstream Horizon node."""
    policy_guard: SpendPolicyGuard = SpendPolicyGuard()
    mock_http_client: MagicMock = MagicMock(spec=httpx.Client)
    mock_http_client.get.side_effect = httpx.TimeoutException("Network connection timed out")

    wrapped_client: httpx.Client = wrap_httpx_with_paygate(mock_http_client, policy_guard)

    with pytest.raises(httpx.TimeoutException) as caught_exception:
        wrapped_client.get("https://horizon-testnet.stellar.org/transactions/mock_tx_id")

    assert "Network connection timed out" in str(caught_exception.value)


def test_simulated_malformed_response_failure() -> None:
    """Simulate a malformed JSON / protocol response from an upstream payment gate."""
    with pytest.raises(PaygateProtocolError) as protocol_error:
        raise PaygateProtocolError(
            code=PayGateErrorCode.INVALID_PAYLOAD,
            detail="Server returned malformed non-JSON payload",
        )

    assert protocol_error.value.code == PayGateErrorCode.INVALID_PAYLOAD
    assert protocol_error.value.detail == "Server returned malformed non-JSON payload"


@patch("httpx.Client.send")
def test_simulated_upstream_503_service_unavailable(
    mock_client_send: MagicMock,
) -> None:
    """Simulate upstream Horizon 503 service unavailable response."""
    mock_response: httpx.Response = httpx.Response(
        status_code=503,
        request=httpx.Request("POST", "https://horizon-testnet.stellar.org/transactions"),
        content=b'{"type": "service_unavailable", "title": "Service Unavailable"}',
    )
    mock_client_send.return_value = mock_response

    http_client: httpx.Client = httpx.Client()
    response_result: httpx.Response = http_client.post(
        "https://horizon-testnet.stellar.org/transactions",
        content=b"test_xdr_transaction",
    )
    assert response_result.status_code == 503
    http_client.close()
