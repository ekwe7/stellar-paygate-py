"""Subprocess-based integration tests for stellar-paygate CLI sub-commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FIXTURES_DIRECTORY = Path(__file__).resolve().parent.parent / "fixtures"
SOURCE_DIRECTORY = Path(__file__).resolve().parent.parent.parent / "src"


def run_cli_subprocess(
    command_arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    """Execute the CLI in a separate subprocess with the project source directory on PYTHONPATH."""
    execution_environment: dict[str, str] = os.environ.copy()
    existing_pythonpath: str = execution_environment.get("PYTHONPATH", "")
    if existing_pythonpath:
        execution_environment["PYTHONPATH"] = f"{SOURCE_DIRECTORY}:{existing_pythonpath}"
    else:
        execution_environment["PYTHONPATH"] = str(SOURCE_DIRECTORY)

    full_command: list[str] = [
        sys.executable,
        "-m",
        "stellar_paygate.cli",
        *command_arguments,
    ]

    return subprocess.run(
        full_command,
        capture_output=True,
        text=True,
        env=execution_environment,
        check=False,
    )


def test_cli_subprocess_help_flag() -> None:
    """Verify that '--help' prints usage documentation and returns exit code 0."""
    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(["--help"])
    assert completed_process.returncode == 0
    assert "verify-payment" in completed_process.stdout
    assert "create-url" in completed_process.stdout
    assert "verify-webhook" in completed_process.stdout


def test_cli_subprocess_verify_payment_valid_fixture() -> None:
    """Verify 'verify-payment' sub-command succeeds with valid fixture XDR."""
    valid_xdr_file: Path = FIXTURES_DIRECTORY / "valid_xdr.txt"
    valid_xdr_content: str = valid_xdr_file.read_text(encoding="utf-8").strip()

    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        ["verify-payment", valid_xdr_content, "--amount", "10 XLM"]
    )
    assert completed_process.returncode == 0
    assert "verification succeeded" in completed_process.stdout.lower()


def test_cli_subprocess_verify_payment_invalid_fixture() -> None:
    """Verify 'verify-payment' sub-command fails with malformed fixture XDR."""
    invalid_xdr_file: Path = FIXTURES_DIRECTORY / "invalid_xdr.txt"
    invalid_xdr_content: str = invalid_xdr_file.read_text(encoding="utf-8").strip()

    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        ["verify-payment", invalid_xdr_content, "--amount", "10 XLM"]
    )
    assert completed_process.returncode == 1
    assert "verification failed" in completed_process.stderr.lower()


def test_cli_subprocess_verify_payment_amount_mismatch() -> None:
    """Verify 'verify-payment' sub-command fails when transaction amount does not match."""
    valid_xdr_file: Path = FIXTURES_DIRECTORY / "valid_xdr.txt"
    valid_xdr_content: str = valid_xdr_file.read_text(encoding="utf-8").strip()

    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        ["verify-payment", valid_xdr_content, "--amount", "999 XLM"]
    )
    assert completed_process.returncode == 1
    assert "verification failed" in completed_process.stderr.lower()


def test_cli_subprocess_create_url_valid() -> None:
    """Verify 'create-url' generates expected SEP-0007 URL to stdout."""
    destination_key: str = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        [
            "create-url",
            "--dest",
            destination_key,
            "--amount",
            "12.50",
            "--memo",
            "order-456",
            "--memo-type",
            "MEMO_TEXT",
        ]
    )
    assert completed_process.returncode == 0
    standard_output: str = completed_process.stdout.strip()
    assert standard_output.startswith("web+stellar:pay?")
    assert f"destination={destination_key}" in standard_output
    assert "amount=12.50" in standard_output
    assert "memo=order-456" in standard_output
    assert "memo_type=MEMO_TEXT" in standard_output


def test_cli_subprocess_create_url_missing_arguments() -> None:
    """Verify 'create-url' without required flags returns non-zero exit code."""
    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        ["create-url", "--amount", "5"]
    )
    assert completed_process.returncode == 2
    assert "required" in completed_process.stderr.lower()


def test_cli_subprocess_verify_webhook_valid_signature_fixture() -> None:
    """Verify 'verify-webhook' succeeds with valid fixture file and signature."""
    sample_payload_path: Path = FIXTURES_DIRECTORY / "sample_payload.json"
    valid_signature_path: Path = FIXTURES_DIRECTORY / "valid_ed25519_signature.txt"
    keypair_info_path: Path = FIXTURES_DIRECTORY / "keypair_information.json"

    import json

    keypair_dictionary: dict[str, str] = json.loads(keypair_info_path.read_text(encoding="utf-8"))
    signature_string: str = valid_signature_path.read_text(encoding="utf-8").strip()

    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        [
            "verify-webhook",
            "--payload",
            str(sample_payload_path),
            "--signature",
            signature_string,
            "--public-key",
            keypair_dictionary["source_public_key"],
        ]
    )
    assert completed_process.returncode == 0
    assert "verification succeeded" in completed_process.stdout.lower()


def test_cli_subprocess_verify_webhook_invalid_signature() -> None:
    """Verify 'verify-webhook' fails with corrupted signature."""
    sample_payload_path: Path = FIXTURES_DIRECTORY / "sample_payload.json"
    invalid_signature_path: Path = FIXTURES_DIRECTORY / "invalid_sig.txt"
    keypair_info_path: Path = FIXTURES_DIRECTORY / "keypair_information.json"

    import json

    keypair_dictionary: dict[str, str] = json.loads(keypair_info_path.read_text(encoding="utf-8"))
    invalid_signature_string: str = invalid_signature_path.read_text(encoding="utf-8").strip()

    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        [
            "verify-webhook",
            "--payload",
            str(sample_payload_path),
            "--signature",
            invalid_signature_string,
            "--public-key",
            keypair_dictionary["source_public_key"],
        ]
    )
    assert completed_process.returncode == 1
    assert "verification failed" in completed_process.stderr.lower()


def test_cli_subprocess_verify_webhook_inline_hmac() -> None:
    """Verify 'verify-webhook' succeeds with inline payload and HMAC secret."""
    import hashlib
    import hmac

    inline_payload: str = '{"event": "ping", "data": "test"}'
    shared_secret: str = "super_secret_test_key"
    hmac_digest: str = hmac.new(
        shared_secret.encode("utf-8"),
        inline_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        [
            "verify-webhook",
            "--payload",
            inline_payload,
            "--signature",
            hmac_digest,
            "--secret",
            shared_secret,
        ]
    )
    assert completed_process.returncode == 0
    assert "verification succeeded" in completed_process.stdout.lower()


def test_cli_subprocess_unknown_subcommand() -> None:
    """Verify unknown subcommand returns exit code 2."""
    completed_process: subprocess.CompletedProcess[str] = run_cli_subprocess(
        ["non-existent-action"]
    )
    assert completed_process.returncode == 2
