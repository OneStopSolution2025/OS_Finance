import os
from datetime import timedelta

class Settings:
    APP_NAME = "Udhayam MFI"
    APP_DATABASE_URL: str = os.getenv("APP_DATABASE_URL", "postgresql+psycopg://os_user:os_pass@localhost:5432/os_finances")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-railway-env")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE = timedelta(hours=12)

    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")

    # Document storage - Railway volume mount or S3-compatible bucket
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")  # local | s3
    LOCAL_STORAGE_PATH: str = os.getenv("LOCAL_STORAGE_PATH", "/data/documents")

    TIMEZONE: str = "Asia/Kolkata"

    # One-time setup key for /auth/bootstrap-superadmin. Set this in Railway,
    # use it once to create your first platform login, then remove the variable.
    BOOTSTRAP_SECRET: str = os.getenv("BOOTSTRAP_SECRET", "")

    # (TWOFACTOR_API_KEY removed — SMS OTP verification is out of scope)

    # WhatsApp Cloud API (Meta) — payment receipts, loan status notifications
    WHATSAPP_API_TOKEN: str = os.getenv("WHATSAPP_API_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

    # Credit bureau / KYC aggregator (CIBIL, Karza, Signzy, etc.) — not usable
    # until you've signed with a real provider; see app/utils/credit_check.py
    # (CREDIT_BUREAU_API_KEY removed — CIBIL integration is out of scope)

    # Sentry — error tracking. Get a DSN from sentry.io (free tier available),
    # create a Python/FastAPI project, and paste the DSN it gives you here.
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    SENTRY_ENVIRONMENT: str = os.getenv("SENTRY_ENVIRONMENT", "production")

    # Better Stack (betterstack.com) — centralized log shipping. Create a
    # "Source" under Logs in their dashboard (choose "Python" as the platform)
    # and it gives you a source token — paste it here.
    BETTERSTACK_SOURCE_TOKEN: str = os.getenv("BETTERSTACK_SOURCE_TOKEN", "")
    BETTERSTACK_INGESTING_HOST: str = os.getenv("BETTERSTACK_INGESTING_HOST", "")

settings = Settings()
