"""Tests for incoming webhook signature verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import cast

import pytest
from stellar_sdk import Keypair

from stellar_paygate.cli import Paygate

FIXTURES_DIRECTORY = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def sample_payload_content() -> str:
    """Read sample payload json string from fixtures."""
    fixture_file_path: Path = FIXTURES_DIRECTORY / "sample_payload.json"
    return fixture_file_path.read_text(encoding="utf-8")


@pytest.fixture
def tampered_payload_content() -> str:
    """Read tampered payload json string from fixtures."""
    fixture_file_path: Path = FIXTURES_DIRECTORY / "tampered_payload.json"
    return fixture_file_path.read_text(encoding="utf-8")


@pytest.fixture
def keypair_information_data() -> dict[str, str]:
    """Read keypair fixture information."""
    fixture_file_path: Path = FIXTURES_DIRECTORY / "keypair_information.json"
    return cast(dict[str, str], json.loads(fixture_file_path.read_text(encoding="utf-8")))


def test_verify_webhook_ed25519_hex_signature(
    sample_payload_content: str,
    keypair_information_data: dict[str, str],
) -> None:
    """Verify webhook with valid Ed25519 signature in hexadecimal format."""
    paygate_client: Paygate = Paygate()
    verification_success: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=keypair_information_data["valid_ed25519_signature_hex"],
        public_key=keypair_information_data["source_public_key"],
    )
    assert verification_success is True


def test_verify_webhook_ed25519_base64_signature(
    sample_payload_content: str,
    keypair_information_data: dict[str, str],
) -> None:
    """Verify webhook with valid Ed25519 signature in base64 format."""
    raw_signature_bytes: bytes = bytes.fromhex(
        keypair_information_data["valid_ed25519_signature_hex"]
    )
    base64_signature_string: str = base64.b64encode(raw_signature_bytes).decode("ascii")

    paygate_client: Paygate = Paygate()
    verification_success: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=base64_signature_string,
        public_key=keypair_information_data["source_public_key"],
    )
    assert verification_success is True


def test_verify_webhook_embedded_public_key_format(
    sample_payload_content: str,
    keypair_information_data: dict[str, str],
) -> None:
    """Verify webhook signature using '<public_key>:<signature>' composite header format."""
    composite_signature_string: str = (
        f"{keypair_information_data['source_public_key']}:"
        f"{keypair_information_data['valid_ed25519_signature_hex']}"
    )

    paygate_client: Paygate = Paygate()
    verification_success: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=composite_signature_string,
        public_key=None,
    )
    assert verification_success is True


def test_verify_webhook_tampered_payload_fails(
    tampered_payload_content: str,
    keypair_information_data: dict[str, str],
) -> None:
    """Verify that tampering with the payload body causes signature verification to fail."""
    paygate_client: Paygate = Paygate()
    verification_success: bool = paygate_client.verify_webhook(
        payload=tampered_payload_content,
        signature=keypair_information_data["valid_ed25519_signature_hex"],
        public_key=keypair_information_data["source_public_key"],
    )
    assert verification_success is False


def test_verify_webhook_invalid_signature_fixture(
    sample_payload_content: str,
    keypair_information_data: dict[str, str],
) -> None:
    """Verify that an invalid signature fixture returns False."""
    invalid_signature_string: str = (
        (FIXTURES_DIRECTORY / "invalid_sig.txt").read_text(encoding="utf-8").strip()
    )
    paygate_client: Paygate = Paygate()
    verification_success: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=invalid_signature_string,
        public_key=keypair_information_data["source_public_key"],
    )
    assert verification_success is False


def test_verify_webhook_hmac_sha256_verification(
    sample_payload_content: str,
) -> None:
    """Verify webhook with HMAC-SHA256 signature and shared secret."""
    shared_secret_string: str = "production_super_secret_webhook_token_98765"
    payload_raw_bytes: bytes = sample_payload_content.encode("utf-8")

    expected_hmac_hex: str = hmac.new(
        shared_secret_string.encode("utf-8"),
        payload_raw_bytes,
        hashlib.sha256,
    ).hexdigest()

    paygate_client: Paygate = Paygate()
    verification_success: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=expected_hmac_hex,
        secret=shared_secret_string,
    )
    assert verification_success is True

    corrupted_hmac_hex: str = "deadbeef" * 8
    failed_verification: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=corrupted_hmac_hex,
        secret=shared_secret_string,
    )
    assert failed_verification is False


def test_verify_webhook_timestamp_tampering_check() -> None:
    """Verify that payload timestamp alteration invalidates cryptographic signature."""
    signing_keypair: Keypair = Keypair.random()
    original_event_payload: dict[str, object] = {
        "event_type": "subscription_charged",
        "timestamp": 1700000000,
        "amount": "25.00",
    }
    original_payload_bytes: bytes = json.dumps(original_event_payload).encode("utf-8")
    valid_signature_bytes: bytes = signing_keypair.sign(original_payload_bytes)

    paygate_client: Paygate = Paygate()
    valid_result: bool = paygate_client.verify_webhook(
        payload=original_payload_bytes,
        signature=valid_signature_bytes.hex(),
        public_key=signing_keypair.public_key,
    )
    assert valid_result is True

    # Modify timestamp to simulate a stale replay or tampered timestamp
    stale_event_payload: dict[str, object] = dict(original_event_payload)
    stale_event_payload["timestamp"] = 1600000000
    stale_payload_bytes: bytes = json.dumps(stale_event_payload).encode("utf-8")

    tampered_result: bool = paygate_client.verify_webhook(
        payload=stale_payload_bytes,
        signature=valid_signature_bytes.hex(),
        public_key=signing_keypair.public_key,
    )
    assert tampered_result is False


def test_verify_webhook_environment_variable_fallback(
    sample_payload_content: str,
    keypair_information_data: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that webhook secret and public key fallback to environment variables."""
    monkeypatch.setenv("STELLAR_PUBLIC_KEY", keypair_information_data["source_public_key"])
    monkeypatch.setenv("STELLAR_WEBHOOK_SECRET", "env_secret_key_123")

    paygate_client: Paygate = Paygate()
    # verify ed25519 without passing public_key explicitly
    ed25519_result: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=keypair_information_data["valid_ed25519_signature_hex"],
    )
    assert ed25519_result is True

    # verify HMAC using env secret
    payload_raw_bytes: bytes = sample_payload_content.encode("utf-8")
    hmac_digest_string: str = hmac.new(
        b"env_secret_key_123", payload_raw_bytes, hashlib.sha256
    ).hexdigest()

    hmac_result: bool = paygate_client.verify_webhook(
        payload=sample_payload_content,
        signature=hmac_digest_string,
    )
    assert hmac_result is True
