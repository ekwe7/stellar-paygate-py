# Comprehensive Testing Strategy & Quality Assurance Plan — `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Document:** `docs/TESTING_STRATEGY.md`  
**Architecture:** Modular Monolith (Hexagonal / Ports & Adapters)  
**Test Frameworks:** `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx.ASGITransport`, `FaultInjectingSorobanRpc`  
**Detailed Fault Reference:** [`docs/fault-injection.md`](fault-injection.md)  

---

## 1. Hexagonal Testing Pyramid

```text
                  / \
                 /   \       Opt-In Live Testnet Smoke Checks
                / E2E \      (Real Soroban RPC, Testnet USDC Accounts)
               /-------\
              /         \    In-Process ASGI & httpx Transport Integration Tests
             /  Adapters \   (FastAPI Paywall + httpx AsyncTransport + SpendPolicyGuard)
            /-------------\
           /               \ Pure XDR Unit Tests & Deterministic Fault-Harness Suite
          /   Unit & Fault  \(types.py, codec.py, exact.py, fault_harness.py — 100% Offline)
         /-------------------\
```

1. **Pure XDR & Domain Unit Tests (`tests/test_codec_and_types.py`, `tests/test_exact_and_faults.py`)**:
   * Executes in `< 1 second` with zero network access.
   * Verifies Base64 JSON header encoding/decoding (`PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, `PAYMENT-RESPONSE`), `SorobanAuthorizationEntry` preimage hashing, Ed25519 signature verification, tampered amount/recipient rejection, unsponsored `TransactionEnvelope` validation, smart-account policy event filtering, and CAP-67 muxed ID verification.
2. **Deterministic Fault-Injection Suite (`fault_harness.py` & `tests/test_exact_and_faults.py`)**:
   * Reproduces 10 canonical protocol and distributed systems scenarios (`S01`–`S10`) using `FaultInjectingSorobanRpc` and `FaultInjectionConfig` (`FaultConfig`), including `+4` ledger RPC skew, `95,000`-stroop smart-account fees, rogue secondary token-drain events, and `InsufficientRefundableFee` rent drift.
3. **In-Process ASGI & Agent Transport Tests (`tests/test_exact_and_faults.py`)**:
   * Connects an `httpx.AsyncClient` (wrapped with `client.py`) directly to a `FastAPI` instance (protected by `fastapi_ext.py`) over `httpx.ASGITransport` with `FaultInjectingSorobanRpc` injected into both sides.
   * Verifies full `402` challenge negotiation, `SpendPolicyGuard` budget accounting, `exact` and `upto` settlements, and `PAYMENT-RESPONSE` receipt decoding.

---

## 2. Boundary & Adversarial Test Matrix

| Test Category | Condition Under Test | Expected Outcome |
| :--- | :--- | :--- |
| **XDR Integrity** | Outer payload JSON claims `900,000` stroops while signed XDR authorizes `100,000` | Rejected with `invalid_exact_stellar_amount_mismatch` |
| **Sub-Invocations** | `SorobanAuthorizationEntry` contains non-empty `sub_invocations` | Rejected with `invalid_exact_stellar_unexpected_sub_invocations` |
| **RPC Ledger Skew** | Client ledger is `+4` ahead of facilitator (`skew_tolerance_ledgers=2` vs `5`) | Legacy `2` rejects (`invalid_exact_stellar_signature_expiration_too_far`); default `5` passes |
| **Smart Accounts** | `C-address` emits 2 `__check_auth` policy events before SEP-41 `transfer` | Passes verification; records `policyEventsObserved` count = `2` |
| **Anti-Drain Guard** | Simulation emits a second unexpected SEP-41 `transfer` event to an third-party address | Rejected with `invalid_exact_stellar_payload_unexpected_token_transfer` |
| **CAP-67 Muxed** | `payTo` is `M...` (muxed ID `987654321`), simulation emits mismatched muxed ID | Rejected with `invalid_exact_stellar_recipient_mismatch` |
| **Rent Drift** | Submission requires `43,500` stroops while simulation reported `40,000` stroops | Unpadded (`0%`) fails (`settlement_stellar_insufficient_refundable_fee`); padded (`+15%`) passes |
| **Idempotency** | Identical signed payload submitted to `settle()` twice | Second call returns cached `tx_hash` with `idempotentReplay=True` and `0` extra RPC submissions |
| **Nonce Replay** | Consumed `(payer, nonce)` reused on a new payload with different recipient/amount | Rejected with `invalid_exact_stellar_nonce_replayed` |
| **Upto Ceiling** | Server attempts to settle `1,500,000` stroops against a `1,000,000` stroop `upto` ceiling | Rejected with `invalid_upto_stellar_exceeds_authorized_max` |
| **Spend Policy** | Challenge exceeds `max_per_call_atomic` or `max_rolling_budget_atomic` | Client aborts before signing with `policy_stellar_*_exceeded` |
