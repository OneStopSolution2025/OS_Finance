import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum, Numeric
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class MoneyEventType(str, enum.Enum):
    loan_disbursed = "loan_disbursed"
    payment_collected = "payment_collected"
    salary_paid = "salary_paid"


class MoneyAuditLog(Base):
    """
    Append-only ledger of every money movement — loan disbursals, repayments
    collected, and salaries paid. Nothing in this table is ever updated or
    deleted by application code, even by a SuperAdmin — it exists specifically
    to answer "what actually happened" independent of whatever the mutable
    Loan/Payment/Payslip rows say later. Treat any UPDATE or DELETE against
    this table outside of a documented, authorized process as a red flag.
    """
    __tablename__ = "money_audit_log"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=True)
    event_type = Column(Enum(MoneyEventType), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    direction = Column(String, nullable=False)  # 'out' (disbursal, salary) | 'in' (repayment)
    actor_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)  # who performed the action
    counterparty_type = Column(String, nullable=True)  # 'customer' | 'employee'
    counterparty_id = Column(UUID(as_uuid=False), nullable=True)
    method = Column(String, nullable=True)  # 'cash' | 'bank_transfer' | 'razorpay'
    reference = Column(String, nullable=True)  # receipt number / UTR / payout ID
    related_record_id = Column(UUID(as_uuid=False), nullable=True)  # loan_id / payment_id / payslip_id
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
