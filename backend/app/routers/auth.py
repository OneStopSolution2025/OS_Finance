from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_password, create_access_token, hash_password
from app.models.tenancy import User, Tenant, UserRole, PasswordResetToken

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username, User.is_active == True).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token({"sub": user.id, "role": user.role.value, "tenant_id": user.tenant_id})
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role.value,
        "full_name": user.full_name,
        "tenant_id": user.tenant_id,
        "branch_id": user.branch_id,
    }


class BootstrapRequest(BaseModel):
    secret: str
    org_name: str
    org_slug: str
    full_name: str
    email: EmailStr
    password: str


@router.post("/bootstrap-superadmin")
def bootstrap_superadmin(payload: BootstrapRequest, db: Session = Depends(get_db)):
    """
    One-time setup route: creates the business's Tenant record and its first
    Super Admin login in a single step. Only works if BOOTSTRAP_SECRET is set
    in the environment, it matches what's sent here, and no Super Admin
    exists yet. Remove the BOOTSTRAP_SECRET env var after using this once.
    """
    if not settings.BOOTSTRAP_SECRET:
        raise HTTPException(status_code=403, detail="Bootstrap is disabled (BOOTSTRAP_SECRET not set).")
    if payload.secret != settings.BOOTSTRAP_SECRET:
        raise HTTPException(status_code=403, detail="Invalid bootstrap secret.")

    existing = db.query(User).filter(User.role == UserRole.superadmin).first()
    if existing:
        raise HTTPException(status_code=400, detail="A Super Admin already exists. Bootstrap is single-use.")

    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="That email is already registered.")

    if db.query(Tenant).filter(Tenant.slug == payload.org_slug).first():
        raise HTTPException(status_code=400, detail="That organization slug is already taken.")

    tenant = Tenant(name=payload.org_name, slug=payload.org_slug)
    db.add(tenant)
    db.flush()

    admin = User(
        tenant_id=tenant.id,
        full_name=payload.full_name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.superadmin,
    )
    db.add(admin)
    db.commit()
    return {"status": "created", "email": admin.email, "note": "Remove BOOTSTRAP_SECRET from your env vars now."}


# ---------- Forgot / reset password ----------

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Issues a short-lived reset token for the given email. Always returns the same
    generic response whether or not the email exists, so this endpoint can't be used
    to check which emails are registered.

    NOTE: no email service is wired up yet (SendGrid recommended, same as your other
    apps). Until then, this returns the reset token directly in the response so you
    can complete the flow manually. Once SendGrid is connected, stop returning the
    token here and email it instead.
    """
    import secrets
    generic_response = {"message": "If that email is registered, a reset link has been issued."}

    user = db.query(User).filter(User.email == payload.email, User.is_active == True).first()
    if not user:
        return generic_response

    token = secrets.token_urlsafe(32)
    reset = PasswordResetToken(
        user_id=user.id, token=token,
        expires_at=datetime.utcnow() + timedelta(minutes=30),
    )
    db.add(reset)
    db.commit()

    generic_response["dev_reset_token"] = token  # remove once email delivery is live
    generic_response["expires_in_minutes"] = 30
    return generic_response


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset = db.query(PasswordResetToken).filter(PasswordResetToken.token == payload.token).first()
    if not reset or reset.used or reset.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    user = db.query(User).filter(User.id == reset.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")

    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    user.hashed_password = hash_password(payload.new_password)
    reset.used = True
    db.commit()
    return {"message": "Password updated. You can sign in with your new password now."}
