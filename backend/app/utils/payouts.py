"""
Bank payouts — PLUGGABLE SCAFFOLD, NOT A LIVE INTEGRATION.

Standard Razorpay only COLLECTS payments (a customer pays you). Actually SENDING
money — paying an employee's salary, or disbursing a loan to a customer's bank
account — requires RazorpayX (Razorpay's separate banking/payouts product),
which needs its own business KYC and approval, entirely independent from the
standard Razorpay account already used for collections in this app. There is
no way to unlock this with just an API key; it's a regulatory requirement.

This module exists so that once RazorpayX is set up, wiring in real payouts is
a contained change (implement send_payout below) rather than a rebuild — every
call site already handles the "not configured" case by falling back to a
manual "mark as paid with a reference number" flow, which works today without
any of this.
"""
from app.core.config import settings


def is_configured() -> bool:
    return bool(settings.RAZORPAYX_ACCOUNT_NUMBER and settings.RAZORPAYX_KEY_ID and settings.RAZORPAYX_KEY_SECRET)


def send_payout(account_number: str, ifsc: str, account_holder_name: str, amount: float, purpose: str, reference_id: str) -> dict:
    """
    Returns {"payout_id": str, "status": str, "utr": str | None} once RazorpayX is
    connected. Until then, raises RuntimeError — callers must fall back to the
    manual "mark as paid" flow, never silently treat "not configured" as "sent."
    """
    if not is_configured():
        raise RuntimeError(
            "Bank payouts aren't configured yet. This requires a RazorpayX account "
            "(separate business KYC from standard Razorpay) — contact OS2 Studio once "
            "you've set one up to enable automatic bank transfers for payroll and loan disbursal."
        )
    # Real implementation goes here once RazorpayX credentials are set — call
    # POST https://api.razorpay.com/v1/payouts with the account details, amount
    # (in paise), purpose, and a fund account created for the recipient.
    raise NotImplementedError("RazorpayX integration not yet built — see comment above.")
