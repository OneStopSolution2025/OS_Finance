import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class InterestType(str, enum.Enum):
    flat = "flat"
    reducing = "reducing"


class LoanStatus(str, enum.Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    disbursed = "disbursed"
    active = "active"
    closed = "closed"
    written_off = "written_off"
    npa = "npa"


class PaymentMethod(str, enum.Enum):
    cash = "cash"
    razorpay = "razorpay"
    bank_transfer = "bank_transfer"
    upi = "upi"


class DocumentType(str, enum.Enum):
    aadhaar = "aadhaar"
    pan = "pan"
    photo = "photo"
    voter_id = "voter_id"
    signed_application = "signed_application"
    address_proof = "address_proof"
    guarantor_id = "guarantor_id"
    income_proof = "income_proof"
    loan_agreement = "loan_agreement"
    other = "other"


class Customer(Base):
    __tablename__ = "customers"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=False)
    customer_code = Column(String, nullable=False)   # auto-generated, e.g. BR01-CUS-0001
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=True)
    dob = Column(Date, nullable=True)
    gender = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    aadhaar_number = Column(String, nullable=True)   # store masked/encrypted in production
    pan_number = Column(String, nullable=True)
    kyc_verified = Column(Boolean, default=False)
    phone_verified = Column(Boolean, default=False)
    guarantor_name = Column(String, nullable=True)
    guarantor_phone = Column(String, nullable=True)
    photo_document_id = Column(UUID(as_uuid=False), nullable=True)
    created_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class LoanProduct(Base):
    __tablename__ = "loan_products"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)             # e.g. "Weekly Group Loan"
    interest_type = Column(Enum(InterestType), default=InterestType.flat)
    interest_rate_annual = Column(Numeric(6, 3), nullable=False)   # e.g. 24.000 (%)
    min_amount = Column(Numeric(12, 2), nullable=False)
    max_amount = Column(Numeric(12, 2), nullable=False)
    tenure_months = Column(Integer, nullable=False)
    repayment_frequency = Column(String, default="monthly")  # weekly | biweekly | monthly
    processing_fee_pct = Column(Numeric(5, 2), default=0)
    is_active = Column(Boolean, default=True)


class Loan(Base):
    __tablename__ = "loans"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=False)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id"), nullable=False)
    loan_product_id = Column(UUID(as_uuid=False), ForeignKey("loan_products.id"), nullable=False)
    loan_number = Column(String, nullable=False)      # e.g. BR01-LN-2026-0001
    principal_amount = Column(Numeric(12, 2), nullable=False)
    interest_rate_annual = Column(Numeric(6, 3), nullable=False)
    tenure_months = Column(Integer, nullable=False)
    disbursed_amount = Column(Numeric(12, 2), nullable=True)
    total_payable = Column(Numeric(12, 2), nullable=True)
    status = Column(Enum(LoanStatus), default=LoanStatus.pending_approval)
    approved_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    disbursed_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    disbursed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)


class EMISchedule(Base):
    __tablename__ = "emi_schedule"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    loan_id = Column(UUID(as_uuid=False), ForeignKey("loans.id"), nullable=False)
    installment_no = Column(Integer, nullable=False)
    due_date = Column(Date, nullable=False)
    principal_due = Column(Numeric(12, 2), nullable=False)
    interest_due = Column(Numeric(12, 2), nullable=False)
    total_due = Column(Numeric(12, 2), nullable=False)
    amount_paid = Column(Numeric(12, 2), default=0)
    is_paid = Column(Boolean, default=False)
    paid_at = Column(DateTime, nullable=True)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=False)
    loan_id = Column(UUID(as_uuid=False), ForeignKey("loans.id"), nullable=False)
    emi_id = Column(UUID(as_uuid=False), ForeignKey("emi_schedule.id"), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(Enum(PaymentMethod), nullable=False)
    razorpay_payment_id = Column(String, nullable=True)
    razorpay_order_id = Column(String, nullable=True)
    collected_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    receipt_number = Column(String, nullable=True)
    receipt_pdf_path = Column(String, nullable=True)
    paid_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(String, nullable=True)


class Document(Base):
    __tablename__ = "documents"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id"), nullable=True)
    loan_id = Column(UUID(as_uuid=False), ForeignKey("loans.id"), nullable=True)
    doc_type = Column(Enum(DocumentType), nullable=False)
    file_name = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)
    uploaded_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(UUID(as_uuid=False), ForeignKey("branches.id"), nullable=False)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    check_in = Column(DateTime, nullable=True)
    check_out = Column(DateTime, nullable=True)
    status = Column(String, default="present")  # present | absent | half_day | leave
