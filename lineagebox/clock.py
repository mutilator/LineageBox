"""The time as LineageBox writes it down: local time to the second (the catalog's dates are local, as photos' are)."""
from datetime import datetime


def now() -> str:
    """Now, as the catalog keeps it: "2026-11-17T09:30:00"."""
    return datetime.now().isoformat(timespec="seconds")


def now_datetime() -> datetime:
    """Now to the second, to work out from (when a sign-in or a code runs out)."""
    return datetime.now().replace(microsecond=0)
