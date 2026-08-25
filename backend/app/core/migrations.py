"""
Lightweight schema-drift fixer for cases where a table already exists in the
database but a new column was added to the SQLAlchemy model afterward.
Base.metadata.create_all() only creates missing TABLES, never missing COLUMNS
on tables that already exist — so without this, adding a column to a model
silently does nothing on a database that predates the change.

This is intentionally minimal (not a replacement for Alembic on a larger
project) — it just walks each table's expected columns, checks what actually
exists via information_schema, and ALTERs in whatever's missing with a safe
default so existing rows don't break.

IMPORTANT — Postgres type matching: any column that's a foreign key to
users.id/tenants.id/etc. must be added as UUID here, not VARCHAR. The Python
model declares these as UUID(as_uuid=False), and SQLAlchemy binds query
parameters against UUID columns with an explicit ::UUID cast — Postgres then
refuses to compare that against a VARCHAR column ("operator does not exist:
character varying = uuid"). This bit us once already (loans.applied_by /
loans.rejected_by) — fix_mistyped_columns() below retroactively corrects any
column that was already created with the wrong type by an earlier version of
this file, since simply fixing the DDL text here doesn't touch a column that
already exists on a live database.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def run_safe_migrations(engine: Engine):
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # table -> [(column_name, ddl_type_and_default), ...]
    expected_columns = {
        "tenants": [
            ("application_status", "VARCHAR DEFAULT 'approved' NOT NULL"),
            ("tracking_code", "VARCHAR"),
            ("rejection_reason", "VARCHAR"),
        ],
        "customers": [
            ("phone_verified", "BOOLEAN DEFAULT FALSE"),
            ("bank_account_holder_name", "VARCHAR"),
            ("bank_account_number", "VARCHAR"),
            ("bank_ifsc", "VARCHAR"),
            ("bank_name", "VARCHAR"),
        ],
        "loan_products": [
            ("custom_interest_label", "VARCHAR"),
            ("calculation_basis", "VARCHAR"),
        ],
        "loans": [
            ("rejected_by", "UUID"),
            ("rejected_at", "TIMESTAMP"),
            ("rejection_reason", "VARCHAR"),
            ("applied_by", "UUID"),
            ("disbursal_method", "VARCHAR"),
            ("disbursal_reference", "VARCHAR"),
        ],
        "users": [
            ("address", "VARCHAR"),
            ("bank_account_holder_name", "VARCHAR"),
            ("bank_account_number", "VARCHAR"),
            ("bank_ifsc", "VARCHAR"),
            ("bank_name", "VARCHAR"),
        ],
        "documents": [
            ("employee_id", "UUID"),
        ],
    }

    with engine.begin() as conn:
        for table, columns in expected_columns.items():
            if table not in existing_tables:
                continue  # create_all() will create it fresh with all columns
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            for col_name, ddl in columns:
                if col_name not in existing_columns:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col_name}" {ddl}'))

    fix_mistyped_columns(engine)


def fix_mistyped_columns(engine: Engine):
    """
    Retroactively corrects columns that an earlier version of run_safe_migrations
    created with the wrong type (VARCHAR instead of UUID) before this file's DDL
    was fixed. Only applies to Postgres — SQLite doesn't distinguish these types,
    so there's nothing to fix there, and this must never run against it.
    """
    if engine.dialect.name != "postgresql":
        return

    inspector = inspect(engine)
    # table -> [column names that must be UUID type]
    should_be_uuid = {
        "loans": ["applied_by", "rejected_by"],
        "documents": ["employee_id"],
    }

    with engine.begin() as conn:
        for table, columns in should_be_uuid.items():
            if table not in inspector.get_table_names():
                continue
            current = {c["name"]: str(c["type"]).upper() for c in inspector.get_columns(table)}
            for col in columns:
                if col in current and current[col] != "UUID":
                    conn.execute(text(
                        f'ALTER TABLE "{table}" ALTER COLUMN "{col}" TYPE UUID USING "{col}"::uuid'
                    ))
