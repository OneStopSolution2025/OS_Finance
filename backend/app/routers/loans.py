from datetime import date, timedelta
import re
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_any, require_superadmin
from app.models.tenancy import User, UserRole, Tenant, Branch
from app.models.finance import Customer, LoanProduct, Loan, EMISchedule, LoanStatus, InterestType, LoanGroup, LoanGroupMember, GroupContribution, Payment, PaymentMethod
from app.utils.whatsapp import send_loan_status_notification
from app.utils.payouts import send_payout, is_configured as payout_configured
from app.utils.tz import ist_today
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
    """
    SuperAdmin sees the whole tenant. An employee sees only customers THEY
    personally onboarded — not their whole branch's customers, so two
    employees at the same branch don't see each other's people. Customers
    created before this field existed (created_by is NULL) still show up for
    everyone at that branch, so nothing already onboarded suddenly vanishes.
    """
    q = scope_branch(db.query(Customer), Customer, user)
    if user.role == UserRole.employee:
        q = q.filter(or_(Customer.created_by == user.id, Customer.created_by.is_(None)))
    return q.all()


# ---------- Loan Groups (Joint Liability Groups) ----------

class GroupCreate(BaseModel):
    branch_id: str
    name: str
    customer_ids: list[str]
    center_place: str | None = None


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

    group = LoanGroup(
        tenant_id=user.tenant_id, branch_id=payload.branch_id, name=payload.name.strip(),
        center_place=(payload.center_place.strip() if payload.center_place else None),
        created_by=user.id,
    )
    db.add(group)
    db.flush()
    for cid in payload.customer_ids:
        db.add(LoanGroupMember(group_id=group.id, customer_id=cid))
    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name, "center_place": group.center_place, "member_count": len(payload.customer_ids)}


@router.get("/groups")
def list_groups(db: Session = Depends(get_db), user: User = Depends(require_any)):
    q = scope_branch(db.query(LoanGroup), LoanGroup, user)
    if user.role == UserRole.employee:
        q = q.filter(or_(LoanGroup.created_by == user.id, LoanGroup.created_by.is_(None)))
    groups = q.all()
    branch_ids = {g.branch_id for g in groups}
    branches = {b.id: b for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}
    creator_ids = {g.created_by for g in groups if g.created_by}
    creators = {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()} if creator_ids else {}
    group_ids = [g.id for g in groups]
    loans_by_group = {}
    if group_ids:
        for l in db.query(Loan).filter(Loan.group_id.in_(group_ids)).order_by(Loan.applied_at.desc()).all():
            loans_by_group.setdefault(l.group_id, l)  # most recent loan per group, since applied_at desc

    result = []
    for g in groups:
        members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == g.id).all()
        member_names = []
        for m in members:
            c = db.query(Customer).filter(Customer.id == m.customer_id).first()
            member_names.append(c.full_name if c else "—")

        branch = branches.get(g.branch_id)
        creator = creators.get(g.created_by)
        loan = loans_by_group.get(g.id)

        result.append({
            "id": g.id, "name": g.name, "branch_id": g.branch_id, "center_place": g.center_place,
            "branch_name": branch.name if branch else "Unknown",
            "created_by_name": creator.full_name if creator else "Unknown",
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "member_count": len(members), "member_names": member_names,
            "loan_number": loan.loan_number if loan else None,
            "loan_status": loan.status.value if loan else None,
            "principal_amount": float(loan.principal_amount) if loan else None,
            "disbursed_amount": float(loan.disbursed_amount) if loan and loan.disbursed_amount else None,
        })
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


class GroupUpdate(BaseModel):
    name: str | None = None
    center_place: str | None = None


@router.patch("/groups/{group_id}")
def update_group(group_id: str, payload: GroupUpdate, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    Edits a group's own details (name, center place). Does not touch its
    members or any loan history — see add_group_member/remove_group_member
    below for membership changes, and delete_group for removing the group
    itself.
    """
    group = db.query(LoanGroup).filter(LoanGroup.id == group_id, LoanGroup.tenant_id == user.tenant_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="Group name is required.")
        group.name = payload.name.strip()
    if payload.center_place is not None:
        group.center_place = payload.center_place.strip() or None
    db.commit()
    db.refresh(group)
    return {"id": group.id, "name": group.name, "center_place": group.center_place}


class GroupMemberAdd(BaseModel):
    customer_id: str


@router.post("/groups/{group_id}/members")
def add_group_member(group_id: str, payload: GroupMemberAdd, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    Adds one more member to an existing group. Safe at any time — it only
    adds a row, so it never disturbs a loan already applied for, approved,
    or disbursed against this group (those keep the member roster they were
    taken with; a wider group only affects loans applied for afterwards).
    """
    group = db.query(LoanGroup).filter(LoanGroup.id == group_id, LoanGroup.tenant_id == user.tenant_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    customer = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.tenant_id == user.tenant_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    existing = db.query(LoanGroupMember).filter(
        LoanGroupMember.group_id == group_id, LoanGroupMember.customer_id == payload.customer_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="This customer is already a member of the group.")
    member = LoanGroupMember(group_id=group_id, customer_id=payload.customer_id)
    db.add(member)
    db.commit()
    db.refresh(member)
    return {"group_member_id": member.id, "customer_id": member.customer_id, "customer_name": customer.full_name, "phone": customer.phone}


@router.delete("/groups/{group_id}/members/{member_id}")
def remove_group_member(group_id: str, member_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    Removes a member from a group. Blocked once that member has any
    GroupContribution row against them (created the moment a group loan
    involving them is disbursed — see disburse_loan below), since that
    history references this member's id and must stay intact. Also keeps
    the group at 2+ members, matching the minimum enforced at creation.
    """
    group = db.query(LoanGroup).filter(LoanGroup.id == group_id, LoanGroup.tenant_id == user.tenant_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    member = db.query(LoanGroupMember).filter(LoanGroupMember.id == member_id, LoanGroupMember.group_id == group_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this group.")
    has_contributions = db.query(GroupContribution).filter(GroupContribution.group_member_id == member_id).first()
    if has_contributions:
        raise HTTPException(
            status_code=400,
            detail="This member already has installment history on a disbursed group loan and can't be removed.",
        )
    remaining = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == group_id).count()
    if remaining <= 2:
        raise HTTPException(status_code=400, detail="A group needs at least 2 members — add a replacement before removing this one.")
    db.delete(member)
    db.commit()
    return {"status": "removed", "group_member_id": member_id}


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    Deletes a group outright, along with its member roster. Blocked if any
    loan (of any status) has ever been applied for against this group, since
    Loan.group_id points at it — deleting it then would orphan that loan.
    """
    group = db.query(LoanGroup).filter(LoanGroup.id == group_id, LoanGroup.tenant_id == user.tenant_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    has_loans = db.query(Loan).filter(Loan.group_id == group_id).first()
    if has_loans:
        raise HTTPException(status_code=400, detail="This group has loan history and can't be deleted.")
    db.query(LoanGroupMember).filter(LoanGroupMember.group_id == group_id).delete()
    db.delete(group)
    db.commit()
    return {"status": "deleted"}


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
    custom_schedule_enabled: bool = False
    custom_phase1_weeks: int | None = 10
    custom_phase2_weeks: int | None = 6
    custom_phase3_weeks: int | None = 4
    custom_weekly_savings: float | None = 0  # superseded by the per-phase savings fields below — kept for compatibility, unused
    # Default per-phase WEEKLY figures for a custom phased schedule — entered by hand,
    # not calculated or split from any total, and not derived from interest_rate_annual.
    # Every real loan snapshots its own copy of these (editable at application time), so
    # these are only the starting defaults shown on the loan product and on a brand-new
    # application for this product. The weekly total is simply Principal + Interest +
    # Savings — Interest is entered directly here, not derived from a combined EMI figure.
    custom_phase1_principal: float | None = 0
    custom_phase1_interest: float | None = 0
    custom_phase1_savings: float | None = 0
    custom_phase2_principal: float | None = 0
    custom_phase2_interest: float | None = 0
    custom_phase2_savings: float | None = 0
    custom_phase3_principal: float | None = 0
    custom_phase3_interest: float | None = 0
    custom_phase3_savings: float | None = 0

    def validate_other(self):
        if self.interest_type == InterestType.other:
            if not self.custom_interest_label:
                raise HTTPException(status_code=400, detail="Give the custom interest type a label when selecting 'Other'.")
            if self.calculation_basis not in ("flat", "reducing"):
                raise HTTPException(status_code=400, detail="Choose whether 'Other' calculates like Flat or Reducing balance.")
            if self.custom_schedule_enabled:
                if not all([self.custom_phase1_weeks, self.custom_phase2_weeks, self.custom_phase3_weeks]):
                    raise HTTPException(status_code=400, detail="All three phase week-counts are required for a custom phased schedule — none can be left blank or zero.")
                for label, value in [
                    ("Phase 1 principal", self.custom_phase1_principal), ("Phase 1 interest", self.custom_phase1_interest), ("Phase 1 savings", self.custom_phase1_savings),
                    ("Phase 2 principal", self.custom_phase2_principal), ("Phase 2 interest", self.custom_phase2_interest), ("Phase 2 savings", self.custom_phase2_savings),
                    ("Phase 3 principal", self.custom_phase3_principal), ("Phase 3 interest", self.custom_phase3_interest), ("Phase 3 savings", self.custom_phase3_savings),
                ]:
                    if value is not None and value < 0:
                        raise HTTPException(status_code=400, detail=f"{label} can't be negative.")
        elif self.custom_schedule_enabled:
            raise HTTPException(status_code=400, detail="The custom phased schedule is only available when Interest Type is 'Other (custom)'.")

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
    custom_schedule_enabled: bool | None = None
    custom_phase1_weeks: int | None = None
    custom_phase2_weeks: int | None = None
    custom_phase3_weeks: int | None = None
    custom_weekly_savings: float | None = None
    custom_phase1_principal: float | None = None
    custom_phase1_interest: float | None = None
    custom_phase1_savings: float | None = None
    custom_phase2_principal: float | None = None
    custom_phase2_interest: float | None = None
    custom_phase2_savings: float | None = None
    custom_phase3_principal: float | None = None
    custom_phase3_interest: float | None = None
    custom_phase3_savings: float | None = None


@router.patch("/loan-products/{product_id}")
def update_loan_product(product_id: str, payload: LoanProductUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    updates = payload.dict(exclude_unset=True)
    if "name" in updates and not (updates["name"] or "").strip():
        raise HTTPException(status_code=400, detail="Loan product name cannot be empty.")
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


@router.get("/loan-products/{product_id}/installment-sheet")
def download_installment_sheet(
    product_id: str, amount: float, format: str = "pdf", start_date: date | None = None,
    db: Session = Depends(get_db), user: User = Depends(require_superadmin),
):
    """
    A projected installment schedule for this product at a chosen sample
    amount — no real loan is created, this is purely for showing a
    prospective customer what their repayments would look like. Only
    SuperAdmin can pull this, matching the rest of the reports/exports.
    """
    from app.utils.installment_sheet import generate_installment_sheet_pdf, generate_installment_sheet_xlsx

    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    if not (product.min_amount <= Decimal(str(amount)) <= product.max_amount):
        raise HTTPException(status_code=400, detail=f"Amount must be between {product.min_amount} and {product.max_amount} for this product.")
    if product.is_group_loan:
        raise HTTPException(status_code=400, detail="Installment sheets are for individual products — a group loan's per-member share depends on the group size chosen at application time.")

    show_savings = bool(product.custom_schedule_enabled)
    if show_savings:
        if not start_date:
            raise HTTPException(status_code=400, detail="This product uses the custom phased schedule — choose a start date and click Generate.")
        # A custom-schedule product has no rate-driven math to run against a sample
        # amount — the schedule comes entirely from the product's own manually-entered
        # per-phase defaults (Principal/Interest/Savings), same as the preview endpoint.
        raw_rows = calculate_custom_phased_schedule(product_phase_config(product), start_date)
        rows = [{
            "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(),
            "principal_due": float(r["principal_due"]), "interest_due": float(r["interest_due"]),
            "savings_due": float(r["savings_due"]), "total_due": float(r["total_due"]),
        } for r in raw_rows]
    else:
        raw_rows = calculate_emi_schedule(amount, product.interest_rate_annual, product.tenure_months, product)
        rows = [{
            "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(),
            "principal_due": float(r["principal_due"]), "interest_due": float(r["interest_due"]), "total_due": float(r["total_due"]),
        } for r in raw_rows]

    if format == "xlsx":
        file_path = generate_installment_sheet_xlsx(product.name, amount, product, rows, show_savings=show_savings)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        file_path = generate_installment_sheet_pdf(product.name, amount, product, rows, show_savings=show_savings)
        media_type = "application/pdf"
        ext = "pdf"

    filename = f"{product.name.replace(' ', '_')}_installment_sheet.{ext}"
    return FileResponse(file_path, media_type=media_type, filename=filename)


@router.get("/loans/{loan_id}/installment-sheet")
def download_loan_installment_sheet(loan_id: str, format: str = "pdf", for_customer: bool = False, view: str = "list", member_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    The real installment sheet for one specific loan, available once
    SuperAdmin has approved it — the employee who applied for it (or anyone
    else in the tenant, matching the same access as viewing the schedule
    itself) can pull this with the actual loan number, customer/group name,
    and branch on it. If the loan hasn't been disbursed yet, this is a
    clearly-labeled projection; once disbursed, it's built from the real
    schedule with real due dates and paid/unpaid status.

    for_customer=True produces the copy meant to be handed to the customer —
    it omits the Savings column and the processing-fee line, both of which
    stay visible to staff (employee/SuperAdmin) on the default copy.

    view selects the document layout for a group loan, matching the
    client's own paper templates — it defaults to "list" (the original,
    unchanged per-member flat list this endpoint has always produced), and
    can instead be "center" (one aggregate roster + schedule sheet for the
    whole group, using the loan's own EMI/Principal/Interest/Saving figures
    directly rather than a per-member split) or "member" (an individual
    "M.L.L." ledger page for one member, named by member_id — the
    LoanGroupMember id from GET /groups/{id}/members — with a running
    balance column). "center"/"member" only apply to group loans.
    """
    from app.utils.installment_sheet import generate_loan_installment_sheet_pdf, generate_loan_installment_sheet_xlsx

    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.status in (LoanStatus.pending_approval, LoanStatus.rejected):
        raise HTTPException(status_code=400, detail="This loan hasn't been approved yet — an installment sheet isn't available until it is.")

    product = db.query(LoanProduct).filter(LoanProduct.id == loan.loan_product_id).first()
    has_custom_schedule = bool(product and product.custom_schedule_enabled)

    branch = db.query(Branch).filter(Branch.id == loan.branch_id).first()
    branch_name = branch.name if branch else "Unknown"

    if loan.group_id:
        group = db.query(LoanGroup).filter(LoanGroup.id == loan.group_id).first()
        payer_name = group.name if group else "Unknown group"
        payer_type = "Group"
    else:
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        payer_name = customer.full_name if customer else "Unknown customer"
        payer_type = "Individual"

    real_schedule = db.query(EMISchedule).filter(EMISchedule.loan_id == loan_id).order_by(EMISchedule.installment_no).all()
    members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == loan.group_id).all() if loan.group_id else []
    member_customer_ids = {m.id: m.customer_id for m in members}
    member_customers = {c.id: c for c in db.query(Customer).filter(Customer.id.in_(member_customer_ids.values())).all()} if members else {}

    if view in ("center", "member"):
        if not loan.group_id:
            raise HTTPException(status_code=400, detail="The center and member views are only available for group loans.")

        is_projected = not bool(real_schedule)
        if is_projected:
            if has_custom_schedule:
                if not loan.custom_start_date:
                    raise HTTPException(status_code=400, detail="This loan uses a custom phased schedule but has no start date set — this shouldn't happen for a loan applied after this feature shipped.")
                raw_rows = calculate_custom_phased_schedule(loan_phase_config(loan, product), loan.custom_start_date)
            else:
                raw_rows = calculate_emi_schedule(loan.principal_amount, loan.interest_rate_annual, loan.tenure_months, product)

        branch_address = ", ".join(filter(None, [branch.address if branch else None, branch.city if branch else None, branch.state if branch else None])) or None
        group = db.query(LoanGroup).filter(LoanGroup.id == loan.group_id).first()
        center_name = group.name if group else "Unknown group"

        if view == "center":
            from app.utils.installment_sheet import generate_group_center_sheet_pdf, generate_group_center_sheet_xlsx

            center_place = group.center_place if group else None
            member_roster = []
            for m in members:
                c = member_customers.get(m.customer_id)
                member_roster.append({"name": c.full_name if c else "Unknown member", "phone": c.phone if c else None})

            if real_schedule:
                agg_rows = [{
                    "installment_no": e.installment_no, "due_date": e.due_date.isoformat(),
                    "principal_due": float(e.principal_due), "interest_due": float(e.interest_due),
                    "savings_due": float(e.savings_due or 0), "total_due": float(e.total_due), "is_paid": e.is_paid,
                } for e in real_schedule]
            else:
                agg_rows = [{
                    "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(),
                    "principal_due": float(r["principal_due"]), "interest_due": float(r["interest_due"]),
                    "savings_due": float(r.get("savings_due", 0)), "total_due": float(r["total_due"]),
                } for r in raw_rows]

            if format == "xlsx":
                file_path = generate_group_center_sheet_xlsx(loan.loan_number, center_name, center_place, branch_name, is_projected, float(loan.principal_amount), member_roster, agg_rows, branch_address=branch_address, show_savings=not for_customer)
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ext = "xlsx"
            else:
                file_path = generate_group_center_sheet_pdf(loan.loan_number, center_name, center_place, branch_name, is_projected, float(loan.principal_amount), member_roster, agg_rows, branch_address=branch_address, show_savings=not for_customer)
                media_type = "application/pdf"
                ext = "pdf"
            filename = f"{loan.loan_number}_center_sheet{'_customer' if for_customer else ''}.{ext}"
            return FileResponse(file_path, media_type=media_type, filename=filename)

        # view == "member"
        if not member_id:
            raise HTTPException(status_code=400, detail="Choose which member's sheet to download (member_id).")
        member = next((m for m in members if m.id == member_id), None)
        if not member:
            raise HTTPException(status_code=404, detail="Group member not found on this loan's group.")
        member_index = members.index(member)
        customer = member_customers.get(member.customer_id)
        member_name = customer.full_name if customer else "Unknown member"
        member_phone = customer.phone if customer else None

        from app.utils.installment_sheet import generate_member_mll_sheet_pdf, generate_member_mll_sheet_xlsx

        member_loan_amount = split_evenly(loan.principal_amount, len(members))[member_index]
        loan_dis_date = loan.disbursed_at.date().isoformat() if loan.disbursed_at else (loan.custom_start_date.isoformat() if loan.custom_start_date else None)

        balance = Decimal(str(member_loan_amount))
        member_rows = []
        if real_schedule:
            for e in real_schedule:
                p_share = split_evenly(e.principal_due, len(members))[member_index]
                i_share = split_evenly(e.interest_due, len(members))[member_index]
                balance -= p_share
                if balance < 0:
                    balance = Decimal("0")
                member_rows.append({
                    "installment_no": e.installment_no, "due_date": e.due_date.isoformat(),
                    "principal_due": float(p_share), "interest_due": float(i_share),
                    "balance": float(balance), "is_paid": e.is_paid,
                })
        else:
            for r in raw_rows:
                p_share = split_evenly(r["principal_due"], len(members))[member_index]
                i_share = split_evenly(r["interest_due"], len(members))[member_index]
                balance -= p_share
                if balance < 0:
                    balance = Decimal("0")
                member_rows.append({
                    "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(),
                    "principal_due": float(p_share), "interest_due": float(i_share),
                    "balance": float(balance),
                })

        # M.L.L. No — this member's sequential position in the group, with the
        # Indian financial year (April-March) of the loan's disbursal (or, if
        # not yet disbursed, its chosen start date).
        from datetime import datetime as _dt
        mll_date = loan.disbursed_at.date() if loan.disbursed_at else loan.custom_start_date
        if mll_date:
            fy_start = mll_date.year if mll_date.month >= 4 else mll_date.year - 1
        else:
            fy_start = _dt.utcnow().year
        mll_no = f"{member_index + 1:02d}/{fy_start}-{fy_start + 1}"

        if format == "xlsx":
            file_path = generate_member_mll_sheet_xlsx(mll_no, center_name, member_name, float(member_loan_amount), loan_dis_date, member_phone, branch_name, is_projected, member_rows, branch_address=branch_address)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        else:
            file_path = generate_member_mll_sheet_pdf(mll_no, center_name, member_name, float(member_loan_amount), loan_dis_date, member_phone, branch_name, is_projected, member_rows, branch_address=branch_address)
            media_type = "application/pdf"
            ext = "pdf"
        filename = f"{loan.loan_number}_{member_name.replace(' ', '_')}_mll_sheet.{ext}"
        return FileResponse(file_path, media_type=media_type, filename=filename)

    if real_schedule:
        is_projected = False
        if loan.group_id:
            # Real, per-member shares — exactly what each member actually
            # owes and whether they've actually paid it, not a group total
            # that hides who's responsible for what.
            rows = []
            for e in real_schedule:
                contributions = db.query(GroupContribution).filter(GroupContribution.emi_schedule_id == e.id).all()
                for c in contributions:
                    member = next((m for m in members if m.id == c.group_member_id), None)
                    customer = member_customers.get(member.customer_id) if member else None
                    rows.append({
                        "installment_no": e.installment_no, "due_date": e.due_date.isoformat(),
                        "member_name": customer.full_name if customer else "Unknown member",
                        "principal_due": float(c.expected_amount), "interest_due": 0.0,
                        "total_due": float(c.expected_amount) + float(c.penalty_amount or 0),
                        "is_paid": c.is_paid,
                    })
        else:
            rows = [{
                "installment_no": e.installment_no, "due_date": e.due_date.isoformat(),
                "principal_due": float(e.principal_due), "interest_due": float(e.interest_due),
                "savings_due": float(e.savings_due or 0),
                "total_due": float(e.total_due), "is_paid": e.is_paid,
            } for e in real_schedule]
    else:
        # Approved but not yet disbursed — no real schedule exists yet, so
        # project one from the loan's own locked-in terms as a preview.
        is_projected = True
        if has_custom_schedule:
            if not loan.custom_start_date:
                raise HTTPException(status_code=400, detail="This loan uses a custom phased schedule but has no start date set — this shouldn't happen for a loan applied after this feature shipped.")
            raw_rows = calculate_custom_phased_schedule(loan_phase_config(loan, product), loan.custom_start_date)
        else:
            raw_rows = calculate_emi_schedule(loan.principal_amount, loan.interest_rate_annual, loan.tenure_months, product)
        if loan.group_id and members:
            # Same even-split math the real disbursal will use, so this
            # preview shows each member the same amount they'll actually be
            # asked to pay once the loan is genuinely disbursed.
            rows = []
            for r in raw_rows:
                shares = split_evenly(r["total_due"], len(members))
                for member, share in zip(members, shares):
                    customer = member_customers.get(member.customer_id)
                    rows.append({
                        "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(),
                        "member_name": customer.full_name if customer else "Unknown member",
                        "principal_due": float(share), "interest_due": 0.0, "total_due": float(share),
                    })
        else:
            rows = [{
                "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(),
                "principal_due": float(r["principal_due"]), "interest_due": float(r["interest_due"]),
                "savings_due": float(r.get("savings_due", 0)),
                "total_due": float(r["total_due"]),
            } for r in raw_rows]

    show_savings = has_custom_schedule and not for_customer
    fee_to_show = float(loan.processing_fee) if (loan.processing_fee and not for_customer) else None

    if format == "xlsx":
        file_path = generate_loan_installment_sheet_xlsx(loan.loan_number, payer_name, payer_type, branch_name, is_projected, rows, is_group=bool(loan.group_id), show_savings=show_savings, processing_fee=fee_to_show)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        file_path = generate_loan_installment_sheet_pdf(loan.loan_number, payer_name, payer_type, branch_name, is_projected, rows, is_group=bool(loan.group_id), show_savings=show_savings, processing_fee=fee_to_show)
        media_type = "application/pdf"
        ext = "pdf"

    filename = f"{loan.loan_number}_installment_sheet.{ext}"
    return FileResponse(file_path, media_type=media_type, filename=filename)


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
    custom_start_date: date | None = None  # only used when the chosen product has custom_schedule_enabled
    # Per-phase WEEKLY overrides for a custom-schedule loan — any left out falls back
    # to the product's own default for that phase. Each phase's weekly principal times
    # its own week-count, summed across all three phases (overridden or default),
    # must exactly equal principal_amount above.
    custom_phase1_principal: float | None = None
    custom_phase1_interest: float | None = None
    custom_phase1_savings: float | None = None
    custom_phase2_principal: float | None = None
    custom_phase2_interest: float | None = None
    custom_phase2_savings: float | None = None
    custom_phase3_principal: float | None = None
    custom_phase3_interest: float | None = None
    custom_phase3_savings: float | None = None


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


def split_evenly(total, count: int) -> list:
    """
    Splits an amount evenly across `count` shares, with the LAST share
    absorbing whatever rounding remainder is left — so the shares always sum
    back to exactly `total` to the paisa. This is the exact same math used
    when a group loan is actually disbursed (see the GroupContribution
    creation below); factored out so a projected installment sheet for an
    approved-but-not-yet-disbursed group loan can show the same per-member
    amounts a member will actually be asked to pay once it IS disbursed.
    """
    total = Decimal(str(total))
    share = (total / count).quantize(Decimal("0.01"))
    shares = [share] * (count - 1)
    shares.append(total - sum(shares))
    return shares


def calculate_emi_schedule(principal, annual_rate_pct, months: int, product: LoanProduct, first_due_date: date | None = None) -> list[dict]:
    """
    Pure calculation, no database writes — the same flat/reducing-balance math
    used at real disbursal, factored out so it can also power a projected
    installment sheet for a loan product (at a chosen sample amount) before
    any actual loan exists. Returns a list of plain dicts, one per installment.
    """
    principal = Decimal(str(principal))
    annual_rate = Decimal(str(annual_rate_pct)) / Decimal(100)
    freq_days = {"weekly": 7, "biweekly": 14, "monthly": 30}.get(product.repayment_frequency, 30)
    installments = months if product.repayment_frequency == "monthly" else int(months * 30 / freq_days)
    basis = resolve_calculation_basis(product)
    start = (first_due_date - timedelta(days=freq_days)) if first_due_date else ist_today()

    rows = []
    if basis == InterestType.flat:
        total_interest = principal * annual_rate * Decimal(months) / Decimal(12)
        total_payable = principal + total_interest
        per_installment = (total_payable / installments).quantize(Decimal("0.01"))
        principal_per = (principal / installments).quantize(Decimal("0.01"))
        interest_per = (total_interest / installments).quantize(Decimal("0.01"))

        due = start
        for i in range(1, installments + 1):
            due = due + timedelta(days=freq_days)
            rows.append({"installment_no": i, "due_date": due, "principal_due": principal_per, "interest_due": interest_per, "total_due": per_installment})
    else:
        monthly_rate = annual_rate / Decimal(12) if product.repayment_frequency == "monthly" else annual_rate / Decimal(365) * freq_days
        outstanding = principal
        principal_per = (principal / installments).quantize(Decimal("0.01"))
        due = start
        for i in range(1, installments + 1):
            due = due + timedelta(days=freq_days)
            interest_due = (outstanding * monthly_rate).quantize(Decimal("0.01"))
            total_due = principal_per + interest_due
            rows.append({"installment_no": i, "due_date": due, "principal_due": principal_per, "interest_due": interest_due, "total_due": total_due})
            outstanding -= principal_per
    return rows


def build_emi_schedule(loan: Loan, product: LoanProduct, db: Session, first_due_date: date | None = None):
    """
    Generates a flat or reducing-balance EMI schedule. By default the first
    installment falls one repayment cycle after today (unchanged, existing
    behavior). If first_due_date is given, that becomes the first installment's
    due date instead, with every later installment still spaced the product's
    normal cycle length apart — same formula, just anchored to a chosen date.
    """
    rows = calculate_emi_schedule(loan.principal_amount, loan.interest_rate_annual, loan.tenure_months, product, first_due_date)
    total_payable = Decimal("0")
    for row in rows:
        db.add(EMISchedule(
            loan_id=loan.id, installment_no=row["installment_no"], due_date=row["due_date"],
            principal_due=row["principal_due"], interest_due=row["interest_due"], total_due=row["total_due"],
        ))
        total_payable += row["total_due"]
    loan.total_payable = total_payable


def calculate_custom_phased_schedule(phases: list[dict], start_date: date) -> list[dict]:
    """
    A separate, self-contained schedule engine for 'Custom' interest-type
    products with custom_schedule_enabled — three consecutive weekly phases
    (e.g. 10 weeks, then 6, then 4) rather than the standard flat/reducing
    month-based math above, which this function never touches or calls.

    Nothing here is derived or split — no interest-rate math, no dividing a
    total across weeks. Each phase in `phases` is a dict with
    weeks/principal/interest/savings, all entered by hand elsewhere (on the
    product as defaults, snapshotted and possibly overridden per loan at
    application time), and every one of the three money figures is already
    the exact per-week amount:
      - "principal": the fixed weekly principal figure, charged every
        single week of that phase, exactly as entered — never split or
        derived from a phase total.
      - "interest": the fixed weekly interest figure, charged every single
        week of that phase, exactly as entered — never calculated from a
        rate and never derived from any other figure.
      - "savings": the fixed weekly savings figure for that phase, added on
        top of principal+interest every week, never touching the
        principal/interest math.
    The only arithmetic this function does is repeating each phase's fixed
    weekly figures across its weeks and running installment numbers/dates —
    everything else is exactly what was typed in. The weekly total is simply
    Principal + Interest + Savings. Totals (phase totals, the loan's overall
    total) are just sums of these weekly figures, computed where they're
    needed (see product_phase_config/loan_phase_config callers) — never the
    other way around.
    """
    rows = []
    installment_no = 0
    due = start_date - timedelta(days=7)  # so the first installment lands exactly on start_date
    for phase_idx, phase in enumerate(phases, start=1):
        weeks = phase.get("weeks") or 0
        if weeks <= 0:
            continue
        weekly_principal = Decimal(str(phase.get("principal") or 0))
        weekly_interest = Decimal(str(phase.get("interest") or 0))
        savings = Decimal(str(phase.get("savings") or 0))
        for _ in range(weeks):
            installment_no += 1
            due = due + timedelta(days=7)
            rows.append({
                "installment_no": installment_no, "due_date": due, "phase_no": phase_idx,
                "principal_due": weekly_principal, "interest_due": weekly_interest,
                "savings_due": savings, "total_due": weekly_principal + weekly_interest + savings,
            })
    return rows


def product_phase_config(product: LoanProduct) -> list[dict]:
    """Default phase figures as configured on the loan product itself."""
    return [
        {"weeks": product.custom_phase1_weeks, "principal": product.custom_phase1_principal, "interest": product.custom_phase1_interest, "savings": product.custom_phase1_savings},
        {"weeks": product.custom_phase2_weeks, "principal": product.custom_phase2_principal, "interest": product.custom_phase2_interest, "savings": product.custom_phase2_savings},
        {"weeks": product.custom_phase3_weeks, "principal": product.custom_phase3_principal, "interest": product.custom_phase3_interest, "savings": product.custom_phase3_savings},
    ]


def loan_phase_config(loan: Loan, product: LoanProduct) -> list[dict]:
    """
    A specific loan's own snapshotted phase figures (principal/interest/savings,
    set at application time) combined with the product's phase week-counts
    (weeks are never overridden per loan — only the money figures are).
    """
    return [
        {"weeks": product.custom_phase1_weeks, "principal": loan.custom_phase1_principal, "interest": loan.custom_phase1_interest, "savings": loan.custom_phase1_savings},
        {"weeks": product.custom_phase2_weeks, "principal": loan.custom_phase2_principal, "interest": loan.custom_phase2_interest, "savings": loan.custom_phase2_savings},
        {"weeks": product.custom_phase3_weeks, "principal": loan.custom_phase3_principal, "interest": loan.custom_phase3_interest, "savings": loan.custom_phase3_savings},
    ]


def build_custom_phased_schedule(loan: Loan, product: LoanProduct, db: Session, start_date: date):
    """
    DB-writing counterpart to calculate_custom_phased_schedule — mirrors what
    build_emi_schedule does for the standard engine, but for the phased one.
    Uses the loan's own snapshotted phase figures, not the product's current
    defaults, so an edit to the product later never changes an already
    applied-for loan's schedule.
    """
    rows = calculate_custom_phased_schedule(loan_phase_config(loan, product), start_date)
    total_payable = Decimal("0")
    for row in rows:
        db.add(EMISchedule(
            loan_id=loan.id, installment_no=row["installment_no"], due_date=row["due_date"],
            principal_due=row["principal_due"], interest_due=row["interest_due"],
            savings_due=row["savings_due"], phase_no=row["phase_no"], total_due=row["total_due"],
        ))
        total_payable += row["total_due"]
    loan.total_payable = total_payable


@router.get("/loan-products/{product_id}/custom-schedule-preview")
def preview_custom_schedule(
    product_id: str, start_date: date,
    phase1_principal: float | None = None, phase1_interest: float | None = None, phase1_savings: float | None = None,
    phase2_principal: float | None = None, phase2_interest: float | None = None, phase2_savings: float | None = None,
    phase3_principal: float | None = None, phase3_interest: float | None = None, phase3_savings: float | None = None,
    db: Session = Depends(get_db), user: User = Depends(require_any),
):
    """
    Powers the "Generate" button for a Custom-schedule product — both on the
    Loan Products page (SuperAdmin, previewing the product's own defaults)
    and on the loan application form (employee or SuperAdmin, previewing the
    actual figures typed in for a real customer/group loan before
    submitting). No database writes; purely a calculation preview. Any
    phaseN_* value left out falls back to the product's own default for
    that phase.
    """
    product = db.query(LoanProduct).filter(LoanProduct.id == product_id, LoanProduct.tenant_id == user.tenant_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Loan product not found")
    if not product.custom_schedule_enabled:
        raise HTTPException(status_code=400, detail="This product doesn't use the custom phased weekly schedule.")

    def resolve(value, default):
        return value if value is not None else float(default or 0)

    phases = [
        {"weeks": product.custom_phase1_weeks, "principal": resolve(phase1_principal, product.custom_phase1_principal), "interest": resolve(phase1_interest, product.custom_phase1_interest), "savings": resolve(phase1_savings, product.custom_phase1_savings)},
        {"weeks": product.custom_phase2_weeks, "principal": resolve(phase2_principal, product.custom_phase2_principal), "interest": resolve(phase2_interest, product.custom_phase2_interest), "savings": resolve(phase2_savings, product.custom_phase2_savings)},
        {"weeks": product.custom_phase3_weeks, "principal": resolve(phase3_principal, product.custom_phase3_principal), "interest": resolve(phase3_interest, product.custom_phase3_interest), "savings": resolve(phase3_savings, product.custom_phase3_savings)},
    ]
    rows = calculate_custom_phased_schedule(phases, start_date)
    total_weeks = sum(p["weeks"] or 0 for p in phases)
    # Each phase's "principal" is a weekly figure now, not a phase total — the overall
    # loan principal this schedule adds up to is that weekly figure times the phase's
    # own week-count, summed across all three phases.
    total_principal = sum(Decimal(str(p["principal"])) * Decimal(p["weeks"] or 0) for p in phases)
    return {
        "total_weeks": total_weeks,
        "phase_weeks": [p["weeks"] for p in phases],
        "phase_principals": [p["principal"] for p in phases],
        "phase_interests": [p["interest"] for p in phases],
        "phase_savings": [p["savings"] for p in phases],
        "total_principal": float(total_principal),
        "rows": [{
            "installment_no": r["installment_no"], "due_date": r["due_date"].isoformat(), "phase_no": r["phase_no"],
            "principal_due": float(r["principal_due"]), "interest_due": float(r["interest_due"]),
            "savings_due": float(r["savings_due"]), "total_due": float(r["total_due"]),
        } for r in rows],
    }


@router.post("/loans/apply")
def apply_loan(payload: LoanApply, db: Session = Depends(get_db), user: User = Depends(require_any)):
    # An employee can only ever apply against their own branch — without this,
    # any authenticated employee could pass any branch_id/customer_id in the
    # same tenant and create loans against a branch they have no business in.
    if user.role == UserRole.employee and payload.branch_id != user.branch_id:
        raise HTTPException(status_code=403, detail="You can only apply for loans in your own branch.")

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
        if group.branch_id != payload.branch_id:
            raise HTTPException(status_code=400, detail="That group belongs to a different branch than the one selected.")
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
        customer = db.query(Customer).filter(Customer.id == payload.customer_id, Customer.tenant_id == user.tenant_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        if customer.branch_id != payload.branch_id:
            raise HTTPException(status_code=400, detail="That customer belongs to a different branch than the one selected.")
        customer_id = payload.customer_id
        group_id = None

    branch_count = db.query(Loan).filter(Loan.branch_id == payload.branch_id).count()
    loan_number = f"LN-{branch_count + 1:06d}"

    custom_phase_fields = {}
    if product.custom_schedule_enabled:
        if not payload.custom_start_date:
            raise HTTPException(status_code=400, detail="This product uses a custom phased schedule — choose a start date for the loan tenure.")

        def resolve(value, default):
            return value if value is not None else float(default or 0)

        p1 = resolve(payload.custom_phase1_principal, product.custom_phase1_principal)
        p2 = resolve(payload.custom_phase2_principal, product.custom_phase2_principal)
        p3 = resolve(payload.custom_phase3_principal, product.custom_phase3_principal)
        # Each phase's principal here is a WEEKLY figure, charged unchanged every week of
        # that phase — never split from a total. So what this loan actually adds up to
        # (and must equal the loan amount) is each phase's weekly principal times its own
        # week-count, summed across all three phases.
        weeks1, weeks2, weeks3 = product.custom_phase1_weeks or 0, product.custom_phase2_weeks or 0, product.custom_phase3_weeks or 0
        phase_principal_sum = (
            Decimal(str(p1)) * Decimal(weeks1) + Decimal(str(p2)) * Decimal(weeks2) + Decimal(str(p3)) * Decimal(weeks3)
        )
        if abs(phase_principal_sum - Decimal(str(payload.principal_amount))) > Decimal("0.01"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Phase 1 ({p1} × {weeks1} wks) + Phase 2 ({p2} × {weeks2} wks) + Phase 3 ({p3} × {weeks3} wks) "
                    f"= {phase_principal_sum}, which must add up exactly to the loan amount ({payload.principal_amount})."
                )
            )
        custom_phase_fields = {
            "custom_phase1_principal": p1, "custom_phase1_interest": resolve(payload.custom_phase1_interest, product.custom_phase1_interest), "custom_phase1_savings": resolve(payload.custom_phase1_savings, product.custom_phase1_savings),
            "custom_phase2_principal": p2, "custom_phase2_interest": resolve(payload.custom_phase2_interest, product.custom_phase2_interest), "custom_phase2_savings": resolve(payload.custom_phase2_savings, product.custom_phase2_savings),
            "custom_phase3_principal": p3, "custom_phase3_interest": resolve(payload.custom_phase3_interest, product.custom_phase3_interest), "custom_phase3_savings": resolve(payload.custom_phase3_savings, product.custom_phase3_savings),
        }

    loan = Loan(
        tenant_id=user.tenant_id, branch_id=payload.branch_id, customer_id=customer_id, group_id=group_id,
        loan_product_id=product.id, loan_number=loan_number,
        principal_amount=payload.principal_amount, interest_rate_annual=product.interest_rate_annual,
        tenure_months=product.tenure_months, status=LoanStatus.pending_approval,
        applied_by=user.id, custom_start_date=payload.custom_start_date,
        **custom_phase_fields,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return loan


class LoanApproveRequest(BaseModel):
    processing_fee: float = 0  # deducted only from the cash disbursed to the customer at disbursal —
                                # the repayment schedule still totals the full approved principal + interest


@router.patch("/loans/{loan_id}/approve")
def approve_loan(loan_id: str, payload: LoanApproveRequest = LoanApproveRequest(), db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    loan = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == user.tenant_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.status != LoanStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Only a loan pending approval can be approved.")
    if payload.processing_fee and payload.processing_fee < 0:
        raise HTTPException(status_code=400, detail="Processing fee can't be negative.")
    if payload.processing_fee and Decimal(str(payload.processing_fee)) >= loan.principal_amount:
        raise HTTPException(status_code=400, detail="Processing fee can't be equal to or more than the approved principal amount.")
    loan.status = LoanStatus.approved
    loan.approved_by = user.id
    loan.processing_fee = payload.processing_fee or 0
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
    first_due_date: date | None = None  # optional — defaults to one cycle after today if not given


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
    # Processing fee (if any, set at approval) is deducted only from the cash
    # actually handed to the customer here — the EMI schedule below is still
    # built from the full loan.principal_amount, so what the customer owes
    # and repays is completely unaffected by this.
    loan.disbursed_amount = loan.principal_amount - (loan.processing_fee or 0)
    loan.disbursed_by = user.id
    loan.disbursed_at = datetime.utcnow()
    loan.disbursal_method = payload.disbursal_method
    loan.disbursal_reference = payload.disbursal_reference
    if product.interest_type == InterestType.other and product.custom_schedule_enabled:
        start = payload.first_due_date or loan.custom_start_date
        if not start:
            raise HTTPException(status_code=400, detail="This loan uses a custom phased schedule — a start date is required (it should have been set at application, but you can also set one here).")
        build_custom_phased_schedule(loan, product, db, start_date=start)
    else:
        build_emi_schedule(loan, product, db, first_due_date=payload.first_due_date)
    db.flush()  # need EMISchedule.id values before creating GroupContribution rows

    if is_group:
        members = db.query(LoanGroupMember).filter(LoanGroupMember.group_id == loan.group_id).all()
        installments = db.query(EMISchedule).filter(EMISchedule.loan_id == loan.id).all()
        for emi in installments:
            shares = split_evenly(emi.total_due, len(members))
            for member, this_share in zip(members, shares):
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
def list_loans(mine_only: bool = False, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    An employee only ever sees loans THEY personally applied for — this
    applies everywhere the app calls this endpoint (the Loans page AND
    Collections), not just when mine_only is explicitly passed. It used to
    default to sharing the whole branch's loans on Collections specifically,
    reasoning an employee might need to cover for an absent colleague — but
    that meant every employee at a branch could see each other's customers
    and collections, which isn't what this business wants. SuperAdmin is
    unaffected either way. The mine_only parameter is kept but now redundant
    for employees — left in place so no caller breaks.
    """
    loans = scope_branch(db.query(Loan), Loan, user)
    if user.role == UserRole.employee:
        loans = loans.filter(Loan.applied_by == user.id)
    loans = loans.order_by(Loan.applied_at.desc()).all()
    customer_ids = {l.customer_id for l in loans if l.customer_id}
    customers = {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()} if customer_ids else {}
    group_ids = {l.group_id for l in loans if l.group_id}
    groups = {g.id: g for g in db.query(LoanGroup).filter(LoanGroup.id.in_(group_ids)).all()} if group_ids else {}
    branch_ids = {l.branch_id for l in loans if l.branch_id}
    branches = {b.id: b for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}
    employee_ids = {l.applied_by for l in loans if l.applied_by}
    employees = {u.id: u for u in db.query(User).filter(User.id.in_(employee_ids)).all()} if employee_ids else {}

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
            .filter(EMISchedule.loan_id.in_(active_group_loan_ids), EMISchedule.is_paid == False, EMISchedule.due_date < ist_today())  # noqa: E712
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
        branch = branches.get(l.branch_id)
        employee = employees.get(l.applied_by)
        result.append({
            "id": l.id, "loan_number": l.loan_number, "principal_amount": float(l.principal_amount),
            "status": l.status.value, "customer_id": l.customer_id, "group_id": l.group_id,
            "customer_name": display_name, "is_group_loan": l.group_id is not None,
            "has_defaulter": l.id in defaulter_loan_ids,
            "applied_at": l.applied_at.isoformat(),
            "rejection_reason": l.rejection_reason,
            "branch_name": branch.name if branch else "Unknown",
            "employee_name": employee.full_name if employee else "Unknown",
            "disbursed_amount": float(l.disbursed_amount) if l.disbursed_amount else None,
            "disbursal_method": l.disbursal_method,
            "disbursed_at": l.disbursed_at.isoformat() if l.disbursed_at else None,
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
