from datetime import date, timedelta
import re
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_any, require_superadmin
from app.models.tenancy import User, UserRole, Tenant
from app.models.finance import Customer, LoanProduct, Loan, EMISchedule, LoanStatus, InterestType, LoanGroup, LoanGroupMember, GroupContribution, Payment, PaymentMethod
from app.utils.whatsapp import send_loan_status_notification
from app.utils.payouts import send_payout, is_configured as payout_configured
from app.utils.audit_log import log_money_event
from app.models.audit import MoneyEventType

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
    bank_account_holder_name: str | None = None
    bank_account_number: str | None = None
    bank_ifsc: str | None = None
    bank_name: str | None = None

    def validate_fields(self):
        if not re.fullmatch(r"\d{10}", self.phone):
            raise HTTPException(status_code=400, detail="Phone number must be exactly 10 digits.")
        if self.aadhaar_number and not re.fullmatch(r"\d{12}", self.aadhaar_number):
            raise HTTPException(status_code=400, detail="Aadhaar number must be exactly 12 digits.")
        if self.guarantor_phone and not re.fullmatch(r"\d{10}", self.guarantor_phone):
            raise HTTPException(status_code=400, detail="Guarantor phone number must be exactly 10 digits.")


@router.post("/customers")
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db), user: User = Depends(require_any)):
    payload.validate_fields()
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


# ---------- Loan Groups (Joint Liability Groups) ----------

class GroupCreate(BaseModel):
    branch_id: str
    name: str
    customer_ids: list[str]


@router.post("/groups")
def create_group(payload: GroupCreate, db: Session = Depends(get_db), user: User = Depends(require_any)):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Group name is required.")
    if len(payload.customer_ids) < 2:
        raise HTTPException(status_code=400, detail="A group needs at least 2 members.")
    if len(set(payload.customer_ids)) != len(payload.customer_ids):
        raise HTTPException(status_code=400, detail="The same customer was selected more than once.")

    customers = db.query(Customer).filter(Customer.id.in_(payload.customer_ids), Customer.tenant_id == user.tenant_id).all()
    if len(customers) != len(payload.customer_ids):
        raise HTTPException(status_code=404, detail="One or more selected customers were not found.")

    group = LoanGroup(tenant_id=user.tenant_id, branch_id=payload.branch_id, name=payload.name.strip(), created_by=user.id)
    db.add(group)
    db.flush()
    for cid in payload.customer_ids:
        db.add(LoanGroupMember(group_id=group.id, customer_id=cid))
    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name, "member_count": len(payload.customer_ids)}


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), user: User = Depends(require_any)):
    groups = scope_branch(db.query(LoanGroup), LoanGroup, user).all()
    result = []
    for g in groups:
        members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == g.id).all()
        member_names = []
        for m in members:
            c = db.query(Customer).filter(Customer.id == m.customer_id).first()
            member_names.append(c.full_name if c else "—")
        result.append({"id": g.id, "name": g.name, "branch_id": g.branch_id, "member_count": len(members), "member_names": member_names})
    return result


@router.get("/groups/{group_id}/members")
def get_group_members(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    group = db.query(LoanGroup).filter(LoanGroup.id == group_id, LoanGroup.tenant_id == user.tenant_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == group_id).all()
    result = []
    for m in members:
        c = db.query(Customer).filter(Customer.id == m.customer_id).first()
        result.append({"group_member_id": m.id, "customer_id": m.customer_id, "customer_name": c.full_name if c else "—", "phone": c.phone if c else None})
    return result


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
    is_group_loan: bool = False
    group_member_count: int | None = None     # required when is_group_loan=True
    penalty_type: str | None = None           # 'flat' | 'percentage'
    penalty_amount: float | None = None       # rupee amount, or % depending on penalty_type

    def validate_other(self):
        if self.interest_type == InterestType.other:
            if not self.custom_interest_label:
                raise HTTPException(status_code=400, detail="Give the custom interest type a label when selecting 'Other'.")
            if self.calculation_basis not in ("flat", "reducing"):
                raise HTTPException(status_code=400, detail="Choose whether 'Other' calculates like Flat or Reducing balance.")

    def validate_group(self):
        if self.is_group_loan and (not self.group_member_count or self.group_member_count < 2):
            raise HTTPException(status_code=400, detail="Group loan products need a member count of at least 2.")
        if self.penalty_type and self.penalty_type not in ("flat", "percentage", "per_day"):
            raise HTTPException(status_code=400, detail="penalty_type must be 'flat' or 'percentage'.")
        if self.penalty_type and not self.penalty_amount:
            raise HTTPException(status_code=400, detail="Set a penalty amount when a penalty type is chosen.")


@router.post("/loan-products")
def create_loan_product(payload: LoanProductCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    payload.validate_other()
    payload.validate_group()
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
    if not (include_inactive and user.role == UserRole.superadmin):
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
def update_loan_product(product_id: str, payload: LoanProductUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
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
def activate_loan_product(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    product.is_active = True
    db.commit()
    return {"status": "active"}


@router.patch("/loan-products/{product_id}/deactivate")
def deactivate_loan_product(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    product.is_active = False
    db.commit()
    return {"status": "inactive"}


@router.delete("/loan-products/{product_id}")
def delete_loan_product(product_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
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
    loan_product_id: str
    principal_amount: float
    customer_id: str | None = None  # for individual loans
    group_id: str | None = None     # for group loans — mutually exclusive with customer_id


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

    if product.is_group_loan:
        if not payload.group_id:
            raise HTTPException(status_code=400, detail="This is a group loan product — select a group.")
        group = db.query(LoanGroup).filter(LoanGroup.id == payload.group_id, LoanGroup.tenant_id == user.tenant_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        member_count = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == group.id).count()
        if product.group_member_count and member_count != product.group_member_count:
            raise HTTPException(
                status_code=400,
                detail=f"This product requires exactly {product.group_member_count} members — the selected group has {member_count}."
            )
        customer_id = None
        group_id = group.id
    else:
        if not payload.customer_id:
            raise HTTPException(status_code=400, detail="Select a customer for this individual loan.")
        customer_id = payload.customer_id
        group_id = None

    branch_count = db.query(Loan).filter(Loan.branch_id == payload.branch_id).count()
    loan_number = f"LN-{branch_count + 1:06d}"

    loan = Loan(
        tenant_id=user.tenant_id, branch_id=payload.branch_id, customer_id=customer_id, group_id=group_id,
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
def approve_loan(loan_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
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
def reject_loan(loan_id: str, payload: LoanRejectRequest, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
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


class LoanDisburse(BaseModel):
    disbursal_method: str = "cash"  # 'cash' | 'bank_transfer'
    disbursal_reference: str | None = None  # required if bank_transfer, e.g. UTR number


@router.patch("/loans/{loan_id}/disburse")
def disburse_loan(loan_id: str, payload: LoanDisburse = LoanDisburse(), db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan or loan.status != LoanStatus.approved:
        raise HTTPException(status_code=400, detail="Loan must be approved before disbursement")
    if payload.disbursal_method not in ("cash", "bank_transfer"):
        raise HTTPException(status_code=400, detail="disbursal_method must be 'cash' or 'bank_transfer'")

    is_group = loan.group_id is not None
    customer = None

    if is_group:
        if payload.disbursal_method == "bank_transfer":
            raise HTTPException(
                status_code=400,
                detail="Bank transfer disbursal isn't supported for group loans yet — disburse as cash."
            )
    else:
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        if payload.disbursal_method == "bank_transfer":
            if not (customer and customer.bank_account_number and customer.bank_ifsc):
                raise HTTPException(
                    status_code=400,
                    detail="This customer has no bank account details on file — required for bank transfer disbursal. "
                           "Add their bank details, or disburse as cash instead."
                )
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            if payout_configured(tenant):
                try:
                    result = send_payout(
                        tenant, customer.bank_account_number, customer.bank_ifsc,
                        customer.bank_account_holder_name or customer.full_name,
                        float(loan.principal_amount), f"Loan disbursal {loan.loan_number}", loan.id,
                    )
                    payload.disbursal_reference = result.get("utr") or result.get("payout_id")
                except (RuntimeError, NotImplementedError) as e:
                    raise HTTPException(status_code=502, detail=str(e))
            elif not payload.disbursal_reference:
                raise HTTPException(
                    status_code=400,
                    detail="Bank payouts aren't connected yet — enter the bank transaction reference (UTR) "
                           "after transferring the funds manually, or connect RazorpayX under Payment Settings."
                )

    product = db.query(LoanProduct).filter(LoanProduct.id == loan.loan_product_id).first()

    from datetime import datetime
    loan.status = LoanStatus.active
    loan.disbursed_amount = loan.principal_amount
    loan.disbursed_by = user.id
    loan.disbursed_at = datetime.utcnow()
    loan.disbursal_method = payload.disbursal_method
    loan.disbursal_reference = payload.disbursal_reference
    build_emi_schedule(loan, product, db)
    db.flush()  # need EMISchedule.id values before creating GroupContribution rows

    if is_group:
        members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == loan.group_id).all()
        installments = db.query(EMISchedule).filter(EMISchedule.loan_id == loan.id).all()
        for emi in installments:
            share = (Decimal(str(emi.total_due)) / len(members)).quantize(Decimal("0.01"))
            allocated = Decimal("0")
            for i, member in enumerate(members):
                # last member absorbs the rounding remainder so shares always sum exactly to total_due
                this_share = (Decimal(str(emi.total_due)) - allocated) if i == len(members) - 1 else share
                allocated += this_share
                db.add(GroupContribution(
                    tenant_id=user.tenant_id, emi_schedule_id=emi.id, group_member_id=member.id,
                    expected_amount=this_share,
                ))

    log_money_event(
        db, tenant_id=user.tenant_id, event_type=MoneyEventType.loan_disbursed,
        amount=loan.principal_amount, direction="out", actor_id=user.id, branch_id=loan.branch_id,
        counterparty_type="group" if is_group else "customer", counterparty_id=loan.group_id if is_group else loan.customer_id,
        method=payload.disbursal_method, reference=payload.disbursal_reference,
        related_record_id=loan.id, notes=f"Loan {loan.loan_number} disbursed",
    )
    db.commit()

    try:
        if is_group:
            members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == loan.group_id).all()
            for member in members:
                c = db.query(Customer).filter(Customer.id == member.customer_id).first()
                if c and c.phone:
                    send_loan_status_notification(c.phone, c.full_name, loan.loan_number, "active")
        elif customer and customer.phone:
            send_loan_status_notification(customer.phone, customer.full_name, loan.loan_number, "active")
    except Exception:
        pass

    return {"status": "disbursed", "loan_number": loan.loan_number, "disbursal_method": loan.disbursal_method}


@router.get("/loans")
def list_loans(db: Session = Depends(get_db), user: User = Depends(require_any)):
    loans = scope_branch(db.query(Loan), Loan, user).order_by(Loan.applied_at.desc()).all()
    customer_ids = {l.customer_id for l in loans if l.customer_id}
    customers = {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()} if customer_ids else {}
    group_ids = {l.group_id for l in loans if l.group_id}
    groups = {g.id: g for g in db.query(LoanGroup).filter(LoanGroup.id.in_(group_ids)).all()} if group_ids else {}

    # For every active group loan, work out whether ANY overdue installment
    # currently has an unpaid member — this is what powers the "⚠ Payment
    # pending" flag on the loans list, so a defaulting member is visible here
    # without having to open Collections and pick through installments.
    active_group_loan_ids = [l.id for l in loans if l.group_id and l.status == LoanStatus.active]
    defaulter_loan_ids = set()
    if active_group_loan_ids:
        from datetime import date
        overdue_emis = (
            db.query(EMISchedule)
            .filter(EMISchedule.loan_id.in_(active_group_loan_ids), EMISchedule.is_paid == False, EMISchedule.due_date < date.today())  # noqa: E712
            .all()
        )
        overdue_emi_ids = [e.id for e in overdue_emis]
        if overdue_emi_ids:
            emi_to_loan = {e.id: e.loan_id for e in overdue_emis}
            unpaid_contribution_emi_ids = {
                c.emi_schedule_id for c in
                db.query(GroupContribution).filter(GroupContribution.emi_schedule_id.in_(overdue_emi_ids), GroupContribution.is_paid == False).all()  # noqa: E712
            }
            defaulter_loan_ids = {emi_to_loan[eid] for eid in unpaid_contribution_emi_ids}

    result = []
    for l in loans:
        if l.group_id:
            g = groups.get(l.group_id)
            display_name = f"{g.name} (Group)" if g else "Group"
        else:
            c = customers.get(l.customer_id)
            display_name = c.full_name if c else "—"
        result.append({
            "id": l.id, "loan_number": l.loan_number, "principal_amount": float(l.principal_amount),
            "status": l.status.value, "customer_id": l.customer_id, "group_id": l.group_id,
            "customer_name": display_name, "is_group_loan": l.group_id is not None,
            "has_defaulter": l.id in defaulter_loan_ids,
            "applied_at": l.applied_at.isoformat(),
            "rejection_reason": l.rejection_reason,
        })
    return result


@router.get("/loans/{loan_id}/schedule")
def get_schedule(loan_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    installments = db.query(EMISchedule).filter(EMISchedule.loan_id == loan_id).order_by(EMISchedule.installment_no).all()

    if not loan.group_id:
        return installments

    # Group loan: attach a paid/unpaid member count to every installment, so the
    # schedule view immediately shows which installments still have people owing.
    result = []
    for emi in installments:
        contributions = db.query(GroupContribution).filter(GroupContribution.emi_schedule_id == emi.id).all()
        unpaid = sum(1 for c in contributions if not c.is_paid)
        result.append({
            "id": emi.id, "installment_no": emi.installment_no, "due_date": emi.due_date.isoformat(),
            "principal_due": float(emi.principal_due), "interest_due": float(emi.interest_due),
            "total_due": float(emi.total_due), "is_paid": emi.is_paid,
            "total_members": len(contributions), "paid_count": len(contributions) - unpaid, "unpaid_count": unpaid,
        })
    return result
