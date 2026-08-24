from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.core.security import require_any
from app.models.tenancy import User, UserRole
from app.models.finance import Loan, Payment, EMISchedule, LoanStatus

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
