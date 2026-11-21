# Product Requirements Document (PRD) — `stellar-paygate-py`

**Project Name:** `stellar-paygate-py`  
**Current Phase:** Specification & Validation Phase (Docs-First Baseline)  
**Architecture:** Modular Monolith (Ports & Adapters / Hexagonal)  
**Target Release:** v0.1.0 (MVP)  
**Target Ecosystem:** Stellar Network / Soroban SEP-41 Smart Contracts & Python AI/API Stack  
**Community Program Alignment:** Drips Wave Stellar Program  

---

## 1. Problem Statement & Verifiable Evidence

### 1.1 The Protocol Reality
Machine-to-machine and autonomous AI agent payments over HTTP use the standard **HTTP `402 Payment Required`** status code (compatible with the open HTTP 402 / x402 v2 payment specification). On the Stellar network, token transfers are executed via the **Soroban SEP-41 Token Interface** (`transfer(from, to, amount)`):
* In a **fee-sponsored** flow (`areFeesSponsored: true`), the buyer signs a `SorobanAuthorizationEntry` (`ENVELOPE_TYPE_SOROBAN_AUTHORIZATION`) authorizing a single contract invocation with a replay `nonce` and a ledger-sequence expiration (`signature_expiration_ledger`). A facilitator or server wraps that authorization entry into a Stellar transaction, sponsors the XLM inclusion/resource fee, and submits it to a Soroban RPC node.
* In an **unsponsored** flow (`areFeesSponsored: false`), the buyer constructs and signs a full `TransactionEnvelope` with `timeBounds.maxTime` and pays their own XLM fee.
* In a **metered ceiling** flow (`upto`), the buyer authorizes a maximum spending ceiling upfront, and the server settles the actual consumed amount (`0 < actual_amount <= max_amount`) after the API workload completes.

### 1.2 The Operational Pain Points in the Stellar Ecosystem
1. **No Native Python Mechanism for Stellar HTTP 402 Payments**:
   * While TypeScript/Node.js has multiple HTTP 402 packages on Stellar, the upstream multi-chain Python SDK (`python/x402/mechanisms`) supports only EVM, Solana, Cardano, and Tron—with **zero Stellar/Soroban support**.
   * Yet Python (`FastAPI`, `Starlette`, `httpx`, LiteLLM, LangChain, CrewAI, PydanticAI, and MCP servers) is the dominant language for AI agent runtimes and inference APIs.
2. **Intermittent Payment Rejections from Load-Balanced Soroban RPC Skew**:
   * Public and commercial Soroban RPC endpoints sit behind load balancers whose backing nodes frequently differ by 2 to 4 ledgers.
   * Existing verifiers hardcode a 2-ledger expiration tolerance, intermittently rejecting valid payments with `invalid_exact_stellar_signature_expiration_too_far` whenever the client samples a node ahead of the verifier's node.
3. **Soroban Smart-Account (`C-Address`) & Policy-Event Rejections**:
   * Autonomous agents increasingly use Soroban custom account contracts (`C-addresses`) enforcing on-chain spend policies via `__check_auth`.
   * Existing verifiers abort simulation checks if *any* contract event is emitted before the SEP-41 `transfer` event (`invalid_exact_stellar_payload_event_not_transfer`), or reject smart-account calls whose cross-contract `__check_auth` simulation exceeds a rigid `50,000`-stroop default fee ceiling.
4. **Unimplemented Unsponsored (`areFeesSponsored: false`) & Metered (`upto`) Flows**:
   * Existing TypeScript facilitators accept `areFeesSponsored: false` without validation and fail 100% of settlements.
   * Variable-cost AI inference APIs cannot charge per generated token using fixed-price `exact` challenges and require a single-transaction `upto` ceiling authorization and post-execution settlement flow.
5. **CAP-67 Muxed Address (`M-Address`) Attribution Failures**:
   * Merchants using virtual muxed destination addresses (`M...`) emit CAP-67 transfer events containing `to_muxed_id` metadata that fails standard `ScAddress` equality checks.
6. **Refundable Rent-Fee Underestimation (`InsufficientRefundableFee`)**:
   * When a token transfer extends persistent storage TTLs, a 1–2 ledger advance between `simulateTransaction` and transaction submission causes unpadded refundable rent fees to fail on-chain roughly 50% of the time.
7. **Lack of Deterministic Offline Fault-Injection Tooling**:
   * Developers currently rely on live Testnet RPC nodes and manual Friendbot/USDC faucets to test payment gates, leaving edge cases and race conditions untested in CI.

---

## 2. Target Users & Personas

### Persona A: The Python AI Inference & API Provider
* **Profile**: Backend engineer operating a FastAPI or Starlette service (LLM inference, financial data feed, or specialized MCP server).
* **Pain Point**: Wants to monetize endpoints per call (`exact`) or per generated token (`upto`) in Stellar USDC without building custom Soroban XDR parsers or deploying separate Node.js sidecar containers.
* **Need**: Drop-in FastAPI middleware and route dependencies (`fastapi_ext.py`) backed by a hardened verification engine (`exact.py`, `codec.py`).

### Persona B: The Autonomous AI Agent Developer
* **Profile**: Engineer building autonomous research or trading agents in Python using `httpx`, LangChain, CrewAI, or MCP tools.
* **Pain Point**: Needs the agent to automatically negotiate HTTP `402` challenges while enforcing strict local budget guardrails (per-call cap, rolling session budget, origin/asset allowlists) and avoiding double-spends on HTTP 502/504 retries.
* **Need**: An `httpx` async transport wrapper and exact-integer `SpendPolicyGuard` (`client.py`).

### Persona C: The Protocol & Security Auditor
* **Profile**: Open-source contributor or CI/CD engineer verifying payment gateway conformance across Stellar protocol upgrades.
* **Pain Point**: Live Testnet tests are slow, non-deterministic, and cannot inject exact `+4` ledger skew, rogue token-drain events, or `InsufficientRefundableFee` conditions on demand.
* **Need**: A 100% offline deterministic fault-injecting RPC harness and CLI conformance runner (`fault_harness.py`).

---

## 3. User Stories

* **User Story 1 (Fixed-Price FastAPI Monetization)**: As a FastAPI developer, I want to attach a single route dependency or middleware specifying a price in Stellar USDC so that unpaid callers receive a compliant `402 PAYMENT-REQUIRED` challenge and paid callers receive a `200 OK` response with a `PAYMENT-RESPONSE` transaction receipt.
* **User Story 2 (Metered LLM Inference Billing)**: As an AI API provider, I want to pre-verify a client's signed `upto` ceiling authorization before running inference and settle only the actual tokens consumed (`actual_amount <= max_amount`) once generation finishes.
* **User Story 3 (Policy-Guarded Agent Auto-Pay)**: As an agent builder, I want my `httpx.AsyncClient` to automatically pay valid `402` challenges up to my configured per-call and rolling USDC limits using exact integer arithmetic, while aborting immediately if a challenge exceeds my policy or comes from an untrusted origin.
* **User Story 4 (Smart-Account & Muxed Address Support)**: As a Stellar wallet/protocol integrator, I want payments from `C-address` smart accounts (with `__check_auth` policy events) and payments to `M-address` muxed merchant accounts to verify and settle reliably.
* **User Story 5 (Offline CI Fault Conformance)**: As a contributor, I want to run a deterministic CLI command (`stellar-paygate-sim`) in CI that executes 10 positive and negative fault-injection scenarios offline in under 5 seconds.

---

## 4. Functional Requirements (MVP v0.1.0)

* **FR-1 (`types.py`)**: Provide strict Pydantic v2 DTOs and Base64 header codecs for `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, and `PAYMENT-RESPONSE`, plus a canonical `PayGateErrorCode` taxonomy.
* **FR-2 (`codec.py`)**: Build, sign, decode, and cryptographically verify `SorobanAuthorizationEntry` payloads (`G-accounts` and `C-accounts`), unsponsored `TransactionEnvelope` payloads (`areFeesSponsored: false`), and simulated `DiagnosticEvent` lists (filtering non-token policy events, blocking rogue secondary transfers, and verifying CAP-67 `to_muxed_id`).
* **FR-3 (`exact.py`)**: Verify and idempotently settle `exact` and `upto` payloads with configurable `skew_tolerance_ledgers` (default `5`), account-aware fee ceilings (`50,000` stroops for `G-accounts`, `250,000` stroops for `C-accounts`), `+15%` refundable rent-fee safety padding, and `(payer, nonce)` replay protection.
* **FR-4 (`client.py`)**: Provide `SpendPolicyGuard` (using exact integer and `decimal.Decimal` arithmetic), an in-flight request idempotency cache, and an `httpx.AsyncBaseTransport` wrapper.
* **FR-5 (`fastapi_ext.py`)**: Provide `PayGateASGIMiddleware`, `require_payment` (`exact`), and `require_upto_ceiling` (`MeteredSettlementContext`) for FastAPI and Starlette.
* **FR-6 (`fault_harness.py`)**: Provide `FaultInjectingSorobanRpc` implementing `SorobanRpcPort` in memory and the `stellar-paygate-sim` CLI conformance runner covering 10 canonical scenarios (`S01`–`S10`).

---

## 5. Non-Functional Requirements (NFRs)

* **NFR-1 (Offline Determinism)**: 100% of the default test suite and CLI conformance scenarios must execute offline with zero external network sockets or secret keys.
* **NFR-2 (Exact Arithmetic)**: Zero use of `float` for currency or fee calculations; all values use Python `int` atomic units or quantized `decimal.Decimal`.
* **NFR-3 (Strict Typing & Linting)**: 100% compliance with `mypy --strict`, `ruff check`, and `ruff format --check`.
* **NFR-4 (Hexagonal Isolation)**: Domain core modules (`types.py`, `codec.py`, `exact.py`) must have zero imports from `fastapi`, `starlette`, or `httpx`.
