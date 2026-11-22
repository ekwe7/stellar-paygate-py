"""Tests for deterministic offline fault-injection harness and stellar-paygate-sim CLI."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from unittest.mock import MagicMock, patch

import httpx
import pytest

from stellar_paygate.client import SpendPolicyGuard, wrap_httpx_with_paygate
from stellar_paygate.fault_harness import (
    ConformanceScenarioResult,
    FaultInjectingSorobanRpc,
    QuorumSorobanRpcAdapter,
    SorobanRpcReaderPort,
    build_scenario_payment_payload,
    format_conformance_json_report,
    format_conformance_junit_report,
    format_conformance_markdown_report,
    format_conformance_sarif_report,
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


def test_quorum_soroban_rpc_adapter_eliminates_negative_ledger_skew() -> None:
    """Verify QuorumSorobanRpcAdapter selects max(ledger_sequence) across lagging RPC nodes."""
    with pytest.raises(ValueError, match="at least one RPC reader"):
        QuorumSorobanRpcAdapter([])

    lagging_reader_one = FaultInjectingSorobanRpc(
        base_ledger=4_999_996,
        skew_tolerance_ledgers=2,
    )
    head_reader_two = FaultInjectingSorobanRpc(
        base_ledger=5_000_000,
        skew_tolerance_ledgers=2,
    )
    lagging_reader_three = FaultInjectingSorobanRpc(
        base_ledger=4_999_998,
        skew_tolerance_ledgers=2,
    )

    assert isinstance(lagging_reader_one, SorobanRpcReaderPort)
    quorum_adapter = QuorumSorobanRpcAdapter(
        [lagging_reader_one, head_reader_two, lagging_reader_three]
    )
    assert isinstance(quorum_adapter, SorobanRpcReaderPort)
    assert quorum_adapter.get_facilitator_latest_ledger() == 5_000_000

    # Client signs at network head 5_000_000. Under strict 2-ledger tolerance:
    # - Lagging reader at 4_999_996 (+4 skew) falsely rejects with CLOCK_SKEW_EXCEEDED.
    # - QuorumSorobanRpcAdapter routes to the 5_000_000 head reader and passes cleanly!
    fresh_head_payload = build_scenario_payment_payload(
        ledger=5_000_000,
        expiration_ledger=5_000_012,
    )
    lagging_verification = lagging_reader_one.verify_payment(fresh_head_payload)
    assert lagging_verification.success is False
    assert lagging_verification.error_code == PayGateErrorCode.CLOCK_SKEW_EXCEEDED

    quorum_verification = quorum_adapter.verify_payment(fresh_head_payload)
    assert quorum_verification.success is True
    assert quorum_verification.error_code is None

    quorum_settlement = quorum_adapter.settle_payment(fresh_head_payload)
    assert quorum_settlement.success is True
    assert quorum_settlement.settled_amount == "1000000"
    assert head_reader_two.submission_count == 1


def test_quorum_soroban_rpc_adapter_reader_timeout_failover() -> None:
    """Verify QuorumSorobanRpcAdapter fails over cleanly when individual RPC readers fail."""
    unhealthy_reader = MagicMock(spec=SorobanRpcReaderPort)
    unhealthy_reader.get_facilitator_latest_ledger.side_effect = RuntimeError(
        "RPC node 1 connection timed out"
    )

    failing_action_reader = MagicMock(spec=SorobanRpcReaderPort)
    failing_action_reader.get_facilitator_latest_ledger.return_value = 5_000_002
    failing_action_reader.verify_payment.side_effect = RuntimeError(
        "RPC node 2 simulateTransaction 503 error"
    )
    failing_action_reader.settle_payment.side_effect = RuntimeError(
        "RPC node 2 sendTransaction 503 error"
    )

    healthy_backup_reader = FaultInjectingSorobanRpc(
        base_ledger=5_000_000,
        skew_tolerance_ledgers=5,
    )

    quorum_adapter = QuorumSorobanRpcAdapter(
        [unhealthy_reader, failing_action_reader, healthy_backup_reader]
    )
    assert quorum_adapter.get_facilitator_latest_ledger() == 5_000_002

    payment_payload = build_scenario_payment_payload(
        ledger=5_000_000,
        expiration_ledger=5_000_012,
    )
    # failing_action_reader has height 5_000_002 so it is tried first, raises RuntimeError,
    # and quorum_adapter automatically fails over to healthy_backup_reader at 5_000_000.
    failover_verify = quorum_adapter.verify_payment(payment_payload)
    assert failover_verify.success is True

    failover_settle = quorum_adapter.settle_payment(payment_payload)
    assert failover_settle.success is True
    assert healthy_backup_reader.submission_count == 1

    # When ALL readers fail, explicit RuntimeError is raised
    all_dead_quorum = QuorumSorobanRpcAdapter([unhealthy_reader])
    with pytest.raises(RuntimeError, match="failed to report ledger height"):
        all_dead_quorum.get_facilitator_latest_ledger()

    only_failing_action_quorum = QuorumSorobanRpcAdapter([failing_action_reader])
    with pytest.raises(RuntimeError, match="verify_payment failover"):
        only_failing_action_quorum.verify_payment(payment_payload)
    with pytest.raises(RuntimeError, match="settle_payment failover"):
        only_failing_action_quorum.settle_payment(payment_payload)


def test_fault_harness_sarif_and_junit_exporters(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SARIF v2.1.0 and JUnit XML exporters via CLI --format and direct functions."""
    # 1. CLI --format sarif
    sarif_exit_code: int = fault_harness_main(["--format", "sarif"])
    assert sarif_exit_code == 0
    sarif_stdout: str = capsys.readouterr().out
    sarif_document = json.loads(sarif_stdout)
    assert sarif_document["version"] == "2.1.0"
    assert "sarif-2.1.0.json" in sarif_document["$schema"]

    sarif_run = sarif_document["runs"][0]
    driver_info = sarif_run["tool"]["driver"]
    assert driver_info["name"] == "stellar-paygate-sim"
    assert len(driver_info["rules"]) == 10
    assert len(sarif_run["results"]) == 10
    for result_entry in sarif_run["results"]:
        assert result_entry["kind"] == "pass"
        assert result_entry["level"] == "note"

    # 2. CLI --format junit
    junit_exit_code: int = fault_harness_main(["--format", "junit"])
    assert junit_exit_code == 0
    junit_stdout: str = capsys.readouterr().out
    assert junit_stdout.startswith('<?xml version="1.0" encoding="utf-8"?>')

    parsed_xml_root = ElementTree.fromstring(junit_stdout)
    assert parsed_xml_root.tag == "testsuites"
    assert parsed_xml_root.attrib["tests"] == "10"
    assert parsed_xml_root.attrib["failures"] == "0"

    testsuite_node = parsed_xml_root.find("testsuite")
    assert testsuite_node is not None
    assert testsuite_node.attrib["tests"] == "10"
    assert testsuite_node.attrib["failures"] == "0"
    testcase_nodes = testsuite_node.findall("testcase")
    assert len(testcase_nodes) == 10
    for testcase_node in testcase_nodes:
        assert testcase_node.find("failure") is None

    # 3. Verify failure elements in SARIF and JUnit when a scenario fails
    failed_scenario = ConformanceScenarioResult(
        scenario_id="S04",
        title="Unsponsored fee rejection",
        passed=False,
        expected_outcome="invalid_payload",
        observed_outcome="success=True",
    )
    failed_sarif = json.loads(format_conformance_sarif_report([failed_scenario]))
    assert failed_sarif["runs"][0]["results"][0]["kind"] == "fail"
    assert failed_sarif["runs"][0]["results"][0]["level"] == "error"

    failed_junit_xml = format_conformance_junit_report([failed_scenario])
    failed_root = ElementTree.fromstring(failed_junit_xml)
    assert failed_root.attrib["failures"] == "1"
    failure_node = failed_root.find("./testsuite/testcase/failure")
    assert failure_node is not None
    assert "Expected 'invalid_payload'" in failure_node.attrib["message"]

    monkeypatch.setattr(
        "stellar_paygate.fault_harness.run_conformance_scenarios",
        lambda: [failed_scenario],
    )
    assert fault_harness_main(["--format", "junit"]) == 1
    capsys.readouterr()
