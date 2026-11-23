# Stellar Paygate-Py

[![CI](https://github.com/ekwe7/stellar-paygate-py/actions/workflows/ci.yml/badge.svg)](https://github.com/ekwe7/stellar-paygate-py/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/stellar-paygate.svg)](https://pypi.org/project/stellar-paygate/)
[![GitHub release](https://img.shields.io/github/v/release/ekwe7/stellar-paygate-py?include_prereleases)](https://github.com/ekwe7/stellar-paygate-py/releases)

Autonomous HTTP 402 payment‑gateway SDK, agent spend-policy client, and deterministic offline fault-injection harness for the Stellar & Soroban network.

**License:** MIT  
**Status:** MVP Ready  
**Architecture:** Modular Monolith  
**Ecosystem:** Stellar / Soroban

---

## Note

**MVP v0.1.0 Complete:** `stellar-paygate-py` provides a lightweight Python SDK that enables developers to accept and verify Stellar payments (`exact` and metered `upto` schemes), protect FastAPI routes with HTTP 402 challenges, automate client payments via `httpx` and `SpendPolicyGuard`, generate SEP-0007 transaction URLs, and handle webhook callbacks without running a full Stellar node.

---

### 1. Problem Statement

Integrating Stellar payments and per-request HTTP 402 paywalls typically requires interacting directly with Horizon or Soroban RPC, managing account balances, constructing and signing transactions, enforcing client-side spend caps, and handling asynchronous webhook events. For web services and AI agents this results in boilerplate code, increased operational overhead, and a steep learning curve.

---

### 2. What Stellar Paygate‑Py Does

`stellar-paygate-py` bridges this gap by offering a **tiny, opinionated SDK** that abstracts low‑level Stellar primitives:

- **Accept & verify payments (`ExactVerifier` & `UptoVerifier`)** – Parse incoming transaction XDRs and HTTP 402 `PAYMENT-SIGNATURE` payloads, validate ledger windows and clock skew, and settle idempotently.
- **FastAPI 402 Paywalls & `httpx` Agent Auto-Pay** – Protect FastAPI endpoints with `add_payment_middleware`, `require_payment`, and `require_upto_ceiling`, and wrap `httpx` clients with `wrap_httpx_with_paygate` + `SpendPolicyGuard`.
- **Generate payment URLs & verify webhooks** – One‑line helpers to create SEP-0007 checkout links and verify Ed25519 / HMAC-SHA256 webhook payloads.
- **100% Offline Fault-Injection Harness (`stellar-paygate-sim`)** – Deterministic simulation of 10 canonical conformance scenarios (`S01`–`S10`).

---

### 3. Project Status & Feature Matrix

| Component | Feature | Implementation Status | Tracking |
|-----------|---------|----------------------|----------|
| Core SDK | Payment verification (`Paygate`, `ExactVerifier`, `UptoVerifier`) | Completed (v0.2.0) | [API Reference](docs/API_REFERENCE.md#2-verification--settlement-engine-stellar_paygateexact) |
| Core SDK | Async `SettlementOutboxManager` (`VERIFIED -> SUBMITTED -> PENDING -> SETTLED \| DLQ`) & `SettlementDriftTracker` | Completed (v0.2.0) | [API Reference](docs/API_REFERENCE.md#2-verification--settlement-engine-stellar_paygateexact) |
| Core SDK | CAP-67 4-topic muxed event scanner, multi-sig Ed25519 verifier & `FeeBumpTransactionEnvelope` support | Completed (v0.2.0) | [API Reference](docs/API_REFERENCE.md#7-codec-utilities--cryptographic-helpers) |
| Core SDK | Agent `SpendPolicyGuard` (dual-threshold `DiscoveryPricingExtension`), `SqliteIdempotencyStore` & `httpx` 402 transport | Completed (v0.2.0) | [API Reference](docs/API_REFERENCE.md#3-agent-spend-policy-guard--httpx-transport-stellar_paygateclient) |
| Core SDK | FastAPI 402 paywall (`require_payment`, `require_upto_ceiling`) & `PayGateStreamingUptoResponse` SSE meter | Completed (v0.2.0) | [API Reference](docs/API_REFERENCE.md#4-fastapi-paywall-middleware--route-dependencies-stellar_paygatefastapi_ext) |
| Core SDK | Transaction URL builder & webhook signature verification | Completed (v0.2.0) | [API Reference](docs/API_REFERENCE.md#1-sdk-client-paygate) |
| CLI & Harness | `stellar-paygate`, `stellar-paygate-sim` (`--json`, `--sarif`, `--junit-xml`) & `QuorumSorobanRpcAdapter` | Completed (v0.2.0) | [CLI Guide](docs/GETTING_STARTED.md#2-command-line-interface-cli-quickstart) |
| Examples | Runnable offline scripts (`examples/`) | Completed (v0.2.0) | [Examples](examples/) |
| Documentation | API reference & quickstart guide | Completed (v0.2.0) | [Documentation](docs/GETTING_STARTED.md) |

---

### 4. Usage Guide & Quickstart

```bash
# Install the SDK, inspect CLI commands, and run the 10 offline conformance scenarios
pip install stellar-paygate-py && stellar-paygate --help && stellar-paygate-sim
```

```python
from stellar_paygate.cli import Paygate

# Initialise the SDK (provide your Horizon URL if you use a private network)
paygate_client = Paygate()

# Verify an incoming payment XDR
is_valid = paygate_client.verify_payment(xdr_string, expected_amount="10.0 XLM")

# Generate a checkout URL for a customer
checkout_url = paygate_client.create_payment_url(
    destination="GABC...", amount="10.0", memo="order-123"
)
print("Send the user to:", checkout_url)


# Verify a webhook payload (Flask example)
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    signature = request.headers.get("Stellar-Signature")
    if paygate_client.verify_webhook(payload, signature):
        # process event
        return "", 200
    return "", 400
```

#### Runnable Example Scripts (`examples/`)

You can run the self-contained offline demonstrations directly from the repository root:

* **[`examples/basic_payment_and_url.py`](examples/basic_payment_and_url.py)** – SEP-0007 checkout URL creation, ephemeral signed payment XDR verification, and Ed25519 / HMAC-SHA256 webhook verification.
* **[`examples/fastapi_httpx_paywall_demo.py`](examples/fastapi_httpx_paywall_demo.py)** – In-memory FastAPI server protected by `require_payment` (`exact`) and `require_upto_ceiling` (`upto`) queried by an `httpx.AsyncClient` wrapped with `wrap_httpx_with_paygate` and `SpendPolicyGuard`.

---

### 5. Documentation

Comprehensive reference guides are available in the [`docs/`](docs/) directory:

* **[Getting Started](docs/GETTING_STARTED.md)** – Step-by-step installation, CLI first-run, and SDK quickstart.
* **[API Reference](docs/API_REFERENCE.md)** – Complete reference tables for public methods, verifiers, FastAPI dependencies, CLI sub-commands, and protocol types.
* **[Contributing Guide](docs/CONTRIBUTING.md)** – Development setup, code style standards, and test quality gates.
* **[Architecture Overview](docs/architecture.md)** – Hexagonal architecture and modular monolith design.

---

### 6. License

`stellar-paygate-py` is released under the **MIT License**.
