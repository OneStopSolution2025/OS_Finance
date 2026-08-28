from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_superadmin
from app.models.tenancy import User, UserRole, Tenant
from app.models.payroll import SalaryStructure, PayrollRun, Payslip, PayslipStatus
from app.utils.payouts import send_payout, is_configured as payout_configured
from app.utils.payroll_pdf import generate_payslip_pdf
from app.utils.audit_log import log_money_event
from app.models.audit import MoneyEventType

router = APIRouter(prefix="/payroll", tags=["payroll"])


# ---------- Salary structures ----------

class SalaryStructureCreate(BaseModel):
    employee_id: str
    basic_salary: float
    allowances: float = 0
    deductions: float = 0


@router.post("/salary-structure")
def set_salary_structure(payload: SalaryStructureCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    employee = db.query(User).filter(User.id == payload.employee_id, User.tenant_id == user.tenant_id, User.role == UserRole.employee).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Only one active structure per employee — deactivate any existing one first.
    existing = db.query(SalaryStructure).filter(SalaryStructure.employee_id == payload.employee_id, SalaryStructure.is_active == True).first()
    if existing:
        existing.is_active = False

    structure = SalaryStructure(
        tenant_id=user.tenant_id, employee_id=payload.employee_id,
        basic_salary=payload.basic_salary, allowances=payload.allowances, deductions=payload.deductions,
    )
    db.add(structure)
    db.commit()
    db.refresh(structure)
    return structure


@router.get("/salary-structures")
def list_salary_structures(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    structures = db.query(SalaryStructure).filter(SalaryStructure.tenant_id == user.tenant_id, SalaryStructure.is_active == True).all()
    result = []
    for s in structures:
        emp = db.query(User).filter(User.id == s.employee_id).first()
        net = Decimal(str(s.basic_salary)) + Decimal(str(s.allowances or 0)) - Decimal(str(s.deductions or 0))
        result.append({
            "id": s.id, "employee_id": s.employee_id, "employee_name": emp.full_name if emp else "—",
            "basic_salary": float(s.basic_salary), "allowances": float(s.allowances or 0),
            "deductions": float(s.deductions or 0), "net_pay": float(net),
        })
    return result


# ---------- Payroll runs ----------

class PayrollRunCreate(BaseModel):
    month: str  # "YYYY-MM"


@router.post("/runs")
def create_payroll_run(payload: PayrollRunCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    existing = db.query(PayrollRun).filter(PayrollRun.tenant_id == user.tenant_id, PayrollRun.month == payload.month).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"A payroll run for {payload.month} already exists.")

    structures = db.query(SalaryStructure).filter(SalaryStructure.tenant_id == user.tenant_id, SalaryStructure.is_active == True).all()
    if not structures:
        raise HTTPException(status_code=400, detail="No active salary structures found — set up salaries for your employees first.")

    run = PayrollRun(tenant_id=user.tenant_id, month=payload.month, created_by=user.id)
    db.add(run)
    db.flush()

    for s in structures:
        employee = db.query(User).filter(User.id == s.employee_id, User.is_active == True).first()
        if not employee:
            continue  # skip suspended/deleted employees
        net_pay = Decimal(str(s.basic_salary)) + Decimal(str(s.allowances or 0)) - Decimal(str(s.deductions or 0))
        db.add(Payslip(
            tenant_id=user.tenant_id, payroll_run_id=run.id, employee_id=s.employee_id,
            basic_salary=s.basic_salary, allowances=s.allowances, deductions=s.deductions,
            net_pay=net_pay, status=PayslipStatus.pending,
        ))
    db.commit()
    db.refresh(run)

    count = db.query(Payslip).filter(Payslip.payroll_run_id == run.id).count()
    return {"id": run.id, "month": run.month, "payslip_count": count}


@router.get("/runs")
def list_payroll_runs(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    runs = db.query(PayrollRun).filter(PayrollRun.tenant_id == user.tenant_id).order_by(PayrollRun.month.desc()).all()
    result = []
    for r in runs:
        payslips = db.query(Payslip).filter(Payslip.payroll_run_id == r.id).all()
        total_net = sum(float(p.net_pay) for p in payslips)
        paid_count = sum(1 for p in payslips if p.status == PayslipStatus.paid)
        result.append({
            "id": r.id, "month": r.month, "payslip_count": len(payslips),
            "paid_count": paid_count, "total_net_pay": total_net, "created_at": r.created_at.isoformat(),
        })
    return result


@router.get("/runs/{run_id}/payslips")
def list_payslips(run_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    run = db.query(PayrollRun).filter(PayrollRun.id == run_id, PayrollRun.tenant_id == user.tenant_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Payroll run not found")
    payslips = db.query(Payslip).filter(Payslip.payroll_run_id == run_id).all()
    result = []
    for p in payslips:
        emp = db.query(User).filter(User.id == p.employee_id).first()
        result.append({
            "id": p.id, "employee_id": p.employee_id, "employee_name": emp.full_name if emp else "—",
            "basic_salary": float(p.basic_salary), "allowances": float(p.allowances or 0),
            "deductions": float(p.deductions or 0), "net_pay": float(p.net_pay),
            "status": p.status.value, "payment_method": p.payment_method,
            "payment_reference": p.payment_reference,
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            "has_bank_details": bool(emp and emp.bank_account_number and emp.bank_ifsc) if emp else False,
        })
    return result


# ---------- Paying a payslip ----------

class PayslipPay(BaseModel):
    payment_method: str = "bank_transfer"  # 'bank_transfer' | 'cash'
    payment_reference: str | None = None   # required if bank_transfer and payouts aren't automated


@router.patch("/payslips/{payslip_id}/pay")
def pay_payslip(payslip_id: str, payload: PayslipPay, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    payslip = db.query(Payslip).filter(Payslip.id == payslip_id, Payslip.tenant_id == user.tenant_id).first()
    if not payslip:
        raise HTTPException(status_code=404, detail="Payslip not found")
    if payslip.status == PayslipStatus.paid:
        raise HTTPException(status_code=400, detail="This payslip is already marked as paid.")
    if payload.payment_method not in ("bank_transfer", "cash"):
        raise HTTPException(status_code=400, detail="payment_method must be 'bank_transfer' or 'cash'")

    employee = db.query(User).filter(User.id == payslip.employee_id).first()
    reference = payload.payment_reference

    if payload.payment_method == "bank_transfer":
        if not (employee and employee.bank_account_number and employee.bank_ifsc):
            raise HTTPException(
                status_code=400,
                detail="This employee has no bank account details on file — required for bank transfer. "
                       "Add their bank details, or pay as cash instead."
            )
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if payout_configured(tenant):
            try:
                result = send_payout(
                    tenant, employee.bank_account_number, employee.bank_ifsc,
                    employee.bank_account_holder_name or employee.full_name,
                    float(payslip.net_pay), f"Salary {payslip.payroll_run_id}", payslip.id,
                )
                reference = result.get("utr") or result.get("payout_id")
            except (RuntimeError, NotImplementedError) as e:
                raise HTTPException(status_code=502, detail=str(e))
        elif not reference:
            raise HTTPException(
                status_code=400,
                detail="Bank payouts aren't connected yet — enter the bank transaction reference (UTR) "
                       "after transferring the funds manually, or connect RazorpayX under Payment Settings."
            )

    payslip.status = PayslipStatus.paid
    payslip.payment_method = payload.payment_method
    payslip.payment_reference = reference
    payslip.paid_at = datetime.utcnow()
    payslip.paid_by = user.id

    log_money_event(
        db, tenant_id=user.tenant_id, event_type=MoneyEventType.salary_paid,
        amount=payslip.net_pay, direction="out", actor_id=user.id,
        counterparty_type="employee", counterparty_id=payslip.employee_id,
        method=payload.payment_method, reference=reference, related_record_id=payslip.id,
        notes=f"Salary paid for payroll run {payslip.payroll_run_id}",
    )
    db.commit()
    return {"status": "paid", "payment_reference": reference}


@router.get("/payslips/{payslip_id}/download")
def download_payslip(payslip_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    payslip = db.query(Payslip).filter(Payslip.id == payslip_id, Payslip.tenant_id == user.tenant_id).first()
    if not payslip:
        raise HTTPException(status_code=404, detail="Payslip not found")
    employee = db.query(User).filter(User.id == payslip.employee_id).first()
    run = db.query(PayrollRun).filter(PayrollRun.id == payslip.payroll_run_id).first()

    file_path = generate_payslip_pdf(payslip, employee, run.month if run else "")
    return FileResponse(file_path, media_type="application/pdf", filename=f"payslip-{employee.full_name if employee else 'employee'}-{run.month if run else ''}.pdf")
