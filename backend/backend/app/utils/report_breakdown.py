"""
Builds detailed, transaction-level payment reports for the SuperAdmin report
export — sectioned day-wise, week-wise, month-wise, employee-wise, or
branch-wise, but every row is one real payment with full context (who
actually paid — the individual, or the specific member within a group — who
collected it, when, on-time or late) — never rolled up into a total.

Also builds the outstanding/unpaid side of the picture: installments that are
overdue and still not paid, including — for group loans — exactly which
member within the group hasn't paid their share, so a defaulting member is
never hidden behind a "group paid" summary.

Pure Python aggregation over already-fetched rows rather than DB-specific
date-trunc SQL, so it works identically on SQLite (local/tests) and Postgres
(production) without dialect branching.
"""
from datetime import date
from sqlalchemy.orm import Session

from app.models.finance import Payment, Loan, Customer, LoanGroup, LoanGroupMember, EMISchedule, GroupContribution
from app.models.tenancy import User, UserRole, Branch
from app.utils.tz import ist_today


def _scoped_payments(db: Session, user: User):
    q = db.query(Payment).filter(Payment.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        q = q.filter(Payment.branch_id == user.branch_id)
    return q.order_by(Payment.paid_at).all()


def _scoped_loans(db: Session, user: User):
    q = db.query(Loan).filter(Loan.tenant_id == user.tenant_id)
    if user.role == UserRole.employee:
        q = q.filter(Loan.branch_id == user.branch_id)
    return q.all()


def _week_label(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _group_label_for(group_by: str, d: date, employee_name: str, branch_name: str) -> str:
    if group_by == "day":
        return d.isoformat()
    if group_by == "week":
        return _week_label(d)
    if group_by == "month":
        return d.strftime("%Y-%m")
    if group_by == "employee":
        return employee_name
    if group_by == "branch":
        return branch_name
    raise ValueError(f"Unsupported group_by: {group_by}")


def _customers_for_loans(db: Session, loans: list[Loan]) -> dict:
    """
    Fetches every customer reachable from a set of loans — both the direct
    individual-loan customer AND every member of any group loan. A group
    loan's own customer_id is always null, so member lookups have to go
    through LoanGroupMember, not through the loan row itself.
    """
    customer_ids = {l.customer_id for l in loans if l.customer_id}
    group_ids = {l.group_id for l in loans if l.group_id}
    if group_ids:
        member_customer_ids = {
            m.customer_id for m in db.query(LoanGroupMember).filter(LoanGroupMember.group_id.in_(group_ids)).all()
        }
        customer_ids |= member_customer_ids
    return {c.id: c for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()} if customer_ids else {}


def build_breakdown(db: Session, user: User, group_by: str) -> list[dict]:
    """
    Returns one row per actual payment collected — never a rolled-up total.
    For a group loan, 'payer_name' is the specific member who paid (not just
    the group), with 'group_name' showing which group they belong to.
    Includes a 'status' field: 'On-time' or 'Late (Nd)'.
    """
    payments = _scoped_payments(db, user)

    loan_ids = {p.loan_id for p in payments}
    loans = {l.id: l for l in db.query(Loan).filter(Loan.id.in_(loan_ids)).all()} if loan_ids else {}
    customers = _customers_for_loans(db, list(loans.values()))

    group_ids = {l.group_id for l in loans.values() if l.group_id}
    groups = {g.id: g for g in db.query(LoanGroup).filter(LoanGroup.id.in_(group_ids)).all()} if group_ids else {}

    employee_ids = {p.collected_by for p in payments if p.collected_by}
    employees = {u.id: u for u in db.query(User).filter(User.id.in_(employee_ids)).all()} if employee_ids else {}

    branch_ids = {p.branch_id for p in payments if p.branch_id}
    branches = {b.id: b for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}

    emi_ids = {p.emi_id for p in payments if p.emi_id}
    emis = {e.id: e for e in db.query(EMISchedule).filter(EMISchedule.id.in_(emi_ids)).all()} if emi_ids else {}

    contribution_ids = {p.group_contribution_id for p in payments if p.group_contribution_id}
    contributions = (
        {c.id: c for c in db.query(GroupContribution).filter(GroupContribution.id.in_(contribution_ids)).all()}
        if contribution_ids else {}
    )
    member_ids = {c.group_member_id for c in contributions.values()}
    members = {m.id: m for m in db.query(LoanGroupMember).filter(LoanGroupMember.id.in_(member_ids)).all()} if member_ids else {}

    rows = []
    for p in payments:
        d = p.paid_at.date()
        employee = employees.get(p.collected_by)
        branch = branches.get(p.branch_id)
        employee_name = employee.full_name if employee else "Unknown"
        branch_name = branch.name if branch else "Unknown"
        group_label = _group_label_for(group_by, d, employee_name, branch_name)

        loan = loans.get(p.loan_id)
        group_name = "—"
        if loan and loan.group_id:
            group_name = groups[loan.group_id].name if loan.group_id in groups else "Unknown group"
            payer_type = "Group member"
            # Resolve the SPECIFIC member who made this payment via its GroupContribution,
            # rather than just naming the group — that's the whole point of the highlight.
            contribution = contributions.get(p.group_contribution_id)
            member = members.get(contribution.group_member_id) if contribution else None
            payer_name = customers[member.customer_id].full_name if member and member.customer_id in customers else "Unknown member"
        elif loan and loan.customer_id:
            payer_name = customers[loan.customer_id].full_name if loan.customer_id in customers else "Unknown customer"
            payer_type = "Individual"
        else:
            payer_name = "Unknown"
            payer_type = "—"

        emi = emis.get(p.emi_id)
        if emi:
            days_late = (d - emi.due_date).days
            status = f"Late ({days_late}d)" if days_late > 0 else "On-time"
        else:
            status = "—"

        rows.append({
            "group_label": group_label,
            "date": d.isoformat(),
            "payer_name": payer_name,
            "payer_type": payer_type,
            "group_name": group_name,
            "employee_name": employee_name,
            "branch_name": branch_name,
            "loan_number": loan.loan_number if loan else "—",
            "receipt_number": p.receipt_number,
            "amount": float(p.amount),
            "status": status,
        })

    rows.sort(key=lambda r: (r["group_label"], r["date"]))
    return rows


def build_outstanding(db: Session, user: User, group_by: str) -> list[dict]:
    """
    Every overdue, still-unpaid installment — the flip side of build_breakdown.
    For a group loan, this drills into GroupContribution and returns one row
    PER NON-PAYING MEMBER, not one row for the group as a whole, so a
    defaulting member inside an otherwise-current group is never hidden.
    """
    today = ist_today()
    loans = _scoped_loans(db, user)
    loan_ids = [l.id for l in loans]
    if not loan_ids:
        return []

    overdue_emis = (
        db.query(EMISchedule)
        .filter(EMISchedule.loan_id.in_(loan_ids), EMISchedule.is_paid == False, EMISchedule.due_date < today)  # noqa: E712
        .all()
    )
    if not overdue_emis:
        return []

    loans_by_id = {l.id: l for l in loans}
    customers = _customers_for_loans(db, loans)
    group_ids = {l.group_id for l in loans if l.group_id}
    groups = {g.id: g for g in db.query(LoanGroup).filter(LoanGroup.id.in_(group_ids)).all()} if group_ids else {}
    branch_ids = {l.branch_id for l in loans if l.branch_id}
    branches = {b.id: b for b in db.query(Branch).filter(Branch.id.in_(branch_ids)).all()} if branch_ids else {}
    employee_ids = {l.applied_by for l in loans if l.applied_by}
    employees = {u.id: u for u in db.query(User).filter(User.id.in_(employee_ids)).all()} if employee_ids else {}

    # Bulk-fetch every unpaid contribution for every overdue group installment in one query.
    overdue_group_emi_ids = [e.id for e in overdue_emis if loans_by_id.get(e.loan_id) and loans_by_id[e.loan_id].group_id]
    unpaid_contributions_by_emi: dict[str, list] = {}
    if overdue_group_emi_ids:
        all_unpaid = (
            db.query(GroupContribution)
            .filter(GroupContribution.emi_schedule_id.in_(overdue_group_emi_ids), GroupContribution.is_paid == False)  # noqa: E712
            .all()
        )
        for c in all_unpaid:
            unpaid_contributions_by_emi.setdefault(c.emi_schedule_id, []).append(c)
        member_ids = {c.group_member_id for c in all_unpaid}
        members_by_id = {m.id: m for m in db.query(LoanGroupMember).filter(LoanGroupMember.id.in_(member_ids)).all()} if member_ids else {}
    else:
        members_by_id = {}

    rows = []
    for emi in overdue_emis:
        loan = loans_by_id.get(emi.loan_id)
        if not loan:
            continue
        branch = branches.get(loan.branch_id)
        employee = employees.get(loan.applied_by)
        branch_name = branch.name if branch else "Unknown"
        employee_name = employee.full_name if employee else "Unknown"
        days_overdue = (today - emi.due_date).days
        group_label = _group_label_for(group_by, emi.due_date, employee_name, branch_name)

        if loan.group_id:
            group = groups.get(loan.group_id)
            for contribution in unpaid_contributions_by_emi.get(emi.id, []):
                member = members_by_id.get(contribution.group_member_id)
                member_customer = customers.get(member.customer_id) if member else None
                rows.append({
                    "group_label": group_label,
                    "due_date": emi.due_date.isoformat(),
                    "days_overdue": days_overdue,
                    "payer_name": member_customer.full_name if member_customer else "Unknown member",
                    "payer_type": "Group member",
                    "group_name": group.name if group else "Unknown group",
                    "employee_name": employee_name,
                    "branch_name": branch_name,
                    "loan_number": loan.loan_number,
                    "amount_due": float(contribution.expected_amount) + float(contribution.penalty_amount or 0),
                })
        elif loan.customer_id:
            customer = customers.get(loan.customer_id)
            rows.append({
                "group_label": group_label,
                "due_date": emi.due_date.isoformat(),
                "days_overdue": days_overdue,
                "payer_name": customer.full_name if customer else "Unknown customer",
                "payer_type": "Individual",
                "group_name": "—",
                "employee_name": employee_name,
                "branch_name": branch_name,
                "loan_number": loan.loan_number,
                "amount_due": float(emi.total_due) - float(emi.amount_paid or 0),
            })

    rows.sort(key=lambda r: (r["group_label"], -r["days_overdue"]))
    return rows
