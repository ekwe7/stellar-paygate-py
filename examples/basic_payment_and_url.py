"""Offline runnable demonstration of SEP-0007 URL generation, XDR verification, and webhooks.

Run directly from the repository root:
    python examples/basic_payment_and_url.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

PROJECT_SOURCE_DIRECTORY = Path(__file__).resolve().parent.parent / "src"
if str(PROJECT_SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(PROJECT_SOURCE_DIRECTORY))

from stellar_sdk import Account, Asset, Keypair, Network, TransactionBuilder  # noqa: E402

from stellar_paygate.cli import Paygate  # noqa: E402


def build_ephemeral_signed_payment_xdr(
    sender_keypair: Keypair,
    recipient_public_key: str,
    payment_amount: str,
) -> str:
    """Construct and sign an in-memory Stellar Testnet payment transaction envelope."""
    sender_account = Account(account=sender_keypair.public_key, sequence=100_001)
    transaction_envelope = (
        TransactionBuilder(
            source_account=sender_account,
            network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_payment_op(
            destination=recipient_public_key,
            asset=Asset.native(),
            amount=payment_amount,
        )
        .set_timeout(300)
        .build()
    )
    transaction_envelope.sign(sender_keypair)
    return transaction_envelope.to_xdr()


def run_basic_payment_and_url_demo() -> int:
    """Execute SEP-0007 URL generation, XDR verification, and webhook signature checks."""
    paygate_client = Paygate()
    sender_keypair = Keypair.random()
    merchant_keypair = Keypair.random()

    # 1. Generate a SEP-0007 payment checkout URL
    checkout_url: str = paygate_client.create_payment_url(
        destination=merchant_keypair.public_key,
        amount="25.00",
        memo="invoice-2026-001",
        memo_type="MEMO_TEXT",
    )
    print(f"[1] Generated SEP-0007 Checkout URL:\n    {checkout_url}")
    assert checkout_url.startswith("web+stellar:pay?")

    # 2. Construct and verify an in-memory signed Stellar payment transaction XDR
    signed_transaction_xdr: str = build_ephemeral_signed_payment_xdr(
        sender_keypair=sender_keypair,
        recipient_public_key=merchant_keypair.public_key,
        payment_amount="25.00",
    )
    is_valid_payment: bool = paygate_client.verify_payment(
        xdr_string=signed_transaction_xdr,
        expected_amount="25.00 XLM",
    )
    is_wrong_amount_rejected: bool = not paygate_client.verify_payment(
        xdr_string=signed_transaction_xdr,
        expected_amount="999.00 XLM",
    )
    print(f"[2] Payment XDR Verified (25.00 XLM): {is_valid_payment}")
    print(f"    Mismatched Amount Rejected (999.00 XLM): {is_wrong_amount_rejected}")
    assert is_valid_payment is True
    assert is_wrong_amount_rejected is True

    # 3. Verify an Ed25519-signed webhook payload and an HMAC-SHA256 webhook payload
    webhook_event_body: bytes = json.dumps(
        {
            "event": "payment_received",
            "invoice_id": "invoice-2026-001",
            "amount": "25.00",
            "asset": "XLM",
        }
    ).encode("utf-8")

    ed25519_signature_hex: str = merchant_keypair.sign(webhook_event_body).hex()
    ed25519_verified: bool = paygate_client.verify_webhook(
        payload=webhook_event_body,
        signature=ed25519_signature_hex,
        public_key=merchant_keypair.public_key,
    )

    shared_webhook_secret: str = "demo_shared_webhook_secret_key"
    hmac_signature_hex: str = hmac.new(
        shared_webhook_secret.encode("utf-8"),
        webhook_event_body,
        hashlib.sha256,
    ).hexdigest()
    hmac_verified: bool = paygate_client.verify_webhook(
        payload=webhook_event_body,
        signature=hmac_signature_hex,
        secret=shared_webhook_secret,
    )

    print(f"[3] Webhook Ed25519 Signature Verified: {ed25519_verified}")
    print(f"    Webhook HMAC-SHA256 Signature Verified: {hmac_verified}")
    assert ed25519_verified is True
    assert hmac_verified is True

    print("All basic payment, URL, and webhook checks completed offline successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(run_basic_payment_and_url_demo())
