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

settings = Settings()
