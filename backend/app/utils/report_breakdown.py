"""
Builds grouped payment/loan breakdowns for the SuperAdmin report export —
day-wise, week-wise, month-wise, or employee-wise. Pure Python aggregation
over already-fetched rows rather than DB-specific date-trunc SQL, so it works
identically on SQLite (local/tests) and Postgres (production) without dialect
branching.
"""
from collections import defaultdict
from datetime import date
from sqlalchemy.orm import Session

from app.models.finance import Payment, Loan
from app.models.tenancy import User, UserRole


def _scoped_payments(db: Session, user: User):
    q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        q = q.filter(Payment.branch_id == user.branch_id)
    return q.all()


def _week_label(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def build_breakdown(db: Session, user: User, group_by: str) -> list[dict]:
    """
    group_by: 'day' | 'week' | 'month' | 'employee'
    Returns a list of rows, each with a label, total collected, and payment count,
    sorted chronologically (or alphabetically for employee).
    """
    payments = _scoped_payments(db, user)
    buckets: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})

    for p in payments:
        d = p.paid_at.date()
        if group_by == "day":
            key = d.isoformat()
        elif group_by == "week":
            key = _week_label(d)
        elif group_by == "month":
            key = d.strftime("%Y-%m")
        elif group_by == "employee":
            employee = db.query(User).filter(User.id == p.collected_by).first()
            key = employee.full_name if employee else "Unknown"
        else:
            raise ValueError(f"Unsupported group_by: {group_by}")

        buckets[key]["amount"] += float(p.amount)
        buckets[key]["count"] += 1

    rows = [{"label": k, "amount": round(v["amount"], 2), "payment_count": v["count"]} for k, v in buckets.items()]
    rows.sort(key=lambda r: r["label"])
    return rows
