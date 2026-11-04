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
  `tests/test_worker.py` and `tests/test_people.py`. No test downloads models or needs a GPU.
- Ollama and Whisper are faked too, so no test needs a running service.
- `tests/family_fixture.py` is a made-up family for the access tests.
- One test decodes a real Nikon NEF and runs only when `LINEAGEBOX_SAMPLE_NEF` names one. Video tests need ffmpeg, and
  the rotation-writing tests need ExifTool.

CI (`.github/workflows/image.yml`) installs `docker/requirements.txt`, ffmpeg and ExifTool, and runs the suite. Then it
builds the image for linux/amd64 and linux/arm64, and on pushes to `main` and version tags it publishes the image to
GitHub's container registry. Its Python packages are pinned in `docker/requirements.txt`, and all of them have
prebuilt arm64 wheels.

## Conventions

- **Prose:** docstrings, comments and UI text use plain words and say *why*. UI text is written for non-technical
  family members. Keep lines to about 120 characters.
- **Nothing specific to one library.** No hard-coded names, places, years or tags. Thresholds and suggestions are
  rules, measured if need be, and worked out from the installer's own catalog. Use made-up names in tests and
  examples.
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

## Adding things

### A page

1. Add the route to the module in `lineagebox/web/routes/` for that part of the site (`@router.get(...)`), and render
   it with `page(request, "x.html", ...)`. A new module goes in `MODULES` in `routes/__init__.py`.
2. Give the route a class in that module's `ACCESS` (`public`, `member` or `admin`). Without one it answers 500.
3. Add the template's help to `PAGES` in `lineagebox/web/help.py`. `tests/test_help.py` enforces it.
4. For a member route, read with `c=Depends(con)`, the viewer's filtered connection. Check ids with
   `_people_or_404`, `_images_or_404` or `_face_to_change`.
5. Run `tests/test_access_web.py`. It requests every GET route with every id, as each kind of member, looking for
   leaks.

### An API

- POST routes take `dependencies=[Depends(_from_app)]`, which requires `X-Requested-With: lineagebox`.
- Write on `w=Depends(writer)`, after checking a member's ids on `c`. A member's changes go through
  `changes.tracking`, so an admin can see and undo them.
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
release notes to back up their catalog first.

## Project layout

```
lineagebox/                 the package (see architecture.md for the modules)
  migrations/           schema changes, applied in order
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
