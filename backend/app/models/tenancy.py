import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class SubscriptionStatus(str, enum.Enum):
    trial = "trial"
    active = "active"
    past_due = "past_due"
    suspended = "suspended"
    cancelled = "cancelled"


class UserRole(str, enum.Enum):
    superemeadmin = "superemeadmin"   # OS2 platform owner
    superadmin = "superadmin"         # tenant owner, creates branches
    employee = "employee"             # branch staff


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)          # e.g. "Starter", "Growth", "Enterprise"
    max_branches = Column(Integer, nullable=False, default=1)
    max_employees = Column(Integer, nullable=False, default=10)
    max_customers = Column(Integer, nullable=True)  # null = unlimited
    monthly_price_inr = Column(Numeric(10, 2), nullable=False)
    razorpay_plan_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)


class Tenant(Base):
    """A microfinance operator org (owned by a SuperAdmin), subscribed to a plan."""
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    plan_id = Column(UUID(as_uuid=False), ForeignKey("subscription_plans.id"), nullable=True)
    subscription_status = Column(Enum(SubscriptionStatus), default=SubscriptionStatus.trial)
    razorpay_subscription_id = Column(String, nullable=True)
    trial_ends_at = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_suspended = Column(Boolean, default=False)  # manual kill-switch by SuperEmeAdmin

    branches = relationship("Branch", back_populates="tenant", cascade="all, delete-orphan")
    plan = relationship("SubscriptionPlan")


class Branch(Base):
    __tablename__ = "branches"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)   # short branch code, used in loan numbering
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="branches")


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=True)  # null for superemeadmin
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=True)  # null for superadmin/superemeadmin
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    employee_code = Column(String, nullable=True)
    designation = Column(String, nullable=True)   # e.g. "Loan Officer", "Cashier", "Field Agent"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
