# Implementation Roadmap & Milestone Schedule — `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Document:** `docs/IMPLEMENTATION_ROADMAP.md`  
**Architecture:** Modular Monolith (Hexagonal / Ports & Adapters)  
**Target Delivery Window:** Sprints 1–4 (Aligned with Stellar Drips Wave Cycles)  
**Status:** Approved Specification Baseline  

---

## 1. Overview & Phased Delivery Strategy

```text
[Phase 0: Docs & Architecture Specification Baseline (COMPLETED)]
                     |
                     v
[Phase 1: Core Domain DTOs & Pure XDR/Event Codecs (`types.py`, `codec.py`)]
                     |
                     v
[Phase 2: Verification Engine & Offline Fault Harness (`exact.py`, `fault_harness.py`)]
                     |
                     v
[Phase 3: FastAPI Paywall & Agent httpx Transport (`client.py`, `fastapi_ext.py`)] <--- MILESTONE 1: MVP RELEASE (v0.1.0)
                     |
                     +----------------------------------------------+
                     | Post-MVP Drips Wave Expansion                |
                     v                                              |
[Phase 4: Multi-Sig, FeeBump, SQLite Store & SARIF/JUnit Exporters] <--- MILESTONE 2: v0.2.0
                     |
                     v
[Phase 5: Async Settlement Outbox, Discovery Drift & SSE Streaming] <--- MILESTONE 3: v0.3.0
```

---

## 2. Detailed Implementation Phases

### Phase 0: Documentation & Architecture Specification (Current Baseline)
* **Deliverables**: Complete `README.md`, `CONTRIBUTING.md`, and the 15-document specification suite under `docs/` with Mermaid and ASCII architecture, sequence, and fault-injection decision diagrams.
* **Status**: Completed prior to writing any application code.

### Phase 1: Pure Domain DTOs & Cryptographic XDR Codecs (`types.py`, `codec.py`)
* **Objective**: Implement all wire models, error codes, Soroban authorization preimage signers/verifiers, unsponsored transaction verifiers, and simulation event scanners with zero network I/O.
* **Deliverables**:
  * `types.py`: `PaymentRequired`, `PaymentRequirements`, `PaymentPayload`, `VerifyResponse`, `SettleResponse`, `DiagnosticMeta`, `PayGateErrorCode`.
  * `codec.py`: `G-account` and `C-account` `SorobanAuthorizationEntry` codec, unsponsored `TransactionEnvelope` codec, CAP-67 muxed address normalizer, and multi-event simulation scanner.
* **Definition of Done**: 100% passing offline XDR roundtrip and signature tampering unit tests.

### Phase 2: Verification Engine & Deterministic Fault Harness (`exact.py`, `fault_harness.py`)
* **Objective**: Implement the `exact` and `upto` verification and idempotent settlement engine against `SorobanRpcPort` and verify all 10 fault scenarios via `FaultInjectingSorobanRpc`.
* **Deliverables**:
  * `exact.py`: `ExactStellarClient`, `ExactStellarFacilitator`, `UptoStellarClient`, `UptoStellarFacilitator`, `FacilitatorConfig`.
  * `fault_harness.py`: `FaultInjectingSorobanRpc`, `FaultConfig`, and `stellar-paygate-sim` CLI.
* **Definition of Done**: `stellar-paygate-sim` passes all 10 conformance scenarios (`S01`–`S10`) offline.

### Phase 3: Client Policy Guard, `httpx` Transport & FastAPI Paywall (`client.py`, `fastapi_ext.py` — MVP v0.1.0)
* **Objective**: Deliver the user-facing `httpx` agent transport, exact-integer `SpendPolicyGuard`, and FastAPI/Starlette ASGI middleware and route dependencies.
* **Deliverables**:
  * `client.py`: `SpendPolicyGuard`, `SpendPolicyConfig`, `X402StellarAsyncTransport`, `wrap_httpx_with_paygate`.
  * `fastapi_ext.py`: `PayGateASGIMiddleware`, `require_payment`, `require_upto_ceiling`, `MeteredSettlementContext`.
* **Definition of Done**: End-to-end offline ASGI + `httpx` tests pass with zero `ruff` or `mypy --strict` errors; tagged as **v0.1.0**.

### Phase 4 & Phase 5: Drips Wave Community Expansion (v0.2.0 – v0.3.0)
* **Objective**: Execute the 10 scoped Drips Wave issues documented in [`docs/OPEN_SOURCE_CONTRIBUTION_PLAN.md`](OPEN_SOURCE_CONTRIBUTION_PLAN.md) across Trivial (`100` pts), Medium (`150` pts), and High (`200` pts) complexity tiers.

---

## 3. Milestone Summary Table

| Milestone | Target Version | Focus | Verifiable Output |
| :---: | :---: | :--- | :--- |
| **M0** | Docs Baseline | Complete Architecture & Diagram Suite | 100% docs & diagrams signed off before code |
| **M1 (MVP)** | **v0.1.0** | **6-Module Core + Offline Fault Harness** | **FastAPI/httpx E2E + 10-scenario CLI green** |
| **M2** | v0.2.0 | Multi-Sig, FeeBump, SQLite & CI Formatters | Drips Wave Issues #1–#7 merged |
| **M3** | v0.3.0 | Discovery Drift, Outbox Worker & SSE Meter | Drips Wave Issues #8–#10 merged |
