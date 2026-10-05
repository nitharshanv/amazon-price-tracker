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


def calculate_discount_percent(original_price: Optional[float], current_price: Optional[float]) -> Optional[int]:
    """Calculate integer percentage discount from original price."""
    if not original_price or not current_price or original_price <= current_price:
        return None
    pct = round(((original_price - current_price) / original_price) * 100)
    return int(pct) if pct > 0 else None


def format_history_report(history_points: list, currency: str = "INR") -> str:
    """Format price points into a telemetry price trail."""
    if not history_points:
        return "<i>No historical price checks recorded yet.</i>"

    prices = [pt.get("price") for pt in history_points if pt.get("price") is not None]
    if not prices:
        return "<i>No price data available.</i>"

    lines = []
    min_price = min(prices)
    max_price = max(prices)

    # Show up to last 8 checks
    recent = history_points[-8:]
    for i, pt in enumerate(recent):
        p = pt.get("price")
        ts_str = pt.get("timestamp", "")
        # Format timestamp nicely if possible
        date_label = ""
        if ts_str:
            try:
                dt = re.sub(r"\.\d+", "", ts_str).replace("Z", "")
                parts = dt.split("T")
                if len(parts) == 2:
                    date_label = f"{parts[0][5:]} {parts[1][:5]} UTC: "
            except Exception:
                date_label = ""

        # Trend indicator compared to previous point
        trend_icon = "▪️"
        diff_str = ""
        if i > 0:
            prev_p = recent[i - 1].get("price")
            if prev_p is not None and p != prev_p:
                delta = p - prev_p
                if delta < 0:
                    trend_icon = "📉"
                    diff_str = f" (⬇ -{format_currency(abs(delta), currency)})"
                else:
                    trend_icon = "📈"
                    diff_str = f" (⬆ +{format_currency(delta, currency)})"

        lines.append(f"{trend_icon} <code>{date_label}</code><b>{format_currency(p, currency)}</b>{diff_str}")

    # Stat summary
    summary_lines = [
        "",
        f"🟢 <b>Lowest Recorded:</b> {format_currency(min_price, currency)}",
        f"🔴 <b>Highest Recorded:</b> {format_currency(max_price, currency)}",
    ]

    if len(prices) >= 2:
        net_diff = prices[-1] - prices[0]
        net_pct = (net_diff / prices[0]) * 100
        sign = "+" if net_diff > 0 else ""
        summary_lines.append(
            f"📊 <b>Net Fluctuation:</b> {sign}{format_currency(net_diff, currency)} ({sign}{net_pct:.1f}%)"
        )

    return "\n".join(lines + summary_lines)
