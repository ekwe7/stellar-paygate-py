# Research, Ecosystem Reference & Technical Verification Dossier — `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Document:** `docs/RESEARCH_AND_REFERENCES.md`  
**Research Date:** November 2026 Baseline  
**Subject:** Stellar / Soroban HTTP 402 Machine-to-Machine Payments, SEP-41 Authorization Mechanics & Verification Edge Cases  

---

## 1. Primary Specifications & Repositories Inspected

### 1.1 Stellar Protocol Standards (CAPs & SEPs)
* **[SEP-0041: Soroban Token Interface](https://github.com/stellar/stellar-protocol/blob/master/ecosystem/sep-0041.md)**: Defines the standard `transfer(from, to, amount)`, `approve`, and `transfer_from` contract interface and `transfer` contract event format for Stellar Asset Contracts (SAC) and Soroban tokens.
* **[CAP-0046-06: Soroban Authorization Framework](https://github.com/stellar/stellar-protocol/blob/master/core/cap-0046-06.md)**: Defines `SorobanAuthorizationEntry`, `SorobanCredentials`, `SorobanAddressCredentials`, `ENVELOPE_TYPE_SOROBAN_AUTHORIZATION`, and custom account `__check_auth` invocation semantics.
* **[CAP-0067: Unified Asset Events & Muxed Address Support](https://github.com/stellar/stellar-protocol/blob/master/core/cap-0067.md)**: Defines how SEP-41 token transfer events encode 64-bit muxed account destination IDs (`to_muxed_id`) inside an `SCV_MAP` event data payload (`{amount: i128, to_muxed_id: u64}`).
* **[CAP-0071: Custom Account Credentials & Delegated Signers](https://github.com/stellar/stellar-protocol)**: Defines extended credential shapes for Soroban smart accounts.

### 1.2 Upstream HTTP 402 & Stellar Ecosystem Repositories Audited
* **[`x402-foundation/x402`](https://github.com/x402-foundation/x402)**: Inspected `typescript/packages/mechanisms/stellar`, `python/x402/mechanisms`, `go/mechanisms`, and `specs/schemes/`. Verified that `python/x402/mechanisms` only includes `["cardano", "evm", "svm", "tvm"]` and lacks a Stellar mechanism.
* **[`stellar/x402-stellar`](https://github.com/stellar/x402-stellar)**: Official SDF reference repository for HTTP 402 on Stellar (TypeScript). Inspected open issues around unsponsored client-paid fee flows (`#44`, `#45`), metered `upto` ceiling settlement (`#71`, `#72`), and float precision / receipt display issues (`#74`).
* **[`stellar/stellar-rpc`](https://github.com/stellar/stellar-rpc)**: Verified `simulateTransaction` refundable fee underestimation on multi-entry TTL extensions (`stellar/stellar-rpc#918`).

---

## 2. Verified Ecosystem Gaps & How `stellar-paygate-py` Solves Them

| Ecosystem Finding | Primary Evidence Source | Impact on Developers | Solution in `stellar-paygate-py` |
| :--- | :--- | :--- | :--- |
| **Missing Python Stellar HTTP 402 SDK** | `x402-foundation/x402/python/x402/mechanisms` | Python FastAPI servers and Python AI agents cannot natively verify or sign Stellar HTTP 402 payments. | Native Python package (`client.py`, `fastapi_ext.py`, `exact.py`, `codec.py`, `types.py`). |
| **Load-Balanced RPC 2-Ledger Skew Rejections** | [`x402-foundation/x402#3168`](https://github.com/x402-foundation/x402/issues/3168) | Valid payments fail with `invalid_exact_stellar_signature_expiration_too_far` when RPC nodes diverge by $\ge 3$ ledgers. | Configurable `skew_tolerance_ledgers` (default `5`) + `skewDeltaLedgers` diagnostic telemetry. |
| **Smart-Account (`C-Address`) Policy Event Rejection** | [`x402-foundation/x402#3352`](https://github.com/x402-foundation/x402/issues/3352) | Verifiers abort when a smart account's `__check_auth` policy contract emits an event before `transfer`. | Multi-event scanner in `codec.py` records policy events while blocking rogue secondary token transfers. |
| **Smart-Account 50,000-Stroop Fee Ceiling** | [`x402-foundation/x402#3515`](https://github.com/x402-foundation/x402/issues/3515) | Cross-contract `__check_auth` calls consume 70k–120k stroops and get rejected by the 50k G-account default. | Separate `max_smart_account_fee_stroops` (`250,000` default) in `FacilitatorConfig`. |
| **Broken Unsponsored (`areFeesSponsored: false`) Flow** | [`x402-foundation/x402#3333`](https://github.com/x402-foundation/x402/issues/3333) & [`stellar/x402-stellar#44`](https://github.com/stellar/x402-stellar/issues/44), [`#45`](https://github.com/stellar/x402-stellar/issues/45) | Setting `areFeesSponsored: false` silently breaks 100% of settlements. | Full unsponsored `TransactionEnvelope` builder and structural/timeBounds/signature verifier in `codec.py`. |
| **CAP-67 Muxed `payTo` (`M-Address`) Mismatches** | [`x402-foundation/x402#4322`](https://github.com/x402-foundation/x402/pull/4322) | Muxed recipient addresses (`M...`) fail event verification. | Native CAP-67 `MuxedAccount` normalization and `to_muxed_id` map verification in `codec.py`. |
| **Missing Metered `upto` Scheme for AI Inference** | [`stellar/x402-stellar#71`](https://github.com/stellar/x402-stellar/issues/71), [`#72`](https://github.com/stellar/x402-stellar/issues/72) | Variable-cost LLM token endpoints cannot charge post-execution usage. | `UptoStellarClient`, `UptoStellarFacilitator`, and FastAPI `require_upto_ceiling` (`MeteredSettlementContext`). |
| **Refundable Rent-Fee Drift (`InsufficientRefundableFee`)** | [`stellar/stellar-rpc#918`](https://github.com/stellar/stellar-rpc/issues/918) | ~50% failure rate when TTL-extending calls advance by 1–2 ledgers before submission. | Configurable `refundable_fee_safety_margin_bps` (default `+15%` / `1500 bps`) in `exact.py`. |
