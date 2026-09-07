from sqlalchemy.orm import Session
from app.models.audit import MoneyAuditLog, MoneyEventType


def log_money_event(
    db: Session, tenant_id: str, event_type: MoneyEventType, amount, direction: str,
    actor_id: str | None = None, branch_id: str | None = None,
    counterparty_type: str | None = None, counterparty_id: str | None = None,
    method: str | None = None, reference: str | None = None,
    related_record_id: str | None = None, notes: str | None = None,
):
    """
    Writes one audit entry and flushes it in the same transaction as the
    caller's other changes — if the surrounding commit fails, the audit entry
    is rolled back too, so the ledger never records something that didn't
    actually happen. Callers should call this just before their own db.commit().
    """
    entry = MoneyAuditLog(
        tenant_id=tenant_id, branch_id=branch_id, event_type=event_type,
        amount=amount, direction=direction, actor_id=actor_id,
        counterparty_type=counterparty_type, counterparty_id=counterparty_id,
        method=method, reference=reference, related_record_id=related_record_id, notes=notes,
    )
    db.add(entry)
