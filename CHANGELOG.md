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
