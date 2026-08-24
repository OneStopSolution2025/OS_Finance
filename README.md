# OS Finances

A multi-tenant microfinance management platform, built as an OS2 Studio in-house SaaS product.
SuperEmeAdmin (OS2) sells subscriptions to microfinance operators; each SuperAdmin runs their own
branches, loan officers, and portfolio.

## Roles

| Role | Scope | Key actions |
|---|---|---|
| **SuperEmeAdmin** | Platform (OS2) | Create subscription plans, onboard tenants, suspend/reactivate subscriptions |
| **SuperAdmin** | One tenant org | Create branches (up to plan limit), create employees, define loan products, approve/disburse loans, view tenant-wide reports |
| **Employee** | One branch | Onboard customers (KYC), apply for loans, collect repayments (cash or Razorpay), print receipts, mark attendance |

## Stack

- **Backend:** FastAPI + PostgreSQL (SQLAlchemy), JWT auth, reportlab for PDF receipts, Razorpay SDK
- **Frontend:** Single-file responsive SPA (vanilla JS), served via a small Express static server, installable as a PWA
- **Deployment:** Railway (two services — backend + frontend)

## Local development

### Backend
```bash
cd backend
pip install -r requirements.txt
export APP_DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/os_finances"
export JWT_SECRET="a-long-random-string"
export RAZORPAY_KEY_ID="rzp_test_xxx"
export RAZORPAY_KEY_SECRET="xxx"
uvicorn app.main:app --reload
```
Visit `http://localhost:8000/docs` for the interactive API explorer.

### Frontend
```bash
cd frontend
npm install
BACKEND_URL="http://localhost:8000" npm start
```
Visit `http://localhost:3000`.

## First-time setup (creating your first SuperEmeAdmin)

There's no public signup for SuperEmeAdmin — it's OS2's own seat. Insert it directly once, after
the backend has started (which auto-creates tables):

```python
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.tenancy import User, UserRole

db = SessionLocal()
db.add(User(
    full_name="OS2 Admin",
    email="admin@os2studio.com",
    hashed_password=hash_password("choose-a-strong-password"),
    role=UserRole.superemeadmin,
))
db.commit()
```

From there, log in as that SuperEmeAdmin in the app, create a subscription plan, and onboard your
first tenant — that call also provisions their SuperAdmin login in one step.

## Deploying to Railway

1. Push this repo to GitHub (two folders, `backend/` and `frontend/`, can be two Railway services
   from the same repo — set each service's **root directory** accordingly).
2. **Backend service:**
   - Add a PostgreSQL plugin, then set `APP_DATABASE_URL` to its connection string (Railway auto-injects
     `DATABASE_URL` — rename to avoid collisions, same pattern as your other apps).
   - Set `JWT_SECRET`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`.
   - Add a Railway **volume** mounted at `/data` and set `LOCAL_STORAGE_PATH=/data/documents` so
     uploaded KYC documents and generated receipts survive redeploys.
3. **Frontend service:**
   - Set `BACKEND_URL` to the backend service's public Railway URL.
   - No database needed.
4. Once both are live, open the frontend URL on a phone and use "Add to Home Screen" — the manifest
   and service worker make it installable like a native app.

## What's built vs. what's next

**Working end-to-end (tested):** subscription plans, tenant onboarding with branch-limit enforcement,
branch and employee creation, customer KYC records, loan products, loan application → approval →
disbursement with auto-generated EMI schedules (flat and reducing-balance interest), cash and Razorpay
repayment collection, auto-generated PDF receipts, document upload/storage, employee attendance
check-in/out, branch summary and portfolio-at-risk reports, role-based access control, and
subscription-suspension login lockout.

**Recommended next additions:** document viewer/gallery on the customer profile screen, WhatsApp/SMS
payment reminders (you already have this pattern from Smart Garage 360), loan write-off workflow,
Razorpay subscription billing for the tenant's own monthly fee (currently plan/status is managed
manually by SuperEmeAdmin), and branch-wise PDF/Excel export for reports.
