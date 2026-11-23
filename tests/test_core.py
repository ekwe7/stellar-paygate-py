"""Baseline test suite for core schemas, public exports, and Base64-JSON codec.

Verifies that all public symbols are exported in ``stellar_paygate.__init__``,
that Pydantic v2 models round-trip cleanly through :mod:`stellar_paygate.codec`,
and that malformed Base64, non-JSON, non-dict JSON roots, and missing required
fields raise :class:`stellar_paygate.types.PaygateProtocolError` with the exact
:class:`stellar_paygate.types.PayGateErrorCode` (30+ assertions).
"""

from __future__ import annotations

import base64
import json

import pytest

import stellar_paygate
from stellar_paygate import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    FaultInjectionConfig,
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirement,
    ResourceInfo,
    Scheme,
    SettlementResponse,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    decode_payment_required,
    decode_payment_response,
    decode_payment_signature,
    encode_payment_required,
    encode_payment_response,
    encode_payment_signature,
)

# ---------------------------------------------------------------------------
# Helper factories – create minimal valid objects for each model
# ---------------------------------------------------------------------------


def make_resource() -> ResourceInfo:
    """Create a sample ResourceInfo instance."""
    return ResourceInfo(
        url="https://api.example.com",
        description="Test resource",
        mime_type="application/json",
    )


def make_payment_requirement() -> PaymentRequirement:
    """Create a sample PaymentRequirement instance."""
    return PaymentRequirement(
        scheme="exact",
        network="stellar:testnet",
        amount="1000",
        asset="CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA",
        pay_to="GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
    )


def make_payment_required() -> PaymentRequired:
    """Create a sample PaymentRequired challenge instance."""
    return PaymentRequired(resource=make_resource(), accepts=[make_payment_requirement()])


def make_payment_payload() -> PaymentPayload:
    """Create a sample PaymentPayload signature instance."""
    requirement = make_payment_requirement()
    payload_data = StellarPaymentPayloadData(
        payer="GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
        network="stellar:testnet",
        ledger=123456,
        expiration_ledger=123500,
        fee_mode=FeeMode.SPONSORED,
    )
    return PaymentPayload(requirement=requirement, payload=payload_data)


def make_settlement_response() -> SettlementResponse:
    """Create a sample SettlementResponse receipt instance."""
    return SettlementResponse(
        success=True,
        scheme="exact",
        network="stellar:testnet",
        payer="GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF",
        transaction_hash="DEADBEEF",
        settled_amount="1000",
    )


# ---------------------------------------------------------------------------
# Public symbol export & naming consistency tests
# ---------------------------------------------------------------------------


def test_public_symbols_exported_in_package_init() -> None:
    """Verify all Day 1 required public symbols are exported in stellar_paygate.__all__."""
    required_public_symbols = [
        "Scheme",
        "FeeMode",
        "PayGateErrorCode",
        "PaygateProtocolError",
        "ResourceInfo",
        "StellarExtraConfig",
        "PaymentRequirement",
        "PaymentRequired",
        "StellarPaymentPayloadData",
        "PaymentPayload",
        "SettlementResponse",
        "FaultInjectionConfig",
        "PAYMENT_REQUIRED_HEADER",
        "PAYMENT_SIGNATURE_HEADER",
        "PAYMENT_RESPONSE_HEADER",
        "encode_payment_required",
        "decode_payment_required",
        "encode_payment_signature",
        "decode_payment_signature",
        "encode_payment_response",
        "decode_payment_response",
    ]
    for symbol_name in required_public_symbols:
        assert symbol_name in stellar_paygate.__all__
        assert hasattr(stellar_paygate, symbol_name)

    assert Scheme.EXACT.value == "exact"
    assert Scheme.UPTO.value == "upto"
    assert FeeMode.SPONSORED.value == "sponsored"
    assert FeeMode.UNSPONSORED.value == "unsponsored"
    assert PAYMENT_REQUIRED_HEADER == "PAYMENT-REQUIRED"
    assert PAYMENT_SIGNATURE_HEADER == "PAYMENT-SIGNATURE"
    assert PAYMENT_RESPONSE_HEADER == "PAYMENT-RESPONSE"
    assert StellarExtraConfig().are_fees_sponsored is True
    assert FaultInjectionConfig().ledger_skew == 0


# ---------------------------------------------------------------------------
# Positive round‑trip tests
# ---------------------------------------------------------------------------


def test_payment_required_roundtrip() -> None:
    """Verify PaymentRequired encodes to Base64 JSON and decodes back identically."""
    original = make_payment_required()
    encoded = encode_payment_required(original)
    assert isinstance(encoded, str)
    decoded = decode_payment_required(encoded)
    assert decoded == original
    assert decoded.x402_version == 2
    assert decoded.resource.url == "https://api.example.com"


def test_payment_payload_roundtrip() -> None:
    """Verify PaymentPayload encodes to Base64 JSON and decodes back identically."""
    original = make_payment_payload()
    encoded = encode_payment_signature(original)
    assert isinstance(encoded, str)
    decoded = decode_payment_signature(encoded)
    assert decoded == original
    assert decoded.payload.fee_mode == FeeMode.SPONSORED
    assert decoded.requirement.amount == "1000"


def test_settlement_response_roundtrip() -> None:
    """Verify SettlementResponse encodes to Base64 JSON and decodes back identically."""
    original = make_settlement_response()
    encoded = encode_payment_response(original)
    assert isinstance(encoded, str)
    decoded = decode_payment_response(encoded)
    assert decoded == original
    assert decoded.success is True
    assert decoded.transaction_hash == "DEADBEEF"


# ---------------------------------------------------------------------------
# Negative tests – malformed inputs across all three header decoders
# ---------------------------------------------------------------------------


def test_decode_malformed_base64_raises() -> None:
    """Verify malformed Base64 raises PaygateProtocolError(INVALID_HEADER) on all decoders."""
    for decoder_function in (
        decode_payment_required,
        decode_payment_signature,
        decode_payment_response,
    ):
        with pytest.raises(PaygateProtocolError) as exc:
            decoder_function("!!not_base64!!")
        assert exc.value.code == PayGateErrorCode.INVALID_HEADER
        assert "Base64 decode failed" in str(exc.value)


def test_decode_non_json_raises() -> None:
    """Verify non-JSON Base64 raises PaygateProtocolError(INVALID_PAYLOAD) on all decoders."""
    bad_json = base64.b64encode(b"not a json").decode("ascii")
    for decoder_function in (
        decode_payment_required,
        decode_payment_signature,
        decode_payment_response,
    ):
        with pytest.raises(PaygateProtocolError) as exc:
            decoder_function(bad_json)
        assert exc.value.code == PayGateErrorCode.INVALID_PAYLOAD


def test_decode_non_object_json_root_raises() -> None:
    """Verify JSON arrays or primitives at root raise PaygateProtocolError(INVALID_PAYLOAD)."""
    json_array_b64 = base64.b64encode(b"[1, 2, 3]").decode("ascii")
    for decoder_function in (
        decode_payment_required,
        decode_payment_signature,
        decode_payment_response,
    ):
        with pytest.raises(PaygateProtocolError) as exc:
            decoder_function(json_array_b64)
        assert exc.value.code == PayGateErrorCode.INVALID_PAYLOAD
        assert exc.value.detail == "JSON root must be an object"


def test_decode_missing_field_raises() -> None:
    """Verify JSON objects missing required Pydantic fields raise INVALID_PAYLOAD."""
    minimal_object: dict[str, list[object]] = {"accepts": []}
    payload = base64.b64encode(json.dumps(minimal_object).encode("utf-8")).decode("ascii")

    for decoder_function in (
        decode_payment_required,
        decode_payment_signature,
        decode_payment_response,
    ):
        with pytest.raises(PaygateProtocolError) as exc:
            decoder_function(payload)
        assert exc.value.code == PayGateErrorCode.INVALID_PAYLOAD
