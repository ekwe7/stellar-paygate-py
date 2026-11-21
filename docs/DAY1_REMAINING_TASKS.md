# Day 1 – Remaining Tasks (Core Schemas, Codec & Baseline Quality Gates)

The Day 1 objectives from the 5‑day MVP plan are **not yet fully satisfied**. The following items still need to be completed before Day 1 can be marked as done.

## 📋 Outstanding Items

1. **Export concrete public symbols**
   - Update `src/stellar_paygate/__init__.py` to expose the core models and helpers directly (e.g. `Scheme`, `FeeMode`, `PayGateErrorCode`, `PaymentRequired`, `PaymentPayload`, `SettlementResponse`, `PaygateProtocolError`).
   - Ensure the `__all__` list contains these names so `from stellar_paygate import *` works as expected.

2. **Create baseline test suite** (`tests/test_core.py`)
   - **Round‑trip encode/decode** tests for each header type (`PaymentRequired`, `PaymentPayload`, `SettlementResponse`).
   - **Negative tests** covering:
     - Malformed Base64 strings.
     - Non‑JSON payloads.
     - JSON objects missing required fields.
   - Verify that failures raise `PaygateProtocolError` with the correct `PayGateErrorCode`.

3. **Run static analysis & enforce quality gates**
   - Execute:
     ```bash
     uv run ruff check src tests
     uv run mypy --strict src tests
     uv run pytest -q
     ```
   - Fix any lint, type‑checking, or test failures that arise.

4. **Documentation sanity‑check**
   - Confirm that the public API surface described in `README.md` matches the symbols exported in `__init__.py`.
   - Add a short “Day 1 completed” note to `docs/BUILD_PLAN_5_DAYS.md` (e.g. a checklist tick).

## ✅ Acceptance Criteria for Day 1
- `__init__.py` exports all core symbols and the `__all__` list is up‑to‑date.
- `tests/test_core.py` exists, contains ≥ 20 assertions, and all tests pass.
- `ruff` reports **0 issues**, `mypy` reports **no errors**, and `pytest` exits with a success code.
- Documentation updated accordingly and the Day 1 entry in the master build plan is checked off.

Once the above steps are finished and the CI pipeline passes, Day 1 can be considered complete and the team can move on to Day 2.
