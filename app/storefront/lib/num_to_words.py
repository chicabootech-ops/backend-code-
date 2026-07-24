"""Convert paise amounts to Indian-English words for invoices."""

from __future__ import annotations

_ONES = [
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _three(n: int) -> str:
    hundred, rest = divmod(n, 100)
    parts = []
    if hundred:
        parts.append(f"{_ONES[hundred]} Hundred")
    if rest:
        parts.append(_two(rest))
    return " ".join(parts)


def _in_words(n: int) -> str:
    if n == 0:
        return "Zero"
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    hundred = n
    parts = []
    if crore:
        parts.append(f"{_in_words(crore)} Crore")
    if lakh:
        parts.append(f"{_two(lakh)} Lakh")
    if thousand:
        parts.append(f"{_two(thousand)} Thousand")
    if hundred:
        parts.append(_three(hundred))
    return " ".join(parts)


def rupees_in_words(paise: int) -> str:
    """Return e.g. 'Indian Rupee One Thousand Two Hundred Fifty and 50/100 Only'."""
    paise = max(0, int(paise))
    rupees, sub = divmod(paise, 100)
    words = f"Indian Rupee {_in_words(rupees)}"
    if sub:
        words += f" and {sub:02d}/100"
    return words + " Only"
