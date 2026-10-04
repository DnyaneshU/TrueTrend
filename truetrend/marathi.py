"""Numbers and dates written the Marathi way: Devanagari digits and Marathi month names."""

from datetime import date

_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
MONTHS = (
    "जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून",
    "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर",
)  # fmt: skip


def digits(text: str) -> str:
    """'7.1 %' -> '७.१ %'. Only the digits change."""
    return text.translate(_DIGITS)


def number(value: float) -> str:
    """A saved number as Devanagari digits, without trailing zeros or exponents: 7.10 -> '७.१'."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return digits(text if text not in ("", "-0") else "0")


def day(when: date) -> str:
    """2023-02-20 -> '२० फेब्रुवारी २०२३'."""
    return digits(f"{when.day} {MONTHS[when.month - 1]} {when.year}")
