"""Core domain models for stellar-paygate-py.

This module defines the enums, error class, and Pydantic v2 models that represent
the *protocol* objects exchanged via HTTP 402 headers.  No web‑framework or
network imports are used – the module lives purely in the *Core* layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Optional, List

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums & error taxonomy
# ---------------------------------------------------------------------------

class Scheme(StrEnum):
    """Supported payment schemes.

    * ``exact`` – payer must send exactly the amount advertised.
    * ``upto`` – payer may send any amount up to the advertised ceiling.
    """

    EXACT = "exact"
    UPTO = "upto"


class FeeMode(StrEnum):
    """Transaction fee sponsorship mode.

    * ``sponsored`` – the server pays the transaction fee.
    * ``unsponsored`` – the payer covers the fee.
    """

    SPONSORED = "sponsored"
    UNSPONSORED = "unsponsored"


class PayGateErrorCode(StrEnum):
    """Machine‑readable error identifiers used in protocol messages.

    The list mirrors the taxonomy used throughout the project and is kept
    deliberately short for the core layer – additional codes can be added as
    the protocol evolves.
    """

    # Structural / payload errors
    INVALID_X402_VERSION = "invalid_x402_version"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    UNSUPPORTED_NETWORK = "unsupported_network"
    INVALID_HEADER = "invalid_header"
    INVALID_PAYLOAD = "invalid_payload"
    INVALID_ADDRESS = "invalid_address"
    EXPIRED_AUTHORIZATION = "expired_authorization"
    CLOCK_SKEW_EXCEEDED = "clock_skew_exceeded"
    AMOUNT_MISMATCH = "amount_mismatch"
    EXCEEDS_MAX_UPTO = "exceeds_max_upto"
    SETTLEMENT_FAILURE = "settlement_failure"


class PaygateProtocolError(Exception):
    """Exception that carries a :class:`PayGateErrorCode` and an optional detail.

    It is raised by the codec helpers when a header cannot be decoded or when a
    model fails validation.
    """

    def __init__(self, code: PayGateErrorCode, detail: str | None = None) -> None:
        message = f"[{code.value}] {detail}" if detail else f"[{code.value}]"
        super().__init__(message)
        self.code = code
        self.detail = detail

# ---------------------------------------------------------------------------
# Core Pydantic models
# ---------------------------------------------------------------------------

class ResourceInfo(BaseModel):
    """Metadata about the protected HTTP resource."""

    model_config = ConfigDict(populate_by_name=True)

    url: str
    description: Optional[str] = None
    mime_type: Optional[str] = Field(default=None, alias="mimeType")


class StellarExtraConfig(BaseModel):
    """Stellar‑specific configuration that lives inside ``PaymentRequirement``.
    """

    model_config = ConfigDict(populate_by_name=True)

    are_fees_sponsored: bool = Field(default=True, alias="areFeesSponsored")
    upto_contract: Optional[str] = Field(default=None, alias="uptoContract")
    upto_profile: Optional[str] = Field(default=None, alias="uptoProfile")
    max_transaction_fee_stroops: Optional[int] = Field(
        default=None, alias="maxTransactionFeeStroops"
    )


class PaymentRequirement(BaseModel):
    """A single payment option advertised in a ``PAYMENT-REQUIRED`` challenge.
    """

    model_config = ConfigDict(populate_by_name=True)

    scheme: Literal["exact", "upto"] = "exact"
    network: str
    amount: str = Field(..., description="Atomic integer units (e.g., stroops)")
    asset: str = Field(..., description="SEP‑41 contract C‑address")
    pay_to: str = Field(..., alias="payTo", description="Destination G‑, C‑ or M‑address")
    max_timeout_seconds: int = Field(
        default=60,
        alias="maxTimeoutSeconds",
        ge=5,
        le=600,
    )
    extra: StellarExtraConfig = Field(default_factory=StellarExtraConfig)


class PaymentRequired(BaseModel):
    """Wire representation of the HTTP 402 ``PAYMENT-REQUIRED`` header.
    """

    model_config = ConfigDict(populate_by_name=True)

    x402_version: int = Field(default=2, alias="x402Version")
    error: Optional[str] = None
    resource: ResourceInfo
    accepts: List[PaymentRequirement]


class StellarPaymentPayloadData(BaseModel):
    """Inner payload carried inside the ``PAYMENT-SIGNATURE`` header.
    """

    model_config = ConfigDict(populate_by_name=True)

    payer: str  # G‑, C‑ or M‑address
    network: str
    ledger: int = Field(..., alias="ledger")
    expiration_ledger: int = Field(..., alias="expirationLedger")
    fee_mode: FeeMode = Field(..., alias="feeMode")
    valid_after_ledger: Optional[int] = Field(default=None, alias="validAfterLedger")
    signature_expiration_ledger: Optional[int] = Field(
        default=None, alias="signatureExpirationLedger"
    )


class PaymentPayload(BaseModel):
    """Top‑level wrapper that adds protocol version information.
    """

    model_config = ConfigDict(populate_by_name=True)

    x402_version: int = Field(default=2, alias="x402Version")
    requirement: PaymentRequirement
    payload: StellarPaymentPayloadData


class VerifyResponse(BaseModel):
    """Result of verification (pre‑settlement) returned via ``PAYMENT-RESPONSE`` when ``success`` is ``false``.
    """

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    scheme: Literal["exact", "upto"]
    network: str
    payer: str
    error_reason: Optional[str] = Field(default=None, alias="errorReason")
    error_code: Optional[PayGateErrorCode] = Field(default=None, alias="errorCode")


class SettleResponse(BaseModel):
    """Result of a successful settlement (``success`` is ``true``).
    """

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    scheme: Literal["exact", "upto"]
    network: str
    payer: str
    transaction_hash: Optional[str] = Field(default=None, alias="transactionHash")
    settled_amount: Optional[str] = Field(default=None, alias="settledAmount")
    error_reason: Optional[str] = Field(default=None, alias="errorReason")
    error_code: Optional[PayGateErrorCode] = Field(default=None, alias="errorCode")
    """Result returned in the ``PAYMENT-RESPONSE`` header after verification.
    """

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    scheme: Literal["exact", "upto"]
    network: str
    payer: str
    transaction_hash: Optional[str] = Field(default=None, alias="transactionHash")
    settled_amount: Optional[str] = Field(default=None, alias="settledAmount")
    error_reason: Optional[str] = Field(default=None, alias="errorReason")
    error_code: Optional[PayGateErrorCode] = Field(default=None, alias="errorCode")


@dataclass
class FaultInjectionConfig:
    """Deterministic knobs for offline fault injection in tests.

    The fields are deliberately simple – they are consumed by the simulation
    helpers in ``fault_harness.py``.
    """

    ledger_skew: int = 0
    allow_unsponsored_fees: bool = True
    force_settlement_failure: bool = False
    override_settled_amount: Optional[int] = None
    duplicate_delivery_count: int = 0
