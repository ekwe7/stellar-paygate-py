"""Unit tests for CAP-67 muxed event scanning, multi-sig vectors, and FeeBump XDRs."""

from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

import pytest
from stellar_sdk import (
    Account,
    Asset,
    Keypair,
    ManageData,
    MuxedAccount,
    Network,
    TransactionBuilder,
)

from stellar_paygate.codec import (
    Ed25519SignatureEntry,
    normalize_stellar_recipient_address,
    scan_simulation_events_for_transfer,
    verify_multisig_ed25519_signatures,
    verify_unsponsored_transaction_envelope,
)
from stellar_paygate.types import PayGateErrorCode, PaygateProtocolError

SAMPLE_ASSET_CONTRACT = "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA"
SAMPLE_POLICY_CONTRACT = "CC2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
SAMPLE_PAYER_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
SAMPLE_MERCHANT_G_ADDRESS = "GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF"


def build_valid_muxed_address(base_g_address: str, muxed_id: int) -> str:
    """Construct a valid CAP-67 M-address from a base G-address and 64-bit muxed ID."""
    muxed_account = MuxedAccount(account_id=base_g_address, account_muxed_id=muxed_id)
    assert muxed_account.account_muxed is not None
    return muxed_account.account_muxed


def test_normalize_stellar_recipient_address_g_c_and_m_accounts() -> None:
    """Verify G-, C-, and M-address normalization and invalid address rejection."""
    base_g, mux_none = normalize_stellar_recipient_address(SAMPLE_MERCHANT_G_ADDRESS)
    assert base_g == SAMPLE_MERCHANT_G_ADDRESS
    assert mux_none is None

    base_c, mux_c = normalize_stellar_recipient_address(SAMPLE_ASSET_CONTRACT)
    assert base_c == SAMPLE_ASSET_CONTRACT
    assert mux_c is None

    muxed_address_str = build_valid_muxed_address(SAMPLE_MERCHANT_G_ADDRESS, 987654321)
    extracted_g, extracted_mux_id = normalize_stellar_recipient_address(muxed_address_str)
    assert extracted_g == SAMPLE_MERCHANT_G_ADDRESS
    assert extracted_mux_id == 987654321

    for bad_address in ("", "   ", "MBAD_MUXED_ADDRESS", "GSHORT", "X" + "A" * 55):
        with pytest.raises(PaygateProtocolError) as exc_info:
            normalize_stellar_recipient_address(bad_address)
        assert exc_info.value.code == PayGateErrorCode.INVALID_ADDRESS


def test_scan_simulation_events_cap67_map_and_4_topic_muxed_fallback() -> None:
    """Verify CAP-67 SCV_MAP data and 4-topic muxed event scanning plus policy event recording."""
    muxed_id = 42424242
    muxed_pay_to = build_valid_muxed_address(SAMPLE_MERCHANT_G_ADDRESS, muxed_id)

    # 1. CAP-67 map data format: data={"amount": 1000000, "to_muxed_id": 42424242}
    map_events: list[dict[str, Any]] = [
        {
            "in_successful_contract_call": False,
            "type": "contract",
            "contract_id": SAMPLE_ASSET_CONTRACT,
            "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
            "data": 9999999,
        },
        {
            "in_successful_contract_call": True,
            "type": "system",
            "contract_id": SAMPLE_POLICY_CONTRACT,
            "topics": ["system_note"],
            "data": 1,
        },
        {
            "in_successful_contract_call": True,
            "type": "contract",
            "contract_id": SAMPLE_POLICY_CONTRACT,
            "topics": ["spending_limit_checked", SAMPLE_PAYER_ADDRESS],
            "data": True,
        },
        {
            "in_successful_contract_call": True,
            "type": "contract",
            "contract_id": SAMPLE_ASSET_CONTRACT,
            "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
            "data": {"amount": 1_000_000, "to_muxed_id": muxed_id},
        },
    ]
    map_scan_result = scan_simulation_events_for_transfer(
        simulation_events=map_events,
        expected_asset_contract=SAMPLE_ASSET_CONTRACT,
        expected_payer=SAMPLE_PAYER_ADDRESS,
        expected_pay_to=muxed_pay_to,
        expected_amount=1_000_000,
    )
    assert map_scan_result.transferred_amount == 1_000_000
    assert map_scan_result.base_recipient_address == SAMPLE_MERCHANT_G_ADDRESS
    assert map_scan_result.muxed_destination_id == muxed_id
    assert map_scan_result.policy_events_observed == (
        f"{SAMPLE_POLICY_CONTRACT}:spending_limit_checked",
    )

    # 2. CAP-67 4-topic fallback: topics=['transfer', from, to, muxed_id], data=1000000
    four_topic_events: list[dict[str, Any]] = [
        {
            "in_successful_contract_call": True,
            "type": "contract",
            "contract_id": SAMPLE_ASSET_CONTRACT,
            "topics": [
                "transfer",
                SAMPLE_PAYER_ADDRESS,
                SAMPLE_MERCHANT_G_ADDRESS,
                muxed_id,
            ],
            "data": 1_000_000,
        }
    ]
    four_topic_result = scan_simulation_events_for_transfer(
        simulation_events=four_topic_events,
        expected_asset_contract=SAMPLE_ASSET_CONTRACT,
        expected_payer=SAMPLE_PAYER_ADDRESS,
        expected_pay_to=muxed_pay_to,
        expected_amount=1_000_000,
    )
    assert four_topic_result.transferred_amount == 1_000_000
    assert four_topic_result.muxed_destination_id == muxed_id

    # 3. 4-topic muxed ID mismatch raises INVALID_ADDRESS
    mismatched_four_topic_events: list[dict[str, Any]] = [
        {
            "in_successful_contract_call": True,
            "type": "contract",
            "contract_id": SAMPLE_ASSET_CONTRACT,
            "topics": [
                "transfer",
                SAMPLE_PAYER_ADDRESS,
                SAMPLE_MERCHANT_G_ADDRESS,
                11111111,
            ],
            "data": 1_000_000,
        }
    ]
    with pytest.raises(PaygateProtocolError) as mux_mismatch_exc:
        scan_simulation_events_for_transfer(
            simulation_events=mismatched_four_topic_events,
            expected_asset_contract=SAMPLE_ASSET_CONTRACT,
            expected_payer=SAMPLE_PAYER_ADDRESS,
            expected_pay_to=muxed_pay_to,
            expected_amount=1_000_000,
        )
    assert mux_mismatch_exc.value.code == PayGateErrorCode.INVALID_ADDRESS


def test_scan_simulation_events_anti_drain_and_validation_errors() -> None:
    """Verify rogue secondary transfers, missing transfers, and bad event fields fail."""
    valid_transfer_event: dict[str, Any] = {
        "in_successful_contract_call": True,
        "type": "contract",
        "contract_id": SAMPLE_ASSET_CONTRACT,
        "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
        "data": 500_000,
    }

    # Rogue secondary transfer event triggers anti-drain protection
    with pytest.raises(PaygateProtocolError) as drain_exc:
        scan_simulation_events_for_transfer(
            simulation_events=[valid_transfer_event, valid_transfer_event],
            expected_asset_contract=SAMPLE_ASSET_CONTRACT,
            expected_payer=SAMPLE_PAYER_ADDRESS,
            expected_pay_to=SAMPLE_MERCHANT_G_ADDRESS,
            expected_amount=500_000,
        )
    assert drain_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD

    # Upto ceiling mode: partial transfer <= ceiling succeeds, over ceiling fails
    upto_ok = scan_simulation_events_for_transfer(
        simulation_events=[valid_transfer_event],
        expected_asset_contract=SAMPLE_ASSET_CONTRACT,
        expected_payer=SAMPLE_PAYER_ADDRESS,
        expected_pay_to=SAMPLE_MERCHANT_G_ADDRESS,
        expected_amount=1_000_000,
        allow_upto_ceiling=True,
    )
    assert upto_ok.transferred_amount == 500_000

    with pytest.raises(PaygateProtocolError) as upto_over_exc:
        scan_simulation_events_for_transfer(
            simulation_events=[valid_transfer_event],
            expected_asset_contract=SAMPLE_ASSET_CONTRACT,
            expected_payer=SAMPLE_PAYER_ADDRESS,
            expected_pay_to=SAMPLE_MERCHANT_G_ADDRESS,
            expected_amount=200_000,
            allow_upto_ceiling=True,
        )
    assert upto_over_exc.value.code == PayGateErrorCode.EXCEEDS_MAX_UPTO

    # Exact amount mismatch
    with pytest.raises(PaygateProtocolError) as exact_mismatch_exc:
        scan_simulation_events_for_transfer(
            simulation_events=[valid_transfer_event],
            expected_asset_contract=SAMPLE_ASSET_CONTRACT,
            expected_payer=SAMPLE_PAYER_ADDRESS,
            expected_pay_to=SAMPLE_MERCHANT_G_ADDRESS,
            expected_amount=900_000,
        )
    assert exact_mismatch_exc.value.code == PayGateErrorCode.AMOUNT_MISMATCH

    # Zero matching transfer events
    with pytest.raises(PaygateProtocolError) as empty_exc:
        scan_simulation_events_for_transfer(
            simulation_events=[
                {"topics": "not_a_list"},
                {"topics": []},
            ],
            expected_asset_contract=SAMPLE_ASSET_CONTRACT,
            expected_payer=SAMPLE_PAYER_ADDRESS,
            expected_pay_to=SAMPLE_MERCHANT_G_ADDRESS,
            expected_amount=500_000,
        )
    assert empty_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD

    # Wrong token contract, too few topics, wrong sender, wrong recipient, bad data
    bad_event_cases: list[tuple[dict[str, Any], PayGateErrorCode]] = [
        (
            {
                "contract_id": SAMPLE_POLICY_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": 500_000,
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS],
                "data": 500_000,
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_MERCHANT_G_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": 500_000,
            },
            PayGateErrorCode.INVALID_ADDRESS,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_PAYER_ADDRESS],
                "data": 500_000,
            },
            PayGateErrorCode.INVALID_ADDRESS,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": {},
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": {"amount": True},
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": {"amount": 500_000, "to_muxed_id": True},
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": "not_int",
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": None,
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS, True],
                "data": 500_000,
            },
            PayGateErrorCode.INVALID_PAYLOAD,
        ),
        (
            {
                "contract_id": SAMPLE_ASSET_CONTRACT,
                "topics": ["transfer", SAMPLE_PAYER_ADDRESS, SAMPLE_MERCHANT_G_ADDRESS],
                "data": 0,
            },
            PayGateErrorCode.AMOUNT_MISMATCH,
        ),
    ]
    for bad_event, expected_code in bad_event_cases:
        with pytest.raises(PaygateProtocolError) as err_exc:
            scan_simulation_events_for_transfer(
                simulation_events=[bad_event],
                expected_asset_contract=SAMPLE_ASSET_CONTRACT,
                expected_payer=SAMPLE_PAYER_ADDRESS,
                expected_pay_to=SAMPLE_MERCHANT_G_ADDRESS,
                expected_amount=500_000,
            )
        assert err_exc.value.code == expected_code


def test_verify_multisig_ed25519_signatures_sorted_duplicate_and_tampered() -> None:
    """Verify 2-of-2 multi-sig Ed25519 vectors, ascending key order, and duplicate rejection."""
    preimage_bytes = b"soroban_authorization_preimage_digest_32_bytes"
    keypair_alpha = Keypair.random()
    keypair_beta = Keypair.random()

    # Sort keypairs strictly by ascending 32-byte raw public key
    sorted_keypairs = sorted(
        [keypair_alpha, keypair_beta],
        key=lambda kp: kp.raw_public_key(),
    )
    first_kp, second_kp = sorted_keypairs[0], sorted_keypairs[1]

    # 1. Valid 2-of-2 ascending signature vector (using both dataclass and dict representations)
    first_sig_bytes = first_kp.sign(preimage_bytes)
    second_sig_b64 = base64.b64encode(second_kp.sign(preimage_bytes)).decode("ascii")

    verified_count = verify_multisig_ed25519_signatures(
        preimage_bytes=preimage_bytes,
        signature_entries=[
            Ed25519SignatureEntry(
                public_key=first_kp.raw_public_key(),
                signature=first_sig_bytes,
            ),
            {
                "public_key": second_kp.public_key,
                "signature": second_sig_b64,
            },
        ],
    )
    assert verified_count == 2

    # Also test hex public key + hex signature
    assert (
        verify_multisig_ed25519_signatures(
            preimage_bytes=preimage_bytes,
            signature_entries=[
                Ed25519SignatureEntry(
                    public_key=first_kp.raw_public_key().hex(),
                    signature=first_sig_bytes.hex(),
                )
            ],
        )
        == 1
    )

    # 2. Unsorted (descending) signer public keys -> rejected with INVALID_PAYLOAD
    with pytest.raises(PaygateProtocolError) as unsorted_exc:
        verify_multisig_ed25519_signatures(
            preimage_bytes=preimage_bytes,
            signature_entries=[
                Ed25519SignatureEntry(
                    public_key=second_kp.public_key,
                    signature=second_kp.sign(preimage_bytes),
                ),
                Ed25519SignatureEntry(
                    public_key=first_kp.public_key,
                    signature=first_kp.sign(preimage_bytes),
                ),
            ],
        )
    assert unsorted_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD
    assert "ascending" in str(unsorted_exc.value)

    # 3. Duplicate signer public key -> rejected with INVALID_PAYLOAD
    with pytest.raises(PaygateProtocolError) as dup_exc:
        verify_multisig_ed25519_signatures(
            preimage_bytes=preimage_bytes,
            signature_entries=[
                Ed25519SignatureEntry(
                    public_key=first_kp.public_key,
                    signature=first_sig_bytes,
                ),
                Ed25519SignatureEntry(
                    public_key=first_kp.public_key,
                    signature=first_sig_bytes,
                ),
            ],
        )
    assert dup_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD
    assert "Duplicate" in str(dup_exc.value)

    # 4. Tampered signature -> rejected with INVALID_PAYLOAD
    with pytest.raises(PaygateProtocolError) as tampered_exc:
        verify_multisig_ed25519_signatures(
            preimage_bytes=b"different_preimage_bytes",
            signature_entries=[
                Ed25519SignatureEntry(
                    public_key=first_kp.public_key,
                    signature=first_sig_bytes,
                )
            ],
        )
    assert tampered_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD

    # 5. Empty signature vector, invalid key length, invalid sig length, bad entry dict
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(preimage_bytes, [])
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(preimage_bytes, [{"only_key": "val"}])
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(preimage_bytes, ["invalid_entry"])  # type: ignore[list-item]
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(
            preimage_bytes,
            [Ed25519SignatureEntry(public_key=b"short_key", signature=first_sig_bytes)],
        )
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(
            preimage_bytes,
            [Ed25519SignatureEntry(public_key="abcd", signature=first_sig_bytes)],
        )
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(
            preimage_bytes,
            [Ed25519SignatureEntry(public_key=first_kp.public_key, signature=b"short_sig")],
        )
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(
            preimage_bytes,
            [Ed25519SignatureEntry(public_key=first_kp.public_key, signature="!!!not_b64!!!")],
        )
    with pytest.raises(PaygateProtocolError):
        verify_multisig_ed25519_signatures(
            preimage_bytes,
            [Ed25519SignatureEntry(public_key=first_kp.public_key, signature="abcd")],
        )


def test_verify_unsponsored_transaction_and_fee_bump_envelopes() -> None:
    """Verify unsponsored TransactionEnvelope and FeeBumpTransactionEnvelope XDRs."""
    inner_payer_kp = Keypair.random()
    fee_sponsor_kp = Keypair.random()
    destination_kp = Keypair.random()

    inner_account = Account(account=inner_payer_kp.public_key, sequence=100)
    inner_tx_envelope = (
        TransactionBuilder(
            source_account=inner_account,
            network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_payment_op(
            destination=destination_kp.public_key,
            asset=Asset.native(),
            amount="15.50",
        )
        .set_timeout(300)
        .build()
    )
    inner_tx_envelope.sign(inner_payer_kp)

    # 1. Standard TransactionEnvelope verification
    standard_result = verify_unsponsored_transaction_envelope(
        xdr_string=inner_tx_envelope.to_xdr(),
        expected_amount="15.50 XLM",
        expected_destination=destination_kp.public_key,
    )
    assert standard_result.is_fee_bump is False
    assert standard_result.inner_source_account == inner_payer_kp.public_key
    assert standard_result.fee_source_account == inner_payer_kp.public_key
    assert standard_result.matched_amount == Decimal("15.50")

    # 2. FeeBumpTransactionEnvelope wrapping the signed inner transaction
    fee_bump_envelope = TransactionBuilder.build_fee_bump_transaction(
        fee_source=fee_sponsor_kp.public_key,
        base_fee=200,
        inner_transaction_envelope=inner_tx_envelope,
        network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
    )
    fee_bump_envelope.sign(fee_sponsor_kp)

    fee_bump_result = verify_unsponsored_transaction_envelope(
        xdr_string=fee_bump_envelope.to_xdr(),
        expected_amount="15.50",
        expected_destination=destination_kp.public_key,
    )
    assert fee_bump_result.is_fee_bump is True
    assert fee_bump_result.inner_source_account == inner_payer_kp.public_key
    assert fee_bump_result.fee_source_account == fee_sponsor_kp.public_key
    assert fee_bump_result.operation_count == 1
    assert fee_bump_result.matched_amount == Decimal("15.50")

    # 3. Tampered outer signature on FeeBumpTransactionEnvelope -> rejected
    tampered_outer_fee_bump = TransactionBuilder.build_fee_bump_transaction(
        fee_source=fee_sponsor_kp.public_key,
        base_fee=200,
        inner_transaction_envelope=inner_tx_envelope,
        network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
    )
    tampered_outer_fee_bump.sign(Keypair.random())  # wrong signer
    with pytest.raises(PaygateProtocolError) as outer_sig_exc:
        verify_unsponsored_transaction_envelope(tampered_outer_fee_bump.to_xdr())
    assert outer_sig_exc.value.code == PayGateErrorCode.INVALID_PAYLOAD

    # 4. Unsigned envelope -> rejected
    unsigned_fee_bump = TransactionBuilder.build_fee_bump_transaction(
        fee_source=fee_sponsor_kp.public_key,
        base_fee=200,
        inner_transaction_envelope=inner_tx_envelope,
        network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
    )
    with pytest.raises(PaygateProtocolError):
        verify_unsponsored_transaction_envelope(unsigned_fee_bump.to_xdr())

    # 5. Amount or destination mismatch or non-payment operation -> AMOUNT_MISMATCH
    with pytest.raises(PaygateProtocolError) as amt_exc:
        verify_unsponsored_transaction_envelope(
            fee_bump_envelope.to_xdr(),
            expected_amount="999.00",
        )
    assert amt_exc.value.code == PayGateErrorCode.AMOUNT_MISMATCH

    with pytest.raises(PaygateProtocolError) as dest_exc:
        verify_unsponsored_transaction_envelope(
            fee_bump_envelope.to_xdr(),
            expected_destination=inner_payer_kp.public_key,
        )
    assert dest_exc.value.code == PayGateErrorCode.AMOUNT_MISMATCH

    # Non-payment operation envelope when expected_amount is requested
    manage_data_tx = (
        TransactionBuilder(
            source_account=Account(account=inner_payer_kp.public_key, sequence=101),
            network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_operation(ManageData(data_name="k", data_value="v"))
        .set_timeout(300)
        .build()
    )
    manage_data_tx.sign(inner_payer_kp)
    with pytest.raises(PaygateProtocolError):
        verify_unsponsored_transaction_envelope(
            manage_data_tx.to_xdr(),
            expected_amount="10",
        )

    # Corrupted XDR string -> INVALID_PAYLOAD
    with pytest.raises(PaygateProtocolError):
        verify_unsponsored_transaction_envelope("NOT_A_VALID_XDR_ENVELOPE")
