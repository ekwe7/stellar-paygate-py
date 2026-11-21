# Stellar Paygate-Py

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
| CLI (optional) | Simple `paygate` command line wrapper | Planned (Sprint 2) | Roadmap |
| Documentation | API reference & quickstart guide | Completed (v0.1.0) | Docs |

---

### 4. Usage Guide & Quickstart

```bash
# Install the SDK via pip
pip install stellar-paygate-py
```

```python
from stellar_paygate import Paygate

# Initialise the SDK (provide your Horizon URL if you use a private network)
pg = Paygate()

# Verify an incoming payment XDR
is_valid = pg.verify_payment(xdr_string, expected_amount="10.0 XLM")

# Generate a checkout URL for a customer
checkout_url = pg.create_payment_url(destination="GABC...", amount="10.0", memo="order‑123")
print("Send the user to:", checkout_url)

# Verify a webhook payload (Flask example)
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data()
    signature = request.headers.get('Stellar-Signature')
    if pg.verify_webhook(payload, signature):
        # process event
        return '', 200
    return '', 400
```

---

### 5. License

`stellar-paygate-py` is released under the **MIT License**.

