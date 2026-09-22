from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import require_superadmin
from app.models.tenancy import User
from app.models.finance import Partner

router = APIRouter(prefix="/partners", tags=["partners"])


class PartnerCreate(BaseModel):
    name: str
    phone: str | None = None
    invested_amount: float = 0
    invested_date: date | None = None
    withdrawal_amount: float | None = 0
    withdrawal_date: date | None = None
    notes: str | None = None


class PartnerUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    invested_amount: float | None = None
    invested_date: date | None = None
    withdrawal_amount: float | None = None
    withdrawal_date: date | None = None
    notes: str | None = None


@router.post("/")
def create_partner(payload: PartnerCreate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Partner name is required.")
    partner = Partner(
        tenant_id=user.tenant_id,
        name=payload.name.strip(),
        phone=(payload.phone or "").strip() or None,
        invested_amount=payload.invested_amount or 0,
        invested_date=payload.invested_date,
        withdrawal_amount=payload.withdrawal_amount or 0,
        withdrawal_date=payload.withdrawal_date,
        notes=payload.notes,
        created_by=user.id,
    )
    db.add(partner)
    db.commit()
    db.refresh(partner)
    return partner


@router.get("/")
def list_partners(db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    return (
        db.query(Partner)
        .filter(Partner.tenant_id == user.tenant_id, Partner.is_active == True)
        .order_by(Partner.created_at.desc())
        .all()
    )


@router.patch("/{partner_id}")
def update_partner(partner_id: str, payload: PartnerUpdate, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    partner = db.query(Partner).filter(Partner.id == partner_id, Partner.tenant_id == user.tenant_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    updates = payload.dict(exclude_unset=True)
    if "name" in updates and not (updates["name"] or "").strip():
        raise HTTPException(status_code=400, detail="Partner name cannot be empty.")
    for field, value in updates.items():
        setattr(partner, field, value)
    db.commit()
    db.refresh(partner)
    return partner


@router.delete("/{partner_id}")
def delete_partner(partner_id: str, db: Session = Depends(get_db), user: User = Depends(require_superadmin)):
    partner = db.query(Partner).filter(Partner.id == partner_id, Partner.tenant_id == user.tenant_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    # Soft-delete: keeps historical investment/withdrawal figures intact for
    # the capital-investment dashboard math even after a partner record is
    # removed from the visible list.
    partner.is_active = False
    db.commit()
    return {"status": "deleted"}
