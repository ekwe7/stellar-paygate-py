# MVP Validation Checklist — `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Document:** `docs/MVP_VALIDATION_CHECKLIST.md`  
**Target Release:** v0.1.0 (MVP)  
**Scope:** Pre-release verification of architectural boundaries, offline fault-injection scenarios (`S01`–`S10`), FastAPI/`httpx` integration, static typing, and security invariants.  

---

## 1. Safety & Offline Determinism Guarantee

All automated verification commands in this checklist are strictly **offline and non-custodial**:
* **Zero Live Network Traffic**: All tests use `FaultInjectingSorobanRpc` (`fault_harness.py`) and `httpx.ASGITransport` in memory.
* **Zero Real Private Keys**: All tests generate ephemeral in-memory `Keypair.random()` instances.
* **Zero On-Chain Spend**: Incurs zero Testnet or Mainnet transaction fees.

---

## 2. Architectural & Quality Gate Checklist

| Check ID | Gate Description | Verification Target | Expected Outcome |
| :---: | :--- | :--- | :--- |
| **QG-1** | Hexagonal Inward Dependency Rule | `types.py`, `codec.py`, `exact.py` | Zero imports from `fastapi`, `starlette`, or `httpx` inside domain core modules. |
| **QG-2** | Ruff Linter Compliance | `src/` and `tests/` | Zero warnings or errors across `E`, `F`, `I`, `B`, `UP`, and `SIM` rules. |
| **QG-3** | Ruff Formatter Compliance | `src/` and `tests/` | Zero unformatted files (`100`-character line width). |
| **QG-4** | Mypy Strict Static Typing | `src/` and `tests/` | Zero type errors with `strict = true` and `pydantic.mypy` plugin enabled. |
| **QG-5** | Pytest Suite & Line Coverage | `tests/` | 100% passing tests with $\ge 85\%$ line coverage across `stellar_paygate`. |

---

## 3. Deterministic Conformance & Fault-Injection Checklist (`stellar-paygate-sim`)

| Scenario ID | Scenario Under Verification | Expected Conformance Result |
| :---: | :--- | :--- |
| **`S01`** | Baseline `G-account` fee-sponsored `exact` SEP-41 settlement | `PASS` (`success=True`, `settledAmount="1000000"`) |
| **`S02`** | Load-balanced RPC `+4` ledger skew (`tolerance=2` vs. `tolerance=5`) | `PASS` (`legacy=invalid_exact_stellar_signature_expiration_too_far`, `fixed=isValid:True`) |
| **`S03`** | Smart-account (`C-address`) with 2 `__check_auth` policy events & `95k` stroop fee | `PASS` (`isValid=True`, `policyEventsObserved=2`) |
| **`S04`** | Rogue secondary token `transfer` event injection (anti-drain check) | `PASS` (`invalid_exact_stellar_payload_unexpected_token_transfer`) |
| **`S05`** | CAP-67 muxed `payTo` (`M-account`) event matching & ID mismatch rejection | `PASS` (`matching=True`, `mismatched=invalid_exact_stellar_recipient_mismatch`) |
| **`S06`** | Unsponsored (`areFeesSponsored: false`) client-signed `TransactionEnvelope` | `PASS` (`success=True` with verified outer `TransactionEnvelope`) |
| **`S07`** | Refundable rent-fee drift (`0%` margin vs. `+15%` safety margin) | `PASS` (`unpadded=settlement_stellar_insufficient_refundable_fee`, `padded=success:True`) |
| **`S08`** | Idempotent `/settle` deduplication & cross-payload nonce replay rejection | `PASS` (`submissions=1`, `idempotentReplay=True`, `replay=invalid_exact_stellar_nonce_replayed`) |
| **`S09`** | Metered `upto` scheme: authorize `0.10 USDC` ceiling, settle `0.007 USDC` actual | `PASS` (`success=True`, `settledAmount="70000"`) |
| **`S10`** | Metered `upto` scheme: reject over-ceiling settlement (`actual > max_amount`) | `PASS` (`invalid_upto_stellar_exceeds_authorized_max`) |

---

## 4. Client Policy & FastAPI / `httpx` End-to-End Checklist

- [ ] **Exact Decimal Quantization**: `"0.51"` converts to `5_100_000` atomic units without float drift; $> 7$ decimal places raises `ValueError`.
- [ ] **ASGI Middleware Paywall**: Unauthenticated request receives `402` + `PAYMENT-REQUIRED`; `httpx` auto-pay retry receives `200 OK` + `PAYMENT-RESPONSE`.
- [ ] **FastAPI `require_payment` (`exact`)**: Route handler receives verified `SettleResponse` with confirmed transaction hash and payer address.
- [ ] **FastAPI `require_upto_ceiling` (`upto`)**: Route handler settles post-execution metered token usage via `meter.settle_usage(actual_cost_stroops)`.
- [ ] **Policy Guard Enforcement**: Per-call cap breaches, rolling budget breaches, and untrusted origins abort client-side before signing.
