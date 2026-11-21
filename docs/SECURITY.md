# Security Architecture, Threat Model & Non-Custodial Policy — `stellar-paygate-py`

**Project:** `stellar-paygate-py`  
**Document:** `docs/SECURITY.md`  
**Architecture:** Modular Monolith (Hexagonal / Ports & Adapters)  
**Security Posture:** Non-Custodial / Policy-Guarded Signing / Strict XDR Verification  
**Status:** Approved Specification Baseline  

---

## 1. Executive Security Philosophy: "Non-Custodial & Policy-Bounded by Default"

1. **Zero Custody of Buyer Funds**: In sponsored mode (`areFeesSponsored: true`), the buyer signs a `SorobanAuthorizationEntry` authorizing a direct SEP-41 `transfer(from, to, amount)` to the merchant's `payTo` address. The facilitator/verifier only sponsors the XLM network fee and can never redirect the token transfer without invalidating the buyer's cryptographic signature.
2. **Pre-Sign Spend Policy Enforcement**: On the client side (`client.py`), `SpendPolicyGuard` evaluates per-call caps, rolling session budgets, and network/asset/origin allowlists **before** any cryptographic signing occurs.
3. **Simulation Event Verification**: On the server/facilitator side (`codec.py`, `exact.py`), verification never trusts outer JSON metadata or unsimulated XDR args alone; it scans the simulated `DiagnosticEvent` stream to confirm the exact SEP-41 `transfer` event occurred and that no secondary token-drain transfers were triggered.

---

## 2. Threat Modeling & Mitigation Matrix

| Threat Vector | Severity | Attack Description | Mitigation in `stellar-paygate-py` |
| :--- | :--- | :--- | :--- |
| **Malicious 402 Overcharge Challenge** | High | A rogue API endpoint returns an HTTP `402` challenge requesting `10,000 USDC` from an autonomous agent. | `SpendPolicyGuard` enforces `max_per_call_atomic`, `max_rolling_budget_atomic`, and `allowed_origins` before signing, aborting with `policy_stellar_per_call_cap_exceeded`. |
| **Hidden Sub-Invocation / Token Drain** | Critical | A crafted authorization or malicious token contract attempts to trigger a second token transfer during simulation. | `codec.py` asserts `sub_invocations == []` on `exact` auth entries and scans all simulated `DiagnosticEvent` entries, rejecting any unexpected secondary `transfer` event with `invalid_exact_stellar_payload_unexpected_token_transfer`. |
| **Double-Spend on HTTP 502/504 Retry** | High | A resource server settles the payment on-chain but times out before returning `200 OK`; a naive client retries and signs a second payment. | Two-layer protection: (1) `client.py` caches the in-flight signed `PaymentPayload` by request fingerprint until `signature_expiration_ledger`, and (2) `exact.py` deduplicates `/settle` by payload SHA-256 fingerprint (`idempotentReplay=True`). |
| **Cross-Payload Nonce Replay** | High | An attacker replays a previously observed `(payer, nonce)` authorization entry. | `exact.py` tracks consumed `(payer, nonce)` pairs and rejects any different payload reusing a consumed nonce with `invalid_exact_stellar_nonce_replayed`; Soroban ledger state also enforces single-use nonces on-chain. |
| **IEEE-754 Float Precision Manipulation** | Medium | Float rounding errors above $2^{53}$ stroops allow a payment slightly above the policy cap to pass validation. | All token amounts use arbitrary-precision Python `int` atomic units and quantized `decimal.Decimal` conversions (`decimal_to_atomic_units`). |
| **CAP-67 Muxed Invoice Spoofing** | Medium | A payer signs a transfer to the merchant's base `G-address` but omits or alters the CAP-67 64-bit `to_muxed_id`. | `codec.py` decodes `M...` addresses and verifies that the simulated CAP-67 transfer event data map carries the exact `to_muxed_id` required by `payTo`. |

---

## 3. Vulnerability Disclosure Policy

If you discover a security vulnerability in `stellar-paygate-py`:
1. **Do NOT** open a public GitHub issue.
2. Submit a private report via GitHub Security Advisories on the repository.
3. Include the affected module (`types.py`, `codec.py`, `exact.py`, `client.py`, `fastapi_ext.py`, or `fault_harness.py`), reproduction steps using `FaultInjectingSorobanRpc`, and impact assessment.
