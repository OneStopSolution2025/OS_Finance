from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.security import require_superadmin, require_superadmin_or_above, get_current_user, hash_password
from app.models.tenancy import Branch, User, UserRole, Tenant, SubscriptionPlan
from app.utils.passwords import generate_password

router = APIRouter(prefix="/branches", tags=["branches"])


class BranchCreate(BaseModel):
    name: str
    code: str
    address: str | None = None
    city: str | None = None
    state: str | None = None


@router.post("/")
def create_branch(payload: BranchCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == tenant.plan_id).first() if tenant.plan_id else None

    current_count = db.query(Branch).filter(Branch.tenant_id == user.tenant_id).count()
    if plan and current_count >= plan.max_branches:
        raise HTTPException(
            status_code=402,
            detail=f"Branch limit reached for your plan ({plan.max_branches}). Upgrade your subscription to add more branches."
        )

    branch = Branch(tenant_id=user.tenant_id, **payload.dict())
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.get("/")
def list_branches(db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    q = db.query(Branch)
    if user.role == UserRole.superadmin:
        q = q.filter(Branch.tenant_id == user.tenant_id)
    return q.all()


class EmployeeCreate(BaseModel):
    branch_id: str
    full_name: str
    email: EmailStr
    phone: str | None = None
    password: str | None = None  # omit to auto-generate a secure password
    designation: str | None = None
    employee_code: str | None = None


@router.post("/employees")
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    branch = db.query(Branch).filter(Branch.id == payload.branch_id, Branch.tenant_id == user.tenant_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    was_generated = not payload.password
    plaintext_password = payload.password or generate_password()

    employee = User(
        tenant_id=user.tenant_id,
        branch_id=branch.id,
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        hashed_password=hash_password(plaintext_password),
        role=UserRole.employee,
        designation=payload.designation,
        employee_code=payload.employee_code,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return {
        "id": employee.id,
        "email": employee.email,
        # Only returned once, right after creation.
        "password": plaintext_password if was_generated else None,
        "password_was_generated": was_generated,
    }


@router.get("/employees")
def list_employees(db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    q = db.query(User).filter(User.role == UserRole.employee)
    if user.role == UserRole.superadmin:
        q = q.filter(User.tenant_id == user.tenant_id)
    return q.all()
