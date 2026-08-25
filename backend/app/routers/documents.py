import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_any
from app.core.config import settings
from app.models.tenancy import User
from app.models.finance import Document, DocumentType

router = APIRouter(tags=["documents"])

DOCS_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "kyc")
os.makedirs(DOCS_DIR, exist_ok=True)

ALLOWED_TYPES = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_SIZE_MB = 10


# ---------- Documents (KYC storage) ----------

@router.post("/documents/upload")
async def upload_document(
    doc_type: DocumentType = Form(...),
    customer_id: str | None = Form(None),
    loan_id: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_any),
):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF, JPG, PNG files are accepted")

    contents = await file.read()
    if len(contents) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_SIZE_MB}MB limit")

    tenant_dir = os.path.join(DOCS_DIR, user.tenant_id)
    os.makedirs(tenant_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4()}{ext}"
    full_path = os.path.join(tenant_dir, stored_name)
    with open(full_path, "wb") as f:
        f.write(contents)

    doc = Document(
        tenant_id=user.tenant_id, customer_id=customer_id, loan_id=loan_id,
        doc_type=doc_type, file_name=file.filename, storage_path=full_path,
        uploaded_by=user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "file_name": doc.file_name, "doc_type": doc.doc_type.value}


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    doc = db.query(Document).filter(Document.id == document_id, Document.tenant_id == user.tenant_id).first()
    if not doc or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(doc.storage_path, filename=doc.file_name)


@router.get("/customers/{customer_id}/documents")
def list_customer_documents(customer_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    return db.query(Document).filter(Document.customer_id == customer_id, Document.tenant_id == user.tenant_id).all()
