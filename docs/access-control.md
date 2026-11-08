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
(`_from_app`). A cross-site form can't set that header. Two plain posts change nothing and do without it: the phone's
share sheet (`/upload/shared`, which only leads to Upload) and the ticked photos' zip (`/download`, a form, so that the
browser saves the file). `tests/test_access_web.py` fails for any other POST without the check, and for a GET that
changes the catalog.

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
- **Passkeys** (`passkeys.py`, WebAuthn through the `webauthn` package): discoverable credentials (resident keys) with
  user verification required, since the passkey alone signs in; attestation `none`. The relying party ID is the host
  of `public_url`, and the only origin accepted is `public_url`'s. Each answer's challenge (used once, kept in memory
  for 6 minutes and tied to the browser by a cookie), origin, RP ID hash, user presence and verification flags,
  signature and sign count (when not 0: synced passkeys send 0) are checked, and the user handle must be the
  account's, a random 32 bytes (never the email or the account's number, which SQLite can give again). Passkeys are
  made only when HTTPS is on, `public_url` is a name and that name is listed exactly in `allowed_hosts`, with no
  wildcard. The first one comes from an admin's link: one use, 7 days (24 hours for an admin's account), only its
  SHA-256 kept, and its token after the `#`, which never reaches a server's log. More are added by someone signed in
  within 15 minutes of signing in (3 hours after the account's first sign-in). Taking a passkey away ends the
  sessions it started. 30 starts per address in 15 minutes.
- **Local codes for admins:** TOTP (RFC 6238, 6 digits, 30 s), for signing in when Google can't be reached. Only from
  an `allowed_networks` address, only for admins. At most 5 tries per address and email in 15 minutes, and 10 per
  email from all addresses together, so many devices on the home network can't share out the guessing.
- With accounts on, everything except `/signin`, `/auth/*`, `/static/*`, `/healthz`, and the web manifest and service
  worker (`/manifest.webmanifest`, `/sw.js`, which a phone asks for without its cookie and which hold nothing private)
  needs a session, at home too. The host-name check stays.
- **Device links** (photo frames, `devices.py`, `web/frames.py`): a random 16-byte token in the address,
  `/d/<token>/...`, of which the catalog keeps only the SHA-256. `frames.DevicePath` (ASGI, before the gate) takes the
  token off the path, in place, so routes and the server's log see the address without it; the gate looks it up (kept
  10 seconds, at once anew after any change on the site, so **Turn off** works at once), and ignores any session
  cookie that comes along: such a request is never an account. A revoked or made-up token answers 404 for everything
  under it, and so does a link made for the home network asked for from elsewhere; with accounts off, links work
  at home only, like everything. Device links need no cookie, so they work in a dashboard's frame and on old browsers;
  their responses may be framed by any page (`frame-ancestors *`, no `X-Frame-Options`), as they change nothing and
  have nothing to click.

### Hardening

- Rate limits on sign-in, and on costly actions: looking for faces again, uploads, and zip downloads (30 an hour per
  member).
- Before anyone has signed in, a request may send at most 64 KB, with its length given up front. FastAPI reads a
  whole body before any check, so without this one large request from the internet could fill the memory.
- The sign-in page never shows text taken from its own address, and after signing in LineageBox only goes to a page of
  its own (no other site, and no tabs or line breaks that a browser would drop).
- The Settings page refuses host names for a whole top-level domain (`*.com`) and reverse-proxy networks wider than
  /8, either of which would undo the checks above.
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and `X-Frame-Options: DENY` on every response
  (but a photo frame's device link, above), and HSTS over HTTPS when accounts are on.
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
  Its backups must be kept as private as the photos. Of passkeys it keeps only the public keys.

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
visible person added to the photo by hand, plus the member's own uploads, minus photos and folders an admin hid. Then
come the photos other accounts shared with them (below). Everything else follows from these two sets: tags, places,
events, documents, tours, search and counts.

### Shared photos

An account can share a folder, a photo, a tour or an album with one account or with everyone (`share`,
`sharing.py`). `access.shared_with` adds to a member's photos:

- a shared folder's photos, in it or below it, now and later;
- a shared photo;
- a shared tour's photos, as kept in `tour_photo`;
- a shared album's photos, as kept in `album_passed`.

A share counts only while it holds (`access.active_shares`): the account that shared it isn't switched off, and what it
shares is still that account's to share. For a member, that means a folder in their uploads folder, a photo they
uploaded (in that folder, or recorded in `upload`) and their own tour or album. This is checked every time a view is worked out,
not only when the share is made, so a share outlives neither a change of role nor an upload an admin moved into the
library. Photos an admin hid are taken out of what is shared, the sharer's own uploads included.

A tour picks its photos afresh each time it opens, from what its maker sees. Those it is shared with see its photos as
picked for the maker. They are worked out on a connection scoped to the maker's view *without* what was shared with the
maker (`access.compute(..., shared=False)`), so nobody passes on what someone else shared with them only. They are kept
in `tour_photo` whenever the tour is saved, shared or opened (`routes/tours.py` `shown`), and a change to them makes
every view be worked out again.

An album (`albums.py`) is shared the same way. Its photos were picked by hand, so nothing is picked again: of its
photos, those its maker may pass on (seen on the maker's view without what was shared with them; for an admin's
album, all of them) are kept in `album_passed` whenever the album is changed, shared or opened, and only those are
added for whoever it is shared with. An album can only be filled from photos its maker sees: every id is checked on
the maker's own connection before it is written. A member's connection holds only the albums that are theirs or
shared with them (`album`), and of each only the photos they see (`album_photo`); only an album's maker or an admin
changes it, renames it, shares it or deletes it.

Members see only their own rows of `share`, and no rows of `tour_photo` or `album_passed`. Which accounts a member may share with is
limited to those of people they can see, so the list of accounts tells them nothing new.

Each account's viewer is kept in memory. It is worked out again right after a change by anyone (any successful POST),
and otherwise at most every 30 seconds, so the worker's new recognitions appear.

## How it is enforced

There are two layers, so one mistake doesn't leak photos.

### Route classes

Each module in `lineagebox/web/routes/` gives its routes a class, in `ACCESS` (gathered into `ROUTES` in `app.py`):

- `public`: sign-in, static files, the health check, the web manifest and the service worker;
- `member`: any signed-in account, filtered to what they can see (also `/api/language`, which only sets this
  browser's language cookie);
- `admin`: admins only. The menu (`base.html`) shows a member only `member` pages; the first steps on Home and their
  `/api/first-run/hide` are admins' (`first_run.py`, worked out on the admin's own connection).

A route without a class answers 500, and `tests/test_access_web.py` fails, so a new page can't be forgotten. Routes
that take an id check it is visible and answer 404 when it isn't, the same as "doesn't exist", so ids can't be probed.
The by-content URLs (`/t/`, `/t2/`, `/p/`, `/frame/`) look the SHA-256 up in the viewer's photo set. `/t2/` (a
thumbnail at twice the size, for sharp screens) is checked before anything is made: a 2× tile is never made, or
served from the ones kept, for a photo the viewer can't see.

What grids' thumbnails say to screen readers (their alt text: who is in the photo) is read on the viewer's own
connection (`web/tiles.py`), so a member's thumbnails name only the people they see, as the rest of the page does.
A long list loaded a piece at a time (`?skip=`, `?upto=`, `?piece=1`) is the same route, with the same checks, for
each piece.

A **device link** is a level of its own, below every class: `app.route_gate` answers it 404 on every route but
`frames.ROUTES` (the frame page `/photo-frame`, its list `/api/photo-frame`, and `/p/{sha}` and `/play/{image_id}`),
whatever that route's class. Its viewer (`access.device`) is no account and sees only the photos and videos on its
frame's list: the link's one view worked out as the account it was made for sees it (memories), as a tour's maker
does, or as an album's maker may pass it on (`frames.PICKERS`), less every photo an admin hid (as no share passes one
on), kept 10 minutes and anew after any change. An album link shows nothing once its album is deleted, even after a
newer album is given the same number (SQLite gives the highest one again): an album made after the link is not its.
Its connection is scoped to those photos, with no people, accounts, tours, shares or events. A new route is closed to device links unless it is added to `frames.ROUTES`;
`tests/test_access_web.py` sweeps every GET route with every id, and every POST, through a device link.

### Scoped connections

For a member, the `con` dependency gives a database connection with temporary views named like the tables they hide
(`access.scope`, the tables in `SCOPED`):

```sql
CREATE TEMP VIEW image  AS SELECT * FROM main.image  WHERE id IN temp.seen_image;
CREATE TEMP VIEW face   AS SELECT * FROM main.face   WHERE image_id IN temp.seen_image;
CREATE TEMP VIEW person AS SELECT * FROM main.person WHERE id IN temp.seen_person;
-- and file, folder, image_tag, tag, image_person, analysis, transcript, video_frame, event, parent, partner,
-- sibling, relation, person_name, person_group, document, citation, comment, story, video_chapters, tour, ...
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
see. The face-naming queue (People → Name faces) holds only faces in photos the member sees, and suggests only people
they see.

A Live Photo's clip is added to a member's photos whenever they see its still (`access.compute`), so it plays for them;
it is listed nowhere else.

Captions and comments are read through the scoped `comment` view, so a member sees only those on photos they see. Who
wrote one is shown by name only when that is someone in the member's part of the family (or themselves); anyone else
is "a family member".

Recorded stories (`stories.py`) are read through the scoped `story` view: a photo's with the photo, a person's with
the person, and one about a life event only while the member also sees that event (a couple's event: both partners).
Their sound is served by `/story/{id}` only through that view, so a story about someone hidden answers "not found",
like the person. Who recorded one is named as for comments. A photo's stories go into its search row (on `w`), so a
member's search finds them only on photos they see.

Written sources (`documents.py`) are read through the scoped `source_citation` view: citations for people the member
sees, or for life events they see; `source` holds the sources those cite, and the ones the member added. So a source
cited only for someone hidden is not there for them, nor its title among the ones the Sources box suggests; one cited
for both is, with only the citations they see. `tree_media` (files another family tree linked to someone) follows the
person or the source. A person's life by age (`/person/{id}/life/{group}`) and the faces to choose a portrait from
(`/person/{id}/portraits`) are read on the member's own connection: only their photos, and nobody they don't see.

Favourites (`favourites.py`) are each account's own: the scoped `favourite` view holds only the member's hearts, so
their tours and memories weigh only what they love (an admin's weigh everyone's), and nobody sees whose heart is on a
photo. A family book (`book.py`, `/book/person/{id}`, `/book/branch/{id}`) is made on the member's own connection:
the people, life events, documents and photos in it are only those they see, and a branch holds only the descendants
they see. A tour or album saved as a video (`slideshow.py`) keeps the slides as the one who asked saw them, and the
scoped `slideshow_video` view holds only their own, so the file is never another viewer's.

New for you (`outbox.py`): events are written once, when something happens, but checked against the viewer each time
they are shown (`outbox.for_viewer`), never only when written. The scoped `outbox` view holds only events for the
member or for everyone, not of their own doing, not alerts, and not about a photo outside their set; then each kind is
checked again: a share must still hold for them (`access.active_shares`), a photo must be in their set and still on
the share, a "Who is this?" face must still be unnamed, asked and not skipped by them, a comment must still be there;
a "new photos" event shows only the photos they see, less their own uploads. Alerts are for admins. The count at the
top of each page is worked out the same way, against the viewer, on a connection of its own.

The service worker (`static/sw.js`) keeps only the site's own static files of the running version and an offline
page: pages, photos and API answers always go to the server, so a signed-out or switched-off account sees nothing
from the phone's cache. Photos shared to LineageBox from a phone's share sheet are kept on that phone until Upload
sends them through the usual upload (which needs a session); signing out forgets them.

### Members' changes

- Every change goes through `changes.tracking`: a member's, an admin's, and anyone's at home without accounts. It
  records what was replaced (`change_log`), so an admin can undo it from Cleanup → Recent changes. An undo that would
  overwrite a later change is skipped, and says so. A member can undo their own change themselves for 30 minutes
  (`/api/changes/{id}/undo-own`, `changes.undo_own`); after that, and for anyone else's, it is an admin's.
- Anyone who sees a photo may write its caption or a comment; a comment is changed or taken away only by whoever wrote
  it, or an admin (`comments.may_change`).
- Anyone who sees a photo or a person may record a story about it (or one of the person's life events, checked on
  `c`); its words are corrected, or it is taken away, only by whoever recorded it, or an admin
  (`stories.may_change`). Taking one away only hides it for a month, so the undo can show it again. Anyone who sees a
  video or a recording may correct a line of its transcript (`speech.correct`, kept in `transcript.edits`). A member's
  recordings count against the same hourly limit as uploads.
- Anyone who sees a person may write their life story and notes, choose their portrait from the faces named as them
  in photos they see, cite a written source for them or one of their life events (checked on `c`: the person's own
  events), change a source's details, and say how a child belongs to a parent (birth, adopted, foster), as for their
  other details; all through `changes.tracking`, with an Undo.
- Every account's people are worked out before and after each family change. A change that lets any account see more
  is marked, with whom each account gained.
- Members' renames never merge two people, and a typed name is looked up only among the people they can see. Merging
  is for admins (Cleanup → Same person?), and so is correcting what a folder means (its page), and moving a camera's
  dates (Cleanup → Camera clocks, `routes/camera_clock.py`, kept through `changes.tracking`), and renaming, merging or
  removing a tag across the library (`routes/tags.py`, `tagging.rename`; `tag_alias` is scoped to nothing).
- Search by meaning (`embeddings.py`): every photo's vector is kept in memory for all viewers, but what it finds is
  then read on the member's own connection, with the filters, so only their photos come back; the words that name
  people still have to match through their views. **More like this** (`/image/{id}/like`) is the same, and answers
  404 for a photo they don't see. `embedding` is scoped to their photos.
- **Describe again** (`/api/images/{id}/describe-again`, `/api/selection/describe-again`): a member's photo ids are
  checked on `c` (one they don't see refuses the whole ask), the model they name is ignored (the Settings page's is
  used), and the ask is written on `w` (`describe_request`, scoped to their own). Listing Ollama's models is admins'.
- **Places by hand** (`routes/places.py`): a member puts a photo on the map (`/api/images/{id}/place`) only when
  they see it (checked on `c`), written on `w` (`image_place`) through `changes.tracking`. `image_place` and
  `folder_place` are scoped to their photos and folders, so the map (`places.map_data`) reads only theirs. Placing a
  folder (`/api/folders/{id}/place`) and writing into a photo file (`/api/images/{id}/write-file`) are admin routes,
  and `/api/places/target` answers 404 to a member for a folder or a photo they don't see. The town list
  (`/api/towns`) holds no family data.
- **XMP sidecars in downloads** (`sidecars.packet`, `/original/{file_id}/xmp`, the zip routes): made on the
  member's own connection, so a face named as someone outside their views is left out of the names, as are places by
  hand on folders they don't see. The tables of other apps' metadata (`metadata_read`, `imported`) and of sidecars
  (`sidecar`, `sidecar_stale`) are read only by the worker and admins' pages.
- Cleanup → Blank and blurry and Similar shots (`routes/cleanup.py`, `_own_uploads`): a member's list holds only the
  photos every present copy of which is in their uploads folder, read on `c`; moving them goes through
  `/api/images/remove`, which takes only their uploads' files. Keep and Move all are admins' routes.
- Suggestions worked out against everyone (who a face or a group of faces looks like: `people.suggest_for_face`,
  `people.looks_like`, on `w`) are shown to a member only for people they see. Confirm faces' **Ages to check** and
  **Faces missed** read the member's own views (`c`); marking a face's age or a photo's faces as fine is for admins.
  **No faces found** (`/cleanup/no-faces`, looking again and setting aside) and Suggested dates' **Looks too young**
  are admins' only.

### Uploads

- One file per request, streamed to disk as `.<name>.part` and renamed once complete. The scan only takes known
  extensions, so a half-written file is never ingested.
- The extension must be a known photo or video type, and the first bytes must match it. Names are cleaned part by
  part: no path separators, no `..`, no control characters, at most 200 characters. Files are limited to
  `upload_max_gb`, and the share keeps a margin of free space.
- Uploads go to `<watched folder>/<uploads_folder>/<person id>`, and the account is tied to that folder, so merging or
  renaming people never cuts a member off from their uploads.
- Zip downloads are streamed, limited to 2,000 files or 10 GB, and hold only photos the viewer can see, each with its
  XMP sidecar when the family gave it something (worked out on the viewer's own connection).
- Members move their uploads to the Removed folder, a whole folder at a time (`/api/folders/{id}/remove`) or the ticked
  photos' files in one folder; only in their uploads folder. A removed folder is no longer shared.
- By design, an upload with exactly the same bytes as a photo already in the catalog *is* that photo, so the member
  who uploaded it sees it. That includes its description, tags and the names on it (people outside their branch
  without a link), and it applies even to a photo an admin hid. They already have the file, but that is more than
  the file itself says. Sharing it passes that photo on, but never a hidden one.

## Tests

`tests/test_access.py` checks the people rules on a made-up family (`tests/family_fixture.py`).
`tests/test_access_web.py` sweeps every GET route with every id, and every POST with hidden ids, for leaks. Run it
after adding a page or an API. Its `swept` fixture adds what the newer features keep (recorded stories, a written
source, a place by hand, comments, a device link), the GET sweep also types into search letter by letter, adds every
filter, walks each kind of ←/→ list and every group of each life by age, asks for every long list's pieces and its
photos to tick (`?select=1`, `?piece=1`), and checks that no GET changed the catalog;
the POST sweep covers captions, comments, stories, sources, portraits, the naming queue, describing again, places by
hand, the admins' tools and undoing someone else's change. It also sweeps every route through a device link (only the
frame and its photos answer), turns a link off and asks for one from outside the home network;
`tests/test_frames.py` sends crafted addresses under a link (dot segments, encoded slashes, a second token). `tests/test_sharing.py` checks what sharing
lets through, and what it doesn't; `tests/test_news.py` what New for you tells each account; `tests/test_frames.py`
what a frame shows.
