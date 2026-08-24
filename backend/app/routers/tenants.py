from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.security import require_superemeadmin, hash_password
from app.models.tenancy import Tenant, SubscriptionPlan, SubscriptionStatus, User, UserRole
from app.utils.passwords import generate_password

router = APIRouter(prefix="/tenants", tags=["tenants (SuperEmeAdmin)"])


class TenantCreate(BaseModel):
    org_name: str
    slug: str
    plan_id: str
    admin_full_name: str
    admin_email: EmailStr
    admin_password: str | None = None  # omit to auto-generate a secure password
    trial_days: int = 14


@router.post("/", dependencies=[Depends(require_superemeadmin)])
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)):
    """SuperEmeAdmin onboards a new microfinance operator and provisions their SuperAdmin login."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == payload.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if db.query(Tenant).filter(Tenant.slug == payload.slug).first():
        raise HTTPException(status_code=400, detail="Slug already taken")
    if db.query(User).filter(User.email == payload.admin_email).first():
        raise HTTPException(status_code=400, detail="Admin email already registered")

    tenant = Tenant(
        name=payload.org_name,
        slug=payload.slug,
        plan_id=payload.plan_id,
        subscription_status=SubscriptionStatus.trial,
        trial_ends_at=datetime.utcnow() + timedelta(days=payload.trial_days),
    )
    db.add(tenant)
    db.flush()

    was_generated = not payload.admin_password
    plaintext_password = payload.admin_password or generate_password()

    admin = User(
        tenant_id=tenant.id,
        full_name=payload.admin_full_name,
        email=payload.admin_email,
        hashed_password=hash_password(plaintext_password),
        role=UserRole.superadmin,
    )
    db.add(admin)
    db.commit()
    db.refresh(tenant)
    return {
        "tenant_id": tenant.id,
        "slug": tenant.slug,
        "status": tenant.subscription_status.value,
        "admin_email": admin.email,
        # Only returned once, right after creation — never retrievable again after this response.
        "admin_password": plaintext_password if was_generated else None,
        "password_was_generated": was_generated,
    }



@router.get("/", dependencies=[Depends(require_superemeadmin)])
def list_tenants(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).all()
    return [
        {
            "id": t.id, "name": t.name, "slug": t.slug,
            "status": t.subscription_status.value, "is_suspended": t.is_suspended,
            "plan": t.plan.name if t.plan else None,
            "branch_count": len(t.branches),
        } for t in tenants
    ]


@router.patch("/{tenant_id}/suspend", dependencies=[Depends(require_superemeadmin)])
def suspend_tenant(tenant_id: str, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.is_suspended = True
    tenant.subscription_status = SubscriptionStatus.suspended
    db.commit()
    return {"status": "suspended"}


@router.patch("/{tenant_id}/reactivate", dependencies=[Depends(require_superemeadmin)])
def reactivate_tenant(tenant_id: str, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.is_suspended = False
    tenant.subscription_status = SubscriptionStatus.active
    db.commit()
    return {"status": "active"}


class PlanCreate(BaseModel):
    name: str
    max_branches: int
    max_employees: int
    monthly_price_inr: float
    max_customers: int | None = None


@router.post("/plans", dependencies=[Depends(require_superemeadmin)])
def create_plan(payload: PlanCreate, db: Session = Depends(get_db)):
    plan = SubscriptionPlan(**payload.dict())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return {"id": plan.id, "name": plan.name}


@router.get("/plans", dependencies=[Depends(require_superemeadmin)])
def list_plans(db: Session = Depends(get_db)):
    return db.query(SubscriptionPlan).all()
