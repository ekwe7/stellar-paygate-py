"""Tests for Stellar payment transaction verification logic."""

from __future__ import annotations

from pathlib import Path

import pytest
from stellar_sdk import (
    Account,
    Asset,
    Keypair,
    ManageData,
    Network,
    TransactionBuilder,
)

from stellar_paygate.cli import Paygate

FIXTURES_DIRECTORY = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def valid_transaction_xdr() -> str:
    """Read and return valid transaction XDR from fixture file."""
    fixture_file_path: Path = FIXTURES_DIRECTORY / "valid_xdr.txt"
    return fixture_file_path.read_text(encoding="utf-8").strip()


@pytest.fixture
def invalid_transaction_xdr() -> str:
    """Read and return malformed transaction XDR from fixture file."""
    fixture_file_path: Path = FIXTURES_DIRECTORY / "invalid_xdr.txt"
    return fixture_file_path.read_text(encoding="utf-8").strip()


def create_signed_payment_xdr(
    payment_amount: str = "10",
    asset_code: str | None = None,
    asset_issuer_key: str | None = None,
    network_passphrase: str = Network.TESTNET_NETWORK_PASSPHRASE,
) -> str:
    """Helper creating a signed transaction XDR for custom testing scenarios."""
    source_keypair: Keypair = Keypair.random()
    destination_keypair: Keypair = Keypair.random()
    source_account: Account = Account(account=source_keypair.public_key, sequence=100)

    payment_asset: Asset
    if asset_code is not None and asset_issuer_key is not None:
        payment_asset = Asset(code=asset_code, issuer=asset_issuer_key)
    else:
        payment_asset = Asset.native()

    transaction_envelope = (
        TransactionBuilder(
            source_account=source_account,
            network_passphrase=network_passphrase,
            base_fee=100,
        )
        .append_payment_op(
            destination=destination_keypair.public_key,
            asset=payment_asset,
            amount=payment_amount,
        )
        .set_timeout(300)
        .build()
    )
    transaction_envelope.sign(source_keypair)
    return transaction_envelope.to_xdr()


@pytest.mark.parametrize(
    ("expected_amount_query", "should_succeed"),
    [
        ("10 XLM", True),
        ("10", True),
        ("10.0 XLM", True),
        ("5 XLM", False),
        ("20 XLM", False),
        ("10 USDC", False),
    ],
)
def test_verify_payment_amounts_and_assets(
    valid_transaction_xdr: str,
    expected_amount_query: str,
    should_succeed: bool,
) -> None:
    """Verify that expected amount and asset matching behaves correctly."""
    paygate_client: Paygate = Paygate()
    verification_result: bool = paygate_client.verify_payment(
        xdr_string=valid_transaction_xdr,
        expected_amount=expected_amount_query,
    )
    assert verification_result is should_succeed


def test_verify_payment_without_expected_amount(
    valid_transaction_xdr: str,
) -> None:
    """Verify payment succeeds when no expected amount is specified."""
    paygate_client: Paygate = Paygate()
    verification_result: bool = paygate_client.verify_payment(
        xdr_string=valid_transaction_xdr,
        expected_amount=None,
    )
    assert verification_result is True


def test_verify_payment_malformed_xdr(
    invalid_transaction_xdr: str,
) -> None:
    """Verify payment fails gracefully on corrupted XDR."""
    paygate_client: Paygate = Paygate()
    verification_result: bool = paygate_client.verify_payment(
        xdr_string=invalid_transaction_xdr,
        expected_amount="10 XLM",
    )
    assert verification_result is False


@pytest.mark.parametrize(
    "corrupted_xdr_sample",
    [
        "",
        "NOT_BASE_64!!!",
        "AAAA",
        "====",
    ],
)
def test_verify_payment_various_corrupted_inputs(
    corrupted_xdr_sample: str,
) -> None:
    """Verify payment returns False for various non-XDR strings."""
    paygate_client: Paygate = Paygate()
    verification_result: bool = paygate_client.verify_payment(
        xdr_string=corrupted_xdr_sample,
        expected_amount="10 XLM",
    )
    assert verification_result is False


def test_verify_payment_custom_asset() -> None:
    """Verify payments made with non-native custom credit assets."""
    issuer_keypair: Keypair = Keypair.random()
    custom_asset_xdr: str = create_signed_payment_xdr(
        payment_amount="25.50",
        asset_code="USDC",
        asset_issuer_key=issuer_keypair.public_key,
    )

    paygate_client: Paygate = Paygate()
    successful_verification: bool = paygate_client.verify_payment(
        xdr_string=custom_asset_xdr,
        expected_amount="25.50 USDC",
    )
    assert successful_verification is True

    mismatched_asset_verification: bool = paygate_client.verify_payment(
        xdr_string=custom_asset_xdr,
        expected_amount="25.50 EURC",
    )
    assert mismatched_asset_verification is False


def test_verify_payment_with_mismatched_network_passphrase() -> None:
    """Verify network passphrase fallback and explicit override handling."""
    public_network_xdr: str = create_signed_payment_xdr(
        payment_amount="15",
        network_passphrase=Network.PUBLIC_NETWORK_PASSPHRASE,
    )

    paygate_client: Paygate = Paygate(network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE)
    # The client contains fallback logic that checks alternative standard passphrases
    fallback_verification_result: bool = paygate_client.verify_payment(
        xdr_string=public_network_xdr,
        expected_amount="15 XLM",
    )
    assert fallback_verification_result is True

    # Explicitly providing public network passphrase should succeed directly
    explicit_verification_result: bool = paygate_client.verify_payment(
        xdr_string=public_network_xdr,
        expected_amount="15 XLM",
        network_passphrase=Network.PUBLIC_NETWORK_PASSPHRASE,
    )
    assert explicit_verification_result is True


def test_verify_payment_non_payment_operation_returns_false() -> None:
    """Verify transaction without payment operations returns False."""
    source_keypair: Keypair = Keypair.random()
    source_account: Account = Account(account=source_keypair.public_key, sequence=200)

    # Build a transaction with only ManageData operation (no payment)
    transaction_envelope = (
        TransactionBuilder(
            source_account=source_account,
            network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_operation(ManageData(data_name="metadata_key", data_value="metadata_value"))
        .set_timeout(300)
        .build()
    )
    transaction_envelope.sign(source_keypair)
    non_payment_xdr: str = transaction_envelope.to_xdr()

    paygate_client: Paygate = Paygate()
    verification_result: bool = paygate_client.verify_payment(
        xdr_string=non_payment_xdr,
        expected_amount="10 XLM",
    )
    assert verification_result is False


def test_verify_payment_multiple_operations() -> None:
    """Verify transaction with multiple operations where payment matches."""
    source_keypair: Keypair = Keypair.random()
    destination_keypair: Keypair = Keypair.random()
    source_account: Account = Account(account=source_keypair.public_key, sequence=300)

    transaction_envelope = (
        TransactionBuilder(
            source_account=source_account,
            network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_operation(ManageData(data_name="session_id", data_value="session_12345"))
        .append_payment_op(
            destination=destination_keypair.public_key,
            asset=Asset.native(),
            amount="50",
        )
        .set_timeout(300)
        .build()
    )
    transaction_envelope.sign(source_keypair)
    multi_operation_xdr: str = transaction_envelope.to_xdr()

    paygate_client: Paygate = Paygate()
    verification_result: bool = paygate_client.verify_payment(
        xdr_string=multi_operation_xdr,
        expected_amount="50 XLM",
    )
    assert verification_result is True
