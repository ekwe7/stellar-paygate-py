"""Baseline test suite for core schemas and codec.

These tests verify that the Pydantic models can be serialized to the wire format
via the Base64‑JSON helpers in :mod:`stellar_paygate.codec` and that malformed
inputs raise the appropriate :class:`stellar_paygate.types.PaygateProtocolError`.
"""

from __future__ import annotations

import base64
import json
import pytest

from stellar_paygate import (
    encode_payment_required,
    decode_payment_required,
    encode_payment_signature,
    decode_payment_signature,
    encode_payment_response,
    decode_payment_response,
    PayGateErrorCode,
    PaygateProtocolError,
    ResourceInfo,
    StellarExtraConfig,
    PaymentRequirement,
    PaymentRequired,
    StellarPaymentPayloadData,
    PaymentPayload,
    SettlementResponse,
)

# ---------------------------------------------------------------------------
# Helper factories – create minimal valid objects for each model
# ---------------------------------------------------------------------------

def make_resource() -> ResourceInfo:
    return ResourceInfo(url="https://api.example.com", description="Test resource")


def make_payment_requirement() -> PaymentRequirement:
    return PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount="1000",
        asset="CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA",
        pay_to="GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
    )


def make_payment_required() -> PaymentRequired:
    return PaymentRequired(resource=make_resource(), accepts=[make_payment_requirement()])


def make_payment_payload() -> PaymentPayload:
    requirement = make_payment_requirement()
    payload_data = StellarPaymentPayloadData(
        payer="GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
        network="stellar:testnet",
        ledger=123456,
        expiration_ledger=123500,
        fee_mode="sponsored",
    )
    return PaymentPayload(requirement=requirement, payload=payload_data)


def make_settlement_response() -> SettlementResponse:
    return SettlementResponse(
        success=True,
        scheme="exact",
        network="stellar:testnet",
        payer="GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
        transaction_hash="DEADBEEF",
        settled_amount="1000",
    )

# ---------------------------------------------------------------------------
# Positive round‑trip tests
# ---------------------------------------------------------------------------

def test_payment_required_roundtrip() -> None:
    original = make_payment_required()
    encoded = encode_payment_required(original)
    assert isinstance(encoded, str)
    decoded = decode_payment_required(encoded)
    assert decoded == original


def test_payment_payload_roundtrip() -> None:
    original = make_payment_payload()
    encoded = encode_payment_signature(original)
    assert isinstance(encoded, str)
    decoded = decode_payment_signature(encoded)
    assert decoded == original


def test_settlement_response_roundtrip() -> None:
    original = make_settlement_response()
    encoded = encode_payment_response(original)
    assert isinstance(encoded, str)
    decoded = decode_payment_response(encoded)
    assert decoded == original

# ---------------------------------------------------------------------------
# Negative tests – malformed inputs
# ---------------------------------------------------------------------------

def test_decode_malformed_base64_raises() -> None:
    with pytest.raises(PaygateProtocolError) as exc:
        decode_payment_required("!!not_base64!!")
    assert exc.value.code == PayGateErrorCode.INVALID_HEADER


def test_decode_non_json_raises() -> None:
    # Encode a plain string that is valid Base64 but not JSON
    bad_json = base64.b64encode(b"not a json").decode("ascii")
    with pytest.raises(PaygateProtocolError) as exc:
        decode_payment_required(bad_json)
    assert exc.value.code == PayGateErrorCode.INVALID_PAYLOAD


def test_decode_missing_field_raises() -> None:
    # Build a dict missing the required 'resource' field for PaymentRequired
    minimal = {"accepts": []}
    payload = base64.b64encode(json.dumps(minimal).encode("utf-8")).decode("ascii")
    with pytest.raises(PaygateProtocolError) as exc:
        decode_payment_required(payload)
    # Pydantic validation error is wrapped as INVALID_PAYLOAD
    assert exc.value.code == PayGateErrorCode.INVALID_PAYLOAD
