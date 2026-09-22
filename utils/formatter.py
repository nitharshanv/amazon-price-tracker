"""Formatting utilities for prices, currency, and text display."""

import re
from typing import Optional


def parse_price_text(text: str) -> Optional[float]:
    """Parse numeric price from messy text like '₹29,999.00', '25,000', 'Rs. 499'.

    Handles Indian numbering commas and decimal points.
    """
    if not text:
        return None

    # Strip currency symbols and letters
    cleaned = re.sub(r"[^\d.,]", "", text).strip()
    if not cleaned:
        return None

    # Standardize commas: remove thousand separators
    # e.g., '29,999.00' -> '29999.00' or '29,999' -> '29999'
    parts = cleaned.split(".")
    if len(parts) > 1:
        # Last part is decimal if <= 2 digits, otherwise it was probably a thousand separator
        if len(parts[-1]) <= 2:
            integer_part = "".join(parts[:-1]).replace(",", "")
            decimal_part = parts[-1]
            num_str = f"{integer_part}.{decimal_part}"
        else:
            num_str = "".join(parts).replace(",", "")
    else:
        num_str = cleaned.replace(",", "")

    try:
        val = float(num_str)
        return val if val > 0 else None
    except ValueError:
        return None


def format_currency(amount: Optional[float], currency: str = "INR") -> str:
    """Format numeric amount into friendly currency string (e.g. ₹29,999).

    Uses Indian numbering system grouping (e.g. 1,00,000) when currency is INR.
    """
    if amount is None:
        return "N/A"

    symbol = "₹" if currency.upper() == "INR" else f"{currency} "

    # Indian comma grouping for integers
    int_part = int(round(amount))
    s = str(int_part)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        # Insert commas every 2 digits for the rest from right to left
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        formatted_num = ",".join(groups) + "," + last3
    else:
        formatted_num = s

    return f"{symbol}{formatted_num}"


def clean_title(title: str, max_length: int = 70) -> str:
    """Clean up product title by normalizing whitespace and truncating if too long."""
    if not title:
        return "Unknown Product"
    cleaned = " ".join(title.split()).strip()
    if len(cleaned) > max_length:
        return cleaned[:max_length].rstrip() + "…"
    return cleaned
