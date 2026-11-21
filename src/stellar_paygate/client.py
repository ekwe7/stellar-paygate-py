"""client.py placeholder
Provides a stub SpendPolicyGuard and httpx transport wrapper.
"""

from __future__ import annotations

from typing import TypeVar

import httpx

from .types import PaymentPayload

ClientT = TypeVar("ClientT", httpx.Client, httpx.AsyncClient)


class SpendPolicyGuard:
    """Simple stub that always allows spending."""

    def __init__(self, max_per_call: int = 1_000_000_000, max_budget: int = 10_000_000_000) -> None:
        self.max_per_call = max_per_call
        self.max_budget = max_budget

    def check(self, payload: PaymentPayload) -> None:
        # In real implementation, enforce limits. Here we accept everything.
        pass


def wrap_httpx_with_paygate(client: ClientT, guard: SpendPolicyGuard) -> ClientT:
    """Placeholder wrapper that returns the client unchanged."""
    return client
