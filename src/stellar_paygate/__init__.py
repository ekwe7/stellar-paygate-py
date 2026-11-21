# -*- coding: utf-8 -*-
"""stellar_paygate package initialization

Expose core protocol symbols for convenient imports.
"""

from __future__ import annotations

# Re‑export submodules for backwards compatibility
from . import client, codec, exact, fastapi_ext, fault_harness, types

from .codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    PAYMENT_RESPONSE_HEADER,
    decode_payment_required,
    decode_payment_response,
    decode_payment_signature,
    encode_payment_required,
    encode_payment_response,
    encode_payment_signature,
)

from .types import (
    FaultInjectionConfig,
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequirement,
    PaymentRequired,
    ResourceInfo,
    SettlementResponse,
    Scheme,
    StellarExtraConfig,
    StellarPaymentPayloadData,
)

__all__ = [
    # Core symbols
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
    # Codec helpers
    "PAYMENT_REQUIRED_HEADER",
    "PAYMENT_SIGNATURE_HEADER",
    "PAYMENT_RESPONSE_HEADER",
    "encode_payment_required",
    "decode_payment_required",
    "encode_payment_signature",
    "decode_payment_signature",
    "encode_payment_response",
    "decode_payment_response",
    # Submodules (legacy)
    "types",
    "codec",
    "exact",
    "client",
    "fastapi_ext",
    "fault_harness",
]
