# Development

How to set up LineageBox for working on the code, run the tests, and fit changes in with the rest. Read
[architecture](architecture.md) first for how the pieces fit together.

## Setting up

```sh
git clone https://github.com/mutilator/LineageBox lineagebox && cd lineagebox
python -m venv .venv
.venv/bin/python -m pip install -e ".[faces,dev]"      # Windows: .\.venv\Scripts\python.exe -m pip install ...
cp config.example.toml config.toml                     # point media_root at a folder of test photos
.venv/bin/python -m lineagebox serve                       # http://127.0.0.1:8000
.venv/bin/python -m lineagebox worker                      # in another terminal
```

You need Python 3.12. ffmpeg and ffprobe are needed for video work, and ExifTool for writing rotations into files.
Tests that need them skip without them. `faces` adds InsightFace and onnxruntime. On an NVIDIA GPU, swap in
`onnxruntime-gpu[cuda,cudnn]` ([from source](quick-start/from-source.md#1-install)).

Use a small copy of a photo library, not your real one, while you work. Cleanup moves files, and the AI stages take a
long time on a big library.

To build and run the Docker image locally: `LINEAGEBOX_IMAGE=lineagebox-local docker compose up -d --build` (a name of
its own, so the updater service doesn't swap it for the published image).

## Releases

The version is `__version__` in `lineagebox/__init__.py`, the only place it is written. To release:

1. Set `__version__` to the new version (`1.3.0`) and commit it.
2. Tag that commit `v1.3.0` and push the tag. A pre-release is `v1.3.0-rc.1`.
3. CI runs the tests and refuses a tag that doesn't match `__version__`. It publishes both images as `1.3.0`, `1.3` and
   `latest` (a pre-release only as itself), and then makes the GitHub Release, with notes from the pull requests.
   Installs find it within a day and offer the update.

Pushes to `main` publish `main` and `sha-<commit>` images, never `latest`. A change to the catalog's tables is a new
`lineagebox/migrations/NNN_*.sql` or `.py`; never change one that has been released. Installs copy the catalog before
applying new ones, and refuse a catalog newer than they know.

## Tests

```sh
.venv/bin/python -m pytest -q                               # the whole suite, about a minute
.venv/bin/python -m pytest tests/test_people.py -k together -q   # one test
```

- The tests build a small sample library in a temporary folder (`tests/conftest.py`: the `library`, `cfg` and `con`
  fixtures, and `rel()` for catalog paths).
- The web app is called with `TestClient(webapp.create_app(cfg), client=LAN)`. A client address is needed because
  LineageBox refuses clients that aren't on a private network.
- The face model is faked by monkeypatching `faces.load_model` and `faces.ensure_models`, as in
  `tests/test_worker.py` and `tests/test_people.py`. No test downloads models or needs a GPU. Faces are made-up
  embeddings: `like(v)` is another photo of the same person, and `toward(v, cosine)` (`tests/test_face_checks.py`) a
  face that scores about that much against them, for testing a threshold. Where no file is opened, photos can be rows
  only (`photo()` there).
- Ollama and Whisper are faked too, so no test needs a running service. Search by meaning's model is faked in
  `tests/test_meaning.py` (`FakeEmbed`: words of one idea point the same way, so "kids sledding" is close to
  "children on a snowy hill"); `embeddings.forget()` drops what the module keeps in memory between tests. The default
  `Config` has no embedding model, so other tests never ask for one.
- `tests/family_fixture.py` is a made-up family for the access tests. `tests/test_access_web.py`'s `site` fixture
  adds accounts, tours, albums (with a heart), a share and New for you events about photos each member may or may not see; `_urls` gives
  every GET route with every id (a route with a new kind of id needs its ids there, and a page whose ids come in the
  query string needs its addresses added to `queries`). The `swept` fixture adds stories, a source, a place by hand,
  comments and a device link for the two sweeps, which also check that no GET changed the catalog (`_snapshot`: add a
  new table of the family's there). The same sweep runs through a photo frame's device link (`/d/<token>` + each
  address), where only `frames.ROUTES` may answer. A new POST without `_from_app` fails
  `test_every_change_comes_from_the_apps_own_scripts` unless it is one of `PLAIN_POSTS`.
- A GEDCOM round trip into an empty catalog: a second `Config` with another `data_dir` and `db.connect` on it, as
  `tests/test_life_story.py` does (`test_an_export_comes_back_whole_in_another_catalog`), for 7.0 and 5.5.1 both.
  Run it, and `tests/test_gedcom.py` and `tests/test_family_features.py`, after any change to `gedcom*.py`: other
  programs read what LineageBox writes.
- A device link is made in a test with `devices.make(con, name, view, ...)`, which returns its token. Two TestClients
  of one app share its memory (revoking through one is seen at once by the other); two apps don't.
- Recorded stories are sent as a WAV made with Python's `wave` (`tests/test_stories.py`), so they need no ffmpeg;
  Whisper hearing one is faked by monkeypatching `speech.video.audio_wav` and `speech.Whisper.transcribe`. Sound
  recordings and long tapes are made by ffmpeg's test sources (`tests/test_tapes.py`: a sine tone tagged as a song or
  a memo, and an 11-minute tape of test cards at 64×48, 2 frames a second, with a blank between two of them).
- Albums and the family book use `tests/test_sharing.py`'s `site` fixture (Cora, Tina, Bart and Sam the admin) and
  its `client`, `share` and `uploaded` helpers (`tests/test_albums.py`). A slideshow video is made in the test by
  calling the worker's stage, `slideshow.make_waiting(con, cfg)`, with each photo held 1 s; the test skips that part
  without ffmpeg. Slides are read from a page's `<script id="slides">` JSON, the title card first.
- Other apps' metadata (`tests/test_imports.py`) is made in the test: XMP through Pillow's `xmp=` when saving a JPEG
  (`packet()` writes MWG regions as digiKam does), IPTC as an APP13 block put in by hand, and `.picasa.ini`,
  `contacts.xml` and Takeout JSON as text beside the photo. Faces are rows (`faces()`), with the photo marked as
  looked at for faces, so imported names find them. XMP sidecars (`tests/test_sidecars.py`) are read back with
  `xmp.parse`; with ExifTool installed, they are also read by ExifTool and written into a file
  (`writeback.write_family`). A read-only share is faked by making `sidecars._write` raise `EROFS`. Hostile files
  beside the photos (JSON of the wrong shape or nested too deep, a `.picasa.ini` of garbage, thousands of keywords)
  must leave the stage reading the next photo. The copies service's endless loop (`copies.run_forever`) is ended by
  a `time.sleep` that raises a `BaseException` of the test's own.
- Long lists in pieces (`tests/test_pieces.py`): the `big` fixture puts 450 photos into a folder as catalog rows only
  (no files: the pages don't open them), dated from 2001 into 2004, so a test checks the pieces, the scrubber and
  ticking without ingesting them; `monkeypatch.setattr(pieces, "PIECE", 50)` cuts a year into pieces. Sharp tiles are
  made from the sample library's previews.
- The worker on a small board (`tests/test_pipeline_edges.py`): a stage made to raise must leave the rest of the pass
  running (each logs "<stage> failed: ..."); a stage's share of the pass is tested with `time.monotonic` replaced by a
  clock the test moves on, and `worker.run_forever` is ended by a `run_once` that raises a `BaseException` of the
  test's own. ffmpeg failing midway is a real encoder killed between two slides (`encoder.p.kill()`), and a frame
  that takes too long a `video.frame` that raises `video.TimedOut`. Raw files are faked with `tests/test_media.py`'s
  `FakeRaw`.
- One test decodes a real Nikon NEF and runs only when `LINEAGEBOX_SAMPLE_NEF` names one. Video tests need ffmpeg, and
  the rotation-writing tests and those that read sidecars with ExifTool or write into a file need ExifTool.

CI (`.github/workflows/image.yml`) installs `docker/requirements.txt`, ffmpeg and ExifTool, and runs the suite. Then it
builds the image for linux/amd64 and linux/arm64, and on pushes to `main` and version tags it publishes the image to
GitHub's container registry. Its Python packages are pinned in `docker/requirements.txt`, and all of them have
prebuilt arm64 wheels.

## Conventions

- **Prose:** docstrings, comments and UI text use plain words and say *why*. UI text is written for non-technical
  family members. Keep lines to about 120 characters. On pages members see, keep what only an admin can act on (file
  formats, codecs, model and program names, "the worker", raw error messages) behind `viewer.admin`, and mark keyboard
  hints `kbd-hint` (hidden where there is no mouse). The answers about a face are **Not them** and **Don't know**
  everywhere. Colours come from the variables at the top of `app.css`; a new one goes in all three of its blocks (light,
  dark for the system, dark when chosen).
- **Nothing specific to one library.** No hard-coded names, places, years or tags. Thresholds and suggestions are
  rules, measured if need be, and worked out from the installer's own catalog. Use made-up names in tests and
  examples. A measurement goes into the comment by the threshold and into [design notes](design-notes.md), with what
  was measured on and how often it was right; the faces' ages and the date suggestions are examples.
- **People's edits always win.** Anything a person set by hand (`assigned_by = 'user'`, `source = 'user'`,
  `taken_at_source = 'user'`) must survive every re-run. See [architecture](architecture.md#peoples-edits-always-win).
- **Short transactions.** A long write locks the web UI out ("busy" 503s). Commit in batches.
- **Stages are incremental and resumable.** A new stage records its progress per photo, recovers from being stopped
  at any point, and never stops a pass over one bad file.
- **The photos are read-only** except for Cleanup, uploads, 720p copies and confirmed rotations and locations
  (`writeback.py`). Nothing else may write to the media root.
- **Line endings:** files are committed with LF (`.gitattributes`). Some working copies on Windows get CRLF. Keep a
  file's existing endings when you edit it with a script.
- Jinja's `tojson` sorts keys, so `data-body` JSON in rendered pages comes out in key order. Tests that look for it
  must expect that order.

## Text people read

Every word a page or script shows goes through one function, so that a translation can be added without touching the
code ([translating](translating.md); how it works: [architecture](architecture.md#other-languages)). English, the only
language shipped, comes out exactly as it is written. New text is written this way from the start:

- **In a template, between tags**: `{% trans %}Saved.{% endtrans %}`. Values go in by name:
  `{% trans name=person.name %}Photos of {{ name }}{% endtrans %}` (a plain name such as `{{ total }}` needs no
  binding). A sentence with a number has its plural: `{% trans count=n %}{{ count }} photo{% pluralize %}{{ count }}
  photos{% endtrans %}`, never `photo{{ "s" if n != 1 }}`. The text is HTML, kept with its tags. Whitespace control
  goes on the trans tags (`{%- trans %}`), and a `{% if %}` can't be inside one: put the whole sentence in each
  branch rather than a piece of it.
- **In an attribute or a Jinja expression**: `title="{{ _('Remove this link') }}"`,
  `{{ _('Open %(name)s', name=p.name) }}`, `{{ _('Save') if new else _('Change') }}`. `_()` gives plain text, escaped
  where it is shown, so it is also right inside `|tojson`; a % is written %%. With a number:
  `{{ ngettext('%(n)s photo', '%(n)s photos', count, n=count|number) }}`. Never join text with `~`: one message with
  its values in it, so a translation can put them where its language needs.
- **Words the code reads stay English**: what a button sends (`data-body`), hidden inputs, `<option value>`, and
  anything compared in a template (`{% if kind == "adopted" %}`). Translate the label shown beside them instead
  (`cleanup_family.html` keeps `word` for the API and shows `word_shown`).
- **Numbers, dates and sizes** through the filters: `{{ n|number }}` (never `"{:,}".format(n)`), `{{ x|number(1) }}`
  for a set number of decimals, `{{ iso|date }}` or `|date(false)`, `{{ bytes|size }}`, and `month_names[m]`,
  `weekday_names[d]` rather than `strftime("%B")`.
- **In a script**: `t("That didn't work: {why}", { why: err.message })`, and with a number
  `tn("{n} photo", "{n} photos", n)` (it fills `{n}` itself); `num(n)` writes a number. Pass a plain string to `t()`,
  not a template literal, so that pybabel finds it; HTML made in a script takes `attrText(t(...))`.
- **In Python** (help.py): `N_("...")` where a text is written into a table, and `gettext(text)` where it is shown.

**Messages from Python code** (each module's `errors.Problem`, `{"message": ...}` answers, activity lines, kinship words)
are English for now. To translate one later, call `i18n.gettext()` where the message is made, with `%(name)s` for its
values: `raise Problem(gettext("No one called %(name)s here.") % {"name": name})`. A request's language is set for
the whole request (the gate in `web/app.py`), so code that runs for a request speaks the visitor's language without
being told it; `pages.cfg` already reads every `.py` file for `gettext()` and `N_()`. Text the worker makes with no
request (activity lines, New for you's events) has to be kept as a message and its values, and translated when a page
shows it, as help.py's tables are.

`tests/test_i18n.py` renders a set of pages in a made-up language whose every message holds quotes and apostrophes,
and checks that each page keeps the tags and attributes, and the `data-body` and JSON, it has in English. Add a new
page to its list when it shows something the others don't.

## Adding things

### A page

1. Add the route to the module in `lineagebox/web/routes/` for that part of the site (`@router.get(...)`), and render
   it with `page(request, "x.html", ...)`. A new module goes in `MODULES` in `routes/__init__.py`.
2. Give the route a class in that module's `ACCESS` (`public`, `member` or `admin`). Without one it answers 500.
3. Add the template's help to `PAGES` in `lineagebox/web/help.py`. `tests/test_help.py` enforces it. A page with a
   link in the menu (`base.html`: up front, or under More) also gets a line in `MENU`, in the menu's order, which the
   help's "Every page" list shows; `test_every_page_list_matches_the_menu` checks an admin's and a member's.
4. For a member route, read with `c=Depends(con)`, the viewer's filtered connection. Check ids with
   `_people_or_404`, `_images_or_404` or `_face_to_change`.
5. Run `tests/test_access_web.py`. It requests every GET route with every id, as each kind of member, looking for
   leaks, and through a photo frame's device link. A device link opens a route only when it is in `frames.ROUTES`
   (`web/frames.py`); leave it out unless a frame needs it.
6. Something that would be news to someone (shared with them, asked of them, about their photos) is written once with
   `outbox.add(...)` where it happens, inside the change it is about; `outbox._Checks` says who may see each kind when
   it is shown.

### An API

- POST routes take `dependencies=[Depends(_from_app)]`, which requires `X-Requested-With: lineagebox`.
- Write on `w=Depends(writer)`, after checking a member's ids on `c`. Every change goes through `changes.tracking`
  (a member's, an admin's, or anyone's at home), so it shows on Recent changes and can be undone. Its `specs` name
  the rows the change can touch; `with changes.tracking(...) as kept:` gives the change's id after the block, and a
  route returns it as `"undo"` when someone may want to take it back at once: app.js then shows an Undo button.
- A route that should ask before acting (a rename that merges two people) answers `{"confirm": "..."}`; app.js asks
  and sends the same request again with `merge: true`, or with what the answer's `"again"` holds.
- Messages for the person go through app.js's `notice()` (never `alert()`), and errors as `errors.Problem`.
- Derived data (the search index, names in videos) is worked out on `w`, never on a member's filtered views.
- In the page, prefer `app.js`'s data attributes (`data-api`, `data-body`, `data-reload`, `data-remove`...) over new
  JavaScript.

### A setting

1. Add the field to `Config` in `lineagebox/config.py`, and its `(section, key)` to `KEYS`. The environment variable
   `LINEAGEBOX_<SECTION>_<KEY>` follows from that.
2. If the Settings page changes it, add a check to `_CHECKS` in `lineagebox/settings.py`, and the field to
   `settings.html`. The page shows one part (`data-part`) at a time, picked in the menu at the side
   (`_settings_nav.html`, whose links name each part's first panel): a new panel goes in a part, or in a new part
   with its own link.
3. Document it in [configuration.md](configuration.md), and in `docker/config.toml` and `.env.example` if installers
   are likely to set it.

### A migration

Add `lineagebox/migrations/NNN_name.sql`, or `NNN_name.py` with `upgrade(con, cfg)`, numbered after the last one. It
runs once, under the write lock, when the web app or worker next starts. Never change a migration that has been
released. Write it to work on a catalog of any size, in steps that don't hold the lock for long. Tell installers in the
release notes to back up their catalog first. Test it on a catalog of the version before, with rows in it:
`db.migrate(con, cfg, upto=N)` stops at version N (`tests/test_migrations.py`). A column added to a table that
`changes.py` keeps (`KEYS`) is fine: changes kept before it are undone in the columns they had.

## Project layout

```
lineagebox/                 the package (see architecture.md for the modules)
  migrations/           schema changes, applied in order
  data/                 data that comes with LineageBox: towns.tsv.gz, GeoNames' towns (towns.py; package data in
                        pyproject.toml, and in the image with the package)
  locale/               translations, a folder for each language (none shipped yet), and what pybabel reads
                        (pages.cfg, scripts.cfg): translating.md
  web/                  app.py, site.py, deps.py, common.py, help.py, routes/, templates/,
                        static/ (app.js, app.css, vendored Leaflet)
whisper_service/        the speech-to-text service: FastAPI in front of whisper.cpp, with its own Dockerfile
docker/                 the image's config.toml and pinned requirements.txt
tests/                  pytest suite
docs/                   documentation
compose*.yaml           Docker Compose: lineagebox, plus optional Ollama, Whisper and SMB files
Dockerfile              the lineagebox image (linux/amd64 and linux/arm64)
config.example.toml     settings for running from source
.env.example            settings for Docker
```

Temporary scripts belong in `scratch/`, which is ignored by git.
