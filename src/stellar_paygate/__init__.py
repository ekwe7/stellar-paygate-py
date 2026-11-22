"""stellar_paygate package initialization

Expose core protocol symbols for convenient imports.
"""

from __future__ import annotations

# Re‑export submodules for backwards compatibility
from . import client, codec, exact, fastapi_ext, fault_harness, types
from .client import (
    PaygateAsyncTransport,
    PaygateSyncTransport,
    SpendPolicyGuard,
    atomic_units_to_decimal,
    compute_request_fingerprint,
    decimal_to_atomic_units,
    wrap_httpx_with_paygate,
)
from .codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    decode_payment_required,
    decode_payment_response,
    decode_payment_signature,
    encode_payment_required,
    encode_payment_response,
    encode_payment_signature,
)
from .exact import (
    ExactVerifier,
    UptoVerifier,
    VerificationPort,
    VerifierConfig,
    calculate_padded_resource_fee_stroops,
    compute_payload_fingerprint,
    get_verifier_for_scheme,
    is_valid_stellar_address,
    settle_payload,
    verify_payload,
)
from .types import (
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
    SettleResponse,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    VerifyResponse,
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
    "VerifyResponse",
    "SettleResponse",
    "SettlementResponse",
    "FaultInjectionConfig",
    # Verification & settlement engine
    "VerifierConfig",
    "VerificationPort",
    "ExactVerifier",
    "UptoVerifier",
    "is_valid_stellar_address",
    "calculate_padded_resource_fee_stroops",
    "compute_payload_fingerprint",
    "get_verifier_for_scheme",
    "verify_payload",
    "settle_payload",
    # Client policy guard & HTTPX transports
    "SpendPolicyGuard",
    "PaygateSyncTransport",
    "PaygateAsyncTransport",
    "wrap_httpx_with_paygate",
    "decimal_to_atomic_units",
    "atomic_units_to_decimal",
    "compute_request_fingerprint",
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
