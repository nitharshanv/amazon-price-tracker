"""Unit tests for URL parsing, Amazon/Flipkart providers, and formatters."""

import pytest
from providers.amazon import AmazonProvider
from providers.flipkart import FlipkartProvider
from providers import get_provider
from utils.formatter import clean_title, format_currency, parse_price_text
from utils.url_parser import extract_urls


def test_url_extraction():
    text = "Check this out https://www.amazon.in/dp/B09XXXXXXX and also https://flipkart.com/p/itm12345"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "amazon.in" in urls[0]
    assert "flipkart.com" in urls[1]


def test_amazon_provider_asin_extraction():
    provider = AmazonProvider()

    urls = [
        ("https://www.amazon.in/dp/B09XYZ1234", "B09XYZ1234"),
        ("https://amazon.in/Sony-WH-1000XM5/dp/B09XYZ1234?tag=affiliate-21", "B09XYZ1234"),
        ("https://www.amazon.in/gp/product/B09XYZ1234/", "B09XYZ1234"),
        ("https://www.amazon.in/gp/aw/d/B09XYZ1234", "B09XYZ1234"),
        ("https://www.amazon.in/d/B09XYZ1234", "B09XYZ1234"),
    ]

    for url, expected_asin in urls:
        assert provider.supports(url) is True
        assert provider.extract_id(url) == expected_asin
        assert provider.canonical_url(url) == f"https://www.amazon.in/dp/{expected_asin}"


def test_flipkart_provider_id_extraction():
    provider = FlipkartProvider()

    urls = [
        (
            "https://www.flipkart.com/sony-wh-1000xm5-bluetooth-headset/p/itm123456789abcd?pid=MOBGF12345678901",
            "MOBGF12345678901",
        ),
        (
            "https://www.flipkart.com/apple-iphone-16/p/itm9876543210ab",
            "itm9876543210ab",
        ),
        (
            "https://dl.flipkart.com/s/abcdef123",
            None,  # Shortened URLs need redirect resolution
        ),
    ]

    for url, expected_id in urls:
        assert provider.supports(url) is True
        if expected_id is not None:
            assert provider.extract_id(url) == expected_id


def test_provider_resolver():
    amazon_url = "https://www.amazon.in/dp/B09XYZ1234"
    flipkart_url = "https://www.flipkart.com/p/itm12345"
    unsupported_url = "https://example.com/product/123"

    assert isinstance(get_provider(amazon_url), AmazonProvider)
    assert isinstance(get_provider(flipkart_url), FlipkartProvider)
    assert get_provider(unsupported_url) is None


def test_parse_price_text():
    assert parse_price_text("₹29,999.00") == 29999.0
    assert parse_price_text("₹29,999") == 29999.0
    assert parse_price_text("25000") == 25000.0
    assert parse_price_text("Rs. 1,499.50") == 1499.50
    assert parse_price_text("₹ 1,00,000") == 100000.0
    assert parse_price_text("invalid") is None
    assert parse_price_text("") is None


def test_format_currency():
    assert format_currency(29999.0) == "₹29,999"
    assert format_currency(100000.0) == "₹1,00,000"
    assert format_currency(499.0) == "₹499"
    assert format_currency(None) == "N/A"


def test_clean_title():
    long_title = "Sony WH-1000XM5 Wireless Industry Leading Noise Canceling Headphones with Auto NC Optimizer"
    cleaned = clean_title(long_title, max_length=40)
    assert len(cleaned) <= 41  # includes ellipsis
    assert cleaned.endswith("…")
