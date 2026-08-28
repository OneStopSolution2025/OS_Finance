import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    superadmin = "superadmin"  # business owner — top of the hierarchy, creates branches
    employee = "employee"      # branch staff


class Tenant(Base):
    """
    The single business this deployment belongs to. Kept as its own table
    (rather than folding these fields onto User) because Branch, Customer,
    Loan, etc. all scope through tenant_id — this preserves that without
    forcing a bigger rewrite. There is exactly one row in this table for a
    single-tenant deployment, created once during setup.
    """
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Your own Razorpay/RazorpayX credentials — collections go straight into
    # your own account, never through anyone else's.
    razorpay_key_id = Column(String, nullable=True)
    razorpay_key_secret = Column(String, nullable=True)
    razorpayx_account_number = Column(String, nullable=True)
    razorpayx_key_id = Column(String, nullable=True)
    razorpayx_key_secret = Column(String, nullable=True)

    branches = relationship("Branch", back_populates="tenant", cascade="all, delete-orphan")


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


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=True)  # null for superadmin
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False)
    employee_code = Column(String, nullable=True)
    designation = Column(String, nullable=True)   # e.g. "Loan Officer", "Cashier", "Field Agent"
    address = Column(String, nullable=True)
    bank_account_holder_name = Column(String, nullable=True)
    bank_account_number = Column(String, nullable=True)
    bank_ifsc = Column(String, nullable=True)
    bank_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
