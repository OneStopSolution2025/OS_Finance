"""
India Standard Time helpers. The database stores timestamps in UTC (the
Python/SQLAlchemy default), and the server itself may run in UTC too — but
every user of this application is in India. Without this, "today" and
"which day did this payment land on" are computed in UTC, which silently
disagrees with IST for roughly 5.5 hours every night (UTC's midnight-to-5:30am
is still "yesterday evening" in India, and vice versa) — exactly the kind of
mismatch that makes a collections chart look wrong right when someone checks
it first thing in the morning or last thing at night.
"""
from datetime import datetime, date, timedelta

IST_OFFSET = timedelta(hours=5, minutes=30)


def ist_today() -> date:
    """The current calendar date in India, regardless of what timezone the server itself runs in."""
    return (datetime.utcnow() + IST_OFFSET).date()


def ist_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """
    The UTC datetime range that corresponds to one full IST calendar day —
    use this to filter a UTC timestamp column against an IST day, instead of
    naively extracting the UTC calendar date from it (which is what silently
    goes wrong near midnight IST).
    """
    start_ist_as_utc = datetime.combine(d, datetime.min.time()) - IST_OFFSET
    end_ist_as_utc = start_ist_as_utc + timedelta(days=1)
    return start_ist_as_utc, end_ist_as_utc
