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


def eligible_amount_for_score(score: int, product_max) -> "Decimal":
    """
    Caps how much of a loan product's max_amount a customer is eligible for,
    based on their credit score. Standard tiering used by most Indian lenders;
    adjust the thresholds/percentages to your own risk policy if needed.
    """
    from decimal import Decimal
    product_max = Decimal(str(product_max))
    if score >= 750:
        pct = Decimal("1.00")
    elif score >= 700:
        pct = Decimal("0.75")
    elif score >= 650:
        pct = Decimal("0.50")
    elif score >= 600:
        pct = Decimal("0.25")
    else:
        pct = Decimal("0")
    return (product_max * pct).quantize(Decimal("0.01"))


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
