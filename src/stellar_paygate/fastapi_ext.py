# fastapi_ext.py – Stub module for FastAPI extensions
"""FastAPI extension utilities for stellar-paygate-py.

This file provides placeholder functions and classes that will be expanded
with real implementation later. Keeping the module importable now allows
type‑checking and linting to succeed.

The design mirrors the style used in `soroban-sentinel` – lightweight
wrappers around FastAPI routes that integrate Stellar payment verification.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response


def add_payment_middleware(app: FastAPI) -> None:
    """Register a dummy payment‑required middleware.

    The real implementation will inspect incoming HTTP 402 headers and
    validate Stellar payment signatures. For now it simply passes the request
    through unchanged.
    """

    @app.middleware("http")
    async def _payment_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Placeholder – no verification performed
        response = await call_next(request)
        return response


# Export name for convenient import
__all__ = ["add_payment_middleware"]
