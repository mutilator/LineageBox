"""Capture dates from EXIF strings, file names and file times, as ISO-8601 local time; and dates set by hand on
photos, as exactly as they are known (photo_date)."""
import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from lineagebox.events import Problem, day_first, exact_date

MIN_YEAR = 1800  # as dates.photo_date: old prints, scanned with their own date written in
# The dates cameras and computers write when their clock was never set; a real photo on one of these days is rare
ZERO_DATES = {date(1904, 1, 1), date(1970, 1, 1), date(1980, 1, 1)}
FUTURE_SLACK = timedelta(days=1)  # camera clocks in other time zones; anything later is a wrong clock

_EXIF = re.compile(r"(\d{4})[:\-](\d{2})[:\-](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
# IMG_20160811_182239, 20160811_182239, VID_20190816-120102, 2016-08-11 18.22.39, PXL_20190816_...
_FILENAME = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:[ _\-T]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2}))?(?!\d)")


def _build(parts) -> str | None:
    y, mo, d, h, mi, s = (int(p) if p else 0 for p in parts)
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    if dt.year < MIN_YEAR or dt.date() in ZERO_DATES or dt > datetime.now() + FUTURE_SLACK:
        return None
    return dt.isoformat(timespec="seconds")


def from_parts(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> str | None:
    """A date from its numbers, or None if it is impossible, too early or in the future."""
    return _build((year, month, day, hour, minute, second))


def parse_exif(value) -> str | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    m = _EXIF.search(str(value))
    return _build(m.groups()) if m else None


def from_filename(name: str) -> str | None:
    for m in _FILENAME.finditer(name):
        if iso := _build(m.groups()):
            return iso
    return None


def from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).isoformat(timespec="seconds")


# ---- dates set by hand on photos -----------------------------------------------------------------------------------

# Each season's first month north of the equator; south of it the same words come six months later. A season that
# runs into the next year belongs to the year it starts in: winter 2023 (in the south, summer 2023) is December 2023 to
# February 2024, so a year's Christmas is in that year's winter. Photo dates, search and tours all go by this.
SEASONS = ("winter", "spring", "summer", "fall")
_SEASON_START = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}
_THIRDS = {"early": 0, "mid": 1, "middle": 1, "late": 2}
_ABOUT = {"about", "around", "circa", "ca", "c", "abt", "approximately", "approx", "~"}
_SAY = "Write it like 1987-08-14, June 1987, summer 1987, 1987, the 1980s, about 1987 or between 1986 and 1988."


@dataclass(frozen=True)
class Span:
    """A photo's date as exactly as it is known: as it is shown ("Aug 14, 1987", "summer 1987", "between 1986 and
    1988"), and its first and last day."""
    text: str
    first: date
    last: date

    @property
    def middle(self) -> date:
        return self.first + (self.last - self.first) // 2

    @property
    def exact(self) -> bool:
        return self.first == self.last


def south(con) -> bool:
    """Whether the library's seasons are the southern hemisphere's (Settings → Whose photos)."""
    row = con.execute("SELECT value FROM settings WHERE key = 'southern_hemisphere'").fetchone()
    return bool(row and json.loads(row[0]))


def season_start(season: str, south: bool = False) -> int:
    month = _SEASON_START[season]
    return (month + 5) % 12 + 1 if south else month


def season_span(season: str, year: int, south: bool = False) -> tuple[date, date]:
    """The season's first and last day."""
    start = season_start(season, south)
    return date(year, start, 1), _month_end(year + 1, start - 10) if start > 10 else _month_end(year, start + 2)


def season_of(month: int, year: int, south: bool = False) -> tuple[str, int]:
    """The season a month is in, and its year: January 2024 is in winter 2023 (summer 2023 in the south)."""
    for season in SEASONS:
        start = season_start(season, south)
        if (month - start) % 12 < 3:
            return season, year - (month < start)
    raise ValueError(month)


def photo_date(text: str, south: bool = False, day_first: bool = False) -> Span | None:
    """A photo's date as people know it: a day ("1987-08-14", "Aug 14, 1987", "8/14/1987"), a month ("June 1987"), a
    season ("summer of 1987"), a year, a decade ("the 1980s"), early, mid or late in a year or decade ("late 1980s"),
    "about" any of these, or two of them as a range ("between summer 1987 and summer 1988", "1986-1988",
    "summer/fall 1987"). None for an empty one. Raises Problem for one it can't read, or one before 1800 or to come.
    south: seasons are the southern hemisphere's; day_first: 5/6/1990 is 5 June."""
    t = re.sub(r"\b(?:of|the)\b", " ", str(text or "").lower().replace(",", " ").replace("mid-", "mid "))
    t = " ".join(t.split())
    if not t:
        return None
    try:
        span = _span(t, south, day_first)
    except Problem:
        span = _range(re.sub(r"^(?:between|from)\s+", "", t), south, day_first)
    if span.first.year < 1800:
        raise Problem("Photos don't go back before 1800.")
    if span.first > date.today():
        raise Problem(f"{span.text} hasn't come yet.")
    return span


def _range(body: str, south: bool = False, day_first: bool = False) -> Span:
    """Two dates as a range, split wherever both halves can be read: "summer 1987 and summer 1988", "1986-1988"."""
    for sep in re.finditer(r"\s+(?:and|to|or|through|until)\s+|\s*[-–/]\s*", body):
        x, y = body[:sep.start()], body[sep.end():]
        try:
            if not re.search(r"\d", x) and (shared := re.fullmatch(r"(.+?)\s+(\d{4})", y)):
                # "summer or fall 1987": the year is the first's, and the second comes after it
                a, b = _span(f"{x} {shared[2]}", south, day_first), _span(y, south, day_first)
                if b.first <= a.first:
                    b = _span(f"{shared[1]} {int(shared[2]) + 1}", south, day_first)
            else:
                a, b = _span(x, south, day_first), _span(y, south, day_first)
        except Problem:
            continue
        if b.last < a.first:
            raise Problem(f"{a.text} comes after {b.text}.")
        return Span(f"between {a.text} and {b.text}", a.first, b.last)
    raise Problem(f"Can't read the date {body!r}. {_SAY}")


def _span(t: str, south: bool = False, day_first: bool = False) -> Span:
    word, _, rest = t.partition(" ")
    if word.rstrip(".") in _ABOUT and rest:
        s = _span(rest, south, day_first)
        return Span(f"about {s.text}", *_widen(s.first, s.last))
    if word in _THIRDS and rest:
        s = _span(rest, south, day_first)
        third = _THIRDS[word]
        if s.first == date(s.first.year, 1, 1) and s.last == date(s.first.year, 12, 31):  # a year: four months each
            first, last = date(s.first.year, 1 + 4 * third, 1), _month_end(s.first.year, 4 + 4 * third)
        elif s.text.endswith("0s") and not s.text.startswith(("about", "between")):  # a decade: 0-3, 4-6, 7-9
            y = s.first.year
            first, last = date(y + (0, 4, 7)[third], 1, 1), date(y + (3, 6, 9)[third], 12, 31)
        else:
            raise Problem("Early, mid and late go with a year or a decade, like late 1987 or the early 1980s.")
        return Span(f"{'mid' if word == 'middle' else word} {s.text}", first, last)
    if m := re.fullmatch(r"(\d{3})0s", t):
        y = int(m[1]) * 10
        return Span(f"{y}s", date(y, 1, 1), date(y + 9, 12, 31))
    if (m := re.fullmatch(r"([a-z]+)\s+(\d{4})", t)) and m[1] in _SEASON_START:
        return Span(f"{'fall' if m[1] == 'autumn' else m[1]} {m[2]}", *season_span(m[1], int(m[2]), south))
    try:
        sort = exact_date(t, day_first).sort  # "1987-08-14", "1987-08" or "1987"
    except Problem:
        raise Problem(f"Can't read the date {t!r}. {_SAY}") from None
    y = int(sort[:4])
    if len(sort) == 10:
        day = date.fromisoformat(sort)
        return Span(f"{day:%b} {day.day}, {y}", day, day)
    if len(sort) == 7:
        month = date(y, int(sort[5:]), 1)
        return Span(f"{month:%B} {y}", month, _month_end(y, month.month))
    return Span(str(y), date(y, 1, 1), date(y, 12, 31))


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _widen(first: date, last: date) -> tuple[date, date]:
    """'About': as far again before and after (a year for a year, three months for a season, a week for a day)."""
    if first.day == 1 and last == _month_end(last.year, last.month):
        months = (last.year - first.year) * 12 + last.month - first.month + 1
        before = first.year * 12 + first.month - 1 - months
        after = last.year * 12 + last.month - 1 + months
        return date(before // 12, before % 12 + 1, 1), _month_end(after // 12, after % 12 + 1)
    days = timedelta(days=max((last - first).days + 1, 7))
    return first - days, last + days
