"""
Wipes all business data (branches, employees, customers, loans, payments,
groups, documents, audit logs) while preserving your SuperAdmin login and
the tenant record itself — for clearing out test data before going live
with real customers, without losing the ability to sign back in afterward.

SAFE BY DEFAULT: running this with no arguments only shows what WOULD be
deleted, and deletes nothing. Nothing is actually removed until you pass
--confirm.

Usage (run from the backend/ directory, with APP_DATABASE_URL set — the
same env var Railway already has configured):

    python -m app.scripts.cleanup_test_data                 # dry run — shows counts only
    python -m app.scripts.cleanup_test_data --confirm        # actually deletes

STRONGLY RECOMMENDED: take a fresh database backup first (see
backup_database.py, or use Railway's own Postgres backup/snapshot feature)
in case you want any of this data back later. This cannot be undone.

What's KEPT:
  - Every user with role=superadmin (your login access)
  - The Tenant record itself (Udhayam Micro Finance Institutions)

What's DELETED (in the order required to satisfy foreign key constraints):
  Payment, Document, Attendance, GroupContribution, MoneyAuditLog,
  EMISchedule, LoanGroupMember, Loan, LoanGroup, Customer, LoanProduct,
  every User with role=employee, every Branch.
"""
import sys

from app.core.database import SessionLocal
from app.models.finance import (
    Payment, Document, Attendance, GroupContribution, EMISchedule,
    LoanGroupMember, Loan, LoanGroup, Customer, LoanProduct,
)
from app.models.audit import MoneyAuditLog
from app.models.tenancy import User, Branch, UserRole


# Deletion order matters — children before parents, or Postgres will reject
# the delete with a foreign key violation. This list is that order.
DELETION_PLAN = [
    ("Payments", Payment, {}),
    ("Documents", Document, {}),
    ("Attendance records", Attendance, {}),
    ("Group contributions", GroupContribution, {}),
    ("Money audit log entries", MoneyAuditLog, {}),
    ("EMI schedule rows", EMISchedule, {}),
    ("Group members", LoanGroupMember, {}),
    ("Loans", Loan, {}),
    ("Groups", LoanGroup, {}),
    ("Customers", Customer, {}),
    ("Loan products", LoanProduct, {}),
    ("Employees (non-SuperAdmin users)", User, {"role": UserRole.employee}),
    ("Branches", Branch, {}),
]


def run(confirm: bool):
    db = SessionLocal()
    try:
        superadmin_count = db.query(User).filter(User.role == UserRole.superadmin).count()
        if superadmin_count == 0:
            print("REFUSING TO RUN: no SuperAdmin account found in this database.")
            print("Running this cleanup would leave you unable to log back in.")
            sys.exit(1)

        print(f"SuperAdmin accounts that will be KEPT: {superadmin_count}")
        print()
        print(f"{'DRY RUN — nothing will be deleted' if not confirm else 'LIVE RUN — deleting for real'}")
        print("-" * 60)

        counts = {}
        for label, model, filters in DELETION_PLAN:
            q = db.query(model).filter_by(**filters)
            count = q.count()
            counts[label] = count
            print(f"{label:40s} {count:6d} row(s)")

        total = sum(counts.values())
        print("-" * 60)
        print(f"{'TOTAL':40s} {total:6d} row(s)")
        print()

        if not confirm:
            print("This was a dry run — nothing was deleted.")
            print("Re-run with --confirm to actually delete this data.")
            return

        if total == 0:
            print("Nothing to delete.")
            return

        answer = input(f"Type DELETE to permanently remove these {total} rows: ")
        if answer.strip() != "DELETE":
            print("Confirmation text didn't match — aborted, nothing deleted.")
            return

        for label, model, filters in DELETION_PLAN:
            db.query(model).filter_by(**filters).delete(synchronize_session=False)
        db.commit()
        print()
        print(f"Done. Deleted {total} rows. Your SuperAdmin login still works.")
    finally:
        db.close()


if __name__ == "__main__":
    run(confirm="--confirm" in sys.argv)
