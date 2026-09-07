"""
Bank payouts — PLUGGABLE, PER-TENANT.

Standard Razorpay only COLLECTS payments (a customer pays you). Actually SENDING
money — paying an employee's salary, or disbursing a loan to a customer's bank
account — requires RazorpayX (Razorpay's separate banking/payouts product),
which needs its own business KYC and approval. Each tenant (microfinance
operator) must connect their own RazorpayX account — set from their Payment
Settings screen — since it's their money moving through their own business,
not a shared platform-wide account.

This module exists so that once a tenant connects RazorpayX, wiring in real
payouts is a contained change (implement send_payout below) rather than a
rebuild — every call site already handles the "not configured for this
tenant" case by falling back to a manual "mark as paid with a reference
number" flow, which works today without any of this.
"""


def is_configured(tenant) -> bool:
    return bool(tenant and tenant.razorpayx_account_number and tenant.razorpayx_key_id and tenant.razorpayx_key_secret)


def send_payout(tenant, account_number: str, ifsc: str, account_holder_name: str, amount: float, purpose: str, reference_id: str) -> dict:
    """
    Returns {"payout_id": str, "status": str, "utr": str | None} once this
    tenant has connected RazorpayX. Until then, raises RuntimeError — callers
    must fall back to the manual "mark as paid" flow, never silently treat
    "not configured" as "sent."
    """
    if not is_configured(tenant):
        raise RuntimeError(
            "Bank payouts aren't connected for your account yet. This requires a RazorpayX "
            "account (separate business KYC from standard Razorpay) — add your credentials "
            "under Payment Settings once you've set one up, to enable automatic bank transfers "
            "for payroll and loan disbursal."
        )
    # Real implementation goes here once this tenant's RazorpayX credentials are
    # set — call POST https://api.razorpay.com/v1/payouts using tenant.razorpayx_key_id/
    # razorpayx_key_secret for auth and tenant.razorpayx_account_number as the source account.
    raise NotImplementedError("RazorpayX integration not yet built — see comment above.")
