# Contributing to Stellar Paygate-Py

Thank you for contributing to **`stellar-paygate-py`**!

`stellar-paygate-py` is a native Python HTTP 402 payment gateway and autonomous agent spend-policy client for Stellar / Soroban SEP-41 tokens. We welcome bug fixes, documentation improvements, and feature contributions.

---

## 1. Development Setup

### Prerequisites
* **Python**: Version `3.11` or higher.
* **`uv`**: Fast Python package installer and resolver.
* **Git**: Version control.

### Clone and Install
```bash
# Clone the repository
git clone https://github.com/ekwe7/stellar-paygate-py.git
cd stellar-paygate-py

# Create virtual environment and install all development dependencies
uv sync --all-extras
```

---

## 2. Code Style & Standards

To maintain clean, readable, and maintainable software, all contributions must adhere to the following conventions:

### Descriptive Naming Rule
* **Every variable, parameter, class, and function name must be self-explanatory and descriptive.**
* **Never use single-letter variable names** (e.g. no `e`, `f`, `i`, `x`, `p`, `rc`, `cb`).
* **Never use obscure abbreviations** (e.g. use `exception` instead of `err`, `transaction_envelope` instead of `te`, `argument_parser` instead of `parser`).

### Python Standards
* **Python Version**: Standard Python 3.11+ syntax (`T | None` instead of `Optional[T]`).
* **Line Length**: Max 100 characters (enforced by `ruff`).
* **Type Annotations**: 100% strict typing compliance (enforced by `mypy --strict`).

---

## 3. Quality Gates & Testing

Before submitting a Pull Request, verify that all four automated quality gates pass cleanly:

```bash
# 1. Run Ruff linter
uv run ruff check src tests

# 2. Run Ruff format check
uv run ruff format --check src tests

# 3. Run strict MyPy type checking
uv run mypy --strict src tests

# 4. Run Pytest suite
uv run pytest -q
```

### Auto-Formatting
You can automatically fix formatting and import ordering issues with:
```bash
uv run ruff check --fix src tests
uv run ruff format src tests
```

---

## 4. Submitting a Pull Request

1. **Branching**:
   * Create a feature branch from `main`:
     ```bash
     git checkout -b feat/your-feature-name
     ```
   * Use conventional prefixes: `feat/`, `fix/`, `docs/`, `refactor/`, `test/`.
2. **Commit Messages**:
   * Follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
     * `feat: add payment timeout configuration`
     * `fix: correct base64 padding in codec`
     * `docs: add CLI usage guide in getting started`
3. **Pull Request Checklist**:
   * [ ] All new code includes unit or integration tests.
   * [ ] Variable and function names are descriptive and avoid single-letter names.
   * [ ] `uv run ruff check src tests` reports 0 errors.
   * [ ] `uv run ruff format --check src tests` reports 0 formatting issues.
   * [ ] `uv run mypy --strict src tests` succeeds with 0 issues.
   * [ ] All tests pass under `uv run pytest`.
   * [ ] Documentation is updated for any new public APIs or CLI flags.

---

## 5. Related Documentation

* [Getting Started Guide](GETTING_STARTED.md)
* [API Reference](API_REFERENCE.md)
* [Architecture Design](architecture.md)
* [Protocol Invariants & Edge Cases](protocol-and-edge-cases.md)
