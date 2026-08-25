from datetime import date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_any, require_superadmin_or_above
from app.models.tenancy import User, UserRole
from app.models.finance import Customer, LoanProduct, Loan, EMISchedule, LoanStatus, InterestType
from app.utils.whatsapp import send_loan_status_notification
from app.utils.kyc_validation import validate_aadhaar, validate_pan
from app.utils.credit_check import check_credit_score, eligible_amount_for_score, is_configured as credit_check_configured

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
    phone_verified: bool = False  # set true by the frontend only after a successful OTP check


@router.post("/customers")
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db), user: User = Depends(require_any)):
    if payload.aadhaar_number:
        valid, err = validate_aadhaar(payload.aadhaar_number)
        if not valid:
            raise HTTPException(status_code=400, detail=err)
    if payload.pan_number:
        valid, err = validate_pan(payload.pan_number)
        if not valid:
            raise HTTPException(status_code=400, detail=err)

    count = db.query(Customer).filter(Customer.branch_id == payload.branch_id).count()
    code = f"CUS-{count + 1:05d}"
    customer = Customer(tenant_id=user.tenant_id, customer_code=code, created_by=user.id, **payload.dict())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/customers/{customer_id}/credit-check")
def credit_check(customer_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    Returns a real credit bureau score once CREDIT_BUREAU_API_KEY is configured
    with a licensed provider. Until then, returns a clear 'not configured'
    response rather than a fake score — see app/utils/credit_check.py.
    """
    customer = db.query(Customer).filter(Customer.id == customer_id, Customer.tenant_id == user.tenant_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.pan_number:
        raise HTTPException(status_code=400, detail="This customer has no PAN number on file — required for a credit check.")

    if not credit_check_configured():
        return {
            "configured": False,
            "message": "Credit score checks require a licensed bureau/KYC provider agreement "
                       "(CIBIL, Karza, Signzy, Digio, etc.) — not yet connected for this account.",
        }
    try:
        result = check_credit_score(customer.pan_number)
        return {"configured": True, **result}
    except (RuntimeError, NotImplementedError) as e:
        raise HTTPException(status_code=502, detail=str(e))


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
    custom_interest_label: str | None = None  # required (by validation below) when interest_type='other'
    calculation_basis: str | None = None      # 'flat' | 'reducing' — required when interest_type='other'

    def validate_other(self):
        if self.interest_type == InterestType.other:
            if not self.custom_interest_label:
                raise HTTPException(status_code=400, detail="Give the custom interest type a label when selecting 'Other'.")
            if self.calculation_basis not in ("flat", "reducing"):
                raise HTTPException(status_code=400, detail="Choose whether 'Other' calculates like Flat or Reducing balance.")


@router.post("/loan-products")
def create_loan_product(payload: LoanProductCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    payload.validate_other()
    product = LoanProduct(tenant_id=user.tenant_id, **payload.dict())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("/loan-products")
def list_loan_products(include_inactive: bool = False, db: Session = Depends(get_db), user: User = Depends(require_any)):
    q = db.query(LoanProduct).filter(LoanProduct.tenant_id == user.tenant_id)
    # Only SuperAdmin+ can see inactive products (needed to reactivate them) — employees
    # applying for a loan should only ever see what's currently offered.
    if not (include_inactive and user.role in (UserRole.superadmin, UserRole.superemeadmin)):
        q = q.filter(LoanProduct.is_active == True)
    return q.all()


class LoanProductUpdate(BaseModel):
    name: str | None = None
    interest_type: InterestType | None = None
    interest_rate_annual: float | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    tenure_months: int | None = None
    repayment_frequency: str | None = None
    processing_fee_pct: float | None = None
    custom_interest_label: str | None = None
    calculation_basis: str | None = None


@router.patch("/loan-products/{product_id}")
def update_loan_product(product_id: str, payload: LoanProductUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")

    updates = payload.dict(exclude_unset=True)
    new_interest_type = updates.get("interest_type", product.interest_type)
    if new_interest_type == InterestType.other:
        new_label = updates.get("custom_interest_label", product.custom_interest_label)
        new_basis = updates.get("calculation_basis", product.calculation_basis)
        if not new_label:
            raise HTTPException(status_code=400, detail="Give the custom interest type a label when selecting 'Other'.")
        if new_basis not in ("flat", "reducing"):
            raise HTTPException(status_code=400, detail="Choose whether 'Other' calculates like Flat or Reducing balance.")

    for field, value in updates.items():
        setattr(product, field, value)
    db.commit()
    return {"status": "updated"}


@router.patch("/loan-products/{product_id}/activate")
def activate_loan_product(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    product.is_active = True
    db.commit()
    return {"status": "active"}


@router.patch("/loan-products/{product_id}/deactivate")
def deactivate_loan_product(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    product.is_active = False
    db.commit()
    return {"status": "inactive"}


@router.delete("/loan-products/{product_id}")
def delete_loan_product(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    try:
        db.delete(product)
        db.commit()
        return {"status": "deleted"}
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This loan product has existing loans against it and can't be deleted. "
                   "Deactivate it instead to stop new applications while keeping loan history intact."
        )


# ---------- Loans ----------

class LoanApply(BaseModel):
    branch_id: str
    customer_id: str
    loan_product_id: str
    principal_amount: float


def resolve_calculation_basis(product: LoanProduct) -> InterestType:
    """
    interest_type is a display label; the EMI math always needs a concrete
    flat/reducing formula. For flat/reducing products these are the same
    thing. For 'other' products, calculation_basis (set at creation) says
    which real formula to use — a custom label never changes the actual math.
    """
    if product.interest_type != InterestType.other:
        return product.interest_type
    if product.calculation_basis == "reducing":
        return InterestType.reducing
    return InterestType.flat  # default basis if somehow unset


def build_emi_schedule(loan: Loan, product: LoanProduct, db: Session):
    """Generates a flat or reducing-balance EMI schedule."""
    principal = Decimal(str(loan.principal_amount))
    annual_rate = Decimal(str(loan.interest_rate_annual)) / Decimal(100)
    months = loan.tenure_months
    freq_days = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(product.repayment_frequency, 30)
    installments = months if product.repayment_frequency == "monthly" else int(months * 30 / freq_days)
    basis = resolve_calculation_basis(product)

    if basis == InterestType.flat:
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

    # Credit-score-based cap — only actually restricts anything once a real bureau/KYC
    # provider is connected (CREDIT_BUREAU_API_KEY set). Until then this silently
    # no-ops, exactly like every other credit_check.py call site.
    if credit_check_configured():
        customer = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.tenant_id == user.tenant_id).first()
        if customer and customer.pan_number:
            try:
                result = check_credit_score(customer.pan_number)
                cap = eligible_amount_for_score(result["score"], product.max_amount)
                if Decimal(str(payload.principal_amount)) > cap:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Based on this customer's credit score ({result['score']}), the maximum amount "
                               f"eligible on this product is ₹{cap}."
                    )
            except (RuntimeError, NotImplementedError):
                pass  # bureau call itself failed/unavailable — don't block the application on that

    branch_count = db.query(Loan).filter(Loan.branch_id == payload.branch_id).count()
    loan_number = f"LN-{branch_count + 1:06d}"

    loan = Loan(
        tenant_id=user.tenant_id, branch_id=payload.branch_id, customer_id=payload.customer_id,
        loan_product_id=product.id, loan_number=loan_number,
        principal_amount=payload.principal_amount, interest_rate_annual=product.interest_rate_annual,
        tenure_months=product.tenure_months, status=LoanStatus.pending_approval,
        applied_by=user.id,
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
    if loan.status != LoanStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Only a loan pending approval can be approved.")
    loan.status = LoanStatus.approved
    loan.approved_by = user.id
    db.commit()

    try:
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        if customer and customer.phone:
            send_loan_status_notification(customer.phone, customer.full_name, loan.loan_number, "approved")
    except Exception:
        pass

    return {"status": "approved"}


class LoanRejectRequest(BaseModel):
    reason: str


@router.patch("/loans/{loan_id}/reject")
def reject_loan(loan_id: str, payload: LoanRejectRequest, db: Session = Depends(get_db), user: User = Depends(require_superadmin_or_above)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.status != LoanStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Only a loan pending approval can be rejected.")
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to reject a loan application.")

    from datetime import datetime as _dt
    loan.status = LoanStatus.rejected
    loan.rejected_by = user.id
    loan.rejected_at = _dt.utcnow()
    loan.rejection_reason = payload.reason.strip()
    db.commit()

    try:
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        if customer and customer.phone:
            send_loan_status_notification(customer.phone, customer.full_name, loan.loan_number, "rejected", reason=loan.rejection_reason)
    except Exception:
        pass

    return {"status": "rejected", "reason": loan.rejection_reason}


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

    try:
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        if customer and customer.phone:
            send_loan_status_notification(customer.phone, customer.full_name, loan.loan_number, "active")
    except Exception:
        pass

    return {"status": "disbursed", "loan_number": loan.loan_number}


@router.get("/loans")
def list_loans(db: Session = Depends(get_db), user: User = Depends(require_any)):
    loans = scope_branch(db.query(Loan), Loan, user).order_by(Loan.applied_at.desc()).all()
    customer_ids = {l.customer_id for l in loans}
    customers = {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()} if customer_ids else {}
    result = []
    for l in loans:
        c = customers.get(l.customer_id)
        result.append({
            "id": l.id, "loan_number": l.loan_number, "principal_amount": float(l.principal_amount),
            "status": l.status.value, "customer_id": l.customer_id,
            "customer_name": c.full_name if c else "—", "applied_at": l.applied_at.isoformat(),
            "rejection_reason": l.rejection_reason,
        })
    return result


@router.get("/loans/{loan_id}/schedule")
def get_schedule(loan_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    return db.query(EMISchedule).filter(EMISchedule.loan_id == loan_id).order_by(EMISchedule.installment_no).all()
