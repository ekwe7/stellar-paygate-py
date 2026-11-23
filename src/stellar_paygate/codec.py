"""Codec utilities for stellar-paygate-py.

Base64-JSON encoder/decoder for the HTTP 402 payment protocol, plus
cryptographic helpers for:
- CAP-67 muxed address normalization and multi-event simulation scanning
  (supporting both ``SCV_MAP`` data payloads and 4-topic muxed event IDs).
- Multi-signer weighted threshold Ed25519 signature vector verification.
- Unsponsored ``TransactionEnvelope`` and ``FeeBumpTransactionEnvelope``
  verification.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from stellar_sdk import (
    FeeBumpTransactionEnvelope,
    Keypair,
    MuxedAccount,
    Network,
    TransactionBuilder,
    TransactionEnvelope,
)

from .types import (
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequired,
    SettlementResponse,
)

# ---------------------------------------------------------------------------
# Header name constants – exported for external code
# ---------------------------------------------------------------------------

PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _b64_decode(value: str) -> bytes:
    """Decode a Base64 string, raising a protocol error on failure."""
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_HEADER, f"Base64 decode failed: {exc}"
        ) from exc


def _b64_encode(data: str) -> str:
    """Encode a UTF-8 string to Base64."""
    return base64.b64encode(data.encode("utf-8")).decode("ascii")


def _json_load(data: bytes) -> Any:
    """Parse JSON bytes, ensuring the top-level object is a dict.

    Returns the parsed object or raises ``PaygateProtocolError``.
    """
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"JSON decode failed: {exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise PaygateProtocolError(PayGateErrorCode.INVALID_PAYLOAD, "JSON root must be an object")
    return obj


# ---------------------------------------------------------------------------
# Public encode / decode functions
# ---------------------------------------------------------------------------


def encode_payment_required(msg: PaymentRequired) -> str:
    """Serialize ``PaymentRequired`` to a Base64-encoded JSON string.

    The model is dumped with ``by_alias=True`` and ``exclude_none=True``.
    """
    json_str = msg.model_dump_json(by_alias=True, exclude_none=True)
    return _b64_encode(json_str)


def decode_payment_required(header: str) -> PaymentRequired:
    """Decode a ``PAYMENT-REQUIRED`` header into a ``PaymentRequired``.

    Validation errors are wrapped as ``PaygateProtocolError``.
    """
    raw = _b64_decode(header)
    obj = _json_load(raw)
    try:
        return PaymentRequired.model_validate(obj)
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"PaymentRequired validation error: {exc}"
        ) from exc


def encode_payment_signature(msg: PaymentPayload) -> str:
    """Serialize ``PaymentPayload`` for the ``PAYMENT-SIGNATURE`` header."""
    json_str = msg.model_dump_json(by_alias=True, exclude_none=True)
    return _b64_encode(json_str)


def decode_payment_signature(header: str) -> PaymentPayload:
    """Decode a ``PAYMENT-SIGNATURE`` header into ``PaymentPayload``."""
    raw = _b64_decode(header)
    obj = _json_load(raw)
    try:
        return PaymentPayload.model_validate(obj)
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"PaymentPayload validation error: {exc}"
        ) from exc


def encode_payment_response(msg: SettlementResponse) -> str:
    """Serialize ``SettlementResponse`` for the ``PAYMENT-RESPONSE`` header."""
    json_str = msg.model_dump_json(by_alias=True, exclude_none=True)
    return _b64_encode(json_str)


def decode_payment_response(header: str) -> SettlementResponse:
    """Decode a ``PAYMENT-RESPONSE`` header into ``SettlementResponse``."""
    raw = _b64_decode(header)
    obj = _json_load(raw)
    try:
        return SettlementResponse.model_validate(obj)
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"SettlementResponse validation error: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# CAP-67 Muxed Address Normalization & Multi-Event Simulation Scanner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulationEventScanResult:
    """Result of scanning Soroban simulation events for a SEP-41 transfer."""

    transferred_amount: int
    base_recipient_address: str
    muxed_destination_id: int | None
    policy_events_observed: tuple[str, ...]


@dataclass(frozen=True)
class Ed25519SignatureEntry:
    """Single Ed25519 public key and signature entry inside a multi-sig vector."""

    public_key: str | bytes
    signature: str | bytes


@dataclass(frozen=True)
class UnsponsoredEnvelopeVerificationResult:
    """Verified metadata extracted from an unsponsored Transaction or FeeBump envelope."""

    is_fee_bump: bool
    inner_source_account: str
    fee_source_account: str
    operation_count: int
    matched_amount: Decimal | None


def normalize_stellar_recipient_address(
    recipient_address: str,
    expected_muxed_id: int | None = None,
) -> tuple[str, int | None]:
    """Normalize a ``G...``, ``C...``, or CAP-67 ``M...`` address into ``(base_address, muxed_id)``.

    If ``recipient_address`` is a valid 69-character ``M...`` address decoded via
    :class:`stellar_sdk.MuxedAccount`, extracts the underlying ``G...`` address
    and 64-bit integer ``account_muxed_id``.
    """
    cleaned_address: str = recipient_address.strip()
    if not cleaned_address:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_ADDRESS,
            "Recipient address cannot be empty",
        )

    if cleaned_address.startswith("M"):
        try:
            muxed_account = MuxedAccount.from_account(cleaned_address)
            resolved_muxed_id: int | None = (
                expected_muxed_id
                if expected_muxed_id is not None
                else muxed_account.account_muxed_id
            )
            return muxed_account.account_id, resolved_muxed_id
        except Exception as exc:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_ADDRESS,
                f"Invalid CAP-67 muxed M-address '{cleaned_address}': {exc}",
            ) from exc

    if cleaned_address.startswith(("G", "C")) and len(cleaned_address) == 56:
        return cleaned_address, expected_muxed_id

    raise PaygateProtocolError(
        PayGateErrorCode.INVALID_ADDRESS,
        f"Invalid Stellar recipient address: '{cleaned_address}'",
    )


def _extract_event_amount_and_muxed_id(
    event_topics: Sequence[Any],
    event_data: Any,
) -> tuple[int, int | None]:
    """Extract ``(amount, to_muxed_id)`` from CAP-67 map data or 4-topic fallback."""
    extracted_amount: int
    extracted_muxed_id: int | None = None

    if isinstance(event_data, Mapping):
        if "amount" not in event_data:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                "Transfer event data map is missing required 'amount' key",
            )
        raw_amount = event_data["amount"]
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, str)):
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Invalid transfer event amount in data map: {raw_amount!r}",
            )
        extracted_amount = int(str(raw_amount).strip())

        raw_muxed_id = event_data.get("to_muxed_id")
        if raw_muxed_id is not None:
            if isinstance(raw_muxed_id, bool) or not isinstance(raw_muxed_id, (int, str)):
                raise PaygateProtocolError(
                    PayGateErrorCode.INVALID_PAYLOAD,
                    f"Invalid CAP-67 to_muxed_id in data map: {raw_muxed_id!r}",
                )
            extracted_muxed_id = int(str(raw_muxed_id).strip())
    elif not isinstance(event_data, bool) and isinstance(event_data, (int, str)):
        try:
            extracted_amount = int(str(event_data).strip())
        except ValueError as exc:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Transfer event data is not an integer amount: {event_data!r}",
            ) from exc
    else:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            f"Unsupported transfer event data payload: {event_data!r}",
        )

    # CAP-67 4-topic muxed address fallback (topics[3] = SCV_U64 muxed ID)
    if extracted_muxed_id is None and len(event_topics) >= 4 and event_topics[3] is not None:
        fourth_topic = event_topics[3]
        if isinstance(fourth_topic, bool) or not isinstance(fourth_topic, (int, str)):
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Invalid 4-topic CAP-67 muxed ID in topics[3]: {fourth_topic!r}",
            )
        extracted_muxed_id = int(str(fourth_topic).strip())

    if extracted_amount <= 0:
        raise PaygateProtocolError(
            PayGateErrorCode.AMOUNT_MISMATCH,
            f"Transfer event amount must be positive, got {extracted_amount}",
        )

    return extracted_amount, extracted_muxed_id


def scan_simulation_events_for_transfer(
    simulation_events: Sequence[Mapping[str, Any]],
    expected_asset_contract: str,
    expected_payer: str,
    expected_pay_to: str,
    expected_amount: int | str,
    expected_muxed_id: int | None = None,
    allow_upto_ceiling: bool = False,
) -> SimulationEventScanResult:
    """Scan Soroban simulation events for the authorized SEP-41 token transfer.

    * Ignores events with ``in_successful_contract_call == False`` or
      ``event_type != "contract"``.
    * Records non-``transfer`` smart-account policy events in
      ``policy_events_observed``.
    * Supports both CAP-67 ``SCV_MAP`` event data (``{amount, to_muxed_id}``)
      and the 4-topic muxed ID fallback (``topics[3] = to_muxed_id``).
    * Enforces the anti-drain invariant: rejects any rogue secondary ``transfer``
      event or any transfer mismatching asset, sender, recipient, muxed ID, or
      amount/ceiling.
    """
    expected_base_recipient, required_muxed_id = normalize_stellar_recipient_address(
        recipient_address=expected_pay_to,
        expected_muxed_id=expected_muxed_id,
    )
    target_amount: int = int(str(expected_amount).strip())

    policy_events_observed: list[str] = []
    matched_transfer_amount: int | None = None
    matched_muxed_id: int | None = None

    for event_entry in simulation_events:
        if not event_entry.get("in_successful_contract_call", True):
            continue

        event_type: str = str(event_entry.get("type", "contract")).lower()
        if event_type != "contract":
            continue

        emitting_contract_id: str = str(event_entry.get("contract_id", "")).strip()
        raw_topics = event_entry.get("topics", ())
        if not isinstance(raw_topics, Sequence) or isinstance(raw_topics, (str, bytes)):
            continue
        if len(raw_topics) == 0:
            continue

        event_name_topic: str = str(raw_topics[0]).strip()
        if event_name_topic != "transfer":
            policy_events_observed.append(f"{emitting_contract_id}:{event_name_topic}")
            continue

        # Anti-drain check: only 1 transfer event is permitted
        if matched_transfer_amount is not None:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                "Unexpected secondary token transfer event detected during simulation",
            )

        if emitting_contract_id != expected_asset_contract.strip():
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Transfer event emitted by unexpected token contract '{emitting_contract_id}'",
            )

        if len(raw_topics) < 3:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                "SEP-41 transfer event requires at least 3 topics: ['transfer', from, to]",
            )

        event_sender: str = str(raw_topics[1]).strip()
        event_recipient: str = str(raw_topics[2]).strip()

        if event_sender != expected_payer.strip():
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_ADDRESS,
                f"Transfer event sender '{event_sender}' does not match payer '{expected_payer}'",
            )

        if event_recipient != expected_base_recipient:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_ADDRESS,
                f"Transfer event recipient '{event_recipient}' does not match "
                f"expected destination '{expected_base_recipient}'",
            )

        event_amount, event_muxed_id = _extract_event_amount_and_muxed_id(
            event_topics=raw_topics,
            event_data=event_entry.get("data"),
        )

        if required_muxed_id is not None and event_muxed_id != required_muxed_id:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_ADDRESS,
                f"CAP-67 muxed destination ID mismatch: expected {required_muxed_id}, "
                f"observed {event_muxed_id}",
            )

        if allow_upto_ceiling:
            if event_amount > target_amount:
                raise PaygateProtocolError(
                    PayGateErrorCode.EXCEEDS_MAX_UPTO,
                    f"Simulated transfer {event_amount} exceeds upto ceiling {target_amount}",
                )
        elif event_amount != target_amount:
            raise PaygateProtocolError(
                PayGateErrorCode.AMOUNT_MISMATCH,
                f"Simulated transfer amount {event_amount} does not match expected {target_amount}",
            )

        matched_transfer_amount = event_amount
        matched_muxed_id = event_muxed_id

    if matched_transfer_amount is None:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            "Simulation produced zero matching SEP-41 transfer events",
        )

    return SimulationEventScanResult(
        transferred_amount=matched_transfer_amount,
        base_recipient_address=expected_base_recipient,
        muxed_destination_id=matched_muxed_id,
        policy_events_observed=tuple(policy_events_observed),
    )


# ---------------------------------------------------------------------------
# Multi-Signer Weighted Threshold Ed25519 Signature Vector Verification
# ---------------------------------------------------------------------------


def _resolve_keypair_and_raw_public_key(public_key_input: str | bytes) -> tuple[Keypair, bytes]:
    """Convert a G-address, hex string, or 32-byte key into ``(Keypair, raw_32_bytes)``."""
    try:
        if isinstance(public_key_input, bytes):
            if len(public_key_input) != 32:
                raise ValueError("Raw public key must be exactly 32 bytes")
            keypair = Keypair.from_raw_ed25519_public_key(public_key_input)
            return keypair, public_key_input

        cleaned_key: str = public_key_input.strip()
        if cleaned_key.startswith("G"):
            keypair = Keypair.from_public_key(cleaned_key)
            return keypair, keypair.raw_public_key()

        raw_key_bytes = bytes.fromhex(cleaned_key)
        if len(raw_key_bytes) != 32:
            raise ValueError("Hex public key must decode to 32 bytes")
        keypair = Keypair.from_raw_ed25519_public_key(raw_key_bytes)
        return keypair, raw_key_bytes
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_ADDRESS,
            f"Invalid Ed25519 signer public key: {exc}",
        ) from exc


def _resolve_signature_bytes(signature_input: str | bytes) -> bytes:
    """Convert a 64-byte raw signature, hex string, or Base64 string into raw bytes."""
    if isinstance(signature_input, bytes):
        if len(signature_input) != 64:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Ed25519 signature must be 64 bytes, got {len(signature_input)}",
            )
        return signature_input

    cleaned_sig: str = signature_input.strip()
    try:
        decoded_bytes = bytes.fromhex(cleaned_sig)
    except ValueError:
        try:
            decoded_bytes = base64.b64decode(cleaned_sig.encode("ascii"), validate=True)
        except Exception as exc:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Signature is neither valid hex nor Base64: {exc}",
            ) from exc

    if len(decoded_bytes) != 64:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            f"Decoded Ed25519 signature must be 64 bytes, got {len(decoded_bytes)}",
        )
    return decoded_bytes


def verify_multisig_ed25519_signatures(
    preimage_bytes: bytes,
    signature_entries: Sequence[Ed25519SignatureEntry | Mapping[str, str | bytes]],
) -> int:
    """Verify $N \\ge 1$ Ed25519 signatures over a Soroban authorization preimage.

    Enforces:
    1. Non-empty signature vector ($N \\ge 1$).
    2. Strict ascending 32-byte raw public-key order across all signers.
    3. Zero duplicate public keys.
    4. Cryptographic Ed25519 signature validity for every entry against ``preimage_bytes``.

    Returns the number of verified signers.
    """
    if not signature_entries:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            "SorobanAuthorizationEntry signature vector cannot be empty",
        )

    previous_raw_public_key: bytes | None = None

    for entry in signature_entries:
        if isinstance(entry, Ed25519SignatureEntry):
            public_key_val = entry.public_key
            signature_val = entry.signature
        elif isinstance(entry, Mapping):
            if "public_key" not in entry or "signature" not in entry:
                raise PaygateProtocolError(
                    PayGateErrorCode.INVALID_PAYLOAD,
                    "Signature entry mapping must contain 'public_key' and 'signature'",
                )
            public_key_val = entry["public_key"]
            signature_val = entry["signature"]
        else:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Invalid signature entry type: {type(entry).__name__}",
            )

        verifier_keypair, current_raw_public_key = _resolve_keypair_and_raw_public_key(
            public_key_val
        )
        signature_bytes = _resolve_signature_bytes(signature_val)

        if previous_raw_public_key is not None:
            if current_raw_public_key == previous_raw_public_key:
                raise PaygateProtocolError(
                    PayGateErrorCode.INVALID_PAYLOAD,
                    f"Duplicate signer public key detected: {verifier_keypair.public_key}",
                )
            if current_raw_public_key < previous_raw_public_key:
                raise PaygateProtocolError(
                    PayGateErrorCode.INVALID_PAYLOAD,
                    "Multi-signer public keys must be sorted in strict ascending 32-byte order",
                )

        try:
            verifier_keypair.verify(preimage_bytes, signature_bytes)
        except Exception as exc:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                f"Ed25519 signature verification failed for signer {verifier_keypair.public_key}",
            ) from exc

        previous_raw_public_key = current_raw_public_key

    return len(signature_entries)


# ---------------------------------------------------------------------------
# Unsponsored TransactionEnvelope & FeeBumpTransactionEnvelope Verification
# ---------------------------------------------------------------------------


def _verify_envelope_signature_for_account(
    envelope: TransactionEnvelope | FeeBumpTransactionEnvelope,
    account_id: str,
) -> None:
    """Verify ``envelope.signatures`` contains a valid Ed25519 signature from ``account_id``."""
    if not envelope.signatures:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            f"Transaction envelope contains no signatures for account {account_id}",
        )

    signer_keypair = Keypair.from_public_key(account_id)
    envelope_hash: bytes = envelope.hash()

    for decorated_signature in envelope.signatures:
        try:
            signer_keypair.verify(envelope_hash, decorated_signature.signature)
            return
        except Exception:
            continue

    raise PaygateProtocolError(
        PayGateErrorCode.INVALID_PAYLOAD,
        f"Missing or invalid Ed25519 signature for account {account_id}",
    )


def verify_unsponsored_transaction_envelope(
    xdr_string: str,
    network_passphrase: str = Network.TESTNET_NETWORK_PASSPHRASE,
    expected_amount: str | Decimal | int | None = None,
    expected_destination: str | None = None,
) -> UnsponsoredEnvelopeVerificationResult:
    """Verify an unsponsored ``TransactionEnvelope`` or ``FeeBumpTransactionEnvelope`` XDR.

    Supports both standard client-paid transactions and ``FeeBumpTransactionEnvelope``
    wrappers (Drips Wave Issue #5):
    * Unwraps ``FeeBumpTransactionEnvelope`` to inspect the inner ``TransactionEnvelope``
      while verifying both the outer fee-source signature and the inner transaction
      source signature.
    * Validates that the inner transaction contains at least one operation and
      matches ``expected_amount`` / ``expected_destination`` when specified.
    """
    try:
        parsed_envelope = TransactionBuilder.from_xdr(
            xdr=xdr_string.strip(),
            network_passphrase=network_passphrase,
        )
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            f"Failed to parse transaction envelope XDR: {exc}",
        ) from exc

    is_fee_bump: bool = isinstance(parsed_envelope, FeeBumpTransactionEnvelope)
    inner_envelope: TransactionEnvelope
    fee_source_account_id: str

    if isinstance(parsed_envelope, FeeBumpTransactionEnvelope):
        fee_source_account_id = parsed_envelope.transaction.fee_source.account_id
        _verify_envelope_signature_for_account(parsed_envelope, fee_source_account_id)
        inner_envelope = parsed_envelope.transaction.inner_transaction_envelope
    else:
        inner_envelope = parsed_envelope
        fee_source_account_id = inner_envelope.transaction.source.account_id

    inner_source_account_id: str = inner_envelope.transaction.source.account_id
    _verify_envelope_signature_for_account(inner_envelope, inner_source_account_id)

    operations = inner_envelope.transaction.operations
    if not operations:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD,
            "Unsponsored transaction envelope contains zero operations",
        )

    target_decimal_amount: Decimal | None = (
        Decimal(str(expected_amount).strip().split()[0]) if expected_amount is not None else None
    )
    matched_amount: Decimal | None = None

    for operation in operations:
        op_amount_attr = getattr(operation, "amount", None)
        if op_amount_attr is None:
            continue

        op_decimal_amount = Decimal(str(op_amount_attr))
        if target_decimal_amount is not None and op_decimal_amount != target_decimal_amount:
            continue

        if expected_destination is not None:
            op_destination = getattr(operation, "destination", None)
            op_dest_account_id = (
                getattr(op_destination, "account_id", str(op_destination))
                if op_destination is not None
                else ""
            )
            expected_base_dest, _mux = normalize_stellar_recipient_address(expected_destination)
            if op_dest_account_id != expected_base_dest:
                continue

        matched_amount = op_decimal_amount
        break

    if (expected_amount is not None or expected_destination is not None) and matched_amount is None:
        raise PaygateProtocolError(
            PayGateErrorCode.AMOUNT_MISMATCH,
            "Inner transaction operations did not match expected payment amount or destination",
        )

    return UnsponsoredEnvelopeVerificationResult(
        is_fee_bump=is_fee_bump,
        inner_source_account=inner_source_account_id,
        fee_source_account=fee_source_account_id,
        operation_count=len(operations),
        matched_amount=matched_amount,
    )


__all__ = [
    "PAYMENT_REQUIRED_HEADER",
    "PAYMENT_SIGNATURE_HEADER",
    "PAYMENT_RESPONSE_HEADER",
    "SimulationEventScanResult",
    "Ed25519SignatureEntry",
    "UnsponsoredEnvelopeVerificationResult",
    "encode_payment_required",
    "decode_payment_required",
    "encode_payment_signature",
    "decode_payment_signature",
    "encode_payment_response",
    "decode_payment_response",
    "normalize_stellar_recipient_address",
    "scan_simulation_events_for_transfer",
    "verify_multisig_ed25519_signatures",
    "verify_unsponsored_transaction_envelope",
]
