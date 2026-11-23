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
    SqliteIdempotencyStore,
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
from .fastapi_ext import (
    MeteredSettlementContext,
    PayGateStreamingUptoResponse,
    add_payment_middleware,
    build_payment_required_challenge,
    require_payment,
    require_upto_ceiling,
)
from .fault_harness import (
    ConformanceScenarioResult,
    FaultInjectingSorobanRpc,
    QuorumSorobanRpcAdapter,
    SorobanRpcReaderPort,
    format_conformance_json_report,
    format_conformance_junit_report,
    format_conformance_markdown_report,
    format_conformance_sarif_report,
    run_conformance_scenarios,
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
    "SqliteIdempotencyStore",
    "SpendPolicyGuard",
    "PaygateSyncTransport",
    "PaygateAsyncTransport",
    "wrap_httpx_with_paygate",
    "decimal_to_atomic_units",
    "atomic_units_to_decimal",
    "compute_request_fingerprint",
    # FastAPI paywall extensions
    "MeteredSettlementContext",
    "PayGateStreamingUptoResponse",
    "add_payment_middleware",
    "require_payment",
    "require_upto_ceiling",
    "build_payment_required_challenge",
    # Fault harness & multi-RPC quorum adapter
    "ConformanceScenarioResult",
    "SorobanRpcReaderPort",
    "FaultInjectingSorobanRpc",
    "QuorumSorobanRpcAdapter",
    "run_conformance_scenarios",
    "format_conformance_markdown_report",
    "format_conformance_json_report",
    "format_conformance_sarif_report",
    "format_conformance_junit_report",
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
