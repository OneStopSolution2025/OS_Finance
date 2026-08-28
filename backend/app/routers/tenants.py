from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_superadmin
from app.models.tenancy import User, Tenant
from pydantic import BaseModel

router = APIRouter(prefix="/tenants", tags=["organization"])


# ---------- Payment settings ----------

def _mask(secret: str | None) -> str | None:
    """Never send a saved secret back to the browser in full — show only the last 4 characters."""
    if not secret:
        return None
    return f"{'•' * max(len(secret) - 4, 0)}{secret[-4:]}"


@router.get("/payment-settings", dependencies=[Depends(require_superadmin)])
def get_payment_settings(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "razorpay_configured": bool(tenant.razorpay_key_id and tenant.razorpay_key_secret),
        "razorpay_key_id": tenant.razorpay_key_id,
        "razorpay_key_secret_masked": _mask(tenant.razorpay_key_secret),
        "razorpayx_configured": bool(tenant.razorpayx_account_number and tenant.razorpayx_key_id and tenant.razorpayx_key_secret),
        "razorpayx_account_number": tenant.razorpayx_account_number,
        "razorpayx_key_id": tenant.razorpayx_key_id,
        "razorpayx_key_secret_masked": _mask(tenant.razorpayx_key_secret),
    }


class PaymentSettingsUpdate(BaseModel):
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpayx_account_number: str | None = None
    razorpayx_key_id: str | None = None
    razorpayx_key_secret: str | None = None


@router.put("/payment-settings", dependencies=[Depends(require_superadmin)])
def update_payment_settings(payload: PaymentSettingsUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")

    updates = payload.dict(exclude_unset=True, exclude_none=True)
    for field, value in updates.items():
        setattr(tenant, field, value)
    db.commit()
    return {"status": "updated"}


# ---------- Organization profile ----------

@router.get("/profile", dependencies=[Depends(require_superadmin)])
def get_organization_profile(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"id": tenant.id, "name": tenant.name, "slug": tenant.slug}


class OrganizationUpdate(BaseModel):
    name: str


@router.put("/profile", dependencies=[Depends(require_superadmin)])
def update_organization_profile(payload: OrganizationUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")
    tenant.name = payload.name
    db.commit()
    return {"status": "updated"}
