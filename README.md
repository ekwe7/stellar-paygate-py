# Stellar Paygate-Py

[![CI](https://github.com/ekwe7/stellar-paygate-py/actions/workflows/ci.yml/badge.svg)](https://github.com/ekwe7/stellar-paygate-py/actions/workflows/ci.yml)

Autonomous payment‑gateway SDK for the Stellar network.

**License:** MIT  
**Status:** MVP Ready  
**Architecture:** Modular Monolith  
**Ecosystem:** Stellar

---

## Note

**MVP v0.1.0 Complete:** `stellar-paygate-py` provides a lightweight Python SDK that enables developers to accept and verify Stellar payments, generate transaction URLs, and handle webhook callbacks without running a full Stellar node. The core library is covered by extensive unit tests and has been validated against the public Stellar testnet.

---

### 1. Problem Statement

Integrating Stellar payments typically requires interacting directly with the Horizon API, managing account balances, constructing and signing transactions, and handling asynchronous webhook events. For many web services this results in boilerplate code, increased operational overhead, and a steep learning curve.

---

### 2. What Stellar Paygate‑Py Does

`stellar-paygate-py` bridges this gap by offering a **tiny, opinionated SDK** that abstracts the low‑level Stellar primitives:

- **Accept & verify payments** – Parse incoming transaction XDRs, confirm signatures, and ensure the payment meets your business rules.
- **Generate payment URLs** – One‑line helpers to create checkout links that direct users to the Stellar desktop or mobile wallet.
- **Webhook handling** – Utilities to verify Stellar‑signed webhook payloads and deserialize them into Python objects.
- **No full node required** – All interactions go through the public Horizon endpoints, keeping your deployment footprint minimal.

---

### 3. Project Status & Feature Matrix

| Component | Feature | Implementation Status | Tracking |
|-----------|---------|----------------------|----------|
| Core SDK | Payment verification | Completed (v0.1.0) | MVP Scope |
| Core SDK | Transaction URL builder | Completed (v0.1.0) | MVP Scope |
| Core SDK | Webhook signature verification | Completed (v0.1.0) | MVP Scope |
| CLI (optional) | Simple `paygate` command line wrapper | Completed (v0.1.0) | [CLI Guide](docs/GETTING_STARTED.md#2-command-line-interface-cli-quickstart) |
| Documentation | API reference & quickstart guide | Completed (v0.1.0) | [Documentation](docs/GETTING_STARTED.md) |

---

### 4. Usage Guide & Quickstart

```bash
# Install the SDK and inspect the CLI commands
pip install stellar-paygate-py && stellar-paygate --help
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

---

### 5. Documentation

Comprehensive reference guides are available in the [`docs/`](docs/) directory:

* **[Getting Started](docs/GETTING_STARTED.md)** – Step-by-step installation, CLI first-run, and SDK quickstart.
* **[API Reference](docs/API_REFERENCE.md)** – Complete reference tables for public methods, CLI sub-commands, and protocol types.
* **[Contributing Guide](docs/CONTRIBUTING.md)** – Development setup, code style standards, and test quality gates.
* **[Architecture Overview](docs/architecture.md)** – Hexagonal architecture and modular monolith design.

---

### 6. License

`stellar-paygate-py` is released under the **MIT License**.

