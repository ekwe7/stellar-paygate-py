"""Integration and unit tests for the stellar-paygate command-line interface."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import shutil
import subprocess
import sys
from pathlib import Path

from stellar_sdk import (
    Account,
    Asset,
    Keypair,
    Network,
    TransactionBuilder,
)

from stellar_paygate.cli import build_argument_parser


def execute_cli_command(
    command_arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    """Execute the CLI either via installed executable or module invocation."""
    venv_executable_path = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "stellar-paygate"
    if venv_executable_path.is_file():
        execution_arguments = [str(venv_executable_path), *command_arguments]
    else:
        executable_path: str | None = shutil.which("stellar-paygate")
        if executable_path is not None:
            execution_arguments = [executable_path, *command_arguments]
        else:
            execution_arguments = [
                sys.executable,
                "-m",
                "stellar_paygate.cli",
                *command_arguments,
            ]

    return subprocess.run(
        execution_arguments,
        capture_output=True,
        text=True,
        check=False,
    )


def create_fixture_payment_transaction_xdr(
    payment_amount: str = "5",
) -> str:
    """Create a signed Stellar payment transaction envelope fixture XDR string."""
    source_keypair = Keypair.random()
    destination_keypair = Keypair.random()

    source_account = Account(
        account=source_keypair.public_key,
        sequence=100,
    )

    transaction_envelope = (
        TransactionBuilder(
            source_account=source_account,
            network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_payment_op(
            destination=destination_keypair.public_key,
            asset=Asset.native(),
            amount=payment_amount,
        )
        .set_timeout(300)
        .build()
    )
    transaction_envelope.sign(source_keypair)
    return transaction_envelope.to_xdr()


def test_verify_payment_argument_parsing() -> None:
    """Validate argument parsing for the verify-payment sub-command."""
    argument_parser: argparse.ArgumentParser = build_argument_parser()
    parsed_arguments: argparse.Namespace = argument_parser.parse_args(
        ["verify-payment", "SAMPLE_XDR_STRING", "--amount", "5 XLM"]
    )

    assert parsed_arguments.subcommand == "verify-payment"
    assert parsed_arguments.xdr == "SAMPLE_XDR_STRING"
    assert parsed_arguments.amount == "5 XLM"


def test_create_url_argument_parsing() -> None:
    """Validate argument parsing for the create-url sub-command."""
    argument_parser: argparse.ArgumentParser = build_argument_parser()
    parsed_arguments: argparse.Namespace = argument_parser.parse_args(
        [
            "create-url",
            "--dest",
            "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT",
            "--amount",
            "5",
            "--memo",
            "test-memo",
        ]
    )

    assert parsed_arguments.subcommand == "create-url"
    assert (
        parsed_arguments.destination == "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
    )
    assert parsed_arguments.amount == "5"
    assert parsed_arguments.memo == "test-memo"


def test_verify_webhook_argument_parsing() -> None:
    """Validate argument parsing for the verify-webhook sub-command."""
    argument_parser: argparse.ArgumentParser = build_argument_parser()
    parsed_arguments: argparse.Namespace = argument_parser.parse_args(
        [
            "verify-webhook",
            "--payload",
            "payload.json",
            "--signature",
            "signature_placeholder_string",
        ]
    )

    assert parsed_arguments.subcommand == "verify-webhook"
    assert parsed_arguments.payload == "payload.json"
    assert parsed_arguments.signature == "signature_placeholder_string"


def test_cli_help_displays_all_subcommands() -> None:
    """Validate that --help output documents all available sub-commands."""
    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(["--help"])

    assert completed_process.returncode == 0
    assert "verify-payment" in completed_process.stdout
    assert "create-url" in completed_process.stdout
    assert "verify-webhook" in completed_process.stdout


def test_cli_verify_payment_valid_xdr_returns_zero() -> None:
    """End-to-end test verifying a known valid payment XDR returns exit code 0."""
    fixture_transaction_xdr: str = create_fixture_payment_transaction_xdr(payment_amount="5")

    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        ["verify-payment", fixture_transaction_xdr, "--amount", "5 XLM"]
    )

    assert completed_process.returncode == 0
    assert "verification succeeded" in completed_process.stdout


def test_cli_verify_payment_amount_mismatch_returns_one() -> None:
    """End-to-end test verifying that an amount mismatch returns exit code 1."""
    fixture_transaction_xdr: str = create_fixture_payment_transaction_xdr(payment_amount="5")

    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        ["verify-payment", fixture_transaction_xdr, "--amount", "10 XLM"]
    )

    assert completed_process.returncode == 1
    assert "verification failed" in completed_process.stderr


def test_cli_verify_payment_invalid_xdr_returns_one() -> None:
    """End-to-end test verifying that a malformed XDR string returns exit code 1."""
    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        ["verify-payment", "MALFORMED_NON_XDR_DATA", "--amount", "5 XLM"]
    )

    assert completed_process.returncode == 1
    assert "verification failed" in completed_process.stderr


def test_cli_create_url_prints_url_to_stdout() -> None:
    """End-to-end test verifying checkout URL generation printed to stdout."""
    destination_public_key: str = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        [
            "create-url",
            "--dest",
            destination_public_key,
            "--amount",
            "5",
            "--memo",
            "test-order-42",
        ]
    )

    assert completed_process.returncode == 0
    standard_output_string: str = completed_process.stdout.strip()
    assert standard_output_string.startswith("web+stellar:pay?")
    assert f"destination={destination_public_key}" in standard_output_string
    assert "amount=5" in standard_output_string
    assert "memo=test-order-42" in standard_output_string


def test_cli_verify_webhook_valid_signature_returns_zero(
    tmp_path: Path,
) -> None:
    """End-to-end test verifying a signed webhook payload returns exit code 0."""
    payload_content_bytes: bytes = b'{"event": "payment_completed", "amount": "5 XLM"}'
    temporary_payload_file: Path = tmp_path / "webhook_event.json"
    temporary_payload_file.write_bytes(payload_content_bytes)

    signing_keypair: Keypair = Keypair.random()
    signature_bytes: bytes = signing_keypair.sign(payload_content_bytes)
    signature_hex_string: str = signature_bytes.hex()

    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        [
            "verify-webhook",
            "--payload",
            str(temporary_payload_file),
            "--signature",
            signature_hex_string,
            "--public-key",
            signing_keypair.public_key,
        ]
    )

    assert completed_process.returncode == 0
    assert "verification succeeded" in completed_process.stdout


def test_cli_verify_webhook_invalid_signature_returns_one(
    tmp_path: Path,
) -> None:
    """End-to-end test verifying an invalid webhook signature returns exit code 1."""
    payload_content_bytes: bytes = b'{"event": "unauthorized_event"}'
    temporary_payload_file: Path = tmp_path / "webhook_invalid.json"
    temporary_payload_file.write_bytes(payload_content_bytes)

    corrupted_signature_hex_string: str = "00" * 64
    signing_keypair: Keypair = Keypair.random()

    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        [
            "verify-webhook",
            "--payload",
            str(temporary_payload_file),
            "--signature",
            corrupted_signature_hex_string,
            "--public-key",
            signing_keypair.public_key,
        ]
    )

    assert completed_process.returncode == 1
    assert "verification failed" in completed_process.stderr


def test_cli_verify_webhook_hmac_secret_returns_zero(
    tmp_path: Path,
) -> None:
    """End-to-end test verifying webhook HMAC signature returns exit code 0."""
    payload_content_bytes: bytes = b'{"event": "hmac_verified_payment"}'
    temporary_payload_file: Path = tmp_path / "webhook_hmac.json"
    temporary_payload_file.write_bytes(payload_content_bytes)

    shared_secret_string: str = "super_secure_secret_key_12345"
    expected_hmac_digest: str = hmac.new(
        shared_secret_string.encode("utf-8"),
        payload_content_bytes,
        hashlib.sha256,
    ).hexdigest()

    completed_process: subprocess.CompletedProcess[str] = execute_cli_command(
        [
            "verify-webhook",
            "--payload",
            str(temporary_payload_file),
            "--signature",
            expected_hmac_digest,
            "--secret",
            shared_secret_string,
        ]
    )

    assert completed_process.returncode == 0
    assert "verification succeeded" in completed_process.stdout
