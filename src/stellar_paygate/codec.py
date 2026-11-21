"""Codec utilities for stellar-paygate-py

Base64‑JSON encoder and decoder for the HTTP 402 payment protocol.
All functions operate on the core Pydantic models defined in
``stellar_paygate.types``. Errors are raised as ``PaygateProtocolError``
with a specific :class:`~stellar_paygate.types.PayGateErrorCode`.
"""

from __future__ import annotations

import base64
import json
from typing import Any

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
    """Encode a UTF‑8 string to Base64."""
    return base64.b64encode(data.encode("utf-8")).decode("ascii")

def _json_load(data: bytes) -> Any:
    """Parse JSON bytes, ensuring the top‑level object is a dict.

    Returns the parsed object or raises ``PaygateProtocolError``.
    """
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"JSON decode failed: {exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, "JSON root must be an object"
        )
    return obj

# ---------------------------------------------------------------------------
# Public encode / decode functions
# ---------------------------------------------------------------------------

def encode_payment_required(msg: PaymentRequired) -> str:
    """Serialize ``PaymentRequired`` to a Base64‑encoded JSON string.

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
    """Serialize ``PaymentPayload`` for the ``PAYMENT‑SIGNATURE`` header."""
    json_str = msg.model_dump_json(by_alias=True, exclude_none=True)
    return _b64_encode(json_str)


def decode_payment_signature(header: str) -> PaymentPayload:
    """Decode a ``PAYMENT‑SIGNATURE`` header into ``PaymentPayload``."""
    raw = _b64_decode(header)
    obj = _json_load(raw)
    try:
        return PaymentPayload.model_validate(obj)
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"PaymentPayload validation error: {exc}"
        ) from exc


def encode_payment_response(msg: SettlementResponse) -> str:
    """Serialize ``SettlementResponse`` for the ``PAYMENT‑RESPONSE`` header."""
    json_str = msg.model_dump_json(by_alias=True, exclude_none=True)
    return _b64_encode(json_str)


def decode_payment_response(header: str) -> SettlementResponse:
    """Decode a ``PAYMENT‑RESPONSE`` header into ``SettlementResponse``."""
    raw = _b64_decode(header)
    obj = _json_load(raw)
    try:
        return SettlementResponse.model_validate(obj)
    except Exception as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.INVALID_PAYLOAD, f"SettlementResponse validation error: {exc}"
        ) from exc
