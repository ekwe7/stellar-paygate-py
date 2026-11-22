"""Comprehensive unit tests for the verification and idempotent settlement engine."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Literal

import pytest

from stellar_paygate.exact import (
    ExactVerifier,
    UptoVerifier,
    VerificationPort,
    VerifierConfig,
    calculate_padded_resource_fee_stroops,
    compute_payload_fingerprint,
    get_verifier_for_scheme,
    is_valid_stellar_address,
    parse_positive_atomic_amount,
    settle_payload,
    verify_payload,
)
from stellar_paygate.types import (
    FeeMode,
    PayGateErrorCode,
    PaymentPayload,
    PaymentRequirement,
    Scheme,
    SettleResponse,
    StellarExtraConfig,
    StellarPaymentPayloadData,
    VerifyResponse,
)

VALID_CLASSIC_PAYER_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
VALID_CLASSIC_DESTINATION_ADDRESS = "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
VALID_SMART_CONTRACT_ADDRESS = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
VALID_MUXED_DESTINATION_ADDRESS = (
    "MA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGAAAAAAAAAAAAE234"
)


def build_sample_payment_payload(
    scheme: Literal["exact", "upto"] = "exact",
    network: str = "stellar:testnet",
    payload_network: str | None = None,
    amount: str = "1000000",
    asset: str = VALID_SMART_CONTRACT_ADDRESS,
    pay_to: str = VALID_CLASSIC_DESTINATION_ADDRESS,
    payer: str = VALID_CLASSIC_PAYER_ADDRESS,
    ledger: int = 500_000,
    expiration_ledger: int = 500_010,
    fee_mode: FeeMode = FeeMode.SPONSORED,
    are_fees_sponsored: bool = True,
    max_timeout_seconds: int = 60,
    x402_version: int = 2,
    valid_after_ledger: int | None = None,
    signature_expiration_ledger: int | None = None,
    max_transaction_fee_stroops: int | None = None,
) -> PaymentPayload:
    """Construct a customizable PaymentPayload for verification and settlement tests."""
    extra_config = StellarExtraConfig(
        are_fees_sponsored=are_fees_sponsored,
        max_transaction_fee_stroops=max_transaction_fee_stroops,
    )
    payment_requirement = PaymentRequirement(
        scheme=scheme,
        network=network,
        amount=amount,
        asset=asset,
        pay_to=pay_to,
        max_timeout_seconds=max_timeout_seconds,
        extra=extra_config,
    )
    payload_data = StellarPaymentPayloadData(
        payer=payer,
        network=payload_network if payload_network is not None else network,
        ledger=ledger,
        expiration_ledger=expiration_ledger,
        fee_mode=fee_mode,
        valid_after_ledger=valid_after_ledger,
        signature_expiration_ledger=signature_expiration_ledger,
    )
    return PaymentPayload(
        x402_version=x402_version,
        requirement=payment_requirement,
        payload=payload_data,
    )


def test_hexagonal_inward_dependency_rule_for_exact_module() -> None:
    """Ensure exact.py never imports fastapi, starlette, or httpx."""
    exact_module_path = (
        Path(__file__).resolve().parent.parent.parent / "src" / "stellar_paygate" / "exact.py"
    )
    parsed_syntax_tree = ast.parse(exact_module_path.read_text(encoding="utf-8"))
    forbidden_frameworks = {"fastapi", "starlette", "httpx"}

    imported_root_modules: set[str] = set()
    for syntax_node in ast.walk(parsed_syntax_tree):
        if isinstance(syntax_node, ast.Import):
            for alias_node in syntax_node.names:
                imported_root_modules.add(alias_node.name.split(".")[0])
        elif isinstance(syntax_node, ast.ImportFrom) and syntax_node.module:
            imported_root_modules.add(syntax_node.module.split(".")[0])

    assert imported_root_modules.isdisjoint(forbidden_frameworks)


def test_verification_port_protocol_conformance() -> None:
    """Confirm ExactVerifier and UptoVerifier satisfy the VerificationPort protocol."""
    exact_verifier = ExactVerifier()
    upto_verifier = UptoVerifier()

    assert isinstance(exact_verifier, VerificationPort)
    assert isinstance(upto_verifier, VerificationPort)
    assert isinstance(get_verifier_for_scheme("exact"), ExactVerifier)
    assert isinstance(get_verifier_for_scheme(Scheme.UPTO), UptoVerifier)
    assert isinstance(
        get_verifier_for_scheme("exact", config=VerifierConfig()),
        ExactVerifier,
    )
    assert isinstance(
        get_verifier_for_scheme("upto", config=VerifierConfig()),
        UptoVerifier,
    )


@pytest.mark.parametrize(
    ("address_candidate", "allow_muxed", "allow_native", "expected_validity"),
    [
        (VALID_CLASSIC_PAYER_ADDRESS, True, False, True),
        (VALID_SMART_CONTRACT_ADDRESS, True, False, True),
        (VALID_MUXED_DESTINATION_ADDRESS, True, False, True),
        (VALID_MUXED_DESTINATION_ADDRESS, False, False, False),
        ("native", False, True, True),
        ("native", False, False, False),
        ("", True, True, False),
        ("   ", True, True, False),
        ("GSHORTADDRESS", True, False, False),
        ("CSHORTCONTRACT", True, False, False),
        ("MSHORTMUXED", True, False, False),
        ("X" + "A" * 55, True, False, False),
        ("G" + "1" * 55, True, False, False),  # '1' is outside Stellar Base32 alphabet
        ("M" + "8" * 68, True, False, False),  # '8' is outside Stellar Base32 alphabet
    ],
)
def test_is_valid_stellar_address_matrix(
    address_candidate: str,
    allow_muxed: bool,
    allow_native: bool,
    expected_validity: bool,
) -> None:
    """Validate classic G-, smart contract C-, and muxed M-address formats."""
    actual_validity: bool = is_valid_stellar_address(
        address_candidate=address_candidate,
        allow_muxed_account=allow_muxed,
        allow_native_keyword=allow_native,
    )
    assert actual_validity is expected_validity


def test_calculate_padded_resource_fee_stroops_and_ceilings() -> None:
    """Verify +15% refundable fee safety margin and account-aware fee ceilings."""
    # 40,000 stroops * 1.15 = 46,000 stroops (below 50,000 G-account ceiling)
    padded_classic_fee: int = calculate_padded_resource_fee_stroops(
        simulated_resource_fee_stroops=40_000,
        payer_address=VALID_CLASSIC_PAYER_ADDRESS,
    )
    assert padded_classic_fee == 46_000

    # 48,000 stroops * 1.15 = 55,200 -> capped at 50,000 for G-accounts
    capped_classic_fee: int = calculate_padded_resource_fee_stroops(
        simulated_resource_fee_stroops=48_000,
        payer_address=VALID_CLASSIC_PAYER_ADDRESS,
    )
    assert capped_classic_fee == 50_000

    # 95,000 stroops * 1.15 = 109,250 -> permitted for C-address smart accounts (250k ceiling)
    padded_smart_account_fee: int = calculate_padded_resource_fee_stroops(
        simulated_resource_fee_stroops=95_000,
        payer_address=VALID_SMART_CONTRACT_ADDRESS,
    )
    assert padded_smart_account_fee == 109_250

    with pytest.raises(ValueError, match="non-negative"):
        calculate_padded_resource_fee_stroops(
            simulated_resource_fee_stroops=-100,
            payer_address=VALID_CLASSIC_PAYER_ADDRESS,
        )


@pytest.mark.parametrize(
    ("raw_value", "expected_parsed"),
    [
        (1000, 1000),
        ("1000", 1000),
        ("  500000  ", 500000),
        (0, None),
        (-50, None),
        ("0", None),
        ("-100", None),
        ("10.5", None),
        ("abc", None),
        ("", None),
        (True, None),
    ],
)
def test_parse_positive_atomic_amount(
    raw_value: int | str,
    expected_parsed: int | None,
) -> None:
    """Verify positive atomic integer parsing rejects non-positive or fractional inputs."""
    assert parse_positive_atomic_amount(raw_value) == expected_parsed


def test_exact_verifier_happy_path_across_account_types() -> None:
    """Verify and settle valid exact payloads for G-, C-, and M-addresses."""
    exact_verifier = ExactVerifier()

    for case_index, (payer_address, destination_address) in enumerate(
        [
            (VALID_CLASSIC_PAYER_ADDRESS, VALID_CLASSIC_DESTINATION_ADDRESS),
            (VALID_SMART_CONTRACT_ADDRESS, VALID_CLASSIC_DESTINATION_ADDRESS),
            (VALID_CLASSIC_PAYER_ADDRESS, VALID_MUXED_DESTINATION_ADDRESS),
            (VALID_CLASSIC_PAYER_ADDRESS, VALID_SMART_CONTRACT_ADDRESS),
        ]
    ):
        base_ledger: int = 500_000 + (case_index * 100)
        payment_payload = build_sample_payment_payload(
            payer=payer_address,
            pay_to=destination_address,
            amount="1000000",
            ledger=base_ledger,
            expiration_ledger=base_ledger + 10,
        )
        verification_response: VerifyResponse = exact_verifier.verify(
            payment_payload=payment_payload,
            current_ledger=base_ledger,
            requested_amount="1000000",
        )
        assert verification_response.success is True
        assert verification_response.error_code is None

        settlement_response: SettleResponse = exact_verifier.settle(
            payment_payload=payment_payload,
            current_ledger=base_ledger,
        )
        assert settlement_response.success is True
        assert settlement_response.settled_amount == "1000000"
        assert settlement_response.transaction_hash is not None
        assert len(settlement_response.transaction_hash) == 64
        assert settlement_response.transaction_hash == compute_payload_fingerprint(payment_payload)


def test_exact_verifier_default_ledger_window_validation() -> None:
    """Verify ledger window checks when current_ledger is not explicitly passed."""
    exact_verifier = ExactVerifier()

    expired_without_current_ledger = build_sample_payment_payload(
        ledger=500_010,
        expiration_ledger=500_010,
    )
    assert (
        exact_verifier.verify(expired_without_current_ledger).error_code
        == PayGateErrorCode.EXPIRED_AUTHORIZATION
    )

    future_valid_after_without_current_ledger = build_sample_payment_payload(
        ledger=500_000,
        expiration_ledger=500_010,
        valid_after_ledger=500_005,
    )
    assert (
        exact_verifier.verify(future_valid_after_without_current_ledger).error_code
        == PayGateErrorCode.CLOCK_SKEW_EXCEEDED
    )


def test_exact_verifier_idempotent_settlement_and_nonce_replay_protection() -> None:
    """Confirm settling identical payload is idempotent while cross-payload nonce reuse fails."""
    exact_verifier = ExactVerifier()
    first_payload = build_sample_payment_payload(
        amount="1000000", ledger=600_000, expiration_ledger=600_010
    )

    first_settlement = exact_verifier.settle(first_payload, current_ledger=600_000)
    second_settlement = exact_verifier.settle(first_payload, current_ledger=600_000)

    assert first_settlement.success is True
    assert second_settlement.success is True
    assert first_settlement.transaction_hash == second_settlement.transaction_hash

    # Cross-payload replay: same payer, network, ledger, and expiration_ledger but different amount
    replayed_nonce_payload = build_sample_payment_payload(
        amount="9000000",
        ledger=600_000,
        expiration_ledger=600_010,
    )
    replay_verification = exact_verifier.verify(replayed_nonce_payload, current_ledger=600_000)
    assert replay_verification.success is False
    assert replay_verification.error_code == PayGateErrorCode.INVALID_PAYLOAD
    assert replay_verification.error_reason is not None
    assert "replay" in replay_verification.error_reason.lower()

    replay_settlement = exact_verifier.settle(replayed_nonce_payload, current_ledger=600_000)
    assert replay_settlement.success is False
    assert replay_settlement.error_code == PayGateErrorCode.INVALID_PAYLOAD


@pytest.mark.parametrize(
    ("payload_overrides", "current_ledger", "requested_amount", "expected_error_code"),
    [
        ({"x402_version": 1}, 500_000, None, PayGateErrorCode.INVALID_X402_VERSION),
        ({"scheme": "upto"}, 500_000, None, PayGateErrorCode.UNSUPPORTED_SCHEME),
        ({"network": "stellar:futurenet"}, 500_000, None, PayGateErrorCode.UNSUPPORTED_NETWORK),
        (
            {"network": "stellar:testnet", "payload_network": "stellar:pubnet"},
            500_000,
            None,
            PayGateErrorCode.UNSUPPORTED_NETWORK,
        ),
        ({"payer": "INVALID_PAYER"}, 500_000, None, PayGateErrorCode.INVALID_ADDRESS),
        ({"pay_to": "INVALID_DEST"}, 500_000, None, PayGateErrorCode.INVALID_ADDRESS),
        ({"asset": "INVALID_ASSET"}, 500_000, None, PayGateErrorCode.INVALID_ADDRESS),
        ({"amount": "0"}, 500_000, None, PayGateErrorCode.AMOUNT_MISMATCH),
        ({"amount": "-500"}, 500_000, None, PayGateErrorCode.AMOUNT_MISMATCH),
        ({"amount": "not_a_number"}, 500_000, None, PayGateErrorCode.AMOUNT_MISMATCH),
        ({"amount": "1000"}, 500_000, "2000", PayGateErrorCode.AMOUNT_MISMATCH),
        ({"amount": "1000"}, 500_010, None, PayGateErrorCode.EXPIRED_AUTHORIZATION),
        ({"amount": "1000"}, 500_025, None, PayGateErrorCode.EXPIRED_AUTHORIZATION),
        (
            {"signature_expiration_ledger": 500_000},
            500_000,
            None,
            PayGateErrorCode.EXPIRED_AUTHORIZATION,
        ),
        (
            {"valid_after_ledger": 500_005},
            500_000,
            None,
            PayGateErrorCode.CLOCK_SKEW_EXCEEDED,
        ),
        (
            {"ledger": 500_010, "expiration_ledger": 500_020},
            500_000,
            None,
            PayGateErrorCode.CLOCK_SKEW_EXCEEDED,
        ),
        (
            {"expiration_ledger": 500_100},
            500_000,
            None,
            PayGateErrorCode.CLOCK_SKEW_EXCEEDED,
        ),
        (
            {"are_fees_sponsored": False, "fee_mode": FeeMode.SPONSORED},
            500_000,
            None,
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {"max_transaction_fee_stroops": 75_000},
            500_000,
            None,
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
    ],
)
def test_exact_verifier_error_taxonomy_branches(
    payload_overrides: dict[str, object],
    current_ledger: int,
    requested_amount: str | None,
    expected_error_code: PayGateErrorCode,
) -> None:
    """Verify every PayGateErrorCode rejection condition in ExactVerifier."""
    exact_verifier = ExactVerifier()
    payment_payload = build_sample_payment_payload(**payload_overrides)  # type: ignore[arg-type]

    verification_result = exact_verifier.verify(
        payment_payload=payment_payload,
        current_ledger=current_ledger,
        requested_amount=requested_amount,
    )
    assert verification_result.success is False
    assert verification_result.error_code == expected_error_code

    settlement_result = exact_verifier.settle(
        payment_payload=payment_payload,
        current_ledger=current_ledger,
        requested_amount=requested_amount,
    )
    assert settlement_result.success is False
    assert settlement_result.error_code == expected_error_code


def test_unsponsored_fee_mode_policy_and_forced_settlement_failure() -> None:
    """Verify unsponsored fee rejection when disabled and forced settlement failure handling."""
    strict_config = VerifierConfig(
        allow_unsponsored_fees=False,
        force_settlement_failure=True,
    )
    strict_verifier = ExactVerifier(config=strict_config)

    unsponsored_payload = build_sample_payment_payload(
        fee_mode=FeeMode.UNSPONSORED,
        are_fees_sponsored=False,
    )
    unsponsored_verification = strict_verifier.verify(unsponsored_payload)
    assert unsponsored_verification.success is False
    assert unsponsored_verification.error_code == PayGateErrorCode.INVALID_PAYLOAD

    # When sponsored, verification passes, but settle fails due to force_settlement_failure=True
    sponsored_payload = build_sample_payment_payload()
    assert strict_verifier.verify(sponsored_payload).success is True
    failed_settlement = strict_verifier.settle(sponsored_payload)
    assert failed_settlement.success is False
    assert failed_settlement.error_code == PayGateErrorCode.SETTLEMENT_FAILURE


def test_upto_verifier_metered_settlement_and_ceiling_enforcement() -> None:
    """Verify UptoVerifier allows partial usage below ceiling and rejects over-ceiling amounts."""
    upto_verifier = UptoVerifier()
    upto_payload = build_sample_payment_payload(
        scheme="upto",
        amount="1000000",
        ledger=700_000,
        expiration_ledger=700_010,
    )

    # 1. Verify and settle at partial usage (70,000 <= 1,000,000 ceiling)
    partial_verification = upto_verifier.verify(
        payment_payload=upto_payload,
        current_ledger=700_000,
        requested_amount=70_000,
    )
    assert partial_verification.success is True

    partial_settlement = upto_verifier.settle(
        payment_payload=upto_payload,
        current_ledger=700_000,
        requested_amount=70_000,
    )
    assert partial_settlement.success is True
    assert partial_settlement.settled_amount == "70000"
    assert partial_settlement.transaction_hash is not None

    # Idempotent replay with the same requested_amount returns cached response
    idempotent_replay = upto_verifier.settle(
        payment_payload=upto_payload,
        current_ledger=700_000,
        requested_amount=70_000,
    )
    assert idempotent_replay == partial_settlement

    # Attempting to re-settle the same payload with a different metered usage fails
    conflicting_replay = upto_verifier.settle(
        payment_payload=upto_payload,
        current_ledger=700_000,
        requested_amount=80_000,
    )
    assert conflicting_replay.success is False
    assert conflicting_replay.error_code == PayGateErrorCode.INVALID_PAYLOAD

    # 2. Exceeding ceiling (1,500,000 > 1,000,000) returns EXCEEDS_MAX_UPTO
    over_ceiling_payload = build_sample_payment_payload(
        scheme="upto",
        amount="1000000",
        ledger=700_100,
        expiration_ledger=700_110,
    )
    over_ceiling_verification = upto_verifier.verify(
        payment_payload=over_ceiling_payload,
        current_ledger=700_100,
        requested_amount=1_500_000,
    )
    assert over_ceiling_verification.success is False
    assert over_ceiling_verification.error_code == PayGateErrorCode.EXCEEDS_MAX_UPTO

    over_ceiling_settlement = upto_verifier.settle(
        payment_payload=over_ceiling_payload,
        current_ledger=700_100,
        requested_amount=1_500_000,
    )
    assert over_ceiling_settlement.success is False
    assert over_ceiling_settlement.error_code == PayGateErrorCode.EXCEEDS_MAX_UPTO

    # 3. Non-positive requested amount returns AMOUNT_MISMATCH
    zero_amount_verification = upto_verifier.verify(
        payment_payload=over_ceiling_payload,
        current_ledger=700_100,
        requested_amount=0,
    )
    assert zero_amount_verification.success is False
    assert zero_amount_verification.error_code == PayGateErrorCode.AMOUNT_MISMATCH

    # 4. Invalid ceiling amount in requirement returns AMOUNT_MISMATCH
    bad_ceiling_payload = build_sample_payment_payload(
        scheme="upto",
        amount="0",
        ledger=700_200,
        expiration_ledger=700_210,
    )
    bad_ceiling_verification = upto_verifier.verify(bad_ceiling_payload)
    assert bad_ceiling_verification.success is False
    assert bad_ceiling_verification.error_code == PayGateErrorCode.AMOUNT_MISMATCH


def test_upto_verifier_forced_settlement_failure_and_default_amount() -> None:
    """Verify UptoVerifier default ceiling settlement and forced settlement failure."""
    default_upto_payload = build_sample_payment_payload(
        scheme="upto",
        amount="250000",
        ledger=800_000,
        expiration_ledger=800_010,
    )
    settled_at_ceiling = settle_payload(default_upto_payload, current_ledger=800_000)
    assert settled_at_ceiling.success is True
    assert settled_at_ceiling.settled_amount == "250000"

    failing_upto_verifier = UptoVerifier(config=VerifierConfig(force_settlement_failure=True))
    failing_payload = build_sample_payment_payload(
        scheme="upto",
        amount="250000",
        ledger=800_100,
        expiration_ledger=800_110,
    )
    failed_response = failing_upto_verifier.settle(failing_payload)
    assert failed_response.success is False
    assert failed_response.error_code == PayGateErrorCode.SETTLEMENT_FAILURE


def test_top_level_verify_and_settle_payload_convenience_functions() -> None:
    """Confirm verify_payload and settle_payload dispatch properly for exact and upto."""
    exact_payload = build_sample_payment_payload(
        scheme="exact",
        amount="5000",
        ledger=900_000,
        expiration_ledger=900_010,
    )
    assert verify_payload(exact_payload, current_ledger=900_000).success is True
    exact_settlement = settle_payload(exact_payload, current_ledger=900_000)
    assert exact_settlement.success is True
    assert exact_settlement.settled_amount == "5000"
    assert exact_settlement.transaction_hash is not None
    assert len(exact_settlement.transaction_hash) == 64
