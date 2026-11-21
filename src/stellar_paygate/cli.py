"""Command-line interface for the Stellar Paygate SDK.

Provides entry points for payment verification, checkout URL generation,
and webhook signature verification.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
import urllib.parse
from collections.abc import Sequence
from decimal import Decimal

from stellar_sdk import Keypair, Network, TransactionEnvelope


class Paygate:
    """Core Paygate client for payment verification, URL generation, and webhook checks."""

    def __init__(
        self,
        horizon_url: str | None = None,
        network_passphrase: str | None = None,
        public_key: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.horizon_url: str | None = horizon_url
        self.network_passphrase: str = (
            network_passphrase
            or os.environ.get("STELLAR_NETWORK_PASSPHRASE")
            or Network.TESTNET_NETWORK_PASSPHRASE
        )
        self.public_key: str | None = public_key or os.environ.get("STELLAR_PUBLIC_KEY")
        self.webhook_secret: str | None = webhook_secret or os.environ.get("STELLAR_WEBHOOK_SECRET")

    def verify_payment(
        self,
        xdr_string: str,
        expected_amount: str | None = None,
        network_passphrase: str | None = None,
    ) -> bool:
        """Verify an incoming Stellar payment transaction XDR.

        Validates that the transaction envelope is well-formed, can be parsed,
        and contains a payment matching the expected amount if specified.
        """
        active_network_passphrase: str = network_passphrase or self.network_passphrase

        try:
            try:
                parsed_envelope = TransactionEnvelope.from_xdr(
                    xdr_string, network_passphrase=active_network_passphrase
                )
            except Exception:
                # Attempt with alternative standard passphrases before failing
                alternative_passphrase: str = (
                    Network.PUBLIC_NETWORK_PASSPHRASE
                    if active_network_passphrase == Network.TESTNET_NETWORK_PASSPHRASE
                    else Network.TESTNET_NETWORK_PASSPHRASE
                )
                parsed_envelope = TransactionEnvelope.from_xdr(
                    xdr_string, network_passphrase=alternative_passphrase
                )

            transaction_operations = parsed_envelope.transaction.operations
            if not transaction_operations:
                return False

            if expected_amount is None:
                return True

            amount_parts: list[str] = expected_amount.strip().split()
            expected_numeric_amount: Decimal = Decimal(amount_parts[0])
            expected_asset_code: str | None = (
                amount_parts[1].upper() if len(amount_parts) > 1 else None
            )

            for operation in transaction_operations:
                operation_amount_attribute = getattr(operation, "amount", None)
                if operation_amount_attribute is None:
                    continue

                operation_numeric_amount = Decimal(str(operation_amount_attribute))
                if operation_numeric_amount != expected_numeric_amount:
                    continue

                operation_asset = getattr(operation, "asset", None)
                if expected_asset_code is not None and operation_asset is not None:
                    operation_asset_code: str = getattr(operation_asset, "code", "XLM").upper()
                    if operation_asset_code != expected_asset_code:
                        continue

                return True

            return False
        except Exception:
            return False

    def create_payment_url(
        self,
        destination: str,
        amount: str,
        memo: str | None = None,
        memo_type: str | None = None,
        asset_code: str | None = None,
        asset_issuer: str | None = None,
    ) -> str:
        """Generate a Stellar payment checkout URL conforming to SEP-0007."""
        query_parameters: dict[str, str] = {
            "destination": destination,
            "amount": amount,
        }
        if memo is not None:
            query_parameters["memo"] = memo
            if memo_type is not None:
                query_parameters["memo_type"] = memo_type
        if asset_code is not None:
            query_parameters["asset_code"] = asset_code
        if asset_issuer is not None:
            query_parameters["asset_issuer"] = asset_issuer

        encoded_query_string: str = urllib.parse.urlencode(query_parameters)
        return f"web+stellar:pay?{encoded_query_string}"

    def verify_webhook(
        self,
        payload: bytes | str,
        signature: str,
        public_key: str | None = None,
        secret: str | None = None,
    ) -> bool:
        """Verify that a webhook payload was properly signed."""
        payload_bytes: bytes = payload.encode("utf-8") if isinstance(payload, str) else payload

        target_public_key: str | None = (
            public_key
            or self.public_key
            or os.environ.get("STELLAR_PUBLIC_KEY")
            or os.environ.get("STELLAR_WEBHOOK_PUBLIC_KEY")
        )
        target_secret: str | None = (
            secret or self.webhook_secret or os.environ.get("STELLAR_WEBHOOK_SECRET")
        )

        cleaned_signature: str = signature.strip()

        # Handle embedded public key format, for example: "<public_key>:<signature>"
        if ":" in cleaned_signature and target_public_key is None:
            key_candidate, signature_candidate = cleaned_signature.split(":", 1)
            if key_candidate.startswith("G") and len(key_candidate) == 56:
                target_public_key = key_candidate
                cleaned_signature = signature_candidate

        # Attempt Ed25519 signature verification if public key is available
        if target_public_key is not None:
            try:
                signature_bytes: bytes
                try:
                    signature_bytes = bytes.fromhex(cleaned_signature)
                except ValueError:
                    signature_bytes = base64.b64decode(cleaned_signature)

                verifier_keypair = Keypair.from_public_key(target_public_key)
                verifier_keypair.verify(payload_bytes, signature_bytes)
                return True
            except Exception:
                pass

        # Attempt HMAC-SHA256 verification if secret is available
        if target_secret is not None:
            try:
                secret_bytes: bytes = target_secret.encode("utf-8")
                expected_digest: str = hmac.new(
                    secret_bytes, payload_bytes, hashlib.sha256
                ).hexdigest()
                if hmac.compare_digest(expected_digest, cleaned_signature):
                    return True
            except Exception:
                pass

        return False


def build_argument_parser() -> argparse.ArgumentParser:
    """Construct and return the command-line argument parser."""
    argument_parser = argparse.ArgumentParser(
        prog="stellar-paygate",
        description=(
            "Stellar Paygate command-line interface for payment verification, "
            "checkout URL creation, and webhook verification."
        ),
    )

    subparsers_action = argument_parser.add_subparsers(
        dest="subcommand",
        title="Available sub-commands",
        description="Choose an action to perform",
        required=True,
    )

    # Sub-command: verify-payment
    verify_payment_parser = subparsers_action.add_parser(
        "verify-payment",
        help="Verify an incoming Stellar payment transaction XDR against an expected amount.",
        description="Parses transaction XDR and validates the contained payment operation.",
    )
    verify_payment_parser.add_argument(
        "xdr",
        help="Base64-encoded Stellar transaction envelope XDR string to verify.",
    )
    verify_payment_parser.add_argument(
        "--amount",
        default=None,
        help="Expected payment amount and optional asset code (for example: '5 XLM' or '10.0').",
    )
    verify_payment_parser.add_argument(
        "--network-passphrase",
        default=None,
        help="Optional network passphrase override (default: Stellar Testnet).",
    )

    # Sub-command: create-url
    create_url_parser = subparsers_action.add_parser(
        "create-url",
        help="Generate a Stellar SEP-0007 payment checkout URL.",
        description="Constructs a web+stellar:pay checkout link for a destination and amount.",
    )
    create_url_parser.add_argument(
        "--dest",
        "--destination",
        required=True,
        dest="destination",
        help="Destination Stellar account public key address (G...).",
    )
    create_url_parser.add_argument(
        "--amount",
        required=True,
        help="Payment amount string (for example: '5' or '100.50').",
    )
    create_url_parser.add_argument(
        "--memo",
        default=None,
        help="Optional payment memo text to attach to the transaction.",
    )
    create_url_parser.add_argument(
        "--memo-type",
        default=None,
        help="Optional memo type (for example: MEMO_TEXT, MEMO_ID).",
    )
    create_url_parser.add_argument(
        "--asset-code",
        default=None,
        help="Optional asset code for non-native Stellar payments.",
    )
    create_url_parser.add_argument(
        "--asset-issuer",
        default=None,
        help="Optional asset issuer account public key.",
    )

    # Sub-command: verify-webhook
    verify_webhook_parser = subparsers_action.add_parser(
        "verify-webhook",
        help="Verify a webhook payload and signature received from a payment notification.",
        description="Validates cryptographic signature against the raw payload body.",
    )
    verify_webhook_parser.add_argument(
        "--payload",
        required=True,
        help="File path to the webhook payload body, or raw payload text.",
    )
    verify_webhook_parser.add_argument(
        "--signature",
        required=True,
        help="Cryptographic signature string received with the webhook.",
    )
    verify_webhook_parser.add_argument(
        "--public-key",
        default=None,
        help="Optional Stellar public key address (G...) to verify the Ed25519 signature.",
    )
    verify_webhook_parser.add_argument(
        "--secret",
        default=None,
        help="Optional shared secret key for HMAC signature verification.",
    )

    return argument_parser


def handle_verify_payment_command(
    parsed_arguments: argparse.Namespace, paygate_client: Paygate
) -> int:
    """Execute the verify-payment sub-command."""
    is_valid_payment: bool = paygate_client.verify_payment(
        xdr_string=parsed_arguments.xdr,
        expected_amount=parsed_arguments.amount,
        network_passphrase=parsed_arguments.network_passphrase,
    )

    if is_valid_payment:
        sys.stdout.write("Payment XDR verification succeeded.\n")
        return 0

    sys.stderr.write("Payment XDR verification failed.\n")
    return 1


def handle_create_url_command(parsed_arguments: argparse.Namespace, paygate_client: Paygate) -> int:
    """Execute the create-url sub-command."""
    generated_checkout_url: str = paygate_client.create_payment_url(
        destination=parsed_arguments.destination,
        amount=parsed_arguments.amount,
        memo=parsed_arguments.memo,
        memo_type=parsed_arguments.memo_type,
        asset_code=parsed_arguments.asset_code,
        asset_issuer=parsed_arguments.asset_issuer,
    )
    sys.stdout.write(f"{generated_checkout_url}\n")
    return 0


def handle_verify_webhook_command(
    parsed_arguments: argparse.Namespace, paygate_client: Paygate
) -> int:
    """Execute the verify-webhook sub-command."""
    payload_input_value: str = parsed_arguments.payload
    payload_content_bytes: bytes

    if os.path.isfile(payload_input_value):
        with open(payload_input_value, "rb") as payload_file_handle:
            payload_content_bytes = payload_file_handle.read()
    else:
        payload_content_bytes = payload_input_value.encode("utf-8")

    is_valid_webhook: bool = paygate_client.verify_webhook(
        payload=payload_content_bytes,
        signature=parsed_arguments.signature,
        public_key=parsed_arguments.public_key,
        secret=parsed_arguments.secret,
    )

    if is_valid_webhook:
        sys.stdout.write("Webhook signature verification succeeded.\n")
        return 0

    sys.stderr.write("Webhook signature verification failed.\n")
    return 1


def main(command_line_arguments: Sequence[str] | None = None) -> int:
    """Main CLI entry point for stellar-paygate."""
    argument_parser: argparse.ArgumentParser = build_argument_parser()
    parsed_arguments: argparse.Namespace = argument_parser.parse_args(command_line_arguments)

    paygate_client: Paygate = Paygate()

    if parsed_arguments.subcommand == "verify-payment":
        return handle_verify_payment_command(
            parsed_arguments=parsed_arguments, paygate_client=paygate_client
        )
    if parsed_arguments.subcommand == "create-url":
        return handle_create_url_command(
            parsed_arguments=parsed_arguments, paygate_client=paygate_client
        )
    if parsed_arguments.subcommand == "verify-webhook":
        return handle_verify_webhook_command(
            parsed_arguments=parsed_arguments, paygate_client=paygate_client
        )

    argument_parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
