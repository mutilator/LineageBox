"""Long pages of photos a piece at a time: a folder, a year, a tag, a tour. The page sends the first PIECE tiles; app.js
loads the next piece as the page is scrolled near its end (a plain "Show more" link without it), and a scrubber at the
side jumps to a year or a month by loading the page from there ("Show earlier photos" at its top).

The address says which part of the list a page shows: ?skip= where it starts, ?upto= where it ends. app.js keeps the
address up to date as pieces load, so going back to the page from a photo (or loading it again) shows the same tiles
in the same places, and the browser puts the page back where it was. ?piece=1 answers with only the tiles, for app.js
to add to the page.

Pieces do not change where ← → on a photo go: the photo page works out the whole list from its ctx
(search.context_ids, a tour's photos), whatever part of it was loaded.
"""
from dataclasses import dataclass

from fastapi import Request

from lineagebox import i18n

PIECE = 200        # tiles sent at a time: about 80 kB of page, quick on the router and on a phone
MOST = 3000        # at most this many at once, when a page is loaded again with all it had loaded (?upto=)
SCRUB_FROM = 100   # a list this long, over more than one month, gets a scrubber


@dataclass
class Window:
    start: int
    end: int
    total: int
    piece: bool  # only the tiles: a part for app.js to add to the page

    @property
    def earlier(self) -> int | None:
        """Where the piece before this part starts (None at the top of the list)."""
        return max(0, self.start - PIECE) if self.start > 0 else None

    @property
    def later(self) -> int | None:
        """Where the next piece starts (None at the end of the list)."""
        return self.end if self.end < self.total else None

    @property
    def left(self) -> int:
        return self.total - self.end

    def of(self, items: list) -> list:
        return items[self.start:self.end]


def _number(request: Request, name: str) -> int | None:
    value = request.query_params.get(name, "")
    return int(value) if value.isdecimal() and len(value) < 10 else None


def window(request: Request, total: int) -> Window:
    """The part of a list of total items that this request asks for."""
    start = min(_number(request, "skip") or 0, max(0, total - 1))
    upto = _number(request, "upto")
    end = upto if upto is not None and upto > start else start + PIECE
    return Window(start, min(end, total, start + MOST), total, request.query_params.get("piece") == "1")


def marks(rows, months_only: bool = False) -> list[dict]:
    """The scrubber's years and months for a list in date order: [{'label', 'name' (said in full), 'at' (where it
    starts in the list), 'count', 'id' (its first photo), 'ym' ('1987', or '' for no date), 'months': [the same, by
    month, with 'ym' '1987-06']}]. None for a list shorter than SCRUB_FROM, or all in one month. months_only: a year's
    page, whose marks are its months. The months are named in the viewer's language (i18n.py), as the headings are."""
    years: list[dict] = []
    for at, r in enumerate(rows):
        taken = r["taken_at"] or ""
        year, month = taken[:4], taken[5:7]
        if not years or years[-1]["ym"] != year:
            undated = i18n.pgettext("a scrubber's mark for the photos with no date", "No date")
            years.append({"label": year or undated, "name": year or undated, "at": at, "count": 0, "id": r["id"],
                          "ym": year, "months": []})
        y = years[-1]
        y["count"] += 1
        if year and (not y["months"] or y["months"][-1]["ym"] != taken[:7]):
            m = int(month) if month.isdecimal() and 0 < int(month) < 13 else 0
            y["months"].append({"label": i18n.month_name(m, long=False) if m else "?",
                                "name": f"{i18n.month_name(m)} {year}" if m else year,
                                "at": at, "count": 0, "id": r["id"], "ym": taken[:7], "months": []})
        if y["months"]:
            y["months"][-1]["count"] += 1
    months = [m for y in years for m in y["months"]]
    undated = 1 if years and not years[0]["ym"] else 0
    if len(months) + undated < 2 or (len(rows) < SCRUB_FROM and not months_only):  # a year's months: always
        return []
    return months if months_only else years


def part(request: Request, rows: list, ctx: str, select: bool = False, by_month: bool = False) -> dict:
    """What _pieces.html shows of a list of photos in date order (each with id and taken_at): the part this request
    asks for, the scrubber, and the page's other parameters for the links on (here). by_month: a year's page, whose
    photos are under a heading for each month."""
    w = window(request, len(rows))
    shown = w.of(rows)
    found = marks(rows, months_only=by_month)
    here = {k: v for k, v in request.query_params.items() if k not in ("skip", "upto", "piece")}
    return {"w": w, "here": here, "marks": found, "current": current(found, w.start), "ctx": ctx, "select": select,
            "images": shown, "months": _months(rows, w) if by_month else None,
            "anchor": "month" if by_month else "tile"}


def _months(rows: list, w: Window) -> list[dict]:
    """A year's window by month: [{'month', 'images', 'count' (in all the year), 'head'}]. A month has its heading
    where it begins, and at the top of a page that opens in the middle of it; a piece that goes on with the month the
    page ends in has none (app.js puts its photos with the others)."""
    counts: dict[int, int] = {}
    first: dict[int, int] = {}
    for at, r in enumerate(rows):
        mo = int(r["taken_at"][5:7])
        counts[mo] = counts.get(mo, 0) + 1
        first.setdefault(mo, at)
    out: list[dict] = []
    for at in range(w.start, w.end):
        mo = int(rows[at]["taken_at"][5:7])
        if not out or out[-1]["month"] != mo:
            out.append({"month": mo, "images": [], "count": counts[mo],
                        "head": first[mo] >= w.start or (not out and not w.piece)})
        out[-1]["images"].append(rows[at])
    return out


def current(marks_: list[dict], start: int) -> tuple[str, str]:
    """The year and month a part starting at start opens on, marked in the scrubber as the page loads."""
    year = month = ""
    for y in marks_:
        if y["at"] <= start:
            year, month = y["ym"][:4], y["ym"]
            for m in y["months"]:
                if m["at"] <= start:
                    month = m["ym"]
    return year, month
