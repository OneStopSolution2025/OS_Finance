from datetime import date, timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.core.security import require_any, require_superemeadmin, require_superadmin
from app.models.tenancy import User, UserRole, Tenant
from app.models.finance import Loan, Payment, EMISchedule, LoanStatus, Customer, LoanProduct
from app.utils.report_pdf import generate_branch_report_pdf, generate_breakdown_pdf
from app.utils.report_breakdown import build_breakdown
from app.utils.report_xlsx import generate_breakdown_xlsx
from app.utils.leads_export import generate_leads_xlsx, generate_leads_pdf

router = APIRouter(prefix="/reports", tags=["accounts & reports"])


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

    overdue_q = db.query(EMISchedule).join(Loan).filter(
        Loan.tenant_id == user.tenant_id,
        EMISchedule.is_paid == False,
        EMISchedule.due_date < date.today(),
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
        Loan.tenant_id == user.tenant_id, EMISchedule.is_paid == False, EMISchedule.due_date < date.today()
    )
    if user.role == UserRole.employee:
        overdue = overdue.filter(Loan.branch_id == user.branch_id)

    buckets = {"1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    for emi in overdue.all():
        dpd = (date.today() - emi.due_date).days
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
    """Last 7 days of collections, for the dashboard growth chart."""
    q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        q = q.filter(Payment.branch_id == user.branch_id)

    today = date.today()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    trend = []
    for d in days:
        day_total = q.filter(func.date(Payment.paid_at) == d).with_entities(
            func.coalesce(func.sum(Payment.amount), 0)
        ).scalar()
        trend.append({"date": d.isoformat(), "amount": float(day_total)})
    return trend


@router.get("/recent-activity")
def recent_activity(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """Recent loans and recent payments, scoped by role — feeds the dashboard activity list."""
    loan_q = db.query(Loan).filter(Loan.tenant_id == user.tenant_id)
    payment_q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        loan_q = loan_q.filter(Loan.branch_id == user.branch_id)
        payment_q = payment_q.filter(Payment.branch_id == user.branch_id)

    recent_loans = loan_q.order_by(Loan.applied_at.desc()).limit(5).all()
    recent_payments = payment_q.order_by(Payment.paid_at.desc()).limit(5).all()

    def loan_customer_name(loan):
        c = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        return c.full_name if c else "—"

    return {
        "recent_loans": [
            {"loan_number": l.loan_number, "customer": loan_customer_name(l),
             "amount": float(l.principal_amount), "status": l.status.value, "applied_at": l.applied_at.isoformat()}
            for l in recent_loans
        ],
        "recent_payments": [
            {"receipt_number": p.receipt_number, "amount": float(p.amount),
             "method": p.method.value, "paid_at": p.paid_at.isoformat()}
            for p in recent_payments
        ],
    }


@router.get("/platform-overview", dependencies=[Depends(require_superemeadmin)])
def platform_overview(db: Session = Depends(get_db)):
    """SuperEmeAdmin (Supreme Admin) view: platform-wide growth trend across all tenants."""
    today = date.today()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    trend = []
    for d in days:
        new_tenants = db.query(Tenant).filter(func.date(Tenant.created_at) == d).count()
        trend.append({"date": d.isoformat(), "new_tenants": new_tenants})

    total_disbursed = db.query(Loan).with_entities(func.coalesce(func.sum(Loan.disbursed_amount), 0)).scalar()
    total_collected = db.query(Payment).with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar()

    return {
        "tenant_growth_trend": trend,
        "platform_total_disbursed": float(total_disbursed),
        "platform_total_collected": float(total_collected),
    }


@router.get("/download")
def download_report(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    """SuperAdmin-only: generates a printable PDF snapshot of the tenant's performance."""
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant_name = tenant.name if tenant else "OS Finances"

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
    SuperAdmin-only. group_by: day | week | month | employee. format: xlsx | pdf.
    Exports collections grouped by the selected dimension.
    """
    if group_by not in ("day", "week", "month", "employee"):
        raise HTTPException(status_code=400, detail="group_by must be one of: day, week, month, employee")
    if format not in ("xlsx", "pdf"):
        raise HTTPException(status_code=400, detail="format must be xlsx or pdf")

    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    tenant_name = tenant.name if tenant else "OS Finances"

    rows = build_breakdown(db, user, group_by)

    if format == "xlsx":
        file_path = generate_breakdown_xlsx(tenant_name, group_by, rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        file_path = generate_breakdown_pdf(tenant_name, group_by, rows)
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

    my_payments = db.query(Payment).filter(Payment.collected_by == user.id)
    total_collected = my_payments.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar()
    payment_count = my_payments.count()

    return {
        "active_loans": active_loans,
        "pending_loans": pending_loans,
        "closed_loans": closed_loans,
        "rejected_loans": rejected_loans,
        "total_collected": float(total_collected),
        "payment_count": payment_count,
    }


@router.get("/my-collections-trend")
def my_collections_trend(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """Last 7 days of collections BY THIS PERSON specifically — not the whole branch."""
    q = db.query(Payment).filter(Payment.collected_by == user.id)
    today = date.today()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    trend = []
    for d in days:
        day_total = q.filter(func.date(Payment.paid_at) == d).with_entities(
            func.coalesce(func.sum(Payment.amount), 0)
        ).scalar()
        trend.append({"date": d.isoformat(), "amount": float(day_total)})
    return trend


@router.get("/my-recent-activity")
def my_recent_activity(db: Session = Depends(get_db), user: User = Depends(require_any)):
    """This person's own recent loans and payments only — never another staff member's."""
    recent_loans = db.query(Loan).filter(Loan.applied_by == user.id).order_by(Loan.applied_at.desc()).limit(5).all()
    recent_payments = db.query(Payment).filter(Payment.collected_by == user.id).order_by(Payment.paid_at.desc()).limit(5).all()

    def loan_customer_name(loan):
        c = db.query(Customer).filter(Customer.id == loan.customer_id).first()
        return c.full_name if c else "—"

    return {
        "recent_loans": [
            {"loan_number": l.loan_number, "customer": loan_customer_name(l),
             "amount": float(l.principal_amount), "status": l.status.value, "applied_at": l.applied_at.isoformat()}
            for l in recent_loans
        ],
        "recent_payments": [
            {"receipt_number": p.receipt_number, "amount": float(p.amount),
             "method": p.method.value, "paid_at": p.paid_at.isoformat()}
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
    tenant_name = tenant.name if tenant else "OS Finances"

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
