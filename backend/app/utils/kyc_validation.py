"""
Format and checksum validation for Aadhaar and PAN numbers.

IMPORTANT: this validates that a number is *structurally well-formed* — correct
length, correct pattern, and (for Aadhaar) a valid Verhoeff checksum digit. It
does NOT verify that the number actually belongs to the customer, exists in
UIDAI's database, or is currently active. Real identity verification against
UIDAI (Aadhaar eKYC) requires a licensed AUA/KUA registration; this is a solo
software vendor's utility function, not a substitute for that.
"""
import re

PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Verhoeff algorithm tables — used by UIDAI for Aadhaar's final check digit.
_VERHOEFF_D = [
    [0,1,2,3,4,5,6,7,8,9], [1,2,3,4,0,6,7,8,9,5], [2,3,4,0,1,7,8,9,5,6],
    [3,4,0,1,2,8,9,5,6,7], [4,0,1,2,3,9,5,6,7,8], [5,9,8,7,6,0,4,3,2,1],
    [6,5,9,8,7,1,0,4,3,2], [7,6,5,9,8,2,1,0,4,3], [8,7,6,5,9,3,2,1,0,4],
    [9,8,7,6,5,4,3,2,1,0],
]
_VERHOEFF_P = [
    [0,1,2,3,4,5,6,7,8,9], [1,5,7,6,2,8,3,0,9,4], [5,8,0,3,7,9,6,1,4,2],
    [8,9,1,6,0,4,3,5,2,7], [9,4,5,3,1,2,6,8,7,0], [4,2,8,6,5,7,3,9,0,1],
    [2,7,9,3,8,0,6,4,1,5], [7,0,4,6,9,1,3,2,5,8],
]


def _verhoeff_checksum_valid(number: str) -> bool:
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c == 0


def validate_aadhaar(aadhaar: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message). error_message is empty when valid."""
    digits = re.sub(r"\D", "", aadhaar or "")
    if len(digits) != 12:
        return False, "Aadhaar number must be exactly 12 digits."
    if digits[0] in ("0", "1"):
        return False, "Aadhaar numbers don't start with 0 or 1."
    if not _verhoeff_checksum_valid(digits):
        return False, "This Aadhaar number fails checksum validation — check for a typo."
    return True, ""


def validate_pan(pan: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message). error_message is empty when valid."""
    pan = (pan or "").strip().upper()
    if not PAN_PATTERN.match(pan):
        return False, "PAN must be 10 characters in the format AAAAA9999A (5 letters, 4 digits, 1 letter)."
    return True, ""
