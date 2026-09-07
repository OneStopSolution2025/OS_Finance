"""
Error tracking (Sentry) and log shipping (Better Stack) — both fully wired in,
both dormant until you provide real credentials. Neither library is imported
at module load time if its credentials aren't set, so a deployment with none
of this configured behaves exactly as if this file didn't exist — no crash,
no slowdown, no dependency actually loaded into memory.

Setup:
  Sentry:       sentry.io → New Project → Python/FastAPI → copy the DSN it
                gives you → set SENTRY_DSN in Railway.
  Better Stack: betterstack.com → Logs → Connect source → Python → copy the
                source token and ingesting host it gives you → set
                BETTERSTACK_SOURCE_TOKEN and BETTERSTACK_INGESTING_HOST.
"""
import logging

from app.core.config import settings


def sentry_configured() -> bool:
    return bool(settings.SENTRY_DSN)


def betterstack_configured() -> bool:
    return bool(settings.BETTERSTACK_SOURCE_TOKEN and settings.BETTERSTACK_INGESTING_HOST)


def init_sentry():
    """Call once, at app startup. No-ops entirely if SENTRY_DSN isn't set."""
    if not sentry_configured():
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            traces_sample_rate=0.1,  # 10% of requests get performance tracing — keep this low, it's billed
            send_default_pii=False,  # never send request bodies/headers by default — this app handles KYC data
        )
        logging.getLogger(__name__).info("Sentry error tracking initialized.")
    except ImportError:
        logging.getLogger(__name__).warning(
            "SENTRY_DSN is set but the 'sentry-sdk' package isn't installed. Add it to requirements.txt."
        )


def init_betterstack():
    """
    Call once, at app startup. Attaches a log handler that ships every log
    record to Better Stack, in addition to whatever's already logging to
    stdout (Railway's own log viewer keeps working unchanged). No-ops
    entirely if the source token isn't set.
    """
    if not betterstack_configured():
        return
    try:
        from logtail import LogtailHandler

        handler = LogtailHandler(
            source_token=settings.BETTERSTACK_SOURCE_TOKEN,
            host=settings.BETTERSTACK_INGESTING_HOST,
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        logging.getLogger(__name__).info("Better Stack log shipping initialized.")
    except ImportError:
        logging.getLogger(__name__).warning(
            "BETTERSTACK_SOURCE_TOKEN is set but the 'logtail-python' package isn't installed. "
            "Add it to requirements.txt."
        )
