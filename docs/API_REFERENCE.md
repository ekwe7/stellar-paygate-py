# API Reference

Complete reference for the public Python SDK classes, CLI commands, and wire models in `stellar-paygate-py`.

---

## 1. SDK Client: `Paygate`

The `Paygate` class provides core payment verification, SEP-0007 URL construction, and webhook validation.

### Import Path
```python
from stellar_paygate.cli import Paygate
```

### Public Methods Table

| Method | Parameters | Return Type | Description |
| :--- | :--- | :--- | :--- |
| [`Paygate.__init__`](#paygate__init__) | `horizon_url=None`, `network_passphrase=None`, `public_key=None`, `webhook_secret=None` | `None` | Initializes the client with network passphrase and verification keys. |
| [`Paygate.verify_payment`](#paygateverify_payment) | `xdr_string`, `expected_amount=None`, `network_passphrase=None` | `bool` | Verifies a Stellar transaction envelope XDR and confirms payment amount. |
| [`Paygate.create_payment_url`](#paygatecreate_payment_url) | `destination`, `amount`, `memo=None`, `memo_type=None`, `asset_code=None`, `asset_issuer=None` | `str` | Constructs a SEP-0007 payment URL (`web+stellar:pay?...`). |
| [`Paygate.verify_webhook`](#paygateverify_webhook) | `payload`, `signature`, `public_key=None`, `secret=None` | `bool` | Validates a cryptographic signature (Ed25519 or HMAC-SHA256) against payload. |

---

### Detailed Method Specifications

#### `Paygate.__init__`
```python
def __init__(
    self,
    horizon_url: str | None = None,
    network_passphrase: str | None = None,
    public_key: str | None = None,
    webhook_secret: str | None = None,
) -> None
```
* **Parameters**:
  * `horizon_url` (*str | None*): Optional URL of a custom Horizon endpoint.
  * `network_passphrase` (*str | None*): Network passphrase. Defaults to `STELLAR_NETWORK_PASSPHRASE` environment variable or Stellar Testnet.
  * `public_key` (*str | None*): Default public key address (`G...`). Defaults to `STELLAR_PUBLIC_KEY`.
  * `webhook_secret` (*str | None*): Shared secret key for HMAC validation. Defaults to `STELLAR_WEBHOOK_SECRET`.

#### `Paygate.verify_payment`
```python
def verify_payment(
    self,
    xdr_string: str,
    expected_amount: str | None = None,
    network_passphrase: str | None = None,
) -> bool
```
* **Parameters**:
  * `xdr_string` (*str*): Base64-encoded Stellar transaction envelope XDR.
  * `expected_amount` (*str | None*): Expected payment amount and asset, e.g. `"10.0 XLM"` or `"5"`. If omitted, confirms transaction has at least one valid payment operation.
  * `network_passphrase` (*str | None*): Optional network passphrase override.
* **Returns**:
  * `bool`: `True` if envelope is valid and matches expected amount; `False` otherwise.

#### `Paygate.create_payment_url`
```python
def create_payment_url(
    self,
    destination: str,
    amount: str,
    memo: str | None = None,
    memo_type: str | None = None,
    asset_code: str | None = None,
    asset_issuer: str | None = None,
) -> str
```
* **Parameters**:
  * `destination` (*str*): Recipient Stellar public key address (`G...`).
  * `amount` (*str*): Payment amount string, e.g. `"10.0"`.
  * `memo` (*str | None*): Optional transaction memo text.
  * `memo_type` (*str | None*): Optional memo type, e.g. `"MEMO_TEXT"` or `"MEMO_ID"`.
  * `asset_code` (*str | None*): Optional asset code for custom tokens.
  * `asset_issuer` (*str | None*): Optional issuer account address for custom tokens.
* **Returns**:
  * `str`: Formatted URI string (e.g. `web+stellar:pay?destination=G...&amount=10.0`).

#### `Paygate.verify_webhook`
```python
def verify_webhook(
    self,
    payload: bytes | str,
    signature: str,
    public_key: str | None = None,
    secret: str | None = None,
) -> bool
```
* **Parameters**:
  * `payload` (*bytes | str*): Raw request body bytes or string.
  * `signature` (*str*): Signature received in HTTP headers (hex or Base64, or prefixed with `G...:`).
  * `public_key` (*str | None*): Stellar account public key for Ed25519 verification.
  * `secret` (*str | None*): Secret key for HMAC-SHA256 verification.
* **Returns**:
  * `bool`: `True` if signature is valid; `False` otherwise.

---

## 2. CLI Reference: `stellar-paygate`

### Command Summary

| Command | Usage | Description | Exit Codes |
| :--- | :--- | :--- | :--- |
| `verify-payment` | `stellar-paygate verify-payment <xdr> [options]` | Validates payment transaction XDR against expected amount. | `0` valid, `1` invalid |
| `create-url` | `stellar-paygate create-url --dest <G...> --amount <amount> [options]` | Generates SEP-0007 payment checkout link. | `0` success |
| `verify-webhook` | `stellar-paygate verify-webhook --payload <file> --signature <sig> [options]` | Verifies webhook payload signature. | `0` valid, `1` invalid |

### Detailed Command Options

#### `stellar-paygate verify-payment`
```text
positional arguments:
  xdr                   Base64-encoded Stellar transaction envelope XDR string

options:
  --amount AMOUNT       Expected payment amount (e.g. '5 XLM' or '10.0')
  --network-passphrase  Network passphrase override
```

#### `stellar-paygate create-url`
```text
required options:
  --dest, --destination Destination Stellar public key address (G...)
  --amount              Payment amount string

optional options:
  --memo                Payment memo text
  --memo-type           Memo type (MEMO_TEXT, MEMO_ID)
  --asset-code          Asset code for non-native payments
  --asset-issuer        Asset issuer account address
```

#### `stellar-paygate verify-webhook`
```text
required options:
  --payload             Path to payload file, or raw payload text
  --signature           Cryptographic signature string

optional options:
  --public-key          Stellar public key address (G...) for Ed25519 check
  --secret              Shared secret for HMAC-SHA256 check
```

---

## 3. Protocol Types & Enums

Located in `stellar_paygate.types`:

| Symbol | Type | Description |
| :--- | :--- | :--- |
| `Scheme` | `StrEnum` | Payment scheme: `exact` (fixed price) or `upto` (metered ceiling). |
| `FeeMode` | `StrEnum` | Fee sponsorship: `sponsored` or `unsponsored`. |
| `PayGateErrorCode` | `StrEnum` | Canonical error taxonomy codes (`invalid_header`, `amount_mismatch`, etc.). |
| `PaymentRequirement` | Pydantic model | Individual payment requirement option in a `402` challenge. |
| `PaymentRequired` | Pydantic model | Wire structure of `PAYMENT-REQUIRED` HTTP header. |
| `PaymentPayload` | Pydantic model | Wire structure of `PAYMENT-SIGNATURE` HTTP header. |
| `SettlementResponse` | Pydantic model | Wire structure of `PAYMENT-RESPONSE` HTTP header. |

---

## 4. Codec Utilities

Located in `stellar_paygate.codec`:

* `encode_payment_required(msg: PaymentRequired) -> str`
* `decode_payment_required(header: str) -> PaymentRequired`
* `encode_payment_signature(msg: PaymentPayload) -> str`
* `decode_payment_signature(header: str) -> PaymentPayload`
* `encode_payment_response(msg: SettlementResponse) -> str`
* `decode_payment_response(header: str) -> SettlementResponse`
