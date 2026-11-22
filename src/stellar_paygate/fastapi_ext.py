"""FastAPI paywall middleware and route dependencies for stellar-paygate-py.

Provides HTTP 402 challenge issuance, ``PAYMENT-SIGNATURE`` decoding and
verification, idempotent settlement, and ``PAYMENT-RESPONSE`` receipt injection
via :func:`add_payment_middleware`, :func:`require_payment` (fixed-price ``exact``
scheme), and :func:`require_upto_ceiling` (metered ``upto`` scheme with
:class:`MeteredSettlementContext`).
"""

# NOTE: ``from __future__ import annotations`` is intentionally omitted here
# so that FastAPI's dependency injector receives concrete ``Request`` and
# ``Response`` runtime classes rather than stringified forward references.

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .codec import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    decode_payment_signature,
    encode_payment_required,
    encode_payment_response,
)
from .exact import (
    ExactVerifier,
    UptoVerifier,
    VerificationPort,
    VerifierConfig,
)
from .types import (
    PayGateErrorCode,
    PaygateProtocolError,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirement,
    ResourceInfo,
    Scheme,
    SettleResponse,
    VerifyResponse,
)

DEFAULT_UNPROTECTED_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


def build_payment_required_challenge(
    request: Request,
    requirement: PaymentRequirement,
    error_message: str | None = None,
) -> PaymentRequired:
    """Construct a ``PaymentRequired`` challenge model for the active HTTP request."""
    resource_info = ResourceInfo(
        url=str(request.url),
        description=f"Protected resource at {request.url.path}",
        mime_type="application/json",
    )
    return PaymentRequired(
        error=error_message,
        resource=resource_info,
        accepts=[requirement],
    )


def build_402_json_response(
    request: Request,
    requirement: PaymentRequirement,
    error_code: str = "payment_required",
    error_detail: str = "Payment required to access this resource",
) -> JSONResponse:
    """Construct a FastAPI ``JSONResponse`` with HTTP 402 and ``PAYMENT-REQUIRED`` header."""
    challenge_model: PaymentRequired = build_payment_required_challenge(
        request=request,
        requirement=requirement,
        error_message=error_detail,
    )
    encoded_challenge_header: str = encode_payment_required(challenge_model)
    return JSONResponse(
        status_code=402,
        headers={PAYMENT_REQUIRED_HEADER: encoded_challenge_header},
        content={
            "error": error_code,
            "detail": error_detail,
        },
    )


def raise_402_http_exception(
    request: Request,
    requirement: PaymentRequirement,
    error_code: str = "payment_required",
    error_detail: str = "Payment required to access this resource",
) -> None:
    """Raise a FastAPI ``HTTPException(402)`` carrying the ``PAYMENT-REQUIRED`` header."""
    challenge_model: PaymentRequired = build_payment_required_challenge(
        request=request,
        requirement=requirement,
        error_message=error_detail,
    )
    encoded_challenge_header: str = encode_payment_required(challenge_model)
    raise HTTPException(
        status_code=402,
        detail={
            "error": error_code,
            "detail": error_detail,
        },
        headers={PAYMENT_REQUIRED_HEADER: encoded_challenge_header},
    )


def validate_payload_matches_route_requirement(
    payment_payload: PaymentPayload,
    route_requirement: PaymentRequirement,
) -> tuple[PayGateErrorCode, str] | None:
    """Ensure the incoming signed payload satisfies the route's advertised requirement."""
    payload_requirement: PaymentRequirement = payment_payload.requirement

    if payload_requirement.scheme != route_requirement.scheme:
        return (
            PayGateErrorCode.UNSUPPORTED_SCHEME,
            f"Route requires scheme '{route_requirement.scheme}', "
            f"received '{payload_requirement.scheme}'",
        )

    if payload_requirement.network != route_requirement.network:
        return (
            PayGateErrorCode.UNSUPPORTED_NETWORK,
            f"Route requires network '{route_requirement.network}', "
            f"received '{payload_requirement.network}'",
        )

    if payload_requirement.asset != route_requirement.asset:
        return (
            PayGateErrorCode.INVALID_ADDRESS,
            f"Route requires asset '{route_requirement.asset}', "
            f"received '{payload_requirement.asset}'",
        )

    if payload_requirement.pay_to != route_requirement.pay_to:
        return (
            PayGateErrorCode.INVALID_ADDRESS,
            f"Route requires payTo '{route_requirement.pay_to}', "
            f"received '{payload_requirement.pay_to}'",
        )

    return None


@dataclass
class MeteredSettlementContext:
    """Context injected by ``require_upto_ceiling`` for post-execution metered billing."""

    payment_payload: PaymentPayload
    requirement: PaymentRequirement
    response: Response
    verifier: UptoVerifier
    current_ledger: int | None = None
    last_settlement: SettleResponse | None = field(default=None, init=False)

    def settle_usage(self, actual_amount_stroops: int | str) -> SettleResponse:
        """Settle exact post-execution resource usage against the authorized ceiling.

        Attaches the Base64-encoded ``PAYMENT-RESPONSE`` receipt header to the
        active FastAPI ``Response``. Raises ``HTTPException(402)`` if the
        requested usage is non-positive or exceeds the client's signed ceiling.
        """
        settlement_result: SettleResponse = self.verifier.settle(
            payment_payload=self.payment_payload,
            current_ledger=self.current_ledger,
            requested_amount=actual_amount_stroops,
        )
        if not settlement_result.success:
            failure_code_value: str = (
                settlement_result.error_code.value
                if settlement_result.error_code is not None
                else PayGateErrorCode.SETTLEMENT_FAILURE.value
            )
            failure_reason: str = settlement_result.error_reason or "Metered upto settlement failed"
            raise HTTPException(
                status_code=402,
                detail={
                    "error": failure_code_value,
                    "detail": failure_reason,
                },
            )

        self.last_settlement = settlement_result
        self.response.headers[PAYMENT_RESPONSE_HEADER] = encode_payment_response(settlement_result)
        return settlement_result


def require_payment(
    requirement: PaymentRequirement,
    verifier: ExactVerifier | None = None,
    verifier_config: VerifierConfig | None = None,
    current_ledger: int | None = None,
) -> Callable[[Request, Response], SettleResponse]:
    """FastAPI dependency enforcing an ``exact`` fixed-price payment on a route.

    Returns the confirmed :class:`SettleResponse` and attaches the
    ``PAYMENT-RESPONSE`` receipt header to the outgoing HTTP response.
    """
    active_verifier: ExactVerifier = verifier or ExactVerifier(config=verifier_config)

    def _exact_payment_dependency(
        request: Request,
        response: Response,
    ) -> SettleResponse:
        signature_header_value: str | None = request.headers.get(PAYMENT_SIGNATURE_HEADER)
        if not signature_header_value:
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code="payment_required",
                error_detail="Missing PAYMENT-SIGNATURE header",
            )

        try:
            decoded_payload: PaymentPayload = decode_payment_signature(
                signature_header_value  # type: ignore[arg-type]
            )
        except PaygateProtocolError as protocol_error:
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=protocol_error.code.value,
                error_detail=protocol_error.detail or str(protocol_error),
            )

        mismatch_failure = validate_payload_matches_route_requirement(
            payment_payload=decoded_payload,
            route_requirement=requirement,
        )
        if mismatch_failure is not None:
            mismatch_code, mismatch_reason = mismatch_failure
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=mismatch_code.value,
                error_detail=mismatch_reason,
            )

        settlement_result: SettleResponse = active_verifier.settle(
            payment_payload=decoded_payload,
            current_ledger=current_ledger,
            requested_amount=requirement.amount,
        )
        if not settlement_result.success:
            failure_code: str = (
                settlement_result.error_code.value
                if settlement_result.error_code is not None
                else PayGateErrorCode.SETTLEMENT_FAILURE.value
            )
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=failure_code,
                error_detail=settlement_result.error_reason or "Payment verification failed",
            )

        response.headers[PAYMENT_RESPONSE_HEADER] = encode_payment_response(settlement_result)
        return settlement_result

    _exact_payment_dependency.__annotations__ = {
        "request": Request,
        "response": Response,
        "return": SettleResponse,
    }
    return _exact_payment_dependency


def require_upto_ceiling(
    requirement: PaymentRequirement,
    verifier: UptoVerifier | None = None,
    verifier_config: VerifierConfig | None = None,
    current_ledger: int | None = None,
) -> Callable[[Request, Response], MeteredSettlementContext]:
    """FastAPI dependency pre-verifying an ``upto`` ceiling authorization on a route.

    Injects a :class:`MeteredSettlementContext` so the route handler can call
    ``meter.settle_usage(actual_amount_stroops)`` after execution completes.
    """
    active_verifier: UptoVerifier = verifier or UptoVerifier(config=verifier_config)

    def _upto_ceiling_dependency(
        request: Request,
        response: Response,
    ) -> MeteredSettlementContext:
        signature_header_value: str | None = request.headers.get(PAYMENT_SIGNATURE_HEADER)
        if not signature_header_value:
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code="payment_required",
                error_detail="Missing PAYMENT-SIGNATURE header",
            )

        try:
            decoded_payload: PaymentPayload = decode_payment_signature(
                signature_header_value  # type: ignore[arg-type]
            )
        except PaygateProtocolError as protocol_error:
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=protocol_error.code.value,
                error_detail=protocol_error.detail or str(protocol_error),
            )

        mismatch_failure = validate_payload_matches_route_requirement(
            payment_payload=decoded_payload,
            route_requirement=requirement,
        )
        if mismatch_failure is not None:
            mismatch_code, mismatch_reason = mismatch_failure
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=mismatch_code.value,
                error_detail=mismatch_reason,
            )

        if int(decoded_payload.requirement.amount.strip()) < int(requirement.amount.strip()):
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=PayGateErrorCode.AMOUNT_MISMATCH.value,
                error_detail=(
                    f"Signed ceiling ({decoded_payload.requirement.amount}) is lower "
                    f"than required route ceiling ({requirement.amount})"
                ),
            )

        pre_verification: VerifyResponse = active_verifier.verify(
            payment_payload=decoded_payload,
            current_ledger=current_ledger,
        )
        if not pre_verification.success:
            failure_code: str = (
                pre_verification.error_code.value
                if pre_verification.error_code is not None
                else PayGateErrorCode.INVALID_PAYLOAD.value
            )
            raise_402_http_exception(
                request=request,
                requirement=requirement,
                error_code=failure_code,
                error_detail=pre_verification.error_reason or "Upto ceiling verification failed",
            )

        return MeteredSettlementContext(
            payment_payload=decoded_payload,
            requirement=requirement,
            response=response,
            verifier=active_verifier,
            current_ledger=current_ledger,
        )

    _upto_ceiling_dependency.__annotations__ = {
        "request": Request,
        "response": Response,
        "return": MeteredSettlementContext,
    }
    return _upto_ceiling_dependency


def _is_path_protected(
    request_path: str,
    protected_paths: Collection[str] | None,
    unprotected_paths: Collection[str],
) -> bool:
    """Determine whether an incoming request path requires payment verification."""
    if request_path in unprotected_paths:
        return False

    if protected_paths is None:
        return True

    return any(
        request_path == protected_prefix
        or request_path.startswith(f"{protected_prefix.rstrip('/')}/")
        for protected_prefix in protected_paths
    )


def add_payment_middleware(
    app: FastAPI,
    requirement: PaymentRequirement | None = None,
    protected_paths: Collection[str] | None = None,
    unprotected_paths: Collection[str] = DEFAULT_UNPROTECTED_PATHS,
    verifier: VerificationPort | None = None,
    verifier_config: VerifierConfig | None = None,
    current_ledger: int | None = None,
) -> None:
    """Register HTTP 402 payment verification middleware on a FastAPI application.

    * When ``requirement`` is ``None``, requests without ``PAYMENT-SIGNATURE``
      pass through cleanly (maintaining backwards compatibility), while requests
      carrying ``PAYMENT-SIGNATURE`` are decoded, verified, and settled.
    * When ``requirement`` is provided, protected paths must include a valid
      ``PAYMENT-SIGNATURE`` header or receive an ``HTTP 402`` challenge with
      ``PAYMENT-REQUIRED``.
    """
    configured_verifier: VerificationPort | None = verifier
    if configured_verifier is None and requirement is not None:
        configured_verifier = (
            UptoVerifier(config=verifier_config)
            if requirement.scheme == Scheme.UPTO.value
            else ExactVerifier(config=verifier_config)
        )
    fallback_exact_verifier = ExactVerifier(config=verifier_config)
    fallback_upto_verifier = UptoVerifier(config=verifier_config)

    @app.middleware("http")
    async def _payment_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path_is_protected: bool = _is_path_protected(
            request_path=request.url.path,
            protected_paths=protected_paths,
            unprotected_paths=unprotected_paths,
        )
        if not path_is_protected:
            return await call_next(request)

        signature_header_value: str | None = request.headers.get(PAYMENT_SIGNATURE_HEADER)
        if not signature_header_value:
            if requirement is None:
                return await call_next(request)
            return build_402_json_response(
                request=request,
                requirement=requirement,
                error_code="payment_required",
                error_detail="Missing PAYMENT-SIGNATURE header",
            )

        try:
            decoded_payload: PaymentPayload = decode_payment_signature(signature_header_value)
        except PaygateProtocolError as protocol_error:
            if requirement is not None:
                return build_402_json_response(
                    request=request,
                    requirement=requirement,
                    error_code=protocol_error.code.value,
                    error_detail=protocol_error.detail or str(protocol_error),
                )
            return JSONResponse(
                status_code=400,
                content={
                    "error": protocol_error.code.value,
                    "detail": protocol_error.detail or str(protocol_error),
                },
            )

        if requirement is not None:
            mismatch_failure = validate_payload_matches_route_requirement(
                payment_payload=decoded_payload,
                route_requirement=requirement,
            )
            if mismatch_failure is not None:
                mismatch_code, mismatch_reason = mismatch_failure
                return build_402_json_response(
                    request=request,
                    requirement=requirement,
                    error_code=mismatch_code.value,
                    error_detail=mismatch_reason,
                )

        active_verifier: VerificationPort = (
            configured_verifier
            if configured_verifier is not None
            else (
                fallback_upto_verifier
                if decoded_payload.requirement.scheme == Scheme.UPTO.value
                else fallback_exact_verifier
            )
        )
        requested_amount: str | None = (
            requirement.amount
            if requirement is not None and requirement.scheme == Scheme.EXACT.value
            else None
        )
        settlement_result: SettleResponse = active_verifier.settle(
            payment_payload=decoded_payload,
            current_ledger=current_ledger,
            requested_amount=requested_amount,
        )

        if not settlement_result.success:
            failure_code_str: str = (
                settlement_result.error_code.value
                if settlement_result.error_code is not None
                else PayGateErrorCode.SETTLEMENT_FAILURE.value
            )
            failure_detail_str: str = (
                settlement_result.error_reason or "Payment verification failed"
            )
            if requirement is not None:
                return build_402_json_response(
                    request=request,
                    requirement=requirement,
                    error_code=failure_code_str,
                    error_detail=failure_detail_str,
                )
            return JSONResponse(
                status_code=402,
                content={
                    "error": failure_code_str,
                    "detail": failure_detail_str,
                },
            )

        request.state.payment_settlement = settlement_result
        downstream_response: Response = await call_next(request)
        downstream_response.headers[PAYMENT_RESPONSE_HEADER] = encode_payment_response(
            settlement_result
        )
        return downstream_response


__all__ = [
    "MeteredSettlementContext",
    "add_payment_middleware",
    "require_payment",
    "require_upto_ceiling",
    "build_payment_required_challenge",
]
