import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class PayslipStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"


class SalaryStructure(Base):
    """One active salary structure per employee — used to auto-populate each payroll run."""
    __tablename__ = "salary_structures"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    employee_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    basic_salary = Column(Numeric(12, 2), nullable=False)
    allowances = Column(Numeric(12, 2), default=0)      # HRA, conveyance, etc. — combined figure
    deductions = Column(Numeric(12, 2), default=0)       # standard recurring deductions, e.g. PF
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PayrollRun(Base):
    """One payroll cycle for a given month — generates a Payslip per active employee at creation time."""
    __tablename__ = "payroll_runs"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    month = Column(String, nullable=False)  # "YYYY-MM"
    created_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Payslip(Base):
    __tablename__ = "payslips"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    payroll_run_id = Column(UUID(as_uuid=False), ForeignKey("payroll_runs.id"), nullable=False)
    employee_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    basic_salary = Column(Numeric(12, 2), nullable=False)
    allowances = Column(Numeric(12, 2), default=0)
    deductions = Column(Numeric(12, 2), default=0)
    net_pay = Column(Numeric(12, 2), nullable=False)
    status = Column(Enum(PayslipStatus), default=PayslipStatus.pending)
    payment_method = Column(String, nullable=True)      # 'bank_transfer' | 'cash'
    payment_reference = Column(String, nullable=True)   # bank UTR / transaction ref
    paid_at = Column(DateTime, nullable=True)
    paid_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
