"""Verification and idempotent settlement engine for stellar-paygate-py.

Implements ``VerificationPort``, ``VerifierConfig``, ``ExactVerifier``, and
``UptoVerifier`` inside the pure domain core layer. This module enforces
protocol version checks, network compatibility, classic/smart/muxed Stellar
address validation, ledger expiration windows, clock-skew tolerance,
fee sponsorship rules, refundable fee safety margins, and idempotent
settlement replay protection with zero web-framework or HTTP dependencies.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import string
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .types import (
    FeeMode,
    PayGateErrorCode,
    PaymentPayload,
    Scheme,
    SettleResponse,
    VerifyResponse,
)

SUPPORTED_STELLAR_NETWORKS: frozenset[str] = frozenset(
    {
        "stellar:testnet",
        "stellar:pubnet",
    }
)

STELLAR_BASE32_ALPHABET: frozenset[str] = frozenset(string.ascii_uppercase + "234567")
CLASSIC_AND_CONTRACT_ADDRESS_LENGTH: int = 56
MUXED_ACCOUNT_ADDRESS_LENGTH: int = 69
SECONDS_PER_STELLAR_LEDGER: int = 5
BASIS_POINTS_DIVISOR: int = 10_000


@dataclass(frozen=True)
class VerifierConfig:
    """Configuration parameters for Stellar payment verification and settlement."""

    skew_tolerance_ledgers: int = 5
    allow_unsponsored_fees: bool = True
    refundable_fee_margin_basis_points: int = 1500
    classic_account_fee_ceiling_stroops: int = 50_000
    smart_account_fee_ceiling_stroops: int = 250_000
    supported_networks: frozenset[str] = field(default_factory=lambda: SUPPORTED_STELLAR_NETWORKS)
    force_settlement_failure: bool = False


@runtime_checkable
class VerificationPort(Protocol):
    """Hexagonal port interface for payment verification and settlement engines."""

    def verify(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> VerifyResponse:
        """Validate a payment payload before settlement."""

    def settle(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> SettleResponse:
        """Verify and settle a payment payload idempotently."""


def is_valid_stellar_address(
    address_candidate: str,
    allow_muxed_account: bool = True,
    allow_native_keyword: bool = False,
) -> bool:
    """Validate whether a string is a well-formed Stellar G-, C-, or M-address."""
    cleaned_address: str = address_candidate.strip()
    if not cleaned_address:
        return False

    if allow_native_keyword and cleaned_address == "native":
        return True

    address_prefix: str = cleaned_address[0]
    if address_prefix in ("G", "C"):
        if len(cleaned_address) != CLASSIC_AND_CONTRACT_ADDRESS_LENGTH:
            return False
        return all(character in STELLAR_BASE32_ALPHABET for character in cleaned_address)

    if address_prefix == "M" and allow_muxed_account:
        if len(cleaned_address) != MUXED_ACCOUNT_ADDRESS_LENGTH:
            return False
        return all(character in STELLAR_BASE32_ALPHABET for character in cleaned_address)

    return False


def calculate_padded_resource_fee_stroops(
    simulated_resource_fee_stroops: int,
    payer_address: str,
    verifier_config: VerifierConfig | None = None,
) -> int:
    """Calculate the padded resource fee in stroops with the refundable safety margin.

    Applies ``refundable_fee_margin_basis_points`` (+15% by default) and caps
    the result at the account-aware fee ceiling (50,000 stroops for G-accounts
    or 250,000 stroops for C-address smart accounts).
    """
    active_config: VerifierConfig = verifier_config or VerifierConfig()
    if simulated_resource_fee_stroops < 0:
        raise ValueError("simulated_resource_fee_stroops must be non-negative")

    is_smart_account_payer: bool = payer_address.strip().startswith("C")
    applicable_fee_ceiling: int = (
        active_config.smart_account_fee_ceiling_stroops
        if is_smart_account_payer
        else active_config.classic_account_fee_ceiling_stroops
    )

    margin_multiplier: float = (
        1.0 + active_config.refundable_fee_margin_basis_points / BASIS_POINTS_DIVISOR
    )
    padded_fee_stroops: int = math.ceil(simulated_resource_fee_stroops * margin_multiplier)
    return min(padded_fee_stroops, applicable_fee_ceiling)


def compute_payload_fingerprint(payment_payload: PaymentPayload) -> str:
    """Compute a deterministic SHA-256 hexadecimal fingerprint for a payment payload."""
    canonical_json: str = payment_payload.model_dump_json(
        by_alias=True,
        exclude_none=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest().upper()


def compute_nonce_replay_key(payment_payload: PaymentPayload) -> str:
    """Compute a deterministic nonce key from the payer and ledger validity window.

     binds the payer address, network, creation ledger, and signature expiration
    ledger to detect cross-payload replay attempts where a different destination
    or amount reuses the same authorization window.
    """
    payload_data = payment_payload.payload
    effective_expiration_ledger: int = (
        payload_data.signature_expiration_ledger
        if payload_data.signature_expiration_ledger is not None
        else payload_data.expiration_ledger
    )
    return (
        f"{payload_data.network}:{payload_data.payer}:"
        f"{payload_data.ledger}:{effective_expiration_ledger}"
    )


def parse_positive_atomic_amount(raw_amount_value: int | str) -> int | None:
    """Parse an atomic integer string or int and ensure it is strictly positive."""
    if isinstance(raw_amount_value, bool):
        return None
    if isinstance(raw_amount_value, int):
        return raw_amount_value if raw_amount_value > 0 else None

    stripped_value: str = raw_amount_value.strip()
    if not stripped_value or not stripped_value.isdigit():
        return None

    parsed_integer: int = int(stripped_value)
    return parsed_integer if parsed_integer > 0 else None


class BaseStellarVerifier:
    """Shared verification and idempotent settlement logic for Stellar schemes."""

    expected_scheme: Scheme

    def __init__(self, config: VerifierConfig | None = None) -> None:
        self.config: VerifierConfig = config or VerifierConfig()
        self._settled_responses_by_fingerprint: dict[str, SettleResponse] = {}
        self._consumed_nonce_fingerprints: dict[str, str] = {}

    def _build_failed_verify_response(
        self,
        payment_payload: PaymentPayload,
        error_code: PayGateErrorCode,
        error_reason: str,
    ) -> VerifyResponse:
        return VerifyResponse(
            success=False,
            scheme=payment_payload.requirement.scheme,
            network=payment_payload.requirement.network,
            payer=payment_payload.payload.payer,
            error_code=error_code,
            error_reason=error_reason,
        )

    def _build_failed_settle_response(
        self,
        payment_payload: PaymentPayload,
        error_code: PayGateErrorCode,
        error_reason: str,
    ) -> SettleResponse:
        return SettleResponse(
            success=False,
            scheme=payment_payload.requirement.scheme,
            network=payment_payload.requirement.network,
            payer=payment_payload.payload.payer,
            transaction_hash=None,
            settled_amount=None,
            error_code=error_code,
            error_reason=error_reason,
        )

    def _validate_common_invariants(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None,
    ) -> VerifyResponse | None:
        requirement = payment_payload.requirement
        payload_data = payment_payload.payload

        # 1. Protocol version check
        if payment_payload.x402_version != 2:
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.INVALID_X402_VERSION,
                error_reason=(f"Unsupported x402 protocol version: {payment_payload.x402_version}"),
            )

        # 2. Scheme check
        if requirement.scheme != self.expected_scheme.value:
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.UNSUPPORTED_SCHEME,
                error_reason=(
                    f"Verifier expects scheme '{self.expected_scheme.value}', "
                    f"received '{requirement.scheme}'"
                ),
            )

        # 3. Network compatibility and consistency check
        if (
            requirement.network not in self.config.supported_networks
            or payload_data.network not in self.config.supported_networks
            or requirement.network != payload_data.network
        ):
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.UNSUPPORTED_NETWORK,
                error_reason=(
                    f"Unsupported or mismatched Stellar network: "
                    f"requirement='{requirement.network}', payload='{payload_data.network}'"
                ),
            )

        # 4. Address validation (payer, pay_to, asset)
        if not is_valid_stellar_address(
            payload_data.payer,
            allow_muxed_account=True,
            allow_native_keyword=False,
        ):
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.INVALID_ADDRESS,
                error_reason=f"Invalid Stellar payer address: '{payload_data.payer}'",
            )

        if not is_valid_stellar_address(
            requirement.pay_to,
            allow_muxed_account=True,
            allow_native_keyword=False,
        ):
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.INVALID_ADDRESS,
                error_reason=f"Invalid Stellar destination address: '{requirement.pay_to}'",
            )

        if not is_valid_stellar_address(
            requirement.asset,
            allow_muxed_account=False,
            allow_native_keyword=True,
        ):
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.INVALID_ADDRESS,
                error_reason=f"Invalid Stellar asset identifier: '{requirement.asset}'",
            )

        # 5. Fee sponsorship mode and fee ceiling check
        if payload_data.fee_mode == FeeMode.UNSPONSORED:
            if not self.config.allow_unsponsored_fees:
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.INVALID_PAYLOAD,
                    error_reason="Unsponsored fee mode is disabled by verifier policy",
                )
        elif (
            payload_data.fee_mode == FeeMode.SPONSORED and not requirement.extra.are_fees_sponsored
        ):
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.INVALID_PAYLOAD,
                error_reason=(
                    "Requirement specifies unsponsored fees, but payload claims sponsored feeMode"
                ),
            )

        configured_fee_stroops: int | None = requirement.extra.max_transaction_fee_stroops
        if configured_fee_stroops is not None:
            is_smart_account: bool = payload_data.payer.startswith("C")
            applicable_ceiling: int = (
                self.config.smart_account_fee_ceiling_stroops
                if is_smart_account
                else self.config.classic_account_fee_ceiling_stroops
            )
            if configured_fee_stroops <= 0 or configured_fee_stroops > applicable_ceiling:
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.INVALID_PAYLOAD,
                    error_reason=(
                        f"Configured maxTransactionFeeStroops ({configured_fee_stroops}) "
                        f"exceeds ceiling ({applicable_ceiling})"
                    ),
                )

        # 6. Ledger window & clock-skew validation
        if current_ledger is not None:
            effective_expiration_ledger: int = (
                payload_data.signature_expiration_ledger
                if payload_data.signature_expiration_ledger is not None
                else payload_data.expiration_ledger
            )

            if (
                payload_data.expiration_ledger <= current_ledger
                or effective_expiration_ledger <= current_ledger
                or payload_data.expiration_ledger <= payload_data.ledger
            ):
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.EXPIRED_AUTHORIZATION,
                    error_reason=(
                        f"Authorization expired at ledger {effective_expiration_ledger} "
                        f"(current ledger {current_ledger})"
                    ),
                )

            if (
                payload_data.valid_after_ledger is not None
                and current_ledger < payload_data.valid_after_ledger
            ):
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.CLOCK_SKEW_EXCEEDED,
                    error_reason=(
                        f"Current ledger {current_ledger} is earlier than "
                        f"validAfterLedger {payload_data.valid_after_ledger}"
                    ),
                )

            max_ledger_offset: int = math.ceil(
                requirement.max_timeout_seconds / SECONDS_PER_STELLAR_LEDGER
            )
            max_allowed_expiration_ledger: int = (
                current_ledger + max_ledger_offset + self.config.skew_tolerance_ledgers
            )
            ledger_delta: int = payload_data.ledger - current_ledger

            if (
                abs(ledger_delta) > self.config.skew_tolerance_ledgers
                or effective_expiration_ledger > max_allowed_expiration_ledger
            ):
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.CLOCK_SKEW_EXCEEDED,
                    error_reason=(
                        f"Ledger skew or expiration window exceeded tolerance "
                        f"(tolerance={self.config.skew_tolerance_ledgers} ledgers)"
                    ),
                )
        else:
            effective_expiration_ledger = (
                payload_data.signature_expiration_ledger
                if payload_data.signature_expiration_ledger is not None
                else payload_data.expiration_ledger
            )
            if (
                payload_data.expiration_ledger <= payload_data.ledger
                or effective_expiration_ledger <= payload_data.ledger
            ):
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.EXPIRED_AUTHORIZATION,
                    error_reason=(
                        f"Authorization expiration ledger ({effective_expiration_ledger}) "
                        f"must exceed creation ledger ({payload_data.ledger})"
                    ),
                )
            if (
                payload_data.valid_after_ledger is not None
                and payload_data.ledger < payload_data.valid_after_ledger
            ):
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.CLOCK_SKEW_EXCEEDED,
                    error_reason=(
                        f"Creation ledger {payload_data.ledger} is earlier than "
                        f"validAfterLedger {payload_data.valid_after_ledger}"
                    ),
                )

        # 7. Cross-payload nonce replay detection
        payload_fingerprint: str = compute_payload_fingerprint(payment_payload)
        nonce_replay_key: str = compute_nonce_replay_key(payment_payload)
        existing_fingerprint: str | None = self._consumed_nonce_fingerprints.get(nonce_replay_key)
        if existing_fingerprint is not None and existing_fingerprint != payload_fingerprint:
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.INVALID_PAYLOAD,
                error_reason="Cross-payload authorization nonce replay detected",
            )

        return None


class ExactVerifier(BaseStellarVerifier):
    """Concrete verification and settlement engine for the fixed-price ``exact`` scheme."""

    expected_scheme: Scheme = Scheme.EXACT

    def verify(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> VerifyResponse:
        """Verify an ``exact`` payment payload against protocol and amount rules."""
        common_failure = self._validate_common_invariants(
            payment_payload=payment_payload,
            current_ledger=current_ledger,
        )
        if common_failure is not None:
            return common_failure

        authorized_amount: int | None = parse_positive_atomic_amount(
            payment_payload.requirement.amount
        )
        if authorized_amount is None:
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.AMOUNT_MISMATCH,
                error_reason=(
                    f"Requirement amount must be a positive integer string, "
                    f"got '{payment_payload.requirement.amount}'"
                ),
            )

        if requested_amount is not None:
            parsed_requested_amount: int | None = parse_positive_atomic_amount(requested_amount)
            if parsed_requested_amount is None or parsed_requested_amount != authorized_amount:
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.AMOUNT_MISMATCH,
                    error_reason=(
                        f"Exact scheme requires amount {authorized_amount}, "
                        f"but requested amount was '{requested_amount}'"
                    ),
                )

        return VerifyResponse(
            success=True,
            scheme=payment_payload.requirement.scheme,
            network=payment_payload.requirement.network,
            payer=payment_payload.payload.payer,
        )

    def settle(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> SettleResponse:
        """Verify and settle an ``exact`` payment payload idempotently."""
        payload_fingerprint: str = compute_payload_fingerprint(payment_payload)
        cached_settlement: SettleResponse | None = self._settled_responses_by_fingerprint.get(
            payload_fingerprint
        )
        if cached_settlement is not None:
            return cached_settlement

        verification_result: VerifyResponse = self.verify(
            payment_payload=payment_payload,
            current_ledger=current_ledger,
            requested_amount=requested_amount,
        )
        if not verification_result.success:
            return self._build_failed_settle_response(
                payment_payload=payment_payload,
                error_code=verification_result.error_code or PayGateErrorCode.INVALID_PAYLOAD,
                error_reason=verification_result.error_reason or "Payment verification failed",
            )

        if self.config.force_settlement_failure:
            return self._build_failed_settle_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.SETTLEMENT_FAILURE,
                error_reason="Simulated Soroban RPC settlement submission failure",
            )

        nonce_replay_key: str = compute_nonce_replay_key(payment_payload)
        self._consumed_nonce_fingerprints[nonce_replay_key] = payload_fingerprint

        settlement_response = SettleResponse(
            success=True,
            scheme=payment_payload.requirement.scheme,
            network=payment_payload.requirement.network,
            payer=payment_payload.payload.payer,
            transaction_hash=payload_fingerprint,
            settled_amount=str(int(payment_payload.requirement.amount.strip())),
        )
        self._settled_responses_by_fingerprint[payload_fingerprint] = settlement_response
        return settlement_response


class UptoVerifier(BaseStellarVerifier):
    """Concrete verification and settlement engine for the metered ``upto`` scheme."""

    expected_scheme: Scheme = Scheme.UPTO

    def verify(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> VerifyResponse:
        """Verify an ``upto`` payment payload and ensure requested usage is within ceiling."""
        common_failure = self._validate_common_invariants(
            payment_payload=payment_payload,
            current_ledger=current_ledger,
        )
        if common_failure is not None:
            return common_failure

        authorized_ceiling_amount: int | None = parse_positive_atomic_amount(
            payment_payload.requirement.amount
        )
        if authorized_ceiling_amount is None:
            return self._build_failed_verify_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.AMOUNT_MISMATCH,
                error_reason=(
                    f"Authorized ceiling amount must be a positive integer string, "
                    f"got '{payment_payload.requirement.amount}'"
                ),
            )

        if requested_amount is not None:
            parsed_requested_amount: int | None = parse_positive_atomic_amount(requested_amount)
            if parsed_requested_amount is None:
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.AMOUNT_MISMATCH,
                    error_reason=(
                        f"Requested settlement amount must be a positive integer, "
                        f"got '{requested_amount}'"
                    ),
                )
            if parsed_requested_amount > authorized_ceiling_amount:
                return self._build_failed_verify_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.EXCEEDS_MAX_UPTO,
                    error_reason=(
                        f"Requested amount ({parsed_requested_amount}) exceeds "
                        f"authorized upto ceiling ({authorized_ceiling_amount})"
                    ),
                )

        return VerifyResponse(
            success=True,
            scheme=payment_payload.requirement.scheme,
            network=payment_payload.requirement.network,
            payer=payment_payload.payload.payer,
        )

    def settle(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> SettleResponse:
        """Verify and settle an ``upto`` payment payload at the requested usage amount."""
        payload_fingerprint: str = compute_payload_fingerprint(payment_payload)
        cached_settlement: SettleResponse | None = self._settled_responses_by_fingerprint.get(
            payload_fingerprint
        )
        if cached_settlement is not None:
            if (
                requested_amount is not None
                and str(requested_amount).strip() != cached_settlement.settled_amount
            ):
                return self._build_failed_settle_response(
                    payment_payload=payment_payload,
                    error_code=PayGateErrorCode.INVALID_PAYLOAD,
                    error_reason=("Payload already settled with a different metered usage amount"),
                )
            return cached_settlement

        verification_result: VerifyResponse = self.verify(
            payment_payload=payment_payload,
            current_ledger=current_ledger,
            requested_amount=requested_amount,
        )
        if not verification_result.success:
            return self._build_failed_settle_response(
                payment_payload=payment_payload,
                error_code=verification_result.error_code or PayGateErrorCode.INVALID_PAYLOAD,
                error_reason=verification_result.error_reason or "Upto payment verification failed",
            )

        if self.config.force_settlement_failure:
            return self._build_failed_settle_response(
                payment_payload=payment_payload,
                error_code=PayGateErrorCode.SETTLEMENT_FAILURE,
                error_reason="Simulated Soroban RPC settlement submission failure",
            )

        effective_settled_amount: int = (
            int(str(requested_amount).strip())
            if requested_amount is not None
            else int(payment_payload.requirement.amount.strip())
        )

        nonce_replay_key: str = compute_nonce_replay_key(payment_payload)
        self._consumed_nonce_fingerprints[nonce_replay_key] = payload_fingerprint

        settlement_response = SettleResponse(
            success=True,
            scheme=payment_payload.requirement.scheme,
            network=payment_payload.requirement.network,
            payer=payment_payload.payload.payer,
            transaction_hash=payload_fingerprint,
            settled_amount=str(effective_settled_amount),
        )
        self._settled_responses_by_fingerprint[payload_fingerprint] = settlement_response
        return settlement_response


_DEFAULT_EXACT_VERIFIER = ExactVerifier()
_DEFAULT_UPTO_VERIFIER = UptoVerifier()


def get_verifier_for_scheme(
    scheme: str | Scheme,
    config: VerifierConfig | None = None,
) -> VerificationPort:
    """Return an ``ExactVerifier`` or ``UptoVerifier`` matching the requested scheme."""
    normalized_scheme: str = scheme.value if isinstance(scheme, Scheme) else scheme
    if config is not None:
        if normalized_scheme == Scheme.UPTO.value:
            return UptoVerifier(config=config)
        return ExactVerifier(config=config)

    if normalized_scheme == Scheme.UPTO.value:
        return _DEFAULT_UPTO_VERIFIER
    return _DEFAULT_EXACT_VERIFIER


def verify_payload(
    payload: PaymentPayload,
    current_ledger: int | None = None,
    requested_amount: int | str | None = None,
    config: VerifierConfig | None = None,
) -> VerifyResponse:
    """Verify a ``PaymentPayload`` using the scheme-appropriate verifier."""
    verifier: VerificationPort = get_verifier_for_scheme(
        scheme=payload.requirement.scheme,
        config=config,
    )
    return verifier.verify(
        payment_payload=payload,
        current_ledger=current_ledger,
        requested_amount=requested_amount,
    )


def settle_payload(
    payload: PaymentPayload,
    current_ledger: int | None = None,
    requested_amount: int | str | None = None,
    config: VerifierConfig | None = None,
) -> SettleResponse:
    """Verify and settle a ``PaymentPayload`` idempotently."""
    verifier: VerificationPort = get_verifier_for_scheme(
        scheme=payload.requirement.scheme,
        config=config,
    )
    return verifier.settle(
        payment_payload=payload,
        current_ledger=current_ledger,
        requested_amount=requested_amount,
    )


class SettlementDriftTracker:
    """Rolling-median settlement drift tracker for ``upto`` metered endpoints.

    Tracks the rolling window of settled atomic amounts against an endpoint's
    advertised ``typical_amount`` to detect sellers whose settled amounts
    consistently exceed their declared typical cost by more than
    ``drift_threshold_ratio`` (default 50%).
    """

    def __init__(
        self,
        drift_threshold_ratio: float = 0.50,
        max_window_size: int = 100,
    ) -> None:
        if drift_threshold_ratio < 0.0:
            raise ValueError("drift_threshold_ratio must be non-negative")
        if max_window_size <= 0:
            raise ValueError("max_window_size must be a positive integer")

        self.drift_threshold_ratio: float = drift_threshold_ratio
        self.max_window_size: int = max_window_size
        self._declared_typical_by_url: dict[str, int] = {}
        self._settled_history_by_url: dict[str, deque[int]] = {}

    def record_settlement(
        self,
        resource_url: str,
        declared_typical_atomic: int | str,
        settled_atomic: int | str,
    ) -> None:
        """Record a single settled atomic amount for a metered resource URL."""
        cleaned_resource_url: str = resource_url.strip()
        if not cleaned_resource_url:
            raise ValueError("resource_url cannot be empty")

        parsed_typical_atomic: int | None = parse_positive_atomic_amount(declared_typical_atomic)
        if parsed_typical_atomic is None:
            raise ValueError(
                "declared_typical_atomic must be a positive integer, "
                f"got '{declared_typical_atomic}'"
            )

        parsed_settled_atomic: int | None = parse_positive_atomic_amount(settled_atomic)
        if parsed_settled_atomic is None:
            raise ValueError(f"settled_atomic must be a positive integer, got '{settled_atomic}'")

        self._declared_typical_by_url[cleaned_resource_url] = parsed_typical_atomic
        history_window: deque[int] | None = self._settled_history_by_url.get(cleaned_resource_url)
        if history_window is None:
            history_window = deque(maxlen=self.max_window_size)
            self._settled_history_by_url[cleaned_resource_url] = history_window
        history_window.append(parsed_settled_atomic)

    def get_rolling_median_atomic(self, resource_url: str) -> int | None:
        """Return the rolling median of settled atomic amounts for ``resource_url``."""
        cleaned_resource_url: str = resource_url.strip()
        history_window: deque[int] | None = self._settled_history_by_url.get(cleaned_resource_url)
        if not history_window:
            return None

        sorted_amounts: list[int] = sorted(history_window)
        sample_count: int = len(sorted_amounts)
        middle_index: int = sample_count // 2
        if sample_count % 2 == 1:
            return sorted_amounts[middle_index]
        return (sorted_amounts[middle_index - 1] + sorted_amounts[middle_index]) // 2

    def is_drifting(self, resource_url: str) -> bool:
        """Return ``True`` when the rolling median exceeds declared typical by > threshold."""
        cleaned_resource_url: str = resource_url.strip()
        rolling_median_atomic: int | None = self.get_rolling_median_atomic(cleaned_resource_url)
        declared_typical_atomic: int | None = self._declared_typical_by_url.get(
            cleaned_resource_url
        )
        if rolling_median_atomic is None or declared_typical_atomic is None:
            return False

        maximum_non_drifting_atomic: float = declared_typical_atomic * (
            1.0 + self.drift_threshold_ratio
        )
        return float(rolling_median_atomic) > maximum_non_drifting_atomic


class OutboxSettlementState(StrEnum):
    """Lifecycle states for an asynchronous pending-settlement outbox record."""

    VERIFIED = "VERIFIED"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    DLQ = "DLQ"


@dataclass
class OutboxEntry:
    """In-memory state machine record tracked by :class:`SettlementOutboxManager`."""

    payload_fingerprint: str
    state: OutboxSettlementState
    attempts: int = 0
    settlement_response: SettleResponse | None = None
    last_error: str | None = None
    history: list[OutboxSettlementState] = field(default_factory=list)


RpcSubmissionOutcome = OutboxSettlementState | SettleResponse | str
RpcSubmissionCallback = Callable[
    [PaymentPayload, int],
    RpcSubmissionOutcome | Awaitable[RpcSubmissionOutcome],
]


class SettlementOutboxManager:
    """Asynchronous pending-settlement outbox state machine with per-fingerprint lock coalescing.

    Coalesces concurrent ``settle_async`` requests for the same signed
    ``PaymentPayload`` using per-fingerprint :class:`asyncio.Lock` instances so
    only a single underlying Soroban RPC submission executes. Supports
    multi-poll ``VERIFIED -> SUBMITTED -> PENDING -> SETTLED`` transitions with
    configurable backoff and moves exhausted or unrecoverable submissions into a
    Dead-Letter Queue (``OutboxSettlementState.DLQ``).
    """

    def __init__(
        self,
        config: VerifierConfig | None = None,
        max_poll_attempts: int = 3,
        initial_backoff_seconds: float = 0.0,
        rpc_submit_callback: RpcSubmissionCallback | None = None,
    ) -> None:
        if max_poll_attempts <= 0:
            raise ValueError("max_poll_attempts must be a positive integer")
        if initial_backoff_seconds < 0.0:
            raise ValueError("initial_backoff_seconds must be non-negative")

        self.config: VerifierConfig = config or VerifierConfig()
        self.max_poll_attempts: int = max_poll_attempts
        self.initial_backoff_seconds: float = initial_backoff_seconds
        self.rpc_submit_callback: RpcSubmissionCallback | None = rpc_submit_callback
        self.submission_count: int = 0
        self._entries_by_fingerprint: dict[str, OutboxEntry] = {}
        self._locks_by_fingerprint: dict[str, asyncio.Lock] = {}

    @property
    def dead_letter_queue(self) -> list[OutboxEntry]:
        """Return all outbox entries currently in the Dead-Letter Queue (``DLQ``) state."""
        return [
            entry
            for entry in self._entries_by_fingerprint.values()
            if entry.state == OutboxSettlementState.DLQ
        ]

    def get_entry(self, payload_fingerprint: str) -> OutboxEntry | None:
        """Retrieve the current :class:`OutboxEntry` for ``payload_fingerprint``."""
        return self._entries_by_fingerprint.get(payload_fingerprint)

    def _get_or_create_lock(self, payload_fingerprint: str) -> asyncio.Lock:
        existing_lock: asyncio.Lock | None = self._locks_by_fingerprint.get(payload_fingerprint)
        if existing_lock is None:
            existing_lock = asyncio.Lock()
            self._locks_by_fingerprint[payload_fingerprint] = existing_lock
        return existing_lock

    @staticmethod
    def _record_state_transition(
        entry: OutboxEntry,
        next_state: OutboxSettlementState,
    ) -> None:
        entry.state = next_state
        if not entry.history or entry.history[-1] != next_state:
            entry.history.append(next_state)

    async def _invoke_rpc_callback(
        self,
        payment_payload: PaymentPayload,
        attempt_number: int,
    ) -> RpcSubmissionOutcome:
        assert self.rpc_submit_callback is not None
        callback_result = self.rpc_submit_callback(payment_payload, attempt_number)
        if inspect.isawaitable(callback_result):
            return await callback_result
        return callback_result

    async def settle_async(
        self,
        payment_payload: PaymentPayload,
        current_ledger: int | None = None,
        requested_amount: int | str | None = None,
    ) -> SettleResponse:
        """Coalesce concurrent settlement calls and drive the outbox state machine."""
        payload_fingerprint: str = compute_payload_fingerprint(payment_payload)
        fingerprint_lock: asyncio.Lock = self._get_or_create_lock(payload_fingerprint)

        async with fingerprint_lock:
            existing_entry: OutboxEntry | None = self._entries_by_fingerprint.get(
                payload_fingerprint
            )
            if (
                existing_entry is not None
                and existing_entry.settlement_response is not None
                and existing_entry.state
                in (OutboxSettlementState.SETTLED, OutboxSettlementState.DLQ)
            ):
                return existing_entry.settlement_response

            verifier: VerificationPort = get_verifier_for_scheme(
                scheme=payment_payload.requirement.scheme,
                config=self.config,
            )
            verification_result: VerifyResponse = verifier.verify(
                payment_payload=payment_payload,
                current_ledger=current_ledger,
                requested_amount=requested_amount,
            )
            if not verification_result.success:
                failed_verify_settle = SettleResponse(
                    success=False,
                    scheme=payment_payload.requirement.scheme,
                    network=payment_payload.requirement.network,
                    payer=payment_payload.payload.payer,
                    error_code=verification_result.error_code or PayGateErrorCode.INVALID_PAYLOAD,
                    error_reason=verification_result.error_reason or "Payment verification failed",
                )
                dlq_entry = OutboxEntry(
                    payload_fingerprint=payload_fingerprint,
                    state=OutboxSettlementState.DLQ,
                    attempts=0,
                    settlement_response=failed_verify_settle,
                    last_error=failed_verify_settle.error_reason,
                    history=[OutboxSettlementState.DLQ],
                )
                self._entries_by_fingerprint[payload_fingerprint] = dlq_entry
                return failed_verify_settle

            outbox_entry = OutboxEntry(
                payload_fingerprint=payload_fingerprint,
                state=OutboxSettlementState.VERIFIED,
                attempts=0,
                history=[OutboxSettlementState.VERIFIED],
            )
            self._entries_by_fingerprint[payload_fingerprint] = outbox_entry

            self.submission_count += 1
            self._record_state_transition(outbox_entry, OutboxSettlementState.SUBMITTED)

            if self.rpc_submit_callback is None:
                outbox_entry.attempts = 1
                direct_settlement: SettleResponse = verifier.settle(
                    payment_payload=payment_payload,
                    current_ledger=current_ledger,
                    requested_amount=requested_amount,
                )
                outbox_entry.settlement_response = direct_settlement
                if direct_settlement.success:
                    self._record_state_transition(outbox_entry, OutboxSettlementState.SETTLED)
                else:
                    outbox_entry.last_error = direct_settlement.error_reason
                    self._record_state_transition(outbox_entry, OutboxSettlementState.DLQ)
                return direct_settlement

            for attempt_number in range(1, self.max_poll_attempts + 1):
                outbox_entry.attempts = attempt_number
                try:
                    outcome = await self._invoke_rpc_callback(payment_payload, attempt_number)
                except Exception as exc:
                    outbox_entry.last_error = str(exc)
                    if (
                        attempt_number < self.max_poll_attempts
                        and self.initial_backoff_seconds > 0.0
                    ):
                        await asyncio.sleep(
                            self.initial_backoff_seconds * (2 ** (attempt_number - 1))
                        )
                    continue

                if isinstance(outcome, SettleResponse):
                    outbox_entry.settlement_response = outcome
                    if outcome.success:
                        self._record_state_transition(outbox_entry, OutboxSettlementState.SETTLED)
                    else:
                        outbox_entry.last_error = outcome.error_reason
                        self._record_state_transition(outbox_entry, OutboxSettlementState.DLQ)
                    return outcome

                normalized_outcome: str = (
                    outcome.value
                    if isinstance(outcome, OutboxSettlementState)
                    else str(outcome).upper()
                )
                if normalized_outcome == OutboxSettlementState.PENDING.value:
                    self._record_state_transition(outbox_entry, OutboxSettlementState.PENDING)
                    if (
                        attempt_number < self.max_poll_attempts
                        and self.initial_backoff_seconds > 0.0
                    ):
                        await asyncio.sleep(
                            self.initial_backoff_seconds * (2 ** (attempt_number - 1))
                        )
                    continue

                if normalized_outcome == OutboxSettlementState.SETTLED.value:
                    confirmed_settlement: SettleResponse = verifier.settle(
                        payment_payload=payment_payload,
                        current_ledger=current_ledger,
                        requested_amount=requested_amount,
                    )
                    outbox_entry.settlement_response = confirmed_settlement
                    if confirmed_settlement.success:
                        self._record_state_transition(outbox_entry, OutboxSettlementState.SETTLED)
                    else:
                        outbox_entry.last_error = confirmed_settlement.error_reason
                        self._record_state_transition(outbox_entry, OutboxSettlementState.DLQ)
                    return confirmed_settlement

                outbox_entry.last_error = f"Unrecoverable RPC settlement state: {outcome}"
                break

            exhausted_error_reason: str = (
                outbox_entry.last_error
                or f"Settlement remained PENDING after {self.max_poll_attempts} polling attempts"
            )
            dlq_settlement_response = SettleResponse(
                success=False,
                scheme=payment_payload.requirement.scheme,
                network=payment_payload.requirement.network,
                payer=payment_payload.payload.payer,
                error_code=PayGateErrorCode.SETTLEMENT_FAILURE,
                error_reason=exhausted_error_reason,
            )
            outbox_entry.settlement_response = dlq_settlement_response
            outbox_entry.last_error = exhausted_error_reason
            self._record_state_transition(outbox_entry, OutboxSettlementState.DLQ)
            return dlq_settlement_response


__all__ = [
    "VerifierConfig",
    "VerificationPort",
    "ExactVerifier",
    "UptoVerifier",
    "SettlementDriftTracker",
    "OutboxSettlementState",
    "OutboxEntry",
    "SettlementOutboxManager",
    "is_valid_stellar_address",
    "calculate_padded_resource_fee_stroops",
    "compute_payload_fingerprint",
    "get_verifier_for_scheme",
    "verify_payload",
    "settle_payload",
]
