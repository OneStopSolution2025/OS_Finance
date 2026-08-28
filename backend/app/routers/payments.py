import os
import hmac
import hashlib
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_any
from app.core.config import settings
from app.models.tenancy import User, Tenant
from app.models.finance import Payment, EMISchedule, Loan, Customer, PaymentMethod
from app.utils.receipts import generate_receipt_pdf
from app.utils.whatsapp import send_payment_receipt_notification
from app.utils.audit_log import log_money_event
from app.models.audit import MoneyEventType

router = APIRouter(prefix="/payments", tags=["payments"])


def validate_emi_amount(db: Session, loan_id: str, emi_id: str | None, amount: float):
    """
    If a specific installment is selected, the amount collected must match what's
    actually still owed on it (within a paisa of rounding tolerance) — prevents
    accidental overcollection or a mistyped figure that doesn't reconcile against
    the EMI schedule.
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
    if not emi_id:
        return
    emi = db.query(EMISchedule).filter(EMISchedule.id == emi_id, EMISchedule.loan_id == loan_id).first()
    if not emi:
        raise HTTPException(status_code=404, detail="Installment not found on this loan.")
    if emi.is_paid:
        raise HTTPException(status_code=400, detail="This installment is already fully paid.")
    remaining_due = Decimal(str(emi.total_due)) - Decimal(str(emi.amount_paid or 0))
    if abs(Decimal(str(amount)) - remaining_due) > Decimal("0.01"):
        raise HTTPException(
            status_code=400,
            detail=f"Amount must match the installment's remaining due of ₹{remaining_due:.2f}."
        )


class CashPayment(BaseModel):
    loan_id: str
    emi_id: str | None = None
    amount: float
    notes: str | None = None


@router.post("/cash")
def record_cash_payment(payload: CashPayment, db: Session = Depends(get_db), user: User = Depends(require_any)):
    loan = db.query(Loan).filter(Loan.id == payload.loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    validate_emi_amount(db, payload.loan_id, payload.emi_id, payload.amount)

    count = db.query(Payment).filter(Payment.tenant_id == user.tenant_id).count()
    receipt_number = f"RCPT-{count + 1:06d}"

    payment = Payment(
        tenant_id=user.tenant_id, branch_id=loan.branch_id, loan_id=loan.id, emi_id=payload.emi_id,
        amount=payload.amount, method=PaymentMethod.cash, collected_by=user.id,
        receipt_number=receipt_number, notes=payload.notes,
    )
    db.add(payment)

    if payload.emi_id:
        emi = db.query(EMISchedule).filter(EMISchedule.id == payload.emi_id).first()
        if emi:
            emi.amount_paid = (emi.amount_paid or 0) + payload.amount
            if emi.amount_paid >= emi.total_due:
                emi.is_paid = True
                emi.paid_at = datetime.utcnow()

    log_money_event(
        db, tenant_id=user.tenant_id, event_type=MoneyEventType.payment_collected,
        amount=payload.amount, direction="in", actor_id=user.id, branch_id=loan.branch_id,
        counterparty_type="customer", counterparty_id=loan.customer_id,
        method="cash", reference=receipt_number, related_record_id=payment.id,
        notes=f"Collected against loan {loan.loan_number}",
    )

    try:
        db.commit()
        db.refresh(payment)
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        pdf_path = generate_receipt_pdf(payment, loan, customer)
        payment.receipt_pdf_path = pdf_path
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not record payment: {e}")

    # Best-effort WhatsApp receipt — never lets a notification failure affect the payment itself.
    try:
        if customer and customer.phone:
            send_payment_receipt_notification(customer.phone, customer.full_name, payload.amount, receipt_number, loan.loan_number)
    except Exception:
        pass

    return {"payment_id": payment.id, "receipt_number": receipt_number}


@router.get("/{payment_id}/receipt")
def download_receipt(payment_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    payment = db.query(Payment).filter(Payment.id == payment_id, Payment.tenant_id == user.tenant_id).first()
    if not payment or not payment.receipt_pdf_path or not os.path.exists(payment.receipt_pdf_path):
        raise HTTPException(status_code=404, detail="Receipt not found")
    return FileResponse(payment.receipt_pdf_path, media_type="application/pdf", filename=f"{payment.receipt_number}.pdf")


# ---------- Razorpay online repayment ----------

class RazorpayOrderRequest(BaseModel):
    loan_id: str
    emi_id: str | None = None
    amount: float


@router.post("/razorpay/create-order")
def create_razorpay_order(payload: RazorpayOrderRequest, db: Session = Depends(get_db), user: User = Depends(require_any)):
    loan = db.query(Loan).filter(Loan.id == payload.loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    validate_emi_amount(db, payload.loan_id, payload.emi_id, payload.amount)

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant or not tenant.razorpay_key_id or not tenant.razorpay_key_secret:
        raise HTTPException(
            status_code=500,
            detail="Online payments aren't set up for your account yet. Ask your SuperAdmin to add "
                   "Razorpay credentials under Payment Settings."
        )

    import razorpay
    try:
        client = razorpay.Client(auth=(tenant.razorpay_key_id, tenant.razorpay_key_secret))
        order = client.order.create({
            "amount": int(round(payload.amount * 100)),  # paise
            "currency": "INR",
            "notes": {"loan_id": payload.loan_id, "emi_id": payload.emi_id or ""},
        })
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Razorpay could not create the order: {e}")

    return {"order_id": order["id"], "amount": order["amount"], "currency": order["currency"], "key_id": tenant.razorpay_key_id}


class RazorpayVerify(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    loan_id: str
    emi_id: str | None = None
    amount: float


@router.post("/razorpay/verify")
def verify_razorpay_payment(payload: RazorpayVerify, db: Session = Depends(get_db), user: User = Depends(require_any)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant or not tenant.razorpay_key_secret:
        raise HTTPException(
            status_code=500,
            detail="Online payments aren't set up for your account yet. Ask your SuperAdmin to add "
                   "Razorpay credentials under Payment Settings."
        )

    body = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}"
    expected_signature = hmac.new(
        tenant.razorpay_key_secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    if expected_signature != payload.razorpay_signature:
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    loan = db.query(Loan).filter(Loan.id == payload.loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    count = db.query(Payment).filter(Payment.tenant_id == user.tenant_id).count()
    receipt_number = f"RCPT-{count + 1:06d}"

    payment = Payment(
        tenant_id=user.tenant_id, branch_id=loan.branch_id, loan_id=loan.id, emi_id=payload.emi_id,
        amount=payload.amount, method=PaymentMethod.razorpay,
        razorpay_order_id=payload.razorpay_order_id, razorpay_payment_id=payload.razorpay_payment_id,
        collected_by=user.id, receipt_number=receipt_number,
    )
    db.add(payment)

    if payload.emi_id:
        emi = db.query(EMISchedule).filter(EMISchedule.id == payload.emi_id).first()
        if emi:
            emi.amount_paid = (emi.amount_paid or 0) + payload.amount
            if emi.amount_paid >= emi.total_due:
                emi.is_paid = True
                emi.paid_at = datetime.utcnow()

    log_money_event(
        db, tenant_id=user.tenant_id, event_type=MoneyEventType.payment_collected,
        amount=payload.amount, direction="in", actor_id=user.id, branch_id=loan.branch_id,
        counterparty_type="customer", counterparty_id=loan.customer_id,
        method="razorpay", reference=receipt_number, related_record_id=payment.id,
        notes=f"Collected against loan {loan.loan_number}",
    )

    try:
        db.commit()
        db.refresh(payment)
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        pdf_path = generate_receipt_pdf(payment, loan, customer)
        payment.receipt_pdf_path = pdf_path
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Could not record payment: {e}")

    try:
        if customer and customer.phone:
            send_payment_receipt_notification(customer.phone, customer.full_name, payload.amount, receipt_number, loan.loan_number)
    except Exception:
        pass

    return {"payment_id": payment.id, "receipt_number": receipt_number, "status": "verified"}
