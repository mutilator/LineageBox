"""Other languages (docs/translating.md). LineageBox is written in English, and everything it says goes through gettext
so that a translation can be added without changing the code: the pages' text through Jinja's i18n extension
(`{% trans %}` and `_()` in web/templates/, set up by install()), the scripts' through `t()` in web/static/ (fed by
script_catalog(), which base.html puts in the page), and the help (web/help.py) and the formats of dates, numbers and
sizes through this module. No translation is shipped: with English only, every one of them gives back its text as it
is written, and the pages are exactly as they were.

A language is a folder under locale/: `locale/<language>/LC_MESSAGES/lineagebox.mo` (the pages, help and formats) and
`lineagebox-js.mo` (the scripts), compiled from .po files with Babel's pybabel, a tool for developers that the app
doesn't need to run. Each visitor gets the language they picked in this browser (More → Language, a cookie), else
the first their browser asks for that is installed, else `[web] language`, else English.

Two kinds of text, kept apart because they are escaped differently:
- HTML, written in a template (`{% trans %}` blocks): its translation is HTML too, put in the page as it is. Quotes
  in a translation, outside its tags, become character references, so that a translation can't end an attribute.
- Plain text (`_()` in a template, and every function here): escaped where it is shown, like any other text."""
import contextlib
import contextvars
import functools
import gettext as _gettext
import re
from datetime import datetime
from pathlib import Path

from markupsafe import escape

LOCALE = Path(__file__).parent / "locale"
DOMAIN = "lineagebox"  # the pages, the help and the formats
SCRIPTS = "lineagebox-js"  # the text of app.js, map.js and frame.js
ENGLISH = "en"  # what the text is written in, always there
COOKIE = "lang"  # the language picked in this browser
PLURAL_TABLE = 200  # the scripts get each plural form's number for 0-199 (and 100 + n % 100 beyond), not a formula

_CURRENT: contextvars.ContextVar = contextvars.ContextVar("language", default=ENGLISH)


def N_(message: str) -> str:
    """Marks text to translate where it is written down (help.py's tables), for pybabel to find; it is translated
    where it is shown, in the viewer's language (gettext)."""
    return message


@functools.cache
def installed() -> tuple[str, ...]:
    """The languages this copy speaks: English first, then each with a compiled catalog for the pages. Read once:
    a language added means a restart."""
    found = sorted(p.parent.parent.name for p in LOCALE.glob(f"*/LC_MESSAGES/{DOMAIN}.mo"))
    return (ENGLISH, *[lang for lang in found if lang != ENGLISH])


@functools.cache
def translations(lang: str, domain: str = DOMAIN) -> _gettext.NullTranslations:
    if lang == ENGLISH or lang not in installed():
        return _gettext.NullTranslations()
    return _gettext.translation(domain, LOCALE, [lang], fallback=True)


def forget() -> None:
    """Read the languages again (tests that install one)."""
    installed.cache_clear()
    translations.cache_clear()
    script_catalog.cache_clear()


def _asked(accept_language: str) -> list[str]:
    """The languages a browser asks for (Accept-Language), most wanted first."""
    out = []
    for i, part in enumerate(accept_language.split(",")):
        tag, _, params = part.strip().partition(";")
        q = 1.0
        if (m := re.search(r"q\s*=\s*([0-9.]+)", params)) is not None:
            try:
                q = float(m.group(1))
            except ValueError:
                q = 0.0
        if tag and tag != "*" and q > 0:
            out.append((-q, i, tag.strip()))
    return [tag for _, _, tag in sorted(out)]


def _match(tag: str, have: tuple[str, ...]) -> str | None:
    """The installed language for a tag (de-AT, pt_BR, fr): the same, else the same language of another country."""
    tag = tag.replace("-", "_").lower()
    if not tag:
        return None
    base = tag.split("_")[0]
    for test in (lambda h: h.lower() == tag, lambda h: h.lower() == base, lambda h: h.split("_")[0].lower() == base):
        for h in have:
            if test(h):
                return h
    return None


def choose(accept_language: str = "", chosen: str | None = None, default: str = "") -> str:
    """The language for one visitor: picked in this browser, else asked for by it, else the install's default."""
    have = installed()
    if len(have) == 1:
        return ENGLISH
    if chosen in have:
        return chosen
    for tag in _asked(accept_language):
        if (found := _match(tag, have)) is not None:
            return found
    return _match(default, have) or ENGLISH


def language() -> str:
    """The language of what is being made now (a page, an answer): English outside a request."""
    return _CURRENT.get()


def language_name(lang: str) -> str:
    """A language's name in itself (Deutsch, Français), as its catalog gives it, for the choice of language."""
    return translations(lang).pgettext("the name of this language, in this language", "English")


@contextlib.contextmanager
def speaking(lang: str):
    """While a request is answered: everything it shows is in this language."""
    token = _CURRENT.set(lang)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _now() -> _gettext.NullTranslations:
    return translations(_CURRENT.get())


# Plain text, for Python code: escaped where it is shown.

def gettext(message: str) -> str:
    return _now().gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _now().ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    return _now().pgettext(context, message)


def npgettext(context: str, singular: str, plural: str, n: int) -> str:
    return _now().npgettext(context, singular, plural, n)


# The templates' functions (install()).

_TAG = re.compile(r"(<[^>]*>)")


def _quotes_kept_in(translated: str, english: str) -> str:
    """A translation of HTML, made safe in an attribute too: its quotes outside tags become character references,
    which show as the quotes themselves. English, given back as it is written, needs nothing."""
    if translated is english or translated == english:
        return translated
    return "".join(part if part.startswith("<") else part.replace('"', "&#34;").replace("'", "&#39;")
                   for part in _TAG.split(translated))


def _page_gettext(message: str) -> str:
    return _quotes_kept_in(_now().gettext(message), message)


def _page_ngettext(singular: str, plural: str, n: int) -> str:
    return _quotes_kept_in(_now().ngettext(singular, plural, n), singular if n == 1 else plural)


def _page_pgettext(context: str, message: str) -> str:
    return _quotes_kept_in(_now().pgettext(context, message), message)


def _page_npgettext(context: str, singular: str, plural: str, n: int) -> str:
    return _quotes_kept_in(_now().npgettext(context, singular, plural, n), singular if n == 1 else plural)


def text(message: str, **values) -> str:
    """`_()` in a template: plain text, escaped where it is shown (so a quote in it can't end an attribute, and it
    goes into |tojson as it is). Its %(name)s are filled from values, escaped; write %% for a %. A value that is
    HTML already (a macro's output) is kept as it is, and then so is the whole."""
    said = _now().gettext(message)
    if any(hasattr(v, "__html__") for v in values.values()):
        return escape(said) % values
    return said % values


def install(env) -> None:
    """Jinja's i18n extension on a template environment, translating into each request's language, and the filters
    that format for it (date, number, size)."""
    env.add_extension("jinja2.ext.i18n")
    env.install_gettext_callables(_page_gettext, _page_ngettext, newstyle=True, pgettext=_page_pgettext,
                                  npgettext=_page_npgettext)
    env.policies["ext.i18n.trimmed"] = True  # a {% trans %} block's lines are one line of text, as in the page
    env.globals["_"] = text
    env.globals["language"] = language
    env.globals["languages"] = installed
    env.globals["language_name"] = language_name
    env.globals["script_text"] = lambda: script_catalog(language())
    env.globals["month_names"] = _Months(long=True)
    env.globals["month_short_names"] = _Months(long=False)
    env.globals["weekday_names"] = _Weekdays()
    env.filters["date"] = date
    env.filters["number"] = number
    env.filters["size"] = size


# The scripts' catalog (app.js t() and tn()).

@functools.cache
def script_catalog(lang: str) -> dict:
    """What a page gives its scripts: {"text": {English: translation, or a list of plural forms}, "plural": the form
    for each number below PLURAL_TABLE}. Empty for English, so a page then carries nothing."""
    if lang == ENGLISH or lang not in installed():
        return {}
    t = translations(lang, SCRIPTS)
    found = getattr(t, "_catalog", None)
    if not found:
        return {}
    out: dict = {}
    for key, value in found.items():
        if isinstance(key, tuple):  # (singular, form number): a plural
            msgid, i = key
            forms = out.setdefault(msgid, [])
            forms.extend([None] * (i + 1 - len(forms)))
            forms[i] = value
        elif key and value:
            out[key] = value
    plural = getattr(t, "plural", lambda n: int(n != 1))
    return {"text": out, "plural": [plural(n) for n in range(PLURAL_TABLE)]}


# Dates, numbers and sizes, as each language writes them. In English: Jun 3, 2012 10:05; 12,345.6; 74.8 GB.

def number(n, digits: int | None = None) -> str:
    """A number with its thousands separated ("{:,}"), or rounded to so many digits ("{:,.0f}"), written with the
    separators of the viewer's language."""
    return _numeral(f"{n:,}" if digits is None else f"{n:,.{digits}f}")


def size(n: int | None) -> str:
    """Bytes as people read them: 812 MB, 74.8 GB, 3.6 TB (by 1024s, as the upload limits are); storage.fmt_size,
    in the viewer's language."""
    if n is None:
        return ""
    units = {"TB": pgettext("terabytes", "TB"), "GB": pgettext("gigabytes", "GB"), "MB": pgettext("megabytes", "MB"),
             "KB": pgettext("kilobytes", "KB")}
    unit, digits, value = "KB", 0, n / 2**10
    for name, at in (("TB", 2**40), ("GB", 2**30), ("MB", 2**20)):
        if n >= at:
            unit, value = name, n / at
            digits = 1 if value < 100 else 0
            break
    return pgettext("a size, as 74.8 GB", "{number} {unit}").format(number=number(value, digits), unit=units[unit])


def _numeral(english: str) -> str:
    """A number written with English separators, in the viewer's."""
    thousands = pgettext("thousands separator in numbers, as in 12,345", ",")
    decimal = pgettext("decimal mark in numbers, as in 3.5", ".")
    if (thousands, decimal) == (",", "."):
        return english
    return english.translate({ord(","): thousands, ord("."): decimal})


def month_name(m: int, long: bool = True) -> str:
    """January (or Jan) for 1, in the viewer's language."""
    if long:
        return (pgettext("month", "January"), pgettext("month", "February"), pgettext("month", "March"),
                pgettext("month", "April"), pgettext("month", "May"), pgettext("month", "June"),
                pgettext("month", "July"), pgettext("month", "August"), pgettext("month", "September"),
                pgettext("month", "October"), pgettext("month", "November"), pgettext("month", "December"))[m - 1]
    return (pgettext("month, short", "Jan"), pgettext("month, short", "Feb"), pgettext("month, short", "Mar"),
            pgettext("month, short", "Apr"), pgettext("month, short", "May"), pgettext("month, short", "Jun"),
            pgettext("month, short", "Jul"), pgettext("month, short", "Aug"), pgettext("month, short", "Sep"),
            pgettext("month, short", "Oct"), pgettext("month, short", "Nov"), pgettext("month, short", "Dec"))[m - 1]


def weekday_name(d: int, long: bool = False) -> str:
    """Mon (or Monday) for 0, as datetime.weekday() counts, in the viewer's language."""
    if long:
        return (pgettext("weekday", "Monday"), pgettext("weekday", "Tuesday"), pgettext("weekday", "Wednesday"),
                pgettext("weekday", "Thursday"), pgettext("weekday", "Friday"), pgettext("weekday", "Saturday"),
                pgettext("weekday", "Sunday"))[d]
    return (pgettext("weekday, short", "Mon"), pgettext("weekday, short", "Tue"), pgettext("weekday, short", "Wed"),
            pgettext("weekday, short", "Thu"), pgettext("weekday, short", "Fri"), pgettext("weekday, short", "Sat"),
            pgettext("weekday, short", "Sun"))[d]


class _Months:
    """month_names[m] in a template, in the viewer's language ("" for 0, as the list it replaces had)."""
    def __init__(self, long: bool):
        self.long = long

    def __getitem__(self, m):
        return month_name(int(m), self.long) if m else ""


class _Weekdays:
    """weekday_names[d] in a template: Mon for 0."""
    def __getitem__(self, d):
        return weekday_name(int(d))


def date(iso: str | None, with_time: bool = True) -> str:
    """A date (and its time of day, unless midnight) as people read it: Jun 3, 2012 10:05 in English."""
    if not iso:
        return pgettext("a date not known", "unknown")
    dt = datetime.fromisoformat(iso)
    out = pgettext("a date, as Jun 3, 2012", "{month} {day}, {year}").format(
        month=month_name(dt.month, long=False), day=dt.day, year=dt.year)
    if with_time and (dt.hour or dt.minute):
        clock = pgettext("a time of day, as 14:05", "{hour:02}:{minute:02}").format(hour=dt.hour, minute=dt.minute)
        return pgettext("a date and its time of day", "{date} {time}").format(date=out, time=clock)
    return out
