"""
SMS OTP verification via 2Factor.in — used to verify a customer's phone number
during KYC onboarding before a loan can be applied for.

2Factor.in API reference:
  Send OTP:   GET https://2factor.in/API/V1/{api_key}/SMS/{phone}/AUTOGEN
              -> {"Status": "Success", "Details": "<session_id>"}
  Verify OTP: GET https://2factor.in/API/V1/{api_key}/SMS/VERIFY/{session_id}/{otp}
              -> {"Status": "Success", "Details": "OTP Matched"}  (or Status: Error)

If TWOFACTOR_API_KEY isn't set, both functions raise a clear RuntimeError rather
than silently pretending to succeed — OTP verification is a trust boundary, so
"not configured" must never be treated as "verified."
"""
import requests
from app.core.config import settings

BASE_URL = "https://2factor.in/API/V1"


def send_otp(phone: str) -> str:
    """Sends an OTP to the given 10-digit Indian phone number. Returns a session_id."""
    if not settings.TWOFACTOR_API_KEY:
        raise RuntimeError("SMS verification isn't configured yet (TWOFACTOR_API_KEY missing).")

    phone = phone.strip().lstrip("+").removeprefix("91")
    url = f"{BASE_URL}/{settings.TWOFACTOR_API_KEY}/SMS/{phone}/AUTOGEN"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Could not reach 2Factor.in: {e}")

    if data.get("Status") != "Success":
        raise RuntimeError(data.get("Details", "Failed to send OTP."))
    return data["Details"]  # this is the session_id


def verify_otp(session_id: str, otp: str) -> bool:
    """Verifies the OTP against the given session_id. Returns True only on a confirmed match."""
    if not settings.TWOFACTOR_API_KEY:
        raise RuntimeError("SMS verification isn't configured yet (TWOFACTOR_API_KEY missing).")

    url = f"{BASE_URL}/{settings.TWOFACTOR_API_KEY}/SMS/VERIFY/{session_id}/{otp}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Could not reach 2Factor.in: {e}")

    return data.get("Status") == "Success"
