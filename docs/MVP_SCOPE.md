# Minimum Viable Product (MVP) Scope Specification — `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Document:** `docs/MVP_SCOPE.md`  
**Target Release:** v0.1.0  
**Architecture:** Modular Monolith (6 Core Modules in `src/stellar_paygate/`)  
**Document Status:** Approved Scope Baseline  

---

## 1. Executive Summary & Guiding Principle

The objective of the **`stellar-paygate-py` MVP (v0.1.0)** is to ship a self-contained, production-grade Python HTTP 402 payment gate, agent spend-policy client, and deterministic offline fault-injection harness for Stellar/Soroban SEP-41 tokens.

### The Guiding Principle: "Deterministic Protocol Correctness First"
Rather than bundling external databases, background queue daemons, or web dashboards into v0.1.0, the MVP focuses strictly on:
1. **Wire-Compliant HTTP 402 Headers & DTOs (`types.py`)**: Base64 JSON encoding/decoding of `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, and `PAYMENT-RESPONSE` plus canonical machine-readable error codes.
2. **Cryptographic Soroban XDR & Multi-Event Codec (`codec.py`)**: Full support for `G-accounts`, `C-accounts` (smart accounts with `__check_auth` policy events), `M-accounts` (CAP-67 muxed destinations), and unsponsored (`areFeesSponsored: false`) transaction envelopes.
3. **Hardened Verification & Idempotent Settlement (`exact.py`)**: Both fixed-price `exact` and metered-ceiling `upto` flows with adaptive RPC ledger-skew tolerance (`5` ledgers default), account-aware fee ceilings (`50k` / `250k` stroops), `+15%` refundable rent-fee safety margin, and nonce replay protection.
4. **Exact-Integer Client Guard & `httpx` Auto-Pay Transport (`client.py`)**: `SpendPolicyGuard` using `int` and `decimal.Decimal` arithmetic (zero float precision loss) and an in-flight retry idempotency cache.
5. **FastAPI / Starlette ASGI Paywall (`fastapi_ext.py`)**: Drop-in ASGI middleware, `require_payment` (`exact`), and `require_upto_ceiling` (`MeteredSettlementContext`).
6. **100% Offline Deterministic Fault Harness & CLI (`fault_harness.py`)**: `FaultInjectingSorobanRpc` and the `stellar-paygate-sim` CLI executing 10 canonical conformance scenarios (`S01`–`S10`).

---

## 2. Explicit Inclusion vs. Post-MVP Exclusion Matrix

```text
+-----------------------------------------------------------------------------------+
|                            MVP SCOPE BOUNDARY (v0.1.0)                            |
|                                                                                   |
|  [ IN-SCOPE FOR v0.1.0 MVP ]                                                      |
|  • types.py: PaymentRequired, PaymentPayload, VerifyResponse, SettleResponse      |
|  • codec.py: SorobanAuthorizationEntry (G & C), Unsponsored Tx, CAP-67 Event Scan |
|  • exact.py: Exact & Upto verifiers, skew tolerance, +15% rent margin, replay map |
|  • client.py: Exact-integer SpendPolicyGuard, inflight cache, httpx transport     |
|  • fastapi_ext.py: PayGateASGIMiddleware, require_payment, require_upto_ceiling   |
|  • fault_harness.py: Offline FaultInjectingSorobanRpc & 10-scenario CLI runner    |
|                                                                                   |
|  [ EXPLICITLY DEFERRED TO POST-MVP ROADMAP (v0.2.0+ / Drips Wave Issues) ]        |
|  • SQLite persistent idempotency & receipt store across agent restarts (Issue #6) |
|  • FastAPI Server-Sent Events (SSE) real-time streaming LLM token meter (Issue #10)
|  • Async pending-settlement outbox worker with backoff & DLQ (Issue #9)           |
|  • Discovery pricing metadata (typical vs. ceiling) drift tracker (Issue #8)      |
|  • SARIF v2.1.0 and JUnit XML CLI output formatters (Issue #7)                    |
|  • Multi-sig weighted threshold auth-entry vectors & FeeBump envelopes (#4, #5)   |
+-----------------------------------------------------------------------------------+
```

---

## 3. CLI Specification: `stellar-paygate-sim`

### Usage & Flags
* `stellar-paygate-sim`: Runs all 10 deterministic conformance and fault-injection scenarios (`S01`–`S10`) and prints a Markdown report table to `stdout`.
* `stellar-paygate-sim --json`: Emits a structured JSON conformance report containing `all_passed`, `total`, `passed`, and the per-scenario `expected_outcome` vs. `observed_outcome`.

### Process Exit Codes
* `0`: All 10 deterministic scenarios passed.
* `1`: One or more scenarios failed conformance verification.
* `10`: Invalid CLI flags or arguments.

---

## 4. MVP Acceptance Criteria (v0.1.0)

| Criterion ID | Requirement | Verification Method |
| :---: | :--- | :--- |
| **AC-1** | All 10 scenarios (`S01`–`S10`) in `stellar-paygate-sim` pass offline in `< 3 seconds` | CLI execution & automated test |
| **AC-2** | FastAPI `exact` and `upto` routes complete end-to-end 402 challenge, signing, settlement, and receipt verification with `httpx` | Offline ASGI E2E test suite |
| **AC-3** | Smart-account policy events (`C-address`) and CAP-67 muxed recipients (`M-address`) verify cleanly while rogue secondary transfers are blocked | Unit & fault-injection tests |
| **AC-4** | `SpendPolicyGuard` rejects per-call cap breaches, rolling budget breaches, untrusted origins, and sub-stroop fractional dust | Unit boundary tests |
| **AC-5** | Zero errors under `ruff check`, `ruff format --check`, and `mypy --strict`, with $\ge 85\%$ test coverage | GitHub Actions CI pipeline |
