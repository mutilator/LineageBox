# Access control

How LineageBox decides who may see and change what, and how that is enforced in the code. For setting accounts up, see
[family accounts](family-accounts.md). For reaching LineageBox from outside, see [remote access](remote-access.md).

## Without family accounts

The `gate` middleware in `lineagebox/web/app.py` answers only:

- clients whose address is in `allowed_networks` (every private range by default);
- requests whose `Host` is an address, a home-network-only name (`nas`, `*.lan`, `*.local`, `*.home.arpa`), or one of
  `allowed_hosts`. This stops DNS rebinding.

Behind a reverse proxy, the client's address and scheme come from `X-Forwarded-For` and `X-Forwarded-Proto`, but only
when the request comes from one of `trusted_proxies`. Every POST also needs `X-Requested-With: lineagebox`
(`_from_app`). A cross-site form can't set that header.

There is no sign-in. Everyone on the home network is an admin.

## With family accounts

### Signing in

- **Sign in with Google** is OpenID Connect, using the authorization code flow with PKCE, state and nonce. LineageBox
  swaps the code for the ID token directly with Google over TLS, so the token can be trusted without checking its
  signature (OIDC Core §3.1.3.7). No JWT library is needed. LineageBox checks the issuer, audience, expiry, nonce and
  `email_verified`.
- The email must belong to an account an admin added. There is no self sign-up.
- **Sessions:** a random 32-byte token in a cookie (HttpOnly, Secure over HTTPS, SameSite=Lax). The catalog keeps only
  the token's SHA-256, with the account, when it was made and last used, the IP and the browser. A session lasts
  `session_days` (90) from last use. Disabling or removing an account ends its sessions.
- **Local codes for admins:** TOTP (RFC 6238, 6 digits, 30 s), for signing in when Google can't be reached. Only from
  an `allowed_networks` address, only for admins. At most 5 tries per address and email in 15 minutes, and 10 per
  email from all addresses together, so many devices on the home network can't share out the guessing.
- With accounts on, everything except `/signin`, `/auth/*`, `/static/*` and `/healthz` needs a session, at home too.
  The host-name check stays.

### Hardening

- Rate limits on sign-in, and on costly actions: looking for faces again, uploads, and zip downloads (30 an hour per
  member).
- Before anyone has signed in, a request may send at most 64 KB, with its length given up front. FastAPI reads a
  whole body before any check, so without this one large request from the internet could fill the memory.
- The sign-in page never shows text taken from its own address, and after signing in LineageBox only goes to a page of
  its own (no other site, and no tabs or line breaks that a browser would drop).
- The Settings page refuses host names for a whole top-level domain (`*.com`) and reverse-proxy networks wider than
  /8, either of which would undo the checks above.
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and `X-Frame-Options: DENY` on every response,
  and HSTS over HTTPS when accounts are on.
- A `Content-Security-Policy` on every response: scripts only from the site's own files (no page has an inline
  script; `tests/test_access_web.py` checks the templates), pictures from the site and the map's tile server, and no
  plugins, `<base>`, framing or forms sent elsewhere. A caption or name that ever slipped past escaping still could
  not run as script.
- Thumbnails, previews, frames, face crops, originals, videos and every download (zips, GEDCOM) are sent
  `Cache-Control: private`, so no shared cache keeps a copy for someone else.
- Originals are sent as attachments unless they are a photo or video type, always with nosniff. An uploaded "photo"
  could be an SVG or HTML file that would otherwise run as the person viewing it.
- Sign-ins and failed sign-ins go in the activity log.
- The catalog keeps only sessions' SHA-256, but it does hold the Google client secret and the admins' code secrets.
  Its backups must be kept as private as the photos.

## Who a member sees

`access.py` works this out as a pure function over `family.Graph`. For a member who is person P, the people are:

1. P;
2. P's ancestors, all the way up;
3. everyone descended from those ancestors (all blood relatives);
4. the partners of everyone in 1–3, and those partners' children (but not the partners' parents or siblings);
5. former partners of 1–3, only when they share a child with someone in the tree;
6. P's current partner's side, by the same rules;
7. people the member added, and groups they made with the people in them;
8. the account's exceptions, set by an admin: a person or a branch, added or taken away.

Siblings entered without parents share a stand-in parent (a negative id), so they still count as siblings. Pets,
friends and other relationships are outside the tree unless the member added them.

The photos are those with a face of a visible person (named by hand or by recognition, not merely suggested), or a
visible person added to the photo by hand, plus the member's own uploads, minus photos and folders an admin hid.
Everything else follows from these two sets: tags, places, events, documents, tours, search and counts.

Each account's viewer is kept in memory. It is worked out again right after a change by anyone (any successful POST),
and otherwise at most every 30 seconds, so the worker's new recognitions appear.

## How it is enforced

There are two layers, so one mistake doesn't leak photos.

### Route classes

Each module in `lineagebox/web/routes/` gives its routes a class, in `ACCESS` (gathered into `ROUTES` in `app.py`):

- `public`: sign-in, static files, the health check;
- `member`: any signed-in account, filtered to what they can see;
- `admin`: admins only.

A route without a class answers 500, and `tests/test_access_web.py` fails, so a new page can't be forgotten. Routes
that take an id check it is visible and answer 404 when it isn't, the same as "doesn't exist", so ids can't be probed.
The by-content URLs (`/t/`, `/p/`, `/frame/`) look the SHA-256 up in the viewer's photo set.

### Scoped connections

For a member, the `con` dependency gives a database connection with temporary views named like the tables they hide
(`access.scope`, the tables in `SCOPED`):

```sql
CREATE TEMP VIEW image  AS SELECT * FROM main.image  WHERE id IN temp.seen_image;
CREATE TEMP VIEW face   AS SELECT * FROM main.face   WHERE image_id IN temp.seen_image;
CREATE TEMP VIEW person AS SELECT * FROM main.person WHERE id IN temp.seen_person;
-- and file, folder, image_tag, tag, image_person, analysis, transcript, video_frame, event, parent, partner,
-- sibling, relation, person_name, person_group, document, citation, tour, ...
```

SQLite resolves unqualified names in the temp schema first, so every existing query reads only the member's rows
without being rewritten. Full-text search joined to `image` is filtered the same way. Setting up the views took 6 ms
in a test on 42,000 synthetic photos. A write to one of the views fails instead of reaching hidden rows.

In a route:

- `v=Depends(viewer)` is who is asking;
- `c=Depends(con)` is the viewer's connection, filtered for a member;
- `w=Depends(writer)` is for changes. For an admin it is the same connection, and for a member one that sees
  everything.

Check a member's ids on `c` (or with `_people_or_404`, `_images_or_404`, `_face_to_change`), then write on `w`.
Derived data (the search index, names in videos) must be worked out on `w`, never on a member's views. Admin
connections and the worker get no views.

Face suggestions are worked out against everyone, then shown only when the likeliest person is someone the member can
see.

### Members' changes

- Every change a member makes goes through `changes.tracking`. It records what was replaced (`change_log`), so an admin
  can undo it from Cleanup → Recent changes. An undo that would overwrite a later change is skipped, and says so.
- Every account's people are worked out before and after each family change. A change that lets any account see more
  is marked, with whom each account gained.
- Members' renames never merge two people, and a typed name is looked up only among the people they can see. Merging
  is for admins (Cleanup → Same person?).

### Uploads

- One file per request, streamed to disk as `.<name>.part` and renamed once complete. The scan only takes known
  extensions, so a half-written file is never ingested.
- The extension must be a known photo or video type, and the first bytes must match it. Names are cleaned part by
  part: no path separators, no `..`, no control characters, at most 200 characters. Files are limited to
  `upload_max_gb`, and the share keeps a margin of free space.
- Uploads go to `<watched folder>/<uploads_folder>/<person id>`, and the account is tied to that folder, so merging or
  renaming people never cuts a member off from their uploads.
- Zip downloads are streamed, limited to 2,000 files or 10 GB, and hold only photos the viewer can see.
- By design, an upload with exactly the same bytes as a photo already in the catalog *is* that photo, so the member
  who uploaded it sees it. That includes its description, tags and the names on it (people outside their branch
  without a link), and it applies even to a photo an admin hid. They already have the file, but that is more than
  the file itself says.

## Tests

`tests/test_access.py` checks the people rules on a made-up family (`tests/family_fixture.py`).
`tests/test_access_web.py` sweeps every GET route with every id, and every POST with hidden ids, for leaks. Run it
after adding a page or an API.
