"""The activity list on the Status page, and the problems shown for each service: written by the worker and the copies
service. Kept apart from worker.py so the copies service, held to a few cores, doesn't load face clustering to write a
line."""
import sqlite3
from datetime import datetime

from lineagebox import outbox, settings

KEEP_ACTIVITY = 500  # lines


def one_line(text) -> str:
    """text as part of one line of the container's log: line breaks and other control characters (an email typed on
    the sign-in page, or an address, can hold any) written as \\n, \\x1b..., so that nothing anyone sends starts a
    line that looks like one of the app's own."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii") for ch in str(text))


def log(con: sqlite3.Connection | None, message: str) -> None:
    at = datetime.now()
    print(f"{at:%Y-%m-%d %H:%M:%S}  {one_line(message)}", flush=True)
    if con is not None:
        try:
            con.execute("INSERT INTO activity(at, message) VALUES (?, ?)", (at.isoformat(timespec="seconds"), message))
            con.execute("DELETE FROM activity WHERE id <= (SELECT max(id) FROM activity) - ?", (KEEP_ACTIVITY,))
            con.commit()
        except sqlite3.Error:  # the catalog busy or full: the line above is still in the container's log
            con.rollback()


def note_problem(con: sqlite3.Connection, service: str, message: str | None) -> None:
    """Show a service's problem on the Status and Settings pages, or take it off once it works; logged once."""
    try:
        changed = settings.set_problem(con, service, message)
    except sqlite3.Error:  # busy: noted on a later try
        con.rollback()
        return
    if changed:
        log(con, f"{settings.PROBLEM_SERVICES[service]}: {message}" if message
            else f"{settings.PROBLEM_SERVICES[service]} works again")
        if message:  # and an alert for admins on New for you and the Status page
            outbox.service_problem(con, settings.PROBLEM_SERVICES[service], message)
