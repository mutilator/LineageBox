"""The free-address service (ourplace.here): the server side of address.py, run by LineageBox's project for anyone.

It gives a LineageBox at home a name of its own under one domain (maple-river.ourplace.here), keeps that name
pointed at the house, and publishes the DNS check Let's Encrypt asks for when that LineageBox gets its own certificate.
There are no accounts: a name belongs to whoever holds its token. server.py is the web API (behind nginx), zone.py
the DNS host it writes to, words.py what names are made of. `lineagebox names serve` runs it (docs/remote-access.md).
"""
