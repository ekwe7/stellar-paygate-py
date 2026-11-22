# Stellar Paygate-Py Documentation

Welcome to the **Stellar Paygate-Py** documentation suite.

`stellar-paygate-py` is a lightweight Python SDK and CLI that enables developers to accept and verify Stellar payments, generate SEP-0007 transaction checkout links, and validate webhook callbacks without operating a full Stellar node.

---

## Quick Navigation

* **[Getting Started](GETTING_STARTED.md)** — Installation, CLI first run, and Python SDK quickstart.
* **[API Reference](API_REFERENCE.md)** — Detailed manual reference tables for `Paygate` methods, CLI flags, protocol types, and codec utilities.
* **[Contributing Guide](CONTRIBUTING.md)** — Development environment setup, coding conventions, quality gates, and PR process.

---

## Deep-Dive Architecture & Protocol References

* **[Architecture Overview](architecture.md)** — Hexagonal architecture and modular monolith design.
* **[Protocol Invariants & Edge Cases](protocol-and-edge-cases.md)** — HTTP 402 challenge flow, XDR invariants, and edge cases.
* **[Fault Injection Harness](fault-injection.md)** — Deterministic offline simulation scenarios.
* **[Security Model](SECURITY.md)** — Threat vectors and security practices.
