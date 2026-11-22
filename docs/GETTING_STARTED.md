# Getting Started with Stellar Paygate-Py

`stellar-paygate-py` is a lightweight Python SDK and command-line interface for accepting and verifying Stellar payments, creating standard SEP-0007 checkout URLs, and validating webhook signatures without running a full Stellar node.

---

## 1. Installation & First Run

### Prerequisites
* **Python**: `3.11` or newer.
* **Stellar Network**: Testnet or Public network.

### Quick Install & First Run
Run the following one-line command to install the package and inspect available CLI sub-commands:

```bash
pip install stellar-paygate-py && stellar-paygate --help
```

You can also install using [`uv`](https://github.com/astral-sh/uv) or `poetry`:

```bash
# Using uv
uv add stellar-paygate-py

# Using poetry
poetry add stellar-paygate-py
```

---

## 2. Command-Line Interface (CLI) Quickstart

The `stellar-paygate` CLI exposes the three primary SDK operations directly in your terminal.

### 2.1 Verify an Incoming Payment XDR
Validate that a transaction envelope contains a payment operation matching an expected amount:

```bash
stellar-paygate verify-payment "AAAAAgAAAAB..." --amount "5 XLM"
```
* **Exit code `0`**: Valid payment XDR matching the specified amount.
* **Exit code `1`**: Malformed XDR, missing payment operation, or amount mismatch.

### 2.2 Generate a Checkout URL (SEP-0007)
Create a standard Stellar payment URI that opens desktop and mobile Stellar wallets:

```bash
stellar-paygate create-url \
  --dest "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT" \
  --amount "10.0" \
  --memo "order-42"
```

Output:
```text
web+stellar:pay?destination=GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT&amount=10.0&memo=order-42
```

### 2.3 Verify a Webhook Payload
Verify that an incoming webhook event was cryptographically signed by an authorized sender:

```bash
stellar-paygate verify-webhook \
  --payload "webhook_payload.json" \
  --signature "6059c2b9a7..." \
  --public-key "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
```
* **Exit code `0`**: Signature is authentic.
* **Exit code `1`**: Signature is invalid or payload has been tampered with.

---

## 3. Python SDK Quickstart

Integrate payment acceptance directly into your Python web service (FastAPI, Flask, Django, or Starlette):

```python
from stellar_paygate.cli import Paygate

# Initialise client (defaults to Stellar Testnet)
paygate_client = Paygate()

# 1. Verify payment transaction XDR
transaction_xdr = "AAAAAgAAAAB..."
is_valid = paygate_client.verify_payment(
    xdr_string=transaction_xdr,
    expected_amount="10.0 XLM",
)
if is_valid:
    print("Payment confirmed!")

# 2. Generate a SEP-0007 checkout URL for a customer
checkout_url = paygate_client.create_payment_url(
    destination="GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT",
    amount="10.0",
    memo="order-42",
)
print("Customer payment URL:", checkout_url)

# 3. Verify an incoming webhook signature
payload_bytes = b'{"event": "payment_received", "amount": "10.0"}'
signature = "6059c2b9a7..."
is_authentic = paygate_client.verify_webhook(
    payload=payload_bytes,
    signature=signature,
    public_key="GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT",
)
if is_authentic:
    print("Webhook verified successfully.")
```

---

## 4. Environment Variables

You can configure default credentials and network parameters via environment variables:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `STELLAR_NETWORK_PASSPHRASE` | Network passphrase for transaction envelopes | Testnet passphrase |
| `STELLAR_PUBLIC_KEY` | Default recipient or signer public key address (`G...`) | None |
| `STELLAR_WEBHOOK_SECRET` | Shared secret key for HMAC webhook verification | None |

---

## 5. Next Steps

* Read the full [API Reference](API_REFERENCE.md) for method parameters and return types.
* Read the [Contributing Guide](CONTRIBUTING.md) to set up your local development environment.
* Explore the [Architecture Overview](architecture.md) to understand modular monolith design.
