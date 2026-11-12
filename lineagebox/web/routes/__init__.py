"""The web app's routes, one module per part of the site, each with its router and its routes' rules (ACCESS).
app.py includes them in MODULES' order: where two paths can match one address, the first one listed wins.
"""
from lineagebox.web.routes import (browse, details, places, gedcom, reading, family, tours, faces, naming,
                                   cleanup, photos, comments, folders, status, settings, files, uploads, sharing, home, signin,
                                   passkeys, accounts)
from lineagebox.web.routes import frame, news, phone  # photo frames and device links, New for you, the phone app
from lineagebox.web.routes import stories  # recorded stories and corrected transcripts
from lineagebox.web.routes import ai, tags  # describing again, More like this; tags across the library
from lineagebox.web.routes import albums, book  # albums picked by hand and favourites; a family book to print
from lineagebox.web.routes import face_checks, camera_clock  # ages to check, faces missed; camera clocks
from lineagebox.web.routes import suggested_dates  # scans dated by when they were scanned
from lineagebox.web.routes import first_run  # the first steps on a new LineageBox, on an admin's Home
from lineagebox.web.routes import language  # the language picked in this browser (i18n.py)
from lineagebox.web.routes import private  # Cleanup → Private papers: identity cards, tax forms and the like to hide

MODULES = (browse, details, places, gedcom, reading, family, tours, faces, naming, cleanup,
           photos, comments, folders, status, settings, files, uploads, sharing, home, signin, passkeys, accounts,
           frame, news, phone, stories, ai, tags, albums, book, face_checks, camera_clock,
           suggested_dates, private)
MODULES += (first_run,)
MODULES += (language,)
