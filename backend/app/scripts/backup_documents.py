"""
Backs up everything under LOCAL_STORAGE_PATH — KYC documents, payment
receipts, payslips, generated reports — to the same S3-compatible bucket used
for database backups (see backup_database.py). Reuses the identical
BACKUP_S3_* environment variables; no separate configuration needed.

This is a BACKUP, not a storage migration — documents still live primarily on
the Railway volume day to day; this just mirrors them somewhere durable on a
schedule, the same way backup_database.py mirrors the database. Moving
primary document storage onto GCS/S3 (so the Railway volume isn't the only
copy even between backup runs) is a separate, larger change — worth doing
eventually, but distinct from this script.

Idempotent: skips a file if it's already in the bucket at the same size, so
re-running this daily only uploads what's actually new or changed, not the
entire document history every time.

How to run it:
  - Manually: `python -m app.scripts.backup_documents` from the backend folder.
  - On a schedule: same Railway Cron Job as backup_database.py — run both
    commands, one after the other.

Required environment variables (same ones backup_database.py already uses):
  BACKUP_S3_BUCKET, BACKUP_S3_ENDPOINT_URL, BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY
  BACKUP_S3_REGION (optional, defaults to "auto")
"""
import os
import sys


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"ERROR: {name} is not set. Backups are not configured — see the module docstring for setup.", file=sys.stderr)
        sys.exit(1)
    return value


def run_backup():
    bucket = get_required_env("BACKUP_S3_BUCKET")
    endpoint_url = get_required_env("BACKUP_S3_ENDPOINT_URL")
    access_key = get_required_env("BACKUP_S3_ACCESS_KEY")
    secret_key = get_required_env("BACKUP_S3_SECRET_KEY")
    source_dir = os.getenv("LOCAL_STORAGE_PATH", "/data/documents")

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("ERROR: boto3 is required for backups. Add 'boto3' to requirements.txt.", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(source_dir):
        print(f"Nothing to back up yet — {source_dir} doesn't exist.")
        return

    s3 = boto3.client(
        "s3", endpoint_url=endpoint_url, region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )

    uploaded, skipped, failed = 0, 0, 0
    for root, _dirs, files in os.walk(source_dir):
        for filename in files:
            local_path = os.path.join(root, filename)
            rel_path = os.path.relpath(local_path, source_dir)
            key = f"documents/{rel_path}".replace(os.sep, "/")
            local_size = os.path.getsize(local_path)

            # Skip if the bucket already has this exact file at the same size —
            # good enough idempotency without hashing every file every run.
            try:
                head = s3.head_object(Bucket=bucket, Key=key)
                if head["ContentLength"] == local_size:
                    skipped += 1
                    continue
            except ClientError as e:
                if e.response["Error"]["Code"] not in ("404", "NoSuchKey"):
                    print(f"WARNING: could not check {key}: {e}", file=sys.stderr)

            try:
                s3.upload_file(local_path, bucket, key)
                uploaded += 1
            except Exception as e:
                print(f"WARNING: failed to upload {rel_path}: {e}", file=sys.stderr)
                failed += 1

    print(f"Document backup finished. Uploaded: {uploaded}, already up to date: {skipped}, failed: {failed}.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_backup()
