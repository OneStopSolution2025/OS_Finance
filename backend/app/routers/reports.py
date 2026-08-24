from datetime import date, timedelta, datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.core.security import require_any, require_superemeadmin
from app.models.tenancy import User, UserRole, Tenant
from app.models.finance import Loan, Payment, EMISchedule, LoanStatus, Customer

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
