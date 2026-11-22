"""Tests for Stellar SEP-0007 checkout URL generation."""

from __future__ import annotations

import urllib.parse

import pytest

from stellar_paygate.cli import Paygate

SAMPLE_DESTINATION_ADDRESS = "GA2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"
SAMPLE_ISSUER_ADDRESS = "GB2C5RFPE6GCKMY3Z46BXDF3PXPYWTCEB2AWUK2PO6SI4WHAQW7HGOBT"


def test_create_payment_url_minimal_parameters() -> None:
    """Verify URL generation with required destination and amount only."""
    paygate_client: Paygate = Paygate()
    generated_checkout_url: str = paygate_client.create_payment_url(
        destination=SAMPLE_DESTINATION_ADDRESS,
        amount="10.50",
    )

    assert generated_checkout_url.startswith("web+stellar:pay?")
    parsed_url_components: urllib.parse.ParseResult = urllib.parse.urlparse(generated_checkout_url)
    assert parsed_url_components.scheme == "web+stellar"
    assert parsed_url_components.path == "pay"

    parsed_query_parameters: dict[str, list[str]] = urllib.parse.parse_qs(
        parsed_url_components.query
    )
    assert parsed_query_parameters["destination"] == [SAMPLE_DESTINATION_ADDRESS]
    assert parsed_query_parameters["amount"] == ["10.50"]
    assert "memo" not in parsed_query_parameters
    assert "asset_code" not in parsed_query_parameters


def test_create_payment_url_all_optional_parameters() -> None:
    """Verify URL generation with all optional parameters provided."""
    paygate_client: Paygate = Paygate()
    generated_checkout_url: str = paygate_client.create_payment_url(
        destination=SAMPLE_DESTINATION_ADDRESS,
        amount="100",
        memo="order-invoice-987",
        memo_type="MEMO_TEXT",
        asset_code="USDC",
        asset_issuer=SAMPLE_ISSUER_ADDRESS,
    )

    parsed_url_components: urllib.parse.ParseResult = urllib.parse.urlparse(generated_checkout_url)
    parsed_query_parameters: dict[str, list[str]] = urllib.parse.parse_qs(
        parsed_url_components.query
    )

    assert parsed_query_parameters["destination"] == [SAMPLE_DESTINATION_ADDRESS]
    assert parsed_query_parameters["amount"] == ["100"]
    assert parsed_query_parameters["memo"] == ["order-invoice-987"]
    assert parsed_query_parameters["memo_type"] == ["MEMO_TEXT"]
    assert parsed_query_parameters["asset_code"] == ["USDC"]
    assert parsed_query_parameters["asset_issuer"] == [SAMPLE_ISSUER_ADDRESS]


@pytest.mark.parametrize(
    "special_character_memo",
    [
        "Coffee & Bagel = $5.00 #morning",
        "Payment / invoice : 12345 ? prompt",
        "Unicode test: 支付 완료 🚀",
        "Text with spaces and + plus signs",
        'Quote test: "special" & <brackets>',
    ],
)
def test_create_payment_url_special_characters_encoding(
    special_character_memo: str,
) -> None:
    """Verify that memos containing spaces, emojis, and URL reserved characters encode cleanly."""
    paygate_client: Paygate = Paygate()
    generated_checkout_url: str = paygate_client.create_payment_url(
        destination=SAMPLE_DESTINATION_ADDRESS,
        amount="5",
        memo=special_character_memo,
    )

    parsed_url_components: urllib.parse.ParseResult = urllib.parse.urlparse(generated_checkout_url)
    parsed_query_parameters: dict[str, list[str]] = urllib.parse.parse_qs(
        parsed_url_components.query
    )

    assert parsed_query_parameters["memo"] == [special_character_memo]


@pytest.mark.parametrize(
    ("memo_type_option", "memo_value"),
    [
        ("MEMO_TEXT", "standard_text"),
        ("MEMO_ID", "1234567890"),
        ("MEMO_HASH", "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a"),
    ],
)
def test_create_payment_url_memo_types(
    memo_type_option: str,
    memo_value: str,
) -> None:
    """Verify that different memo types are correctly included in query parameters."""
    paygate_client: Paygate = Paygate()
    generated_checkout_url: str = paygate_client.create_payment_url(
        destination=SAMPLE_DESTINATION_ADDRESS,
        amount="15",
        memo=memo_value,
        memo_type=memo_type_option,
    )

    parsed_query_parameters: dict[str, list[str]] = urllib.parse.parse_qs(
        urllib.parse.urlparse(generated_checkout_url).query
    )
    assert parsed_query_parameters["memo"] == [memo_value]
    assert parsed_query_parameters["memo_type"] == [memo_type_option]
