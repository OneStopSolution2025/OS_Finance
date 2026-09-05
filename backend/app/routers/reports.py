from datetime import date, timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.core.security import require_any, require_superadmin
from app.models.tenancy import User, UserRole, Tenant, Branch
from app.models.finance import Loan, Payment, EMISchedule, LoanStatus, Customer, LoanProduct, LoanGroupMember, GroupContribution, LoanGroup
from app.utils.report_pdf import generate_branch_report_pdf, generate_breakdown_pdf
from app.utils.report_breakdown import build_breakdown, build_outstanding
from app.utils.report_xlsx import generate_breakdown_xlsx
from app.utils.leads_export import generate_leads_xlsx, generate_leads_pdf
from app.utils.tz import ist_today, ist_day_bounds_utc

router = APIRouter(prefix="/reports", tags=["accounts & reports"])


def _resolve_installment_payer(db: Session, loan: Loan, emi: EMISchedule):
    """
    For an individual loan: the customer's name, and how much of this
    installment is still outstanding. For a group loan: one entry PER
    NON-PAYING MEMBER, not a single lump entry for the group — a reminder
    that just says "group owes ₹2,000" is useless for follow-up; it needs to
    say who specifically hasn't paid yet.
    """
    entries = []
    if loan.group_id:
        group = db.query(LoanGroup).filter(LoanGroup.id == loan.group_id).first()
        unpaid = db.query(GroupContribution).filter(
            GroupContribution.emi_schedule_id == emi.id, GroupContribution.is_paid == False  # noqa: E712
        ).all()
        for c in unpaid:
            member = db.query(LoanGroupMember).filter(LoanGroupMember.id == c.group_member_id).first()
            customer = db.query(Customer).filter(Customer.id == member.customer_id).first() if member else None
            entries.append({
                "payer_name": customer.full_name if customer else "Unknown member",
                "group_name": group.name if group else "Unknown group",
                "payer_type": "Group member",
                "amount_due": float(c.expected_amount) + float(c.penalty_amount or 0),
            })
    elif loan.customer_id and not emi.is_paid:
        customer = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        entries.append({
            "payer_name": customer.full_name if customer else "Unknown customer",
            "group_name": "—", "payer_type": "Individual",
            "amount_due": float(emi.total_due) - float(emi.amount_paid or 0),
        })
    return entries


@router.get("/upcoming-repayments")
def upcoming_repayments(days: int = 7, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    """
    SuperAdmin-only. Every installment due in the next `days` days that isn't
    fully paid yet, across the whole tenant — with the responsible employee
    and branch attached to each one, so a reminder can actually be acted on:
    who to follow up with, and which staff member owns that relationship.
    """
    today = ist_today()
    end = today + timedelta(days=days)
    upcoming_emis = (
        db.query(EMISchedule).join(Loan, EMISchedule.loan_id == Loan.id)
        .filter(Loan.tenant_id == user.tenant_id, EMISchedule.is_paid == False, EMISchedule.due_date >= today, EMISchedule.due_date <= end)  # noqa: E712
        .order_by(EMISchedule.due_date).all()
    )
    rows = []
    for emi in upcoming_emis:
        loan = db.query(Loan).filter(Loan.id == emi.loan_id).first()
        if not loan:
            continue
        branch = db.query(Branch).filter(Branch.id == loan.branch_id).first()
        employee = db.query(User).filter(User.id == loan.applied_by).first()
        for entry in _resolve_installment_payer(db, loan, emi):
            rows.append({
                "due_date": emi.due_date.isoformat(),
                "days_until_due": (emi.due_date - today).days,
                "loan_number": loan.loan_number,
                "branch_name": branch.name if branch else "Unknown",
                "employee_name": employee.full_name if employee else "Unknown",
                **entry,
            })
    return rows


@router.get("/my-upcoming-repayments")
def my_upcoming_repayments(days: int = 7, db: Session = Depends(get_db), user: User = Depends(require_any)):
    """This person's own upcoming repayments only — loans they applied, within their branch."""
    today = ist_today()
    end = today + timedelta(days=days)
    upcoming_emis = (
        db.query(EMISchedule).join(Loan, EMISchedule.loan_id == Loan.id)
        .filter(Loan.applied_by == user.id, EMISchedule.is_paid == False, EMISchedule.due_date >= today, EMISchedule.due_date <= end)  # noqa: E712
        .order_by(EMISchedule.due_date).all()
    )
    rows = []
    for emi in upcoming_emis:
        loan = db.query(Loan).filter(Loan.id == emi.loan_id).first()
        if not loan:
            continue
        for entry in _resolve_installment_payer(db, loan, emi):
            rows.append({
                "due_date": emi.due_date.isoformat(),
                "days_until_due": (emi.due_date - today).days,
                "loan_number": loan.loan_number,
                **entry,
            })
    return rows


@router.get("/branch-summary")
def branch_summary(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """Disbursement vs collection, active loans, overdue count — scoped by role."""
    loan_q = db.query(Loan).filter(Loan.tenant_id == user.tenant_id)
    payment_q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        loan_q = loan_q.filter(Loan.branch_id == user.branch_id)
        payment_q = payment_q.filter(Payment.branch_id == user.branch_id)

    total_disbursed = loan_q.with_entities(func.coalesce(func.sum(Loan.disbursed_amount), 0)).scalar()
    total_collected = payment_q.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar()
    active_loans = loan_q.filter(Loan.status == LoanStatus.active).count()
    closed_loans = loan_q.filter(Loan.status == LoanStatus.closed).count()
    individual_loans = loan_q.filter(Loan.customer_id.isnot(None)).count()
    group_loans = loan_q.filter(Loan.group_id.isnot(None)).count()

    overdue_q = db.query(EMISchedule).join(Loan).filter(
        Loan.tenant_id == user.tenant_id,
        EMISchedule.is_paid == False,
        EMISchedule.due_date < ist_today(),
    )
    if user.role == UserRole.employee:
        overdue_q = overdue_q.filter(Loan.branch_id == user.branch_id)
    overdue_count = overdue_q.count()
    overdue_amount = overdue_q.with_entities(func.coalesce(func.sum(EMISchedule.total_due - EMISchedule.amount_paid), 0)).scalar()

    return {
        "total_disbursed": float(total_disbursed),
        "total_collected": float(total_collected),
        "active_loans": active_loans,
        "closed_loans": closed_loans,
        "individual_loans": individual_loans,
        "group_loans": group_loans,
        "overdue_installments": overdue_count,
        "overdue_amount": float(overdue_amount),
        "collection_efficiency_pct": round(
            (float(total_collected) / float(total_disbursed) * 100) if total_disbursed else 0, 2
        ),
    }


@router.get("/portfolio-at-risk")
def portfolio_at_risk(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """PAR buckets: overdue installments grouped by days-past-due."""
    overdue = db.query(EMISchedule).join(Loan).filter(
        Loan.tenant_id == user.tenant_id, EMISchedule.is_paid == False, EMISchedule.due_date < ist_today()
    )
    if user.role == UserRole.employee:
        overdue = overdue.filter(Loan.branch_id == user.branch_id)

    buckets = {"1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for emi in overdue.all():
        dpd = (ist_today() - emi.due_date).days
        amt = float(emi.total_due - (emi.amount_paid or 0))
        if dpd <= 30:
            buckets["1-30"] += amt
        elif dpd <= 60:
            buckets["31-60"] += amt
        elif dpd <= 90:
            buckets["61-90"] += amt
        else:
            buckets["90+"] += amt
    return buckets


@router.get("/collections-trend")
def collections_trend(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    Last 7 days of collections — both what was actually received AND what was
    due that day (from the EMI schedule), so a day showing ₹1,000 required vs
    ₹1,000 collected reads as fully on-target, not just "some money came in."
    """
    payment_q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    due_q = db.query(EMISchedule).join(Loan, EMISchedule.loan_id == Loan.id).filter(Loan.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        payment_q = payment_q.filter(Payment.branch_id == user.branch_id)
        due_q = due_q.filter(Loan.branch_id == user.branch_id)

    today = ist_today()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    trend = []
    for d in days:
        day_start_utc, day_end_utc = ist_day_bounds_utc(d)
        received = payment_q.filter(Payment.paid_at >= day_start_utc, Payment.paid_at < day_end_utc).with_entities(
            func.coalesce(func.sum(Payment.amount), 0)
        ).scalar()
        required = due_q.filter(EMISchedule.due_date == d).with_entities(
            func.coalesce(func.sum(EMISchedule.total_due), 0)
        ).scalar()
        trend.append({"date": d.isoformat(), "amount": float(received), "required": float(required)})
    return trend


@router.get("/recent-activity")
def recent_activity(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """Recent loans and recent payments, scoped by role — feeds the dashboard activity list."""
    from app.models.finance import LoanGroup

    loan_q = db.query(Loan).filter(Loan.tenant_id == user.tenant_id)
    payment_q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        loan_q = loan_q.filter(Loan.branch_id == user.branch_id)
        payment_q = payment_q.filter(Payment.branch_id == user.branch_id)

    recent_loans = loan_q.order_by(Loan.applied_at.desc()).limit(5).all()
    recent_payments = payment_q.order_by(Payment.paid_at.desc()).limit(5).all()

    def loan_display_name(loan):
        """Individual loans show the customer's name; group loans show the group's name — a
        group loan has no customer_id at all, so this has to branch on which is set."""
        if loan.group_id:
            g = db.query(LoanGroup).filter(LoanGroup.id == loan.group_id).first()
            return f"{g.name} (Group)" if g else "Unknown group"
        c = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        return c.full_name if c else "—"

    def payment_context(payment):
        """Resolves everything the dashboard needs to show about one payment: which
        employee collected it, which loan it's against, and who actually paid —
        the specific group member if it's a group contribution, not just the group."""
        employee = db.query(User).filter(User.id == payment.collected_by).first()
        loan = db.query(Loan).filter(Loan.id == payment.loan_id).first()
        payer_name = "—"
        if loan:
            if payment.group_contribution_id:
                contribution = db.query(GroupContribution).filter(GroupContribution.id == payment.group_contribution_id).first()
                if contribution:
                    member = db.query(LoanGroupMember).filter(LoanGroupMember.id == contribution.group_member_id).first()
                    if member:
                        c = db.query(Customer).filter(Customer.id == member.customer_id).first()
                        payer_name = c.full_name if c else "—"
            elif loan.customer_id:
                c = db.query(Customer).filter(Customer.id == loan.customer_id).first()
                payer_name = c.full_name if c else "—"
        return {
            "employee_name": employee.full_name if employee else "Unknown",
            "loan_number": loan.loan_number if loan else "—",
            "payer_name": payer_name,
        }

    return {
        "recent_loans": [
            {"loan_number": l.loan_number, "customer": loan_display_name(l),
             "amount": float(l.principal_amount), "status": l.status.value, "applied_at": l.applied_at.isoformat()}
            for l in recent_loans
        ],
        "recent_payments": [
            {"receipt_number": p.receipt_number, "amount": float(p.amount),
             "method": p.method.value, "paid_at": p.paid_at.isoformat(), **payment_context(p)}
            for p in recent_payments
        ],
    }


@router.get("/download")
def download_report(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    """SuperAdmin-only: generates a printable PDF snapshot of the tenant's performance."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant_name = tenant.name if tenant else "Udhayam MFI"

    summary = branch_summary(db=db, user=user)
    par = portfolio_at_risk(db=db, user=user)
    activity = recent_activity(db=db, user=user)

    file_path = generate_branch_report_pdf(
        tenant_name=tenant_name,
        summary=summary,
        par=par,
        recent_loans=activity["recent_loans"],
        recent_payments=activity["recent_payments"],
    )
    return FileResponse(file_path, media_type="application/pdf", filename=f"{tenant_name.replace(' ', '_')}_report.pdf")


@router.get("/export")
def export_breakdown(
    group_by: str,
    format: str = "xlsx",
    db: Session = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    """
    SuperAdmin-only. group_by: day | week | month | employee | branch. format: xlsx | pdf.
    Exports collections grouped by the selected dimension.
    """
    if group_by not in ("day", "week", "month", "employee", "branch"):
        raise HTTPException(status_code=400, detail="group_by must be one of: day, week, month, employee, branch")
    if format not in ("xlsx", "pdf"):
        raise HTTPException(status_code=400, detail="format must be xlsx or pdf")

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant_name = tenant.name if tenant else "Udhayam MFI"

    rows = build_breakdown(db, user, group_by)
    outstanding_rows = build_outstanding(db, user, group_by)

    if format == "xlsx":
        file_path = generate_breakdown_xlsx(tenant_name, group_by, rows, outstanding_rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        file_path = generate_breakdown_pdf(tenant_name, group_by, rows, outstanding_rows)
        media_type = "application/pdf"
        ext = "pdf"

    filename = f"{tenant_name.replace(' ', '_')}_{group_by}_breakdown.{ext}"
    return FileResponse(file_path, media_type=media_type, filename=filename)


@router.get("/my-activity")
def my_activity(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """
    An individual staff member's own numbers — loans they personally applied for,
    and payments they personally collected. Distinct from branch-summary, which
    shows the whole branch/tenant regardless of who did the work.
    """
    my_loans = db.query(Loan).filter(Loan.applied_by == user.id)
    active_loans = my_loans.filter(Loan.status == LoanStatus.active).count()
    pending_loans = my_loans.filter(Loan.status == LoanStatus.pending_approval).count()
    closed_loans = my_loans.filter(Loan.status == LoanStatus.closed).count()
    rejected_loans = my_loans.filter(Loan.status == LoanStatus.rejected).count()
    individual_loans = my_loans.filter(Loan.customer_id.isnot(None)).count()
    group_loans = my_loans.filter(Loan.group_id.isnot(None)).count()

    my_payments = db.query(Payment).filter(Payment.collected_by == user.id)
    total_collected = my_payments.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar()
    payment_count = my_payments.count()

    return {
        "active_loans": active_loans,
        "pending_loans": pending_loans,
        "closed_loans": closed_loans,
        "rejected_loans": rejected_loans,
        "individual_loans": individual_loans,
        "group_loans": group_loans,
        "total_collected": float(total_collected),
        "payment_count": payment_count,
    }


@router.get("/my-collections-trend")
def my_collections_trend(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """Last 7 days of collections BY THIS PERSON specifically — not the whole branch.
    'Required' uses the same loan.applied_by attribution as the rest of the app's
    per-employee reporting, since an installment itself has no direct collector field
    until it's actually paid."""
    payment_q = db.query(Payment).filter(Payment.collected_by == user.id)
    due_q = db.query(EMISchedule).join(Loan, EMISchedule.loan_id == Loan.id).filter(Loan.applied_by == user.id)
    today = ist_today()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    trend = []
    for d in days:
        day_start_utc, day_end_utc = ist_day_bounds_utc(d)
        received = payment_q.filter(Payment.paid_at >= day_start_utc, Payment.paid_at < day_end_utc).with_entities(
            func.coalesce(func.sum(Payment.amount), 0)
        ).scalar()
        required = due_q.filter(EMISchedule.due_date == d).with_entities(
            func.coalesce(func.sum(EMISchedule.total_due), 0)
        ).scalar()
        trend.append({"date": d.isoformat(), "amount": float(received), "required": float(required)})
    return trend


@router.get("/my-recent-activity")
def my_recent_activity(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """This person's own recent loans and payments only — never another staff member's."""
    from app.models.finance import LoanGroup

    recent_loans = db.query(Loan).filter(Loan.applied_by == user.id).order_by(Loan.applied_at.desc()).limit(5).all()
    recent_payments = db.query(Payment).filter(Payment.collected_by == user.id).order_by(Payment.paid_at.desc()).limit(5).all()

    def loan_display_name(loan):
        if loan.group_id:
            g = db.query(LoanGroup).filter(LoanGroup.id == loan.group_id).first()
            return f"{g.name} (Group)" if g else "Unknown group"
        c = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        return c.full_name if c else "—"

    def payment_context(payment):
        loan = db.query(Loan).filter(Loan.id == payment.loan_id).first()
        payer_name = "—"
        if loan:
            if payment.group_contribution_id:
                contribution = db.query(GroupContribution).filter(GroupContribution.id == payment.group_contribution_id).first()
                if contribution:
                    member = db.query(LoanGroupMember).filter(LoanGroupMember.id == contribution.group_member_id).first()
                    if member:
                        c = db.query(Customer).filter(Customer.id == member.customer_id).first()
                        payer_name = c.full_name if c else "—"
            elif loan.customer_id:
                c = db.query(Customer).filter(Customer.id == loan.customer_id).first()
                payer_name = c.full_name if c else "—"
        return {"loan_number": loan.loan_number if loan else "—", "payer_name": payer_name}

    return {
        "recent_loans": [
            {"loan_number": l.loan_number, "customer": loan_display_name(l),
             "amount": float(l.principal_amount), "status": l.status.value, "applied_at": l.applied_at.isoformat()}
            for l in recent_loans
        ],
        "recent_payments": [
            {"receipt_number": p.receipt_number, "amount": float(p.amount),
             "method": p.method.value, "paid_at": p.paid_at.isoformat(), **payment_context(p)}
            for p in recent_payments
        ],
    }


@router.get("/customer-leads/export")
def export_customer_leads(
    format: str = "xlsx",
    month: str | None = None,          # "YYYY-MM" — filters by customer signup month
    loan_status: str | None = None,    # a LoanStatus value, or "none" for customers with no loan yet
    db: Session = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    """
    SuperAdmin-only. Exports the full customer list as sales/follow-up leads,
    with each customer's latest loan status and amount for context. Filterable
    by signup month and by loan status (or 'none' for customers with no loan yet).
    """
    if format not in ("xlsx", "pdf"):
        raise HTTPException(status_code=400, detail="format must be xlsx or pdf")
    if loan_status and loan_status != "none" and loan_status not in [s.value for s in LoanStatus]:
        raise HTTPException(status_code=400, detail="Invalid loan_status value")

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant_name = tenant.name if tenant else "Udhayam MFI"

    from app.models.tenancy import Branch
    customers_q = db.query(Customer).filter(Customer.tenant_id == user.tenant_id)
    if month:
        try:
            year, mon = (int(x) for x in month.split("-"))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
        customers_q = customers_q.filter(
            func.extract("year", Customer.created_at) == year,
            func.extract("month", Customer.created_at) == mon,
        )
    customers = customers_q.order_by(Customer.created_at.desc()).all()

    branches = {b.id: b.name for b in db.query(Branch).filter(Branch.tenant_id == user.tenant_id).all()}

    leads = []
    for cust in customers:
        latest_loan = (
            db.query(Loan).filter(Loan.customer_id == cust.id)
            .order_by(Loan.applied_at.desc()).first()
        )
        this_loan_status = latest_loan.status.value if latest_loan else None

        if loan_status == "none" and latest_loan is not None:
            continue
        if loan_status and loan_status != "none" and this_loan_status != loan_status:
            continue

        leads.append({
            "customer_code": cust.customer_code,
            "full_name": cust.full_name,
            "phone": cust.phone,
            "branch_name": branches.get(cust.branch_id, "—"),
            "kyc_verified": cust.kyc_verified,
            "phone_verified": cust.phone_verified,
            "loan_status": this_loan_status,
            "loan_amount": float(latest_loan.principal_amount) if latest_loan else None,
            "applied_date": latest_loan.applied_at.strftime("%d %b %Y") if latest_loan else None,
        })

    if format == "xlsx":
        file_path = generate_leads_xlsx(tenant_name, leads)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        file_path = generate_leads_pdf(tenant_name, leads)
        media_type = "application/pdf"
        ext = "pdf"

    filename = f"{tenant_name.replace(' ', '_')}_customer_leads.{ext}"
    return FileResponse(file_path, media_type=media_type, filename=filename)


@router.get("/money-audit-log")
def get_money_audit_log(
    limit: int = 200, db: Session = Depends(get_db), user: User = Depends(require_superadmin)
):
    """
    SuperAdmin-only. The immutable ledger of every money movement — loan
    disbursals, repayments collected, salaries paid. This is the source of
    truth for "what actually happened," independent of the mutable Loan/
    Payment/Payslip rows those events came from.
    """
    from app.models.audit import MoneyAuditLog
    entries = (
        db.query(MoneyAuditLog)
        .filter(MoneyAuditLog.tenant_id == user.tenant_id)
        .order_by(MoneyAuditLog.created_at.desc())
        .limit(min(limit, 1000))
        .all()
    )
    result = []
    for e in entries:
        actor = db.query(User).filter(User.id == e.actor_id).first() if e.actor_id else None
        branch = db.query(Branch).filter(Branch.id == e.branch_id).first() if e.branch_id else None
        result.append({
            "id": e.id, "event_type": e.event_type.value, "amount": float(e.amount), "direction": e.direction,
            "actor_name": actor.full_name if actor else "—", "branch_name": branch.name if branch else "—",
            "method": e.method, "reference": e.reference,
            "notes": e.notes, "created_at": e.created_at.isoformat(),
        })
    return result
