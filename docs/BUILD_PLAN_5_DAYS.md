# Five‑Day MVP Build & Edge‑Case Execution Plan – `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Target release:** `v0.1.0` (MVP)  
**Working directory:** `/home/ekwe/stellar-paygate-py`

---

## 1️⃣ Current Baseline Snapshot

| Area | Status | Remarks |
|------|--------|---------|
| **Core schemas & error taxonomy** | ✅ `src/stellar_paygate/types.py` – enums (`Scheme`, `FeeMode`, `PayGateErrorCode`), `PaygateProtocolError`, Pydantic v2 models (`ResourceInfo`, `StellarExtraConfig`, `PaymentRequirement`, `PaymentRequired`, `StellarPaymentPayloadData`, `PaymentPayload`, `SettlementResponse`, `FaultInjectionConfig`). | Pure core layer, no framework imports.
| **Header codec** | ✅ `src/stellar_paygate/codec.py` – Base64‑JSON `encode_*/decode_*` helpers for `PAYMENT‑REQUIRED`, `PAYMENT‑SIGNATURE`, `PAYMENT‑RESPONSE`. Errors raise `PaygateProtocolError` with appropriate `PayGateErrorCode`.
| **Stubs / scaffolding** | ✅ Placeholder files (`client.py`, `fastapi_ext.py`, `exact.py`, `fault_harness.py`) compile.
| **Packaging & CI** | ✅ `pyproject.toml` (dependencies, ruff, mypy, pytest‑asyncio), GitHub Actions workflow (`.github/workflows/ci.yml`).
| **Tests** | ❌ No test files yet – only an empty `tests/` directory.
| **Documentation** | ✅ MVP scope, checklist, architecture, edge‑case, fault‑injection docs already present.
| **Remaining MVP work (v0.1.0)** | • Implement verification and policy logic (`exact.py`).
• Implement HTTP‑client and FastAPI adapters (`client.py`, `fastapi_ext.py`).
• Implement deterministic fault‑injection harness (`fault_harness.py`).
• Write comprehensive unit / integration tests covering all edge cases.
• Update docs to reflect final behaviour and ensure checklist compliance.

---

## 2️⃣ Day‑by‑Day Build Schedule (All tasks are **code‑free** for this planning step)

### Day 1 – Core Schemas, Codec & Baseline Quality Gates
- **Goals**
  1. Verify naming consistency across `types.py`, `codec.py`, `exact.py`, `fault_harness.py`.
  2. Export public symbols (`Scheme`, `FeeMode`, `PayGateErrorCode`, `PaymentRequired`, `PaymentPayload`, `SettlementResponse`, `FaultInjectionConfig`, `PaygateProtocolError`) in `src/stellar_paygate/__init__.py`.
  3. Create baseline test module `tests/test_core.py`:
     - Round‑trip encode/decode for each header type.
     - Negative cases: malformed Base64, non‑JSON, missing required fields → expect `PaygateProtocolError` with correct `PayGateErrorCode`.
  4. Run static analysis: `ruff`, `mypy --strict`, `pytest` – ensure 0 violations.
- **Deliverables**
  - Updated `__init__.py`.
  - `tests/test_core.py` (≈ 30 assertions).
- **Exit criteria**
  - All core tests pass.
  - Quality gates clean.

### Day 2 – Verification & Policy Engine (`exact.py` + Ports)
- **Goals**
  1. Define a **port interface** `VerificationPort` (protocol) that accepts a `PaymentPayload` and returns a `SettlementResponse`.
  2. Implement two concrete verifiers in `exact.py`:
     - `ExactVerifier` – enforces `scheme == "exact"` (amount must match), validates ledger window, clock‑skew tolerance, fee‑mode, address format, and refundable‑fee safety margin (+15 %).
     - `UptoVerifier` – same checks plus ceiling enforcement (`amount <= authorized_amount`).
  3. Add unit tests (`tests/test_verification.py`) covering every rule and edge‑case matrix:
     - Exact vs Upto match/mismatch.
     - Sponsored vs Unsponsored fee handling.
     - Ledger skew within/outside tolerance.
     - Address types G‑ / C‑ / M‑ (valid and invalid forms).
     - Expiration / TTL windows.
  4. Ensure imports remain hexagonal: `exact.py` only depends on `types` and standard library.
- **Deliverables**
  - `exact.py` with `VerificationPort`, `ExactVerifier`, `UptoVerifier`.
  - `tests/test_verification.py`.
- **Exit criteria**
  - All verification tests pass.
  - No lint or type errors.

### Day 3 – HTTPX Client & FastAPI Paywall Adapters (`client.py`, `fastapi_ext.py`)
- **Goals**
  1. Complete `SpendPolicyGuard` (integer caps, rolling budget enforcement).
  2. Implement `wrap_httpx_with_paygate` async transport:
     - Detect 402, decode via `codec.decode_payment_required`.
     - Select appropriate verifier (`ExactVerifier` / `UptoVerifier`) via a factory based on `scheme`.
     - Call verifier, obtain `SettlementResponse`.
     - Encode `PaymentPayload` via `codec.encode_payment_signature` and retry request with the signature header.
  3. Implement FastAPI middleware `add_payment_middleware`:
     - On each request, call the wrapped `httpx.AsyncClient`.
     - On success, add `PAYMENT-RESPONSE` header.
     - On verification failure, raise HTTPException with proper status and error code.
  4. Write integration tests (`tests/test_client_fastapi.py`) using a minimal FastAPI app and `httpx.AsyncClient` – no external network.
- **Deliverables**
  - Updated `client.py` and `fastapi_ext.py`.
  - Integration test file.
- **Exit criteria**
  - End‑to‑end client flow works in‑process.
  - Quality gates remain clean.

### Day 4 – Deterministic Offline Fault‑Injection Harness (`fault_harness.py` & S01‑S10)
- **Goals**
  1. Expand `FaultInjectionConfig` (already present) with documented knobs:
     - `ledger_skew`, `allow_unsponsored_fees`, `force_settlement_failure`, `override_settled_amount`, `duplicate_delivery_count`.
  2. Implement `FaultInjectingSorobanRpc` that reads the config and simulates:
     - **S01** – Clock‑skew error (ledger skew beyond tolerance).
     - **S02** – Expired authorization (ledger > expiration).
     - **S03** – Unsponsored fee rejection when `allow_unsponsored_fees=False`.
     - **S04** – Upto ceiling overrun.
     - **S05‑S07** – Invalid address formats for G‑, C‑, M‑.
     - **S08** – Duplicate delivery/count idempotency.
     - **S09** – Forced settlement failure.
     - **S10** – Malformed payload / header.
  3. Provide a CLI entry‑point `stellar-paygate-sim` (exposed in `pyproject.toml` scripts) that runs the 10 scenarios and reports pass/fail.
  4. Add test suite `tests/test_fault_harness.py` asserting that each scenario returns the expected `PayGateErrorCode` or success.
- **Deliverables**
  - Fully functional `fault_harness.py`.
  - CLI script.
  - Fault‑injection tests.
- **Exit criteria**
  - All 10 scenarios executable and verifiable.
  - Test coverage for the harness ≥ 100 %.

### Day 5 – End‑to‑End Offline Conformance, Docs Alignment & Release Readiness (`AC‑1`‑`AC‑5`)
- **Goals**
  1. Execute the full integration matrix (combine all schemes, fee modes, address types, ledger windows, and fault‑injection configs) via a single parametrized test module `tests/test_conformance.py`.
  2. Run the **MVP Validation Checklist** (`docs/MVP_VALIDATION_CHECKLIST.md`) manually and verify each item is satisfied.
  3. Update any out‑of‑sync documentation sections:
     - `protocol-and-edge-cases.md`
     - `fault-injection.md`
     - `BUILD_PLAN_5_DAYS.md` (finalized version).
  4. Perform final CI run:
     - `uv run ruff check src tests`
     - `uv run mypy --strict src tests`
     - `uv run pytest -q --cov=src`
     - Ensure coverage ≥ 85 %.
  5. Tag the repository `v0.1.0` and draft a release note linking to the checklist and the 5‑day plan.
- **Deliverables**
  - Final test suite passing.
  - Clean CI artifacts.
  - Git tag and draft release.
- **Exit criteria**
  - All quality gates pass, coverage target met.
  - Release tag created.
  - Documentation fully reflects the shipped MVP.

---

## 3️⃣ Edge‑Case Checklist (Mapped to Tests & Daily Deliverables)
| Category | Edge case | Expected `PayGateErrorCode` | Test module |
|----------|-----------|----------------------------|------------|
| **Scheme** | Exact amount mismatch | `AMOUNT_MISMATCH` | `test_verification.py` |
| | Upto amount exceeds ceiling | `EXCEEDS_MAX_UPTO` | `test_verification.py` |
| **Fee Mode** | Unsponsored fee exceeds allowance | `UNSPONSORED_TX_TIMEBOUNDS_INVALID` | `test_verification.py` |
| **Ledger / Clock** | Ledger skew > tolerance | `CLOCK_SKEW_EXCEEDED` | `test_fault_harness.py` (S01) |
| **Address** | Invalid G‑, C‑, M‑ format | `INVALID_ADDRESS` | `test_fault_harness.py` (S05‑S07) |
| **Refundable Fee** | Fee below +15 % safety margin | `FEE_EXCEEDS_MAXIMUM` | `test_verification.py` |
| **Expiration / TTL** | Authorization expired before use | `EXPIRED_AUTHORIZATION` | `test_fault_harness.py` (S02) |
| **Idempotency** | Duplicate delivery count > 1 | No double settlement, same `transactionHash` | `test_fault_harness.py` (S08) |
| **Settlement Failure** | RPC returns error → settlement fails | `SETTLEMENT_SUBMISSION_FAILED` | `test_fault_harness.py` (S09) |
| **Malformed Header** | Bad Base64 / non‑JSON / missing fields | `INVALID_HEADER` / `INVALID_PAYLOAD` | `test_core.py` |
| **Protocol‑Level** | Any other taxonomy violation | Corresponding `PayGateErrorCode` | `test_core.py` |

---

## 4️⃣ Daily "Done" Gate (to be satisfied at the end of each day)
- [ ] All tasks for the day are **implemented** (or explicitly deferred with justification). 
- [ ] **Unit / integration tests** for the day's work exist **and pass** (`uv run pytest -q`).
- [ ] **Static analysis** passes:
  - `uv run ruff check src tests` → 0 issues.
  - `uv run mypy --strict src tests` → no errors.
- [ ] **Documentation** updated to reflect new behaviour (README, tech‑design, edge‑case docs, checklist).
- [ ] Changes are **committed** with a clear “Day X‑summary” message and pushed.

---

*The plan deliberately refrains from writing any application code now – it only defines a realistic, test‑driven schedule that can be executed by the team to deliver a complete, non‑custodial MVP within five days.*
