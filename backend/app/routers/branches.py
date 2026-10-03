import re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.core.security import require_superadmin, get_current_user, hash_password
from app.models.tenancy import Branch, User, UserRole, Tenant
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
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Branch name is required.")
    if not payload.code or not payload.code.strip():
        raise HTTPException(status_code=400, detail="Branch code is required.")
    branch = Branch(
        tenant_id=user.tenant_id, name=payload.name.strip(), code=payload.code.strip(),
        address=payload.address, city=payload.city, state=payload.state,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.get("/")
def list_branches(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    return db.query(Branch).filter(Branch.tenant_id == user.tenant_id).all()


class BranchUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None


@router.patch("/{branch_id}")
def update_branch(branch_id: str, payload: BranchUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    updates = payload.dict(exclude_unset=True)
    if "name" in updates and not (updates["name"] or "").strip():
        raise HTTPException(status_code=400, detail="Branch name cannot be empty.")
    if "code" in updates and not (updates["code"] or "").strip():
        raise HTTPException(status_code=400, detail="Branch code cannot be empty.")
    for field, value in updates.items():
        setattr(branch, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    return {"status": "updated"}


@router.patch("/{branch_id}/deactivate")
def deactivate_branch(branch_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    branch.is_active = False
    db.commit()
    return {"status": "inactive"}


@router.patch("/{branch_id}/activate")
def activate_branch(branch_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    branch.is_active = True
    db.commit()
    return {"status": "active"}


@router.delete("/{branch_id}")
def delete_branch(branch_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.tenant_id == user.tenant_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    try:
        db.delete(branch)
        db.commit()
        return {"status": "deleted"}
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This branch has employees, customers, or loans tied to it and can't be deleted. "
                   "Deactivate it instead to stop new activity while keeping its history intact."
        )


class EmployeeCreate(BaseModel):
    branch_id: str
    full_name: str
    email: EmailStr
    phone: str | None = None
    password: str | None = None  # omit to auto-generate a secure password
    designation: str | None = None
    employee_code: str | None = None
    address: str | None = None
    photo_id_type: str | None = None    # 'aadhaar' | 'pan' | 'voter_id' | 'driving_license'
    photo_id_number: str | None = None

    def validate_fields(self):
        if self.phone and not re.fullmatch(r"\d{10}", self.phone):
            raise HTTPException(status_code=400, detail="Contact number must be exactly 10 digits.")
        if self.photo_id_type == "aadhaar" and self.photo_id_number and not re.fullmatch(r"\d{12}", self.photo_id_number):
            raise HTTPException(status_code=400, detail="Aadhaar number must be exactly 12 digits.")


@router.post("/employees")
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    payload.validate_fields()
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
        address=payload.address,
        photo_id_type=payload.photo_id_type,
        photo_id_number=payload.photo_id_number,
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
def list_employees(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    q = db.query(User).filter(User.role == UserRole.employee, User.tenant_id == user.tenant_id)
    employees = q.all()
    # Explicit field list — never return the ORM object directly, which would
    # leak hashed_password (and any other internal column) straight to the browser.
    return [
        {
            "id": e.id, "full_name": e.full_name, "email": e.email, "phone": e.phone,
            "designation": e.designation, "employee_code": e.employee_code,
            "branch_id": e.branch_id, "is_active": e.is_active, "created_at": e.created_at.isoformat(),
            "address": e.address, "photo_id_type": e.photo_id_type, "photo_id_number": e.photo_id_number,
        }
        for e in employees
    ]


class EmployeeUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    designation: str | None = None
    employee_code: str | None = None
    branch_id: str | None = None
    address: str | None = None
    photo_id_type: str | None = None
    photo_id_number: str | None = None

    def validate_fields(self):
        if self.full_name is not None and not self.full_name.strip():
            raise HTTPException(status_code=400, detail="Full name cannot be empty.")
        if self.phone and not re.fullmatch(r"\d{10}", self.phone):
            raise HTTPException(status_code=400, detail="Contact number must be exactly 10 digits.")
        if self.photo_id_type == "aadhaar" and self.photo_id_number and not re.fullmatch(r"\d{12}", self.photo_id_number):
            raise HTTPException(status_code=400, detail="Aadhaar number must be exactly 12 digits.")


@router.patch("/employees/{employee_id}")
def update_employee(employee_id: str, payload: EmployeeUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    payload.validate_fields()
    employee = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.role == UserRole.employee).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    if payload.branch_id:
        branch = db.query(Branch).filter(Branch.id == payload.branch_id, Branch.tenant_id == user.tenant_id).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Target branch not found")

    for field, value in payload.dict(exclude_unset=True).items():
        setattr(employee, field, value)
    db.commit()
    return {"status": "updated"}


@router.patch("/employees/{employee_id}/suspend")
def suspend_employee(employee_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    employee = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.role == UserRole.employee).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    employee.is_active = False
    db.commit()
    return {"status": "suspended"}


@router.patch("/employees/{employee_id}/activate")
def activate_employee(employee_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    employee = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.role == UserRole.employee).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    employee.is_active = True
    db.commit()
    return {"status": "active"}


@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    employee = db.query(User).filter(User.id == employee_id, User.tenant_id == user.tenant_id, User.role == UserRole.employee).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    try:
        # An employee's own KYC/ID documents aren't financial history — deleting
        # the employee should take these with them rather than block the delete.
        from app.models.finance import Document
        db.query(Document).filter(Document.employee_id == employee_id).delete()
        db.delete(employee)
        db.commit()
        return {"status": "deleted"}
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This employee has financial history (loans, payments, or audit records tied to them) and can't "
                   "be deleted, to protect that record. Suspend their login instead to keep the history intact."
        )


# ---------- SuperAdmins ----------
# A second (or third) SuperAdmin login — e.g. a temporary one for a vendor,
# an auditor, or testing — created and later removed by an EXISTING
# SuperAdmin, without ever touching any other SuperAdmin's own account.

class SuperAdminCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: str | None = None
    password: str | None = None  # omit to auto-generate a secure password

    def validate_fields(self):
        if not self.full_name or not self.full_name.strip():
            raise HTTPException(status_code=400, detail="Full name is required.")
        if self.phone and not re.fullmatch(r"\d{10}", self.phone):
            raise HTTPException(status_code=400, detail="Contact number must be exactly 10 digits.")


@router.post("/superadmins")
def create_superadmin(payload: SuperAdminCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    """
    Adds another SuperAdmin login under the same tenant. Only an existing
    SuperAdmin can call this, and it only ever adds a new row — it never
    reads or changes the password, role, or any other field of the
    SuperAdmin making the request or any other existing SuperAdmin.
    """
    payload.validate_fields()
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    was_generated = not payload.password
    plaintext_password = payload.password or generate_password()

    admin = User(
        tenant_id=user.tenant_id,
        branch_id=None,
        full_name=payload.full_name.strip(),
        email=payload.email,
        phone=payload.phone,
        hashed_password=hash_password(plaintext_password),
        role=UserRole.superadmin,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return {
        "id": admin.id,
        "email": admin.email,
        # Only returned once, right after creation.
        "password": plaintext_password if was_generated else None,
        "password_was_generated": was_generated,
    }


@router.get("/superadmins")
def list_superadmins(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    admins = db.query(User).filter(User.role == UserRole.superadmin, User.tenant_id == user.tenant_id).all()
    return [
        {
            "id": a.id, "full_name": a.full_name, "email": a.email, "phone": a.phone,
            "is_active": a.is_active, "created_at": a.created_at.isoformat(),
            "is_you": a.id == user.id,
        }
        for a in admins
    ]


@router.delete("/superadmins/{admin_id}")
def delete_superadmin(admin_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    """
    Removes a SuperAdmin login. Refuses to delete your OWN account through
    this endpoint (log in as a different SuperAdmin to remove one), and
    refuses to delete the last remaining SuperAdmin on the tenant, so you
    can never lock yourself — or anyone else — out.
    """
    admin = db.query(User).filter(User.id == admin_id, User.tenant_id == user.tenant_id, User.role == UserRole.superadmin).first()
    if not admin:
        raise HTTPException(status_code=404, detail="SuperAdmin not found")
    if admin.id == user.id:
        raise HTTPException(status_code=400, detail="You can't delete your own account while logged in as it. Log in as another SuperAdmin to remove this one.")
    remaining = db.query(User).filter(User.role == UserRole.superadmin, User.tenant_id == user.tenant_id).count()
    if remaining <= 1:
        raise HTTPException(status_code=400, detail="This is the last SuperAdmin on the account and can't be deleted.")
    db.delete(admin)
    db.commit()
    return {"status": "deleted"}
