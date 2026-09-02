from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.core.migrations import run_safe_migrations
from app.routers import auth, tenants, branches, loans, payments, documents, reports, verification

# Import models so they register on Base before create_all
from app.models import tenancy, finance, audit  # noqa

app = FastAPI(title="Udhayam MFI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(branches.router)
app.include_router(loans.router)
app.include_router(payments.router)
app.include_router(documents.router)
app.include_router(reports.router)
app.include_router(verification.router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    run_safe_migrations(engine)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Udhayam MFI"}
