"""
WhatsApp notifications via Meta's WhatsApp Cloud API — sends payment receipts
and loan status updates directly to a customer's WhatsApp number.

This is intentionally best-effort: every call site wraps these functions in a
try/except and logs failures rather than letting a WhatsApp delivery problem
break the underlying business action (recording a payment, approving a loan).
A microfinance operator's payment must succeed even if WhatsApp is down or
not yet configured — notifications are a bonus, not a dependency.

Setup: create a Meta developer app with WhatsApp product enabled, get a
permanent access token and the phone_number_id, set WHATSAPP_API_TOKEN and
WHATSAPP_PHONE_NUMBER_ID in the environment. Until then, is_configured() is
False and every send_* call below no-ops safely.
"""
import logging
import requests
from app.core.config import settings

logger = logging.getLogger("whatsapp")

GRAPH_API_VERSION = "v20.0"


def is_configured() -> bool:
    return bool(settings.WHATSAPP_API_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)


def _format_phone(phone: str) -> str:
    """WhatsApp Cloud API wants E.164 without a leading +. Assumes Indian numbers by default."""
    phone = phone.strip().lstrip("+")
    if len(phone) == 10:
        phone = "91" + phone
    return phone


def send_whatsapp_text(phone: str, message: str) -> bool:
    """Sends a plain text WhatsApp message. Returns True on success, False otherwise — never raises."""
    if not is_configured():
        logger.info("WhatsApp not configured — skipping message to %s", phone)
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": _format_phone(phone),
        "type": "text",
        "text": {"body": message},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code >= 400:
            logger.warning("WhatsApp send failed (%s): %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as e:
        logger.warning("WhatsApp send error: %s", e)
        return False


def send_payment_receipt_notification(phone: str, customer_name: str, amount: float, receipt_number: str, loan_number: str) -> bool:
    message = (
        f"Hi {customer_name}, we've received your payment of ₹{amount:,.2f} "
        f"for loan {loan_number}. Receipt: {receipt_number}. Thank you — OS Finances."
    )
    return send_whatsapp_text(phone, message)


def send_loan_status_notification(phone: str, customer_name: str, loan_number: str, status: str) -> bool:
    status_text = {
        "approved": "has been approved and is awaiting disbursement",
        "active": "has been disbursed and is now active",
        "rejected": "was not approved this time",
    }.get(status, f"status has changed to {status}")
    message = f"Hi {customer_name}, your loan {loan_number} {status_text}. — OS Finances."
    return send_whatsapp_text(phone, message)
