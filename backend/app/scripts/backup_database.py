"""
Automated database backups to cloud object storage.

This is a REAL, RUNNABLE script — unlike the payment/credit-check scaffolds,
it doesn't need a regulated financial account, just an S3-compatible storage
bucket (AWS S3, Backblaze B2, Cloudflare R2, etc.), which takes a few minutes
to set up and costs a few dollars a month for typical data volumes.

What it does:
  1. Runs `pg_dump` against APP_DATABASE_URL to produce a compressed SQL dump.
  2. Uploads it to the configured S3-compatible bucket, under a dated key.
  3. Deletes local backups older than a day (the bucket is the real copy —
     local disk is just staging space, and Railway's disk isn't persistent
     across deploys anyway).

What it does NOT do (yet):
  - Enforce the retention policy (30 daily / 6 monthly) — that's a bucket
    lifecycle rule you set once in your cloud storage console, not something
    this script needs to manage.
  - Back up uploaded documents (KYC files, receipts, payslips) — those live
    on a separate Railway volume; see the README section on document storage
    for the recommended fix (move to S3-compatible storage as the primary
    location, not just as a backup target).

How to run it:
  - Manually: `python -m app.scripts.backup_database` from the backend folder,
    with the required env vars set (see below).
  - On a schedule: add a Railway Cron Job service pointing at this same repo,
    running the command above, e.g. daily at 02:00.

Required environment variables (all must be set, or the script refuses to run
rather than silently skipping the backup):
  BACKUP_S3_BUCKET       — bucket name
  BACKUP_S3_ENDPOINT_URL — e.g. https://s3.ap-south-1.amazonaws.com, or your
                            provider's S3-compatible endpoint
  BACKUP_S3_ACCESS_KEY
  BACKUP_S3_SECRET_KEY

Optional:
  BACKUP_S3_REGION       — defaults to "auto", which is correct for Cloudflare
                            R2. Set to a real AWS region (e.g. ap-south-1) if
                            using AWS S3 instead.
"""
import os
import subprocess
import sys
from datetime import datetime, timezone


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"ERROR: {name} is not set. Backups are not configured — see the module docstring for setup.", file=sys.stderr)
        sys.exit(1)
    return value


def run_backup():
    database_url = get_required_env("APP_DATABASE_URL")
    bucket = get_required_env("BACKUP_S3_BUCKET")
    endpoint_url = get_required_env("BACKUP_S3_ENDPOINT_URL")
    access_key = get_required_env("BACKUP_S3_ACCESS_KEY")
    secret_key = get_required_env("BACKUP_S3_SECRET_KEY")

    try:
        import boto3
    except ImportError:
        print("ERROR: boto3 is required for backups. Add 'boto3' to requirements.txt.", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    local_path = f"/tmp/os-finances-backup-{timestamp}.sql.gz"

    # pg_dump | gzip, straight to a local file. Uses the standard libpq
    # connection string parsing, so this works with the same APP_DATABASE_URL
    # the app itself uses (after stripping the +psycopg driver suffix, which
    # pg_dump doesn't understand).
    pg_url = database_url.replace("postgresql+psycopg://", "postgresql://")
    print(f"Running pg_dump...")
    with open(local_path, "wb") as f:
        dump = subprocess.Popen(["pg_dump", pg_url, "--no-owner", "--no-privileges"], stdout=subprocess.PIPE)
        gzip = subprocess.Popen(["gzip"], stdin=dump.stdout, stdout=f)
        dump.stdout.close()
        gzip.communicate()
        if gzip.returncode != 0:
            print("ERROR: pg_dump/gzip failed.", file=sys.stderr)
            sys.exit(1)

    size_mb = os.path.getsize(local_path) / (1024 * 1024)
    print(f"Backup created: {local_path} ({size_mb:.2f} MB)")

    print(f"Uploading to s3://{bucket}/daily/{timestamp}.sql.gz ...")
    s3 = boto3.client(
        "s3", endpoint_url=endpoint_url, region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )
    s3.upload_file(local_path, bucket, f"daily/{timestamp}.sql.gz")
    print("Upload complete.")

    os.remove(local_path)
    print("Backup finished successfully.")


if __name__ == "__main__":
    run_backup()
