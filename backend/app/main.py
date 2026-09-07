from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import Base, engine
from app.core.migrations import run_safe_migrations
from app.core.monitoring import init_sentry, init_betterstack
from app.routers import auth, tenants, branches, loans, payments, documents, reports

# Import models so they register on Base before create_all
from app.models import tenancy, finance, audit  # noqa

init_sentry()
init_betterstack()

from app.core.config import settings

app = FastAPI(title="Udhayam MFI API", version="1.0.0")

# Locked to the actual frontend origin(s) — a wildcard here would let any
# website on the internet make authenticated requests using a stolen/leaked
# token from a logged-in user's browser. FRONTEND_URL is set in Railway;
# localhost stays allowed for local development regardless.
allowed_origins = [o.strip() for o in settings.FRONTEND_URL.split(",") if o.strip()] + [
    "http://localhost:3000", "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # without this, browsers silently hide this header from
                                              # fetch() even though it's genuinely present in the response
                                              # — every downloaded file (receipts, reports, exports) would
                                              # fall back to its generic placeholder name, not the real one.
)

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(branches.router)
app.include_router(loans.router)
app.include_router(payments.router)
app.include_router(documents.router)
app.include_router(reports.router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    run_safe_migrations(engine)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Udhayam MFI"}
