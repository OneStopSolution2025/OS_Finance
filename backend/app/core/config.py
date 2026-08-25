import os
from datetime import timedelta

class Settings:
    APP_NAME = "OS Finances"
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

    # One-time setup key for /auth/bootstrap-superemeadmin. Set this in Railway,
    # use it once to create your first platform login, then remove the variable.
    BOOTSTRAP_SECRET: str = os.getenv("BOOTSTRAP_SECRET", "")

    # 2Factor.in — SMS OTP verification (customer phone verification during KYC)
    TWOFACTOR_API_KEY: str = os.getenv("TWOFACTOR_API_KEY", "")

    # WhatsApp Cloud API (Meta) — payment receipts, loan status notifications
    WHATSAPP_API_TOKEN: str = os.getenv("WHATSAPP_API_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

    # Credit bureau / KYC aggregator (CIBIL, Karza, Signzy, etc.) — not usable
    # until you've signed with a real provider; see app/utils/credit_check.py
    CREDIT_BUREAU_API_KEY: str = os.getenv("CREDIT_BUREAU_API_KEY", "")

    # RazorpayX Payouts — separate from standard Razorpay above, needs its own
    # business KYC/approval; see app/utils/payouts.py
    RAZORPAYX_ACCOUNT_NUMBER: str = os.getenv("RAZORPAYX_ACCOUNT_NUMBER", "")
    RAZORPAYX_KEY_ID: str = os.getenv("RAZORPAYX_KEY_ID", "")
    RAZORPAYX_KEY_SECRET: str = os.getenv("RAZORPAYX_KEY_SECRET", "")

settings = Settings()
