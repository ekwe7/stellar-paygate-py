# API Reference

Complete reference for the public Python SDK classes, verification engines, FastAPI extensions, HTTPX client transports, CLI commands, and wire models in `stellar-paygate-py`.

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

## 2. Verification & Settlement Engine (`stellar_paygate.exact`)

Pure domain verification and idempotent settlement engines for the fixed-price `exact` and metered-ceiling `upto` schemes.

### Classes & Functions

| Symbol | Type | Description |
| :--- | :--- | :--- |
| `VerificationPort` | `Protocol` | Hexagonal port interface exposing `verify()` and `settle()`. |
| `VerifierConfig` | `dataclass(frozen=True)` | Configurable `skew_tolerance_ledgers=5`, `allow_unsponsored_fees=True`, `refundable_fee_margin_basis_points=1500`, and account fee ceilings (`50,000` / `250,000` stroops). |
| `ExactVerifier` | Class | Enforces fixed-price `exact` amount equality, ledger window validity, address format checks (`G-`, `C-`, `M-`), and idempotent settlement caching. |
| `UptoVerifier` | Class | Enforces metered `upto` ceiling bounds (`0 < requested_amount <= authorized_ceiling`) and idempotent single-use settlement. |
| `is_valid_stellar_address` | Function | Validates classic (`G...`), smart-contract (`C...`), and CAP-67 muxed (`M...`) Stellar addresses. |
| `calculate_padded_resource_fee_stroops` | Function | Applies the `+15%` refundable rent safety margin capped at the account-aware fee ceiling. |
| `verify_payload` / `settle_payload` | Functions | Convenience helpers that dispatch to `ExactVerifier` or `UptoVerifier` based on `payload.requirement.scheme`. |

---

## 3. Agent Spend-Policy Guard & HTTPX Transport (`stellar_paygate.client`)

Client-side exact-integer spend guard and automatic HTTP 402 challenge-response transport wrappers for `httpx.Client` and `httpx.AsyncClient`.

| Symbol | Type | Description |
| :--- | :--- | :--- |
| `SpendPolicyGuard` | Class | Enforces `max_per_call`, `max_budget`, `allowed_networks`, and `allowed_assets` with an in-flight request idempotency cache and `release_reservation()` rollback. |
| `wrap_httpx_with_paygate` | Function | Wraps an `httpx.Client` or `httpx.AsyncClient` with `PaygateSyncTransport` or `PaygateAsyncTransport` to automatically answer `HTTP 402` challenges. |
| `decimal_to_atomic_units` | Function | Quantizes a human-readable 7-decimal token string (e.g. `"0.51"` -> `5_100_000`) using `decimal.Decimal` with zero float drift. |
| `atomic_units_to_decimal` | Function | Formats atomic integer units back into a 7-decimal token string. |

---

## 4. FastAPI Paywall Middleware & Route Dependencies (`stellar_paygate.fastapi_ext`)

Drop-in middleware and route-level dependencies for protecting FastAPI endpoints with HTTP 402 payment challenges.

| Symbol | Type | Description |
| :--- | :--- | :--- |
| `add_payment_middleware` | Function | Registers HTTP 402 middleware on a `FastAPI` application, bypassing `/health` and `/docs` while protecting configured routes. |
| `require_payment` | Function | FastAPI `Depends(...)` factory enforcing an `exact` `PaymentRequirement` and returning the settled `SettleResponse`. |
| `require_upto_ceiling` | Function | FastAPI `Depends(...)` factory pre-verifying an `upto` ceiling authorization and injecting a `MeteredSettlementContext`. |
| `MeteredSettlementContext` | Dataclass | Provides `meter.settle_usage(actual_amount_stroops)` for post-execution metered token billing and attaches `PAYMENT-RESPONSE`. |

---

## 5. CLI Reference: `stellar-paygate` & `stellar-paygate-sim`

### 5.1 `stellar-paygate` Command Summary

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

### 5.2 `stellar-paygate-sim` Offline Fault-Injection & Conformance CLI

Executes the 10 canonical deterministic fault-injection scenarios (`S01`–`S10`) offline using `FaultInjectingSorobanRpc` (`stellar_paygate.fault_harness`):

```bash
# Emit a Markdown conformance table
stellar-paygate-sim

# Emit a structured JSON conformance report
stellar-paygate-sim --json
```
* **Exit Codes**: `0` when all 10 scenarios pass, `1` if any scenario fails, `10` on invalid flags.

---

## 6. Protocol Types & Enums

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
| `FaultInjectionConfig` | Dataclass | Deterministic knobs (`ledger_skew`, `allow_unsponsored_fees`, `force_settlement_failure`, `override_settled_amount`, `duplicate_delivery_count`). |

---

## 7. Codec Utilities & Runnable Examples

Located in `stellar_paygate.codec`:

* `encode_payment_required(msg: PaymentRequired) -> str`
* `decode_payment_required(header: str) -> PaymentRequired`
* `encode_payment_signature(msg: PaymentPayload) -> str`
* `decode_payment_signature(header: str) -> PaymentPayload`
* `encode_payment_response(msg: SettlementResponse) -> str`
* `decode_payment_response(header: str) -> SettlementResponse`

Runnable offline scripts in [`examples/`](../examples/):

* [`examples/basic_payment_and_url.py`](../examples/basic_payment_and_url.py) – SEP-0007 URL creation, signed testnet XDR verification, and Ed25519/HMAC webhook validation.
* [`examples/fastapi_httpx_paywall_demo.py`](../examples/fastapi_httpx_paywall_demo.py) – In-memory FastAPI `exact` & `upto` paywalled routes queried by an `httpx.AsyncClient` wrapped with `wrap_httpx_with_paygate` and `SpendPolicyGuard`.
