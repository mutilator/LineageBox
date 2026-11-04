"""What a person is told when something they asked for can't be done. Every module's own Problem (people.Problem,
settings.Invalid, ...) is one of these, and the web app answers each with its message (web/app.py), as JSON for the
page's scripts or as a page of its own, so no route has to translate each module's errors itself."""


class Problem(ValueError):
    """Shown to the person who asked, as it is. status: the HTTP answer for it."""
    status = 400
