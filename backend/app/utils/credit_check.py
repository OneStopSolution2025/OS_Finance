"""
Credit score check — PLUGGABLE SCAFFOLD, NOT A LIVE INTEGRATION.

There is no self-serve API for pulling a real CIBIL score. Actual bureau
access (CIBIL/TransUnion directly, or a reseller like Karza, Signzy, Digio,
CRIF Highmark, or Experian) requires a signed commercial agreement, business
KYC on OS2/the tenant's side, and a paid per-pull or subscription fee — this
is a regulatory requirement, not a technical one, so no amount of code here
can substitute for it.

This module exists so that once such a provider is set up, wiring in the real
call is a small, contained change (implement check_credit_score below) rather
than a rebuild — every call site already handles the "not configured" case
gracefully, the same pattern as sms_otp.py and whatsapp.py.
"""
from app.core.config import settings


def is_configured() -> bool:
    return bool(settings.CREDIT_BUREAU_API_KEY)


def check_credit_score(pan_number: str) -> dict:
    """
    Returns {"score": int, "bureau": str, "pulled_at": iso timestamp} once a
    real provider is wired in. Until then, raises RuntimeError — callers must
    treat "not configured" as "no score available," never as a passing score.
    """
    if not is_configured():
        raise RuntimeError(
            "Credit score checks aren't configured yet. This requires a commercial "
            "agreement with a bureau or KYC aggregator (e.g. CIBIL, Karza, Signzy, "
            "Digio) — contact OS2 Studio once you've set one up to wire in the API key."
        )
    # Real implementation goes here once CREDIT_BUREAU_API_KEY is set and a
    # provider is chosen — the request/response shape depends entirely on
    # which bureau or aggregator you sign with.
    raise NotImplementedError("Provider not yet selected — see comment above.")
