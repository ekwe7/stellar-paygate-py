# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Future features, planned protocol extensions, and enhancements.

### Changed
- Refactored components, interface adjustments, or dependency upgrades.

### Deprecated
- Features earmarked for removal in future major releases.

### Removed
- Removed features or decommissioned legacy modules.

### Fixed
- Bug fixes, edge case remediations, and performance patches.

### Security
- Vulnerability resolutions and security updates.

---

## [0.2.0] - 2026-11-23

### Added
- **Verification & Idempotent Settlement Engine (`stellar_paygate.exact`)**:
  - `ExactVerifier`, `UptoVerifier`, `VerifierConfig`, `VerificationPort`, `calculate_padded_resource_fee_stroops`, and `compute_payload_fingerprint`.
  - `SettlementDriftTracker` rolling median settlement tracker to flag metered `upto` endpoints drifting `>50%` above advertised typical prices (Drips Wave Issue #8).
  - `SettlementOutboxManager`, `OutboxEntry`, and `OutboxSettlementState` async pending-settlement outbox state machine (`VERIFIED -> SUBMITTED -> PENDING -> SETTLED | DLQ`) with per-fingerprint `asyncio.Lock` coalescing and Dead-Letter Queue inspection (Drips Wave Issue #9).
- **Cryptographic XDR, CAP-67 Muxed Scanner, Multi-Sig & FeeBump Extensions (`stellar_paygate.codec`)**:
  - `scan_simulation_events_for_transfer` and `normalize_stellar_recipient_address` supporting both CAP-67 `SCV_MAP` payloads and 4-topic muxed event extraction while rejecting rogue secondary token transfers (Drips Wave Issue #1).
  - `verify_multisig_ed25519_signatures` and `Ed25519SignatureEntry` enforcing $N \ge 1$ signatures, strict ascending 32-byte public-key ordering, and zero duplicate signers (Drips Wave Issue #4).
  - `verify_unsponsored_transaction_envelope` and `UnsponsoredEnvelopeVerificationResult` unwrapping and verifying `FeeBumpTransactionEnvelope` outer fee-source and inner transaction signatures (Drips Wave Issue #5).
- **Agent Spend-Policy Guard, Dual-Threshold Discovery Pricing & SQLite Store (`stellar_paygate.client`, `stellar_paygate.types`)**:
  - `DiscoveryPricingExtension` (`typical_amount` vs. ceiling metadata) in `StellarExtraConfig` and dual-threshold validation (`max_typical_call_atomic`, `max_upto_ceiling_atomic`) in `SpendPolicyGuard` (Drips Wave Issue #8).
  - `SqliteIdempotencyStore` persistent SQLite-backed in-flight reservation and settlement receipt audit store (Drips Wave Issue #6).
  - `PaygateSyncTransport`, `PaygateAsyncTransport`, `wrap_httpx_with_paygate`, `decimal_to_atomic_units`, `atomic_units_to_decimal`, and `compute_request_fingerprint`.
- **FastAPI Paywall & SSE Streaming LLM Token Meter (`stellar_paygate.fastapi_ext`)**:
  - `add_payment_middleware`, `require_payment`, `require_upto_ceiling`, `build_payment_required_challenge`, and `MeteredSettlementContext`.
  - `PayGateStreamingUptoResponse` SSE streaming wrapper that counts delivered chunks/tokens, settles exact delivered usage on completion or mid-stream client disconnect, and emits a final `event: paygate-receipt` SSE frame (Drips Wave Issue #10).
- **Multi-RPC Quorum Adapter & SARIF / JUnit Exporters (`stellar_paygate.fault_harness`)**:
  - `QuorumSorobanRpcAdapter` and `SorobanRpcReaderPort` querying multiple RPC nodes concurrently and selecting the maximum ledger height to eliminate negative ledger skew (Drips Wave Issue #2).
  - `format_conformance_sarif_report` (`--sarif`) and `format_conformance_junit_report` (`--junit-xml`) exporters for `stellar-paygate-sim` (Drips Wave Issue #7).

---

## [0.1.0] - 2026-11-22

### Added
- Initial core SDK implementation supporting Stellar SEP-41 token payment verification.
- HTTP 402 Pay-Per-Request payment gate client and FastAPI middleware integration.
- Wire-format codec encoders and decoders for payment headers.
- Command-line interface (`stellar-paygate`) with `verify-payment`, `create-url`, and `verify-webhook`.
- Offline deterministic fault-injection simulation harness (`stellar-paygate-sim`).
- Comprehensive documentation suite, API reference tables, and Getting Started guide.
- GitHub Actions CI workflow for unit testing, type checking, linting, packaging, and link verification.
- Expanded test suite achieving 96% test coverage across all public SDK entry points.
