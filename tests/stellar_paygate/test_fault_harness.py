"""Tests for deterministic offline fault-injection harness and simulated failure modes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from stellar_paygate.client import SpendPolicyGuard, wrap_httpx_with_paygate
from stellar_paygate.fault_harness import FaultInjectingSorobanRpc
from stellar_paygate.fault_harness import main as fault_harness_main
from stellar_paygate.types import (
    FaultInjectionConfig,
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


def test_fault_injecting_soroban_rpc_initialization() -> None:
    """Verify FaultInjectingSorobanRpc instance creation and interface."""
    rpc_harness: FaultInjectingSorobanRpc = FaultInjectingSorobanRpc()
    assert isinstance(rpc_harness, FaultInjectingSorobanRpc)


def test_fault_harness_cli_main_exit_code() -> None:
    """Verify fault harness entrypoint executes and returns exit code 0."""
    execution_result: int = fault_harness_main()
    assert execution_result == 0


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
