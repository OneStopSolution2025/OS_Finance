"""
Builds detailed, transaction-level payment reports for the SuperAdmin report
export — sectioned day-wise, week-wise, month-wise, employee-wise, or
branch-wise, but every row is one real payment with full context (who paid,
who collected it, when) — never rolled up into a total. A SuperAdmin reading
this should be able to see exactly which customer or group paid what, on
which date, collected by which employee, without needing to cross-reference
anything else.

Pure Python aggregation over already-fetched rows rather than DB-specific
date-trunc SQL, so it works identically on SQLite (local/tests) and Postgres
(production) without dialect branching.
"""
from datetime import date
from sqlalchemy.orm import Session

from app.models.finance import Payment, Loan, Customer, LoanGroup
from app.models.tenancy import User, UserRole, Branch


def _scoped_payments(db: Session, user: User):
    q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        q = q.filter(Payment.branch_id == user.branch_id)
    return q.order_by(Payment.paid_at).all()


def _week_label(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def build_breakdown(db: Session, user: User, group_by: str) -> list[dict]:
    """
    group_by: 'day' | 'week' | 'month' | 'employee' | 'branch'
    Returns one row per actual payment — never a rolled-up total. The
    'group_label' field is what the report is sectioned/sorted by; every
    other field describes that one specific transaction.
    """
    payments = _scoped_payments(db, user)

    loan_ids = {p.loan_id for p in payments}
    loans = {l.id: l for l in db.query(Loan).filter(Loan.id.in_(loan_ids)).all()} if loan_ids else {}

    customer_ids = {l.customer_id for l in loans.values() if l.customer_id}
    customers = {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()} if customer_ids else {}

    group_ids = {l.group_id for l in loans.values() if l.group_id}
    groups = {g.id: g for g in db.query(LoanGroup).filter(LoanGroup.id.in_(group_ids)).all()} if group_ids else {}

    employee_ids = {p.collected_by for p in payments if p.collected_by}
    employees = {u.id: u for u in db.query(User).filter(User.id.in_(employee_ids)).all()} if employee_ids else {}

    branch_ids = {p.branch_id for p in payments if p.branch_id}
    branches = {b.id: b for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}

    rows = []
    for p in payments:
        d = p.paid_at.date()
        if group_by == "day":
            group_label = d.isoformat()
        elif group_by == "week":
            group_label = _week_label(d)
        elif group_by == "month":
            group_label = d.strftime("%Y-%m")
        elif group_by == "employee":
            employee = employees.get(p.collected_by)
            group_label = employee.full_name if employee else "Unknown"
        elif group_by == "branch":
            branch = branches.get(p.branch_id)
            group_label = branch.name if branch else "Unknown"
        else:
            raise ValueError(f"Unsupported group_by: {group_by}")

        loan = loans.get(p.loan_id)
        if loan and loan.group_id:
            payer_name = groups[loan.group_id].name if loan.group_id in groups else "Unknown group"
            payer_type = "Group"
        elif loan and loan.customer_id:
            payer_name = customers[loan.customer_id].full_name if loan.customer_id in customers else "Unknown customer"
            payer_type = "Individual"
        else:
            payer_name = "Unknown"
            payer_type = "—"

        employee = employees.get(p.collected_by)
        branch = branches.get(p.branch_id)

        rows.append({
            "group_label": group_label,
            "date": d.isoformat(),
            "payer_name": payer_name,
            "payer_type": payer_type,
            "employee_name": employee.full_name if employee else "Unknown",
            "branch_name": branch.name if branch else "Unknown",
            "loan_number": loan.loan_number if loan else "—",
            "receipt_number": p.receipt_number,
            "amount": float(p.amount),
        })

    rows.sort(key=lambda r: (r["group_label"], r["date"]))
    return rows
