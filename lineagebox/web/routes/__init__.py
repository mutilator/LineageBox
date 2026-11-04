"""The web app's routes, one module per part of the site, each with its router and its routes' rules (ACCESS).
app.py includes them in MODULES' order: where two paths can match one address, the first one listed wins.
"""
from lineagebox.web.routes import (browse, details, places, gedcom, family, tours, faces, cleanup,
                                   photos, status, settings, files, uploads, home, signin, accounts)

MODULES = (browse, details, places, gedcom, family, tours, faces, cleanup,
           photos, status, settings, files, uploads, home, signin, accounts)
