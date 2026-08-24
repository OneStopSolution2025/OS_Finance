from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_any
from app.models.tenancy import User
from app.models.finance import Customer
from app.utils.sms_otp import send_otp, verify_otp

router = APIRouter(prefix="/verification", tags=["phone verification"])


class SendOtpRequest(BaseModel):
    phone: str


@router.post("/send-otp")
def request_otp(payload: SendOtpRequest, user: User = Depends(require_any)):
    """Sends an OTP to a customer's phone (staff-initiated, during KYC onboarding)."""
    try:
        session_id = send_otp(payload.phone)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"session_id": session_id, "message": "OTP sent."}


class VerifyOtpRequest(BaseModel):
    session_id: str
    otp: str
    customer_id: str | None = None  # if provided, marks this customer's phone as verified


@router.post("/verify-otp")
def confirm_otp(payload: VerifyOtpRequest, db: Session = Depends(get_db), user: User = Depends(require_any)):
    try:
        matched = verify_otp(payload.session_id, payload.otp)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not matched:
        raise HTTPException(status_code=400, detail="Incorrect or expired OTP.")

    if payload.customer_id:
        customer = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.tenant_id == user.tenant_id).first()
        if customer:
            customer.phone_verified = True
            db.commit()

    return {"verified": True}
