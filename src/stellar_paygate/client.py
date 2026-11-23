"""Agent spend-policy guard and HTTP 402 transport wrappers for stellar-paygate-py.

Provides exact-integer/Decimal spend enforcement via :class:`SpendPolicyGuard`,
an in-flight request idempotency cache, and automatic HTTP 402 challenge-response
transport wrappers (:class:`PaygateSyncTransport`, :class:`PaygateAsyncTransport`,
and :func:`wrap_httpx_with_paygate`) for ``httpx.Client`` and ``httpx.AsyncClient``.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypeVar

import httpx

from .codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    decode_payment_required,
    decode_payment_response,
    encode_payment_signature,
)
from .types import (
    FeeMode,
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirement,
    StellarPaymentPayloadData,
)

ClientT = TypeVar("ClientT", httpx.Client, httpx.AsyncClient)

STELLAR_ATOMIC_DECIMALS: int = 7
STELLAR_STROOPS_PER_UNIT: int = 10**STELLAR_ATOMIC_DECIMALS
DEFAULT_PAYER_ADDRESS: str = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
DEFAULT_CLIENT_LEDGER: int = 500_000
SECONDS_PER_STELLAR_LEDGER: int = 5


def decimal_to_atomic_units(human_amount: str | Decimal | int) -> int:
    """Convert a human-readable token decimal string into exact atomic units (7 decimals).

    Uses ``decimal.Decimal`` arithmetic with zero IEEE-754 float precision drift.
    Raises ``ValueError`` if the input is negative, non-numeric, or specifies
    more than 7 fractional digits (sub-stroop dust).
    """
    if isinstance(human_amount, bool):
        raise ValueError("Boolean values are not valid token amounts")

    if isinstance(human_amount, int):
        if human_amount <= 0:
            raise ValueError(f"Token amount must be positive, got {human_amount}")
        return human_amount * STELLAR_STROOPS_PER_UNIT

    raw_string: str = (
        str(human_amount).strip() if isinstance(human_amount, Decimal) else human_amount.strip()
    )
    if not raw_string:
        raise ValueError("Token amount string cannot be empty")

    try:
        parsed_decimal = Decimal(raw_string)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal token amount: '{human_amount}'") from exc

    if not parsed_decimal.is_finite() or parsed_decimal <= Decimal(0):
        raise ValueError(f"Token amount must be finite and positive, got '{human_amount}'")

    normalized_tuple = parsed_decimal.as_tuple()
    exponent_value = normalized_tuple.exponent
    if isinstance(exponent_value, int) and exponent_value < -STELLAR_ATOMIC_DECIMALS:
        raise ValueError(
            f"Token amount '{human_amount}' exceeds {STELLAR_ATOMIC_DECIMALS} decimal places"
        )

    atomic_decimal: Decimal = parsed_decimal * Decimal(STELLAR_STROOPS_PER_UNIT)
    return int(atomic_decimal)


def atomic_units_to_decimal(atomic_units: int) -> str:
    """Format an exact integer atomic amount back into a 7-decimal string."""
    if isinstance(atomic_units, bool) or atomic_units < 0:
        raise ValueError(f"Atomic units must be a non-negative integer, got {atomic_units}")
    scaled_decimal: Decimal = Decimal(atomic_units) / Decimal(STELLAR_STROOPS_PER_UNIT)
    return f"{scaled_decimal:.{STELLAR_ATOMIC_DECIMALS}f}"


def parse_challenge_atomic_amount(raw_amount: str | int) -> int:
    """Parse an atomic integer amount from a ``PaymentRequirement`` challenge."""
    if isinstance(raw_amount, bool):
        raise PaygateProtocolError(
            PayGateErrorCode.AMOUNT_MISMATCH,
            "Challenge amount cannot be a boolean",
        )
    if isinstance(raw_amount, int):
        if raw_amount <= 0:
            raise PaygateProtocolError(
                PayGateErrorCode.AMOUNT_MISMATCH,
                f"Challenge amount must be positive, got {raw_amount}",
            )
        return raw_amount

    cleaned_amount: str = raw_amount.strip()
    if not cleaned_amount:
        raise PaygateProtocolError(
            PayGateErrorCode.AMOUNT_MISMATCH,
            "Challenge amount cannot be empty",
        )

    try:
        parsed_decimal = Decimal(cleaned_amount)
    except InvalidOperation as exc:
        raise PaygateProtocolError(
            PayGateErrorCode.AMOUNT_MISMATCH,
            f"Challenge amount is not a valid number: '{raw_amount}'",
        ) from exc

    if (
        not parsed_decimal.is_finite()
        or parsed_decimal <= Decimal(0)
        or parsed_decimal != int(parsed_decimal)
    ):
        raise PaygateProtocolError(
            PayGateErrorCode.AMOUNT_MISMATCH,
            f"Challenge amount must be a positive atomic integer string, got '{raw_amount}'",
        )

    return int(parsed_decimal)


def compute_request_fingerprint(
    http_method: str,
    request_url: str,
    request_body: bytes = b"",
) -> str:
    """Compute a deterministic SHA-256 fingerprint for an outgoing HTTP request."""
    hasher = hashlib.sha256()
    hasher.update(http_method.upper().encode("utf-8"))
    hasher.update(b":")
    hasher.update(request_url.encode("utf-8"))
    hasher.update(b":")
    hasher.update(request_body)
    return hasher.hexdigest()


class SqliteIdempotencyStore:
    """SQLite-backed persistent in-flight payload and settlement receipt audit store.

    Persists ``(request_fingerprint, payment_payload_json, reserved_atomic_amount,
    expiration_ledger, settled_transaction_hash)`` using Python's standard library
    :mod:`sqlite3` so autonomous agents survive process restarts mid-request
    without signing or paying a second time.
    """

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self.database_path: str = str(database_path)
        self._connection: sqlite3.Connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
        )
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paygate_idempotency_records (
                    request_fingerprint TEXT PRIMARY KEY,
                    payment_payload_json TEXT NOT NULL,
                    reserved_atomic_amount INTEGER NOT NULL,
                    expiration_ledger INTEGER NOT NULL,
                    settled_transaction_hash TEXT
                )
                """
            )

    def save_inflight_payload(
        self,
        request_fingerprint: str,
        payment_payload: PaymentPayload,
        reserved_atomic_amount: int,
    ) -> None:
        """Persist or update an in-flight PaymentPayload reservation."""
        payload_json: str = payment_payload.model_dump_json(
            by_alias=True,
            exclude_none=True,
        )
        expiration_ledger: int = payment_payload.payload.expiration_ledger
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO paygate_idempotency_records (
                    request_fingerprint,
                    payment_payload_json,
                    reserved_atomic_amount,
                    expiration_ledger,
                    settled_transaction_hash
                ) VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(request_fingerprint) DO UPDATE SET
                    payment_payload_json = excluded.payment_payload_json,
                    reserved_atomic_amount = excluded.reserved_atomic_amount,
                    expiration_ledger = excluded.expiration_ledger
                """,
                (
                    request_fingerprint,
                    payload_json,
                    reserved_atomic_amount,
                    expiration_ledger,
                ),
            )

    def get_unexpired_payload(
        self,
        request_fingerprint: str,
        current_ledger: int,
    ) -> tuple[PaymentPayload, int] | None:
        """Return an unexpired ``(PaymentPayload, reserved_atomic_amount)`` or prune if expired."""
        cursor = self._connection.execute(
            """
            SELECT payment_payload_json, reserved_atomic_amount, expiration_ledger
            FROM paygate_idempotency_records
            WHERE request_fingerprint = ?
            """,
            (request_fingerprint,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        payload_json_str, reserved_amount_int, expiration_ledger_int = row
        if int(expiration_ledger_int) <= current_ledger:
            self.delete_record(request_fingerprint)
            return None

        restored_payload: PaymentPayload = PaymentPayload.model_validate_json(str(payload_json_str))
        return restored_payload, int(reserved_amount_int)

    def record_settlement_hash(
        self,
        request_fingerprint: str,
        transaction_hash: str,
    ) -> None:
        """Record the confirmed on-chain transaction hash for a settled request."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE paygate_idempotency_records
                SET settled_transaction_hash = ?
                WHERE request_fingerprint = ?
                """,
                (transaction_hash, request_fingerprint),
            )

    def get_settlement_hash(self, request_fingerprint: str) -> str | None:
        """Retrieve the recorded settlement transaction hash for a request fingerprint."""
        cursor = self._connection.execute(
            """
            SELECT settled_transaction_hash
            FROM paygate_idempotency_records
            WHERE request_fingerprint = ?
            """,
            (request_fingerprint,),
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def delete_record(self, request_fingerprint: str) -> int | None:
        """Delete a reservation record and return its reserved atomic amount if present."""
        cursor = self._connection.execute(
            """
            SELECT reserved_atomic_amount
            FROM paygate_idempotency_records
            WHERE request_fingerprint = ?
            """,
            (request_fingerprint,),
        )
        row = cursor.fetchone()
        with self._connection:
            self._connection.execute(
                "DELETE FROM paygate_idempotency_records WHERE request_fingerprint = ?",
                (request_fingerprint,),
            )
        if row is None:
            return None
        return int(row[0])

    def prune_expired_records(self, current_ledger: int) -> int:
        """Delete all unsettled records whose expiration_ledger <= current_ledger."""
        with self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM paygate_idempotency_records
                WHERE expiration_ledger <= ? AND settled_transaction_hash IS NULL
                """,
                (current_ledger,),
            )
            return cursor.rowcount

    def total_active_reserved_amount(self, current_ledger: int) -> int:
        """Compute total reserved atomic units across all unexpired or settled records."""
        self.prune_expired_records(current_ledger)
        cursor = self._connection.execute(
            """
            SELECT COALESCE(SUM(reserved_atomic_amount), 0)
            FROM paygate_idempotency_records
            """
        )
        row = cursor.fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._connection.close()


class SpendPolicyGuard:
    """Exact-integer spend policy guard with rolling budget and in-flight retry cache."""

    def __init__(
        self,
        max_per_call: int = 1_000_000_000,
        max_budget: int = 10_000_000_000,
        allowed_networks: set[str] | frozenset[str] | None = None,
        allowed_assets: set[str] | frozenset[str] | None = None,
        payer_address: str = DEFAULT_PAYER_ADDRESS,
        current_ledger: int = DEFAULT_CLIENT_LEDGER,
        idempotency_store: SqliteIdempotencyStore | None = None,
    ) -> None:
        if max_per_call <= 0:
            raise ValueError("max_per_call must be a positive integer")
        if max_budget <= 0:
            raise ValueError("max_budget must be a positive integer")

        self.max_per_call: int = max_per_call
        self.max_budget: int = max_budget
        self.allowed_networks: frozenset[str] | None = (
            frozenset(allowed_networks) if allowed_networks is not None else None
        )
        self.allowed_assets: frozenset[str] | None = (
            frozenset(allowed_assets) if allowed_assets is not None else None
        )
        self.payer_address: str = payer_address
        self.current_ledger: int = current_ledger
        self.idempotency_store: SqliteIdempotencyStore | None = idempotency_store
        self._inflight_payload_cache: dict[str, PaymentPayload] = {}
        self._inflight_reserved_amounts: dict[str, int] = {}
        self.consumed_budget: int = (
            idempotency_store.total_active_reserved_amount(current_ledger)
            if idempotency_store is not None
            else 0
        )

    @property
    def remaining_budget(self) -> int:
        """Return remaining atomic units available in the rolling budget."""
        return max(0, self.max_budget - self.consumed_budget)

    def check(
        self,
        payload: PaymentPayload,
        request_fingerprint: str | None = None,
    ) -> None:
        """Validate a payment payload against spend caps, network/asset rules, and budget.

        Deducts the approved atomic amount from ``consumed_budget``. When
        ``request_fingerprint`` is provided and matches an active in-flight
        reservation (in memory or in ``idempotency_store``), the cached approval
        is reused without double-deducting.
        """
        if request_fingerprint is not None:
            existing_cached = self.get_cached_payload(request_fingerprint)
            if existing_cached is not None:
                return

        requirement: PaymentRequirement = payload.requirement

        if self.allowed_networks is not None and requirement.network not in self.allowed_networks:
            raise PaygateProtocolError(
                PayGateErrorCode.UNSUPPORTED_NETWORK,
                f"Network '{requirement.network}' is not in SpendPolicyGuard allowed_networks",
            )

        if self.allowed_assets is not None and requirement.asset not in self.allowed_assets:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_ADDRESS,
                f"Asset '{requirement.asset}' is not in SpendPolicyGuard allowed_assets",
            )

        atomic_amount: int = parse_challenge_atomic_amount(requirement.amount)

        if atomic_amount > self.max_per_call:
            raise PaygateProtocolError(
                PayGateErrorCode.AMOUNT_MISMATCH,
                f"Payment amount {atomic_amount} exceeds max_per_call cap ({self.max_per_call})",
            )

        if self.consumed_budget + atomic_amount > self.max_budget:
            raise PaygateProtocolError(
                PayGateErrorCode.EXCEEDS_MAX_UPTO,
                f"Payment amount {atomic_amount} exceeds remaining rolling budget "
                f"({self.remaining_budget} remaining of {self.max_budget})",
            )

        self.consumed_budget += atomic_amount
        if request_fingerprint is not None:
            self._inflight_payload_cache[request_fingerprint] = payload
            self._inflight_reserved_amounts[request_fingerprint] = atomic_amount
            if self.idempotency_store is not None:
                self.idempotency_store.save_inflight_payload(
                    request_fingerprint=request_fingerprint,
                    payment_payload=payload,
                    reserved_atomic_amount=atomic_amount,
                )

    def get_cached_payload(self, request_fingerprint: str) -> PaymentPayload | None:
        """Retrieve an unexpired in-flight PaymentPayload from memory or SQLite store."""
        cached_payload: PaymentPayload | None = self._inflight_payload_cache.get(
            request_fingerprint
        )
        if cached_payload is not None:
            if cached_payload.payload.expiration_ledger > self.current_ledger:
                return cached_payload
            self._inflight_payload_cache.pop(request_fingerprint, None)
            self._inflight_reserved_amounts.pop(request_fingerprint, None)

        if self.idempotency_store is not None:
            persisted_entry = self.idempotency_store.get_unexpired_payload(
                request_fingerprint=request_fingerprint,
                current_ledger=self.current_ledger,
            )
            if persisted_entry is not None:
                restored_payload, reserved_amount = persisted_entry
                self._inflight_payload_cache[request_fingerprint] = restored_payload
                self._inflight_reserved_amounts[request_fingerprint] = reserved_amount
                return restored_payload

        return None

    def record_settlement_receipt(
        self,
        request_fingerprint: str,
        transaction_hash: str | None,
    ) -> None:
        """Record a settled transaction hash in the persistent SQLite audit store."""
        if self.idempotency_store is not None and transaction_hash is not None:
            self.idempotency_store.record_settlement_hash(
                request_fingerprint=request_fingerprint,
                transaction_hash=transaction_hash,
            )

    def release_reservation(
        self,
        atomic_amount: int | None = None,
        request_fingerprint: str | None = None,
    ) -> None:
        """Roll back a budget reservation if an HTTP retry fails with a local transport error."""
        amount_to_release: int = 0
        if request_fingerprint is not None:
            self._inflight_payload_cache.pop(request_fingerprint, None)
            reserved_for_key: int | None = self._inflight_reserved_amounts.pop(
                request_fingerprint, None
            )
            if reserved_for_key is not None:
                amount_to_release = reserved_for_key
            if self.idempotency_store is not None:
                persisted_reserved = self.idempotency_store.delete_record(request_fingerprint)
                if amount_to_release == 0 and persisted_reserved is not None:
                    amount_to_release = persisted_reserved

        if atomic_amount is not None and amount_to_release == 0:
            if atomic_amount < 0:
                raise ValueError("atomic_amount to release must be non-negative")
            amount_to_release = atomic_amount

        self.consumed_budget = max(0, self.consumed_budget - amount_to_release)

    def build_payment_payload_for_challenge(
        self,
        payment_required: PaymentRequired,
        request_fingerprint: str | None = None,
    ) -> PaymentPayload:
        """Select a compatible PaymentRequirement, enforce spend policy, and build a payload."""
        if request_fingerprint is not None:
            cached_payload = self.get_cached_payload(request_fingerprint)
            if cached_payload is not None:
                return cached_payload

        if not payment_required.accepts:
            raise PaygateProtocolError(
                PayGateErrorCode.INVALID_PAYLOAD,
                "PAYMENT-REQUIRED challenge contains no accepted payment requirements",
            )

        selected_requirement: PaymentRequirement | None = None
        for candidate_requirement in payment_required.accepts:
            network_matches: bool = (
                self.allowed_networks is None
                or candidate_requirement.network in self.allowed_networks
            )
            asset_matches: bool = (
                self.allowed_assets is None or candidate_requirement.asset in self.allowed_assets
            )
            if network_matches and asset_matches:
                selected_requirement = candidate_requirement
                break

        if selected_requirement is None:
            selected_requirement = payment_required.accepts[0]

        ledger_offset: int = math.ceil(
            selected_requirement.max_timeout_seconds / SECONDS_PER_STELLAR_LEDGER
        )
        expiration_ledger: int = self.current_ledger + ledger_offset
        selected_fee_mode: FeeMode = (
            FeeMode.SPONSORED
            if selected_requirement.extra.are_fees_sponsored
            else FeeMode.UNSPONSORED
        )

        payload_data = StellarPaymentPayloadData(
            payer=self.payer_address,
            network=selected_requirement.network,
            ledger=self.current_ledger,
            expiration_ledger=expiration_ledger,
            fee_mode=selected_fee_mode,
            signature_expiration_ledger=expiration_ledger,
        )
        payment_payload = PaymentPayload(
            x402_version=payment_required.x402_version,
            requirement=selected_requirement,
            payload=payload_data,
        )

        self.check(payment_payload, request_fingerprint=request_fingerprint)
        return payment_payload


def _clone_request_with_payment_signature(
    original_request: httpx.Request,
    encoded_signature: str,
) -> httpx.Request:
    """Create a retry httpx.Request carrying the ``PAYMENT-SIGNATURE`` header."""
    updated_headers = httpx.Headers(original_request.headers)
    updated_headers[PAYMENT_SIGNATURE_HEADER] = encoded_signature
    return httpx.Request(
        method=original_request.method,
        url=original_request.url,
        headers=updated_headers,
        content=original_request.content,
        extensions=dict(original_request.extensions),
    )


class PaygateSyncTransport(httpx.BaseTransport):
    """Synchronous HTTPX transport that intercepts HTTP 402 challenges and auto-retries."""

    def __init__(
        self,
        inner_transport: httpx.BaseTransport,
        guard: SpendPolicyGuard,
    ) -> None:
        self.inner_transport: httpx.BaseTransport = inner_transport
        self.guard: SpendPolicyGuard = guard

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Send request and automatically answer an HTTP 402 challenge if policy approves."""
        initial_response: httpx.Response = self.inner_transport.handle_request(request)

        if initial_response.status_code != 402:
            return initial_response

        if PAYMENT_SIGNATURE_HEADER in request.headers:
            return initial_response

        challenge_header_value: str | None = initial_response.headers.get(PAYMENT_REQUIRED_HEADER)
        if not challenge_header_value:
            return initial_response

        initial_response.close()
        request_body_bytes: bytes = request.read()
        request_fingerprint: str = compute_request_fingerprint(
            http_method=request.method,
            request_url=str(request.url),
            request_body=request_body_bytes,
        )
        was_already_cached: bool = self.guard.get_cached_payload(request_fingerprint) is not None

        payment_required: PaymentRequired = decode_payment_required(challenge_header_value)
        payment_payload: PaymentPayload = self.guard.build_payment_payload_for_challenge(
            payment_required=payment_required,
            request_fingerprint=request_fingerprint,
        )
        encoded_signature: str = encode_payment_signature(payment_payload)
        retry_request: httpx.Request = _clone_request_with_payment_signature(
            original_request=request,
            encoded_signature=encoded_signature,
        )

        try:
            retry_response: httpx.Response = self.inner_transport.handle_request(retry_request)
        except httpx.TransportError:
            if not was_already_cached:
                self.guard.release_reservation(request_fingerprint=request_fingerprint)
            raise

        receipt_header_value: str | None = retry_response.headers.get(PAYMENT_RESPONSE_HEADER)
        if receipt_header_value:
            try:
                decoded_receipt = decode_payment_response(receipt_header_value)
                self.guard.record_settlement_receipt(
                    request_fingerprint=request_fingerprint,
                    transaction_hash=decoded_receipt.transaction_hash,
                )
            except PaygateProtocolError:
                pass
        return retry_response

    def close(self) -> None:
        """Close the wrapped synchronous transport."""
        self.inner_transport.close()


class PaygateAsyncTransport(httpx.AsyncBaseTransport):
    """Asynchronous HTTPX transport that intercepts HTTP 402 challenges and auto-retries."""

    def __init__(
        self,
        inner_transport: httpx.AsyncBaseTransport,
        guard: SpendPolicyGuard,
    ) -> None:
        self.inner_transport: httpx.AsyncBaseTransport = inner_transport
        self.guard: SpendPolicyGuard = guard

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send async request and automatically answer an HTTP 402 challenge if approved."""
        initial_response: httpx.Response = await self.inner_transport.handle_async_request(request)

        if initial_response.status_code != 402:
            return initial_response

        if PAYMENT_SIGNATURE_HEADER in request.headers:
            return initial_response

        challenge_header_value: str | None = initial_response.headers.get(PAYMENT_REQUIRED_HEADER)
        if not challenge_header_value:
            return initial_response

        await initial_response.aclose()
        request_body_bytes: bytes = await request.aread()
        request_fingerprint: str = compute_request_fingerprint(
            http_method=request.method,
            request_url=str(request.url),
            request_body=request_body_bytes,
        )
        was_already_cached: bool = self.guard.get_cached_payload(request_fingerprint) is not None

        payment_required: PaymentRequired = decode_payment_required(challenge_header_value)
        payment_payload: PaymentPayload = self.guard.build_payment_payload_for_challenge(
            payment_required=payment_required,
            request_fingerprint=request_fingerprint,
        )
        encoded_signature: str = encode_payment_signature(payment_payload)
        retry_request: httpx.Request = _clone_request_with_payment_signature(
            original_request=request,
            encoded_signature=encoded_signature,
        )

        try:
            retry_response: httpx.Response = await self.inner_transport.handle_async_request(
                retry_request
            )
        except httpx.TransportError:
            if not was_already_cached:
                self.guard.release_reservation(request_fingerprint=request_fingerprint)
            raise

        receipt_header_value: str | None = retry_response.headers.get(PAYMENT_RESPONSE_HEADER)
        if receipt_header_value:
            try:
                decoded_receipt = decode_payment_response(receipt_header_value)
                self.guard.record_settlement_receipt(
                    request_fingerprint=request_fingerprint,
                    transaction_hash=decoded_receipt.transaction_hash,
                )
            except PaygateProtocolError:
                pass
        return retry_response

    async def aclose(self) -> None:
        """Close the wrapped asynchronous transport."""
        await self.inner_transport.aclose()


def wrap_httpx_with_paygate(client: ClientT, guard: SpendPolicyGuard) -> ClientT:
    """Wrap an ``httpx.Client`` or ``httpx.AsyncClient`` with an HTTP 402 Paygate transport.

    Mutates the client's transport in-place and returns the same client instance
    for backwards compatibility.
    """
    if isinstance(client, httpx.Client):
        current_sync_transport: httpx.BaseTransport = getattr(
            client, "_transport", httpx.HTTPTransport()
        )
        if not isinstance(current_sync_transport, PaygateSyncTransport):
            client._transport = PaygateSyncTransport(
                inner_transport=current_sync_transport,
                guard=guard,
            )
        return client

    if isinstance(client, httpx.AsyncClient):
        current_async_transport: httpx.AsyncBaseTransport = getattr(
            client, "_transport", httpx.AsyncHTTPTransport()
        )
        if not isinstance(current_async_transport, PaygateAsyncTransport):
            client._transport = PaygateAsyncTransport(
                inner_transport=current_async_transport,
                guard=guard,
            )
        return client

    return client


__all__ = [
    "SqliteIdempotencyStore",
    "SpendPolicyGuard",
    "PaygateSyncTransport",
    "PaygateAsyncTransport",
    "wrap_httpx_with_paygate",
    "decimal_to_atomic_units",
    "atomic_units_to_decimal",
    "compute_request_fingerprint",
]
