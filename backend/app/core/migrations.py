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
        ],
        "loan_products": [
            ("custom_interest_label", "VARCHAR"),
            ("calculation_basis", "VARCHAR"),
        ],
        "loans": [
            ("rejected_by", "VARCHAR"),
            ("rejected_at", "TIMESTAMP"),
            ("rejection_reason", "VARCHAR"),
            ("applied_by", "VARCHAR"),
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
