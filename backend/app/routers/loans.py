from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_any, require_superadmin_or_above
from app.models.tenancy import User, UserRole
from app.models.finance import Customer, LoanProduct, Loan, EMISchedule, LoanStatus, InterestType

router = APIRouter(tags=["customers & loans"])


def scope_branch(query, model, user: User):
    """Employees see only their branch; SuperAdmin sees the whole tenant."""
    query = query.filter(model.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        query = query.filter(model.branch_id == user.branch_id)
    return query


# ---------- Customers ----------

class CustomerCreate(BaseModel):
    branch_id: str
    full_name: str
    phone: str
    email: str | None = None
    address: str | None = None
    aadhaar_number: str | None = None
    pan_number: str | None = None
    guarantor_name: str | None = None
    guarantor_phone: str | None = None


@router.post("/customers")
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db), user: User = Depends(require_any)):
    count = db.query(Customer).filter(Customer.branch_id == payload.branch_id).count()
    code = f"CUS-{count + 1:05d}"
    customer = Customer(tenant_id=user.tenant_id, customer_code=code, created_by=user.id, **payload.dict())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/customers")
def list_customers(db: Session = Depends(get_db), user: User = Depends(require_any)):
    return scope_branch(db.query(Customer), Customer, user).all()


# ---------- Loan Products ----------

class LoanProductCreate(BaseModel):
    name: str
    interest_type: InterestType = InterestType.flat
    interest_rate_annual: float
    min_amount: float
    max_amount: float
    tenure_months: int
    repayment_frequency: str = "monthly"
    processing_fee_pct: float = 0


@router.post("/loan-products")
def create_loan_product(payload: LoanProductCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    product = LoanProduct(tenant_id=user.tenant_id, **payload.dict())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("/loan-products")
def list_loan_products(db: Session = Depends(get_db), user: User = Depends(require_any)):
    return db.query(LoanProduct).filter(LoanProduct.tenant_id == user.tenant_id, LoanProduct.is_active == True).all()


# ---------- Loans ----------

class LoanApply(BaseModel):
    branch_id: str
    customer_id: str
    loan_product_id: str
    principal_amount: float


def build_emi_schedule(loan: Loan, product: LoanProduct, db: Session):
    """Generates a flat or reducing-balance EMI schedule."""
    principal = Decimal(str(loan.principal_amount))
    annual_rate = Decimal(str(loan.interest_rate_annual)) / Decimal(100)
    months = loan.tenure_months
    freq_days = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(product.repayment_frequency, 30)
    installments = months if product.repayment_frequency == "monthly" else int(months * 30 / freq_days)

    if product.interest_type == InterestType.flat:
        total_interest = principal * annual_rate * Decimal(months) / Decimal(12)
        total_payable = principal + total_interest
        per_installment = (total_payable / installments).quantize(Decimal("0.01"))
        principal_per = (principal / installments).quantize(Decimal("0.01"))
        interest_per = (total_interest / installments).quantize(Decimal("0.01"))

        due = date.today()
        for i in range(1, installments + 1):
            due = due + timedelta(days=freq_days)
            db.add(EMISchedule(
                loan_id=loan.id, installment_no=i, due_date=due,
                principal_due=principal_per, interest_due=interest_per, total_due=per_installment,
            ))
        loan.total_payable = total_payable
    else:
        # Reducing balance: recompute interest on outstanding principal each period
        monthly_rate = annual_rate / Decimal(12) if product.repayment_frequency == "monthly" else annual_rate / Decimal(365) * freq_days
        outstanding = principal
        principal_per = (principal / installments).quantize(Decimal("0.01"))
        total_payable = Decimal("0")
        due = date.today()
        for i in range(1, installments + 1):
            due = due + timedelta(days=freq_days)
            interest_due = (outstanding * monthly_rate).quantize(Decimal("0.01"))
            total_due = principal_per + interest_due
            total_payable += total_due
            db.add(EMISchedule(
                loan_id=loan.id, installment_no=i, due_date=due,
                principal_due=principal_per, interest_due=interest_due, total_due=total_due,
            ))
            outstanding -= principal_per
        loan.total_payable = total_payable


@router.post("/loans/apply")
def apply_loan(payload: LoanApply, db: Session = Depends(get_db), user: User = Depends(require_any)):
    product = db.query(LoanProduct).filter(LoanProduct.id == payload.loan_product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    if not (product.min_amount <= Decimal(str(payload.principal_amount)) <= product.max_amount):
        raise HTTPException(status_code=400, detail=f"Amount must be between {product.min_amount} and {product.max_amount}")

    branch_count = db.query(Loan).filter(Loan.branch_id == payload.branch_id).count()
    loan_number = f"LN-{branch_count + 1:06d}"

    loan = Loan(
        tenant_id=user.tenant_id, branch_id=payload.branch_id, customer_id=payload.customer_id,
        loan_product_id=product.id, loan_number=loan_number,
        principal_amount=payload.principal_amount, interest_rate_annual=product.interest_rate_annual,
        tenure_months=product.tenure_months, status=LoanStatus.pending_approval,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return loan


@router.patch("/loans/{loan_id}/approve")
def approve_loan(loan_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    loan.status = LoanStatus.approved
    loan.approved_by = user.id
    db.commit()
    return {"status": "approved"}


@router.patch("/loans/{loan_id}/disburse")
def disburse_loan(loan_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan or loan.status != LoanStatus.approved:
        raise HTTPException(status_code=400, detail="Loan must be approved before disbursement")
    product = db.query(LoanProduct).filter(LoanProduct.id == loan.loan_product_id).first()

    from datetime import datetime
    loan.status = LoanStatus.active
    loan.disbursed_amount = loan.principal_amount
    loan.disbursed_by = user.id
    loan.disbursed_at = datetime.utcnow()
    build_emi_schedule(loan, product, db)
    db.commit()
    return {"status": "disbursed", "loan_number": loan.loan_number}


@router.get("/loans")
def list_loans(db: Session = Depends(get_db), user: User = Depends(require_any)):
    return scope_branch(db.query(Loan), Loan, user).all()


@router.get("/loans/{loan_id}/schedule")
def get_schedule(loan_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    return db.query(EMISchedule).filter(EMISchedule.loan_id == loan_id).order_by(EMISchedule.installment_no).all()
