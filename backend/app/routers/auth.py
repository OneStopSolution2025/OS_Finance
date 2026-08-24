from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_password, create_access_token, hash_password
from app.models.tenancy import User, Tenant, SubscriptionStatus, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username, User.is_active == True).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Block login if tenant subscription is suspended/cancelled (superemeadmin always allowed)
    if user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if not tenant or tenant.is_suspended or tenant.subscription_status in (
            SubscriptionStatus.suspended, SubscriptionStatus.cancelled
        ):
            raise HTTPException(
                status_code=402,
                detail="Your organization's subscription is inactive. Contact OS2 Studio to reactivate."
            )

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
    full_name: str
    email: EmailStr
    password: str


@router.post("/bootstrap-superemeadmin")
def bootstrap_superemeadmin(payload: BootstrapRequest, db: Session = Depends(get_db)):
    """
    One-time setup route: creates the first SuperEmeAdmin (OS2's own platform login).
    Only works if BOOTSTRAP_SECRET is set in the environment, it matches what's sent here,
    and no SuperEmeAdmin exists yet. Remove the BOOTSTRAP_SECRET env var after using this once.
    """
    if not settings.BOOTSTRAP_SECRET:
        raise HTTPException(status_code=403, detail="Bootstrap is disabled (BOOTSTRAP_SECRET not set).")
    if payload.secret != settings.BOOTSTRAP_SECRET:
        raise HTTPException(status_code=403, detail="Invalid bootstrap secret.")

    existing = db.query(User).filter(User.role == UserRole.superemeadmin).first()
    if existing:
        raise HTTPException(status_code=400, detail="A SuperEmeAdmin already exists. Bootstrap is single-use.")

    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="That email is already registered.")

    admin = User(
        full_name=payload.full_name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.superemeadmin,
    )
    db.add(admin)
    db.commit()
    return {"status": "created", "email": admin.email, "note": "Remove BOOTSTRAP_SECRET from your env vars now."}
