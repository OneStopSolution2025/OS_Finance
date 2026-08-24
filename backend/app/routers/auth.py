from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import verify_password, create_access_token
from app.models.tenancy import User, Tenant, SubscriptionStatus

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
