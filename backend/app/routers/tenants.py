from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.security import require_superemeadmin, require_superadmin, hash_password
from app.models.tenancy import Tenant, SubscriptionPlan, SubscriptionStatus, User, UserRole, ApplicationStatus
from app.utils.passwords import generate_password

router = APIRouter(prefix="/tenants", tags=["tenants (SuperEmeAdmin)"])


def make_tracking_code(db: Session) -> str:
    import secrets
    while True:
        code = "APP-" + secrets.token_hex(3).upper()
        if not db.query(Tenant).filter(Tenant.tracking_code == code).first():
            return code


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
    """SuperEmeAdmin directly onboards a microfinance operator — approved immediately, no review queue."""
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
        application_status=ApplicationStatus.approved,
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


# ---------- Public self-signup (no auth) ----------

class SignupRequest(BaseModel):
    org_name: str
    slug: str
    plan_id: str
    admin_full_name: str
    admin_email: EmailStr
    admin_password: str


@router.get("/plans/public")
def list_public_plans(db: Session = Depends(get_db)):
    """No auth — the signup form needs to show plan options to people who aren't logged in yet."""
    plans = db.query(SubscriptionPlan).filter(SubscriptionPlan.is_active == True).all()
    return [
        {"id": p.id, "name": p.name, "max_branches": p.max_branches,
         "max_employees": p.max_employees, "monthly_price_inr": float(p.monthly_price_inr)}
        for p in plans
    ]


@router.post("/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    """
    Public self-signup for microfinance operators. Creates the tenant and the
    SuperAdmin login they'll use later, but both stay locked out (application_status
    = pending) until SuperEmeAdmin reviews and approves. Returns a tracking code so
    they can check status without needing an account yet.
    """
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == payload.plan_id, SubscriptionPlan.is_active == True).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Selected plan not found")
    if db.query(Tenant).filter(Tenant.slug == payload.slug).first():
        raise HTTPException(status_code=400, detail="That organization URL is already taken")
    if db.query(User).filter(User.email == payload.admin_email).first():
        raise HTTPException(status_code=400, detail="That email is already registered")
    if len(payload.admin_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    tracking_code = make_tracking_code(db)
    tenant = Tenant(
        name=payload.org_name,
        slug=payload.slug,
        plan_id=payload.plan_id,
        subscription_status=SubscriptionStatus.trial,
        application_status=ApplicationStatus.pending,
        tracking_code=tracking_code,
    )
    db.add(tenant)
    db.flush()

    admin = User(
        tenant_id=tenant.id,
        full_name=payload.admin_full_name,
        email=payload.admin_email,
        hashed_password=hash_password(payload.admin_password),
        role=UserRole.superadmin,
    )
    db.add(admin)
    db.commit()

    return {
        "message": "Application submitted. Use your tracking code to check approval status.",
        "tracking_code": tracking_code,
    }


@router.get("/application-status/{tracking_code}")
def application_status(tracking_code: str, db: Session = Depends(get_db)):
    """Public — lets an applicant check their own status without logging in."""
    tenant = db.query(Tenant).filter(Tenant.tracking_code == tracking_code).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="No application found with that tracking code")
    return {
        "org_name": tenant.name,
        "status": tenant.application_status.value,
        "submitted_at": tenant.created_at.isoformat(),
        "rejection_reason": tenant.rejection_reason if tenant.application_status == ApplicationStatus.rejected else None,
    }


# ---------- SuperEmeAdmin review queue ----------

@router.get("/applications/pending", dependencies=[Depends(require_superemeadmin)])
def list_pending_applications(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).filter(Tenant.application_status == ApplicationStatus.pending).order_by(Tenant.created_at.asc()).all()
    result = []
    for t in tenants:
        admin = db.query(User).filter(User.tenant_id == t.id, User.role == UserRole.superadmin).first()
        result.append({
            "id": t.id, "org_name": t.name, "slug": t.slug, "tracking_code": t.tracking_code,
            "plan": t.plan.name if t.plan else None,
            "admin_name": admin.full_name if admin else None,
            "admin_email": admin.email if admin else None,
            "submitted_at": t.created_at.isoformat(),
        })
    return result


@router.patch("/{tenant_id}/approve-application", dependencies=[Depends(require_superemeadmin)])
def approve_application(tenant_id: str, trial_days: int = 14, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.application_status != ApplicationStatus.pending:
        raise HTTPException(status_code=400, detail="This application isn't pending review")

    tenant.application_status = ApplicationStatus.approved
    tenant.trial_ends_at = datetime.utcnow() + timedelta(days=trial_days)
    db.commit()
    return {"status": "approved", "org_name": tenant.name}


class RejectRequest(BaseModel):
    reason: str | None = None


@router.patch("/{tenant_id}/reject-application", dependencies=[Depends(require_superemeadmin)])
def reject_application(tenant_id: str, payload: RejectRequest, db: Session = Depends(get_db)):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if tenant.application_status != ApplicationStatus.pending:
        raise HTTPException(status_code=400, detail="This application isn't pending review")

    tenant.application_status = ApplicationStatus.rejected
    tenant.rejection_reason = payload.reason
    db.commit()
    return {"status": "rejected", "org_name": tenant.name}


@router.get("/", dependencies=[Depends(require_superemeadmin)])
def list_tenants(db: Session = Depends(get_db)):
    tenants = db.query(Tenant).filter(Tenant.application_status == ApplicationStatus.approved).all()
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


# ---------- Payment settings (SuperAdmin, per-tenant) ----------

def _mask(secret: str | None) -> str | None:
    """Never send a saved secret back to the browser in full — show only the last 4 characters."""
    if not secret:
        return None
    return f"{'•' * max(len(secret) - 4, 0)}{secret[-4:]}"


@router.get("/payment-settings", dependencies=[Depends(require_superadmin)])
def get_payment_settings(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
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
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Only overwrite a field if a new value was actually sent — this lets the
    # frontend save the Razorpay half without accidentally blanking RazorpayX
    # (since secrets are masked in the GET response and shouldn't round-trip).
    updates = payload.dict(exclude_unset=True, exclude_none=True)
    for field, value in updates.items():
        setattr(tenant, field, value)
    db.commit()
    return {"status": "updated"}
