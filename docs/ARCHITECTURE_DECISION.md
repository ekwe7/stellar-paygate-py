# Architecture Decision Record (ADR): Modular Monolith with Ports & Adapters

**Project:** `stellar-paygate-py`  
**Document:** `docs/ARCHITECTURE_DECISION.md`  
**Status:** Approved  
**Deciders:** Core Engineering Team & Project Maintainer  
**Date:** November 2026 Baseline  

---

## 1. Context & Problem Statement

`stellar-paygate-py` is an open-source Python toolkit for HTTP 402 pay-per-request API monetization, Soroban SEP-41 payment verification, autonomous agent spend-policy enforcement, and deterministic offline fault injection on the Stellar network.

Before writing any application code, we must formalize:
1. **Primary Architectural Pattern**: Choosing between distributed facilitator microservices, framework-coupled monoliths, or a Hexagonal Modular Monolith (Ports & Adapters).
2. **Package & Module Boundaries**: Partitioning the Python package into six cohesive modules (`types.py`, `codec.py`, `exact.py`, `client.py`, `fastapi_ext.py`, `fault_harness.py`) that prevent circular imports and enable parallel Drips Wave contributions.
3. **Protocol vs. Transport Decoupling**: Ensuring Soroban XDR serialization, Ed25519 signature verification, simulation event scanning, and ledger-skew math remain 100% independent of FastAPI, Starlette, `httpx`, and live Soroban RPC sockets.

---

## 2. Evaluation of Architectural Candidates

```text
+---------------------------------------------------------------------------------------------------+
|                                 ARCHITECTURAL PATTERN EVALUATION                                  |
+---------------------+------------------------------+-----------------------+----------------------+
| Criteria            | Candidate A: Microservices   | Candidate B: Framework| Candidate C: Modular |
|                     | (Gateway + Facilitator + DB) | Coupled Monolith      | Monolith (Hexagonal) |
+---------------------+------------------------------+-----------------------+----------------------+
| Deployment Units    | 3+ Containers (Proxy,        | Single FastAPI app    | Single Python pkg +  |
|                     | Facilitator Svc, Redis/PG)   | with inline RPC calls | `stellar-paygate-sim`|
| Operational Overhead| Prohibitive: Requires Docker | Moderate: Hard to use | Minimal: `pip`/`uv`  |
|                     | Compose / K8s for basic use  | outside FastAPI       | install, zero daemons|
| Maintainer Burden   | High: Multi-service schemas, | High: Brittle mocks,  | Low: Pure functions  |
| (Solo Maintainer)   | container orchestration      | tangled XDR & HTTP    | & clean port boundary|
| Domain Isolation    | High (Process boundary)      | Low (No port layer)   | High (Hexagonal port |
|                     |                              |                       | & module rules)      |
| Offline CI Testing  | Poor: Needs full stack up    | Poor: Monkeypatching  | Optimal: In-memory   |
|                     |                              | live RPC calls        | `fault_harness.py`   |
| Verdict             | REJECTED                     | REJECTED              | ADOPTED              |
+---------------------+------------------------------+-----------------------+----------------------+
```

### Decision: Adopt Candidate C — Modular Monolith with Ports & Adapters (Hexagonal Architecture)

1. **Single-Package Ergonomics**: Both API servers (`fastapi_ext.py`) and AI agent clients (`client.py`) install a single Python package (`stellar-paygate`) with zero mandatory sidecars.
2. **Strict Port Abstraction (`SorobanRpcPort`)**: The verification and settlement engine (`exact.py`) communicates with Stellar RPC exclusively through the abstract `SorobanRpcPort` protocol interface.
3. **Deterministic Fault Harness (`fault_harness.py`)**: Because `FaultInjectingSorobanRpc` implements `SorobanRpcPort` in memory, all 10 conformance and fault scenarios run deterministically offline in milliseconds.

---

## 3. Hexagonal Layering & Cross-Module Boundary

```text
+---------------------------------------------------------------------------------------------------+
|                             DRIVING ADAPTERS (Outer Input Layer)                                  |
|                                                                                                   |
|  +------------------------------+  +------------------------------+  +-------------------------+  |
|  | client.py                    |  | fastapi_ext.py               |  | fault_harness.py (CLI)  |  |
|  | • httpx Async Transport      |  | • PayGateASGIMiddleware      |  | • stellar-paygate-sim   |  |
|  | • SpendPolicyGuard           |  | • require_payment (exact)    |  | • Scenario Runner       |  |
|  | • In-Flight Retry Cache      |  | • require_upto_ceiling       |  | • Table / JSON Reporter |  |
|  +--------------+---------------+  +--------------+---------------+  +------------+------------+  |
+-----------------|---------------------------------|-------------------------------|---------------+
                  |                                 |                               |
                  v                                 v                               v
+---------------------------------------------------------------------------------------------------+
|                        APPLICATION & DOMAIN CORE (Zero Network / Socket I/O)                      |
|                                                                                                   |
|  +---------------------------------------------------------------------------------------------+  |
|  | exact.py (Verification & Idempotent Settlement Orchestrator)                                |  |
|  | • ExactStellarClient / ExactStellarFacilitator & UptoStellarClient / UptoStellarFacilitator |  |
|  | • Adaptive Ledger-Skew Window Validator & Account-Aware Fee Ceiling Enforcer                |  |
|  | • Refundable Rent-Fee Safety Margin (+15%) & Idempotent Settlement Outbox                   |  |
|  +----------------------------------------------+----------------------------------------------+  |
|                                                 |                                                 |
|                                                 v                                                 |
|  +----------------------------------------------+----------------------------------------------+  |
|  | codec.py (Cryptographic & Soroban XDR Engine)                                               |  |
|  | • SorobanAuthorizationEntry Builder, Preimage Hasher & Ed25519 Verifier (G- & C-Accounts)   |  |
|  | • Unsponsored TransactionEnvelope Builder & Structural Verifier                             |  |
|  | • Multi-Event Simulation Scanner (C-Account Policy Events + CAP-67 Muxed M-Account Checks)  |  |
|  +----------------------------------------------+----------------------------------------------+  |
|                                                 |                                                 |
|                                                 v                                                 |
|  +----------------------------------------------+----------------------------------------------+  |
|  | types.py (Domain DTOs, Header Codecs, Network Profiles & PayGateErrorCode Taxonomy)         |  |
|  +---------------------------------------------------------------------------------------------+  |
+-------------------------------------------------|-------------------------------------------------+
                                                  |
                                                  | Calls Abstract Port: SorobanRpcPort
                                                  v
+---------------------------------------------------------------------------------------------------+
|                            DRIVEN ADAPTERS (Outer Infrastructure Layer)                           |
|                                                                                                   |
|  +----------------------------------------------+  +-------------------------------------------+  |
|  | fault_harness.py (FaultInjectingSorobanRpc)  |  | Live Soroban JSON-RPC 2.0 Adapter         |  |
|  | • Deterministic Offline Simulation & Faults  |  | • Testnet / Mainnet HTTP Transport        |  |
|  +----------------------------------------------+  +-------------------------------------------+  |
+---------------------------------------------------------------------------------------------------+
```

---

## 4. Strict Inward Dependency Rules

> [!IMPORTANT]
> **The Inward Dependency Rule**:
> 1. `types.py` is the leaf domain module and imports zero internal modules.
> 2. `codec.py` depends only on `types.py` and `stellar-sdk` XDR primitives. It must never perform network I/O or import `httpx`/`fastapi`.
> 3. `exact.py` depends only on `types.py`, `codec.py`, and the abstract `SorobanRpcPort` interface.
> 4. Outer adapters (`client.py`, `fastapi_ext.py`, and the CLI runner in `fault_harness.py`) depend inward on `types.py`, `codec.py`, and `exact.py`.

---

## 5. Consequences of This Decision

### Positive Consequences
* **100% Offline CI & Instant Developer Feedback**: Contributors can run the entire test suite and fault-injection conformance matrix in `< 3 seconds` without internet access or Testnet accounts.
* **Zero Merge-Conflict Bottlenecks in Drips Wave**: Each of the 6 modules owns a distinct layer of the hexagon, allowing contributors to work on `codec.py`, `client.py`, `fastapi_ext.py`, or `fault_harness.py` simultaneously.

### Trade-Offs
* **In-Process State by Default**: The MVP stores idempotency and settlement deduplication maps in process memory (with a scoped Phase 2 Drips Wave issue to add a persistent `sqlite3` adapter for multi-restart durability).
