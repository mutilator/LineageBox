# Architecture

How LineageBox is put together: the processes, the pipeline, the catalog, and the code. This is for developers and for
debugging. [Design notes](design-notes.md) explain why it is built this way, with the measurements behind the choices.

## The pieces

```
        photo folders (local or a network share; read-only except Cleanup, uploads, copies, rotations)
                                  │
     ┌────────────────────────────▼─────────────────────────────┐
     │ worker          python -m lineagebox worker                  │──► Ollama        descriptions, folder names
     │ scan → ingest → dedup → folders → faces → people →       │──► Whisper svc   video transcripts
     │ places → sharpness → check files → index → transcribe →  │
     │ describe                                                 │
     └────────────────────────────┬─────────────────────────────┘
                                  │
         data_dir:  lineagebox.db (SQLite, WAL) · thumbs/ · previews/ · frames/ · insightface/
                                  │
     ┌──────────────────┬─────────┴──────────────┐
     │ web              │ copies                 │
     │ python -m lineagebox │ python -m lineagebox       │
     │ serve            │ copies (720p videos)   │
     └──────────────────┴────────────────────────┘
```

- **web** (`lineagebox/web/app.py`) serves the UI with FastAPI and Jinja2 templates. It runs Cleanup moves and uploads.
- **worker** (`lineagebox/worker.py`) runs every stage for new photos, then waits for the next interval. It wakes early
  when someone presses **Check now** or saves Settings, and when Ollama comes back after being unreachable.
- **copies** (`lineagebox/copies.py`) makes 720p H.264 copies of videos browsers can't play. It runs on its own, because
  a copy takes about as long as the video, and the worker must not wait on that.
- **Ollama** and the **Whisper service** (`whisper_service/`) are separate HTTP services, on this machine or another.
  Each is optional.

All three LineageBox processes come from one Docker image and share the catalog folder. They coordinate through the
database: settings, the worker's state, and requests such as "run now".

## The pipeline

Each stage is incremental, idempotent and safe to stop. Progress is kept per photo: a bitmask in `image.stage`
(`STAGE_INGEST`, `STAGE_FACES`, `STAGE_LLM`), or rows in a stage's own table. Failures are recorded per photo and never
stop a pass.

Only a damaged file is recorded as broken for good. An item a stage can't do for a reason that may pass (a service's
5xx for it, a file unreadable just now, an ffmpeg timeout, an answer that isn't JSON) goes in the `retry` table
(`retry.py`): it is left out for a while, longer each time, and the Status page lists it. When ffmpeg isn't installed,
videos wait for it instead.

1. **scan** (`scan.py`) walks the watched folders and records `folder` and `file` rows. No file contents are read.
   Files that vanish are flagged `missing`, never deleted. A source that is empty or unreadable is treated as offline,
   and nothing in it is marked missing.
2. **ingest** (`ingest.py`, `media.py`, `video.py`) reads each new file once. It computes the SHA-256 (identical files
   become one `image`), EXIF, a perceptual hash, a 400 px thumbnail and a 2048 px preview. Videos get ffprobe
   metadata and sampled frames, and the clearest frame becomes the preview. Later stages use the previews, never the
   originals.
3. **dedup** (`dedup.py`) groups near-duplicates by perceptual hash. **folders** (`folders.py`) reads place, event,
   people hints and dates from folder names, by regex and the LLM. **dates** (`dates.py`) picks each photo's date
   from EXIF, else the folder, else the file name, else the file time.
4. **faces** (`faces.py`) runs InsightFace on the previews. **people** (`people.py`) recognises faces of named people
   (`identify`) and groups the rest with HDBSCAN (`cluster`).
5. **places** (`places.py`), **sharpness** (`quality.py`), **check files** (`check.py`: each original read whole once,
   for disk damage, up to 20 minutes a pass), the **search index** (`search.py`), **transcribe** (`speech.py`) and
   **describe** (`describe.py`).

Describing goes newest first, and stops after one interval so that a scan can run. New photos never wait behind a
backlog. Describing and transcribing keep to the describing schedule. Both wait while their service is unreachable,
and the worker checks every 5 minutes whether it is back.

Once a day, before ingest, previews, thumbnails and video frames that have gone missing get their photo's ingest bit
cleared, and ingest makes them again from the original (`check.rebuild_derivatives`).

## The catalog

SQLite in WAL mode, in `data_dir/lineagebox.db`, with FTS5 for search (`search_fts`). Face embeddings are 512 float32
values stored as BLOBs and compared in NumPy.

The main tables:

| Table | Holds |
|---|---|
| `folder`, `file` | Every folder and file under the watched folders. Paths are relative to the media root, with `/`. |
| `image` | One row per unique content (SHA-256): format, size, date and its source, GPS, stage bits, errors. |
| `face` | Box, landmarks, quality, embedding, cluster, person, and who assigned it (`assigned_by`). |
| `person`, `parent`, `partner`, `sibling`, `relation`, `event`, `person_name` | People and the family tree. Only facts are stored. |
| `analysis`, `tag`, `image_tag` | AI descriptions, kept per model and prompt version, and tags. |
| `transcript`, `video_frame` | Videos. |
| `settings` | Everything the Settings page changes, and the worker's state for the Status page. |
| `account`, `session`, `change_log`, `upload` | Family accounts. |

`VISIBLE` (`catalog.py`) is the SQL filter for images that still have a file present.

**Migrations** are `lineagebox/migrations/NNN_name.sql`, or `NNN_name.py` with an `upgrade(con, cfg)` function.
`db.migrate` applies those above `PRAGMA user_version`, each under the write lock. The web UI and the worker start
together and both run it, so the second process finds the work done. A migration is never changed once released.

**Write in short transactions.** A long write locks out the web UI, which then answers "busy" (503).

## People's edits always win

Re-runs never overwrite what a person decided:

- `face.assigned_by`: `user` (named by hand) beats `auto` (recognised). `rejected` and `not_a_face` faces are set
  aside.
- `face_not_person` records "this face is not that person". Recognition and suggestions respect it.
- `image_tag.source`: `user` marks a tag given by hand, and `not` marks one taken off for good.
- `image.taken_at_source = 'user'` marks a date set by hand. `date_text`, `date_from` and `date_to` hold its span, and
  the automatic date stays in `auto_taken_at`.
- The LLM never sees people's names. Search combines its descriptions with names from the face table, so renaming or
  merging people never needs the photos described again.

## Settings

Two layers ([configuration](configuration.md)):

- `config.py`: defaults, then a config file, then `data_dir/config.toml`, then `LINEAGEBOX_<SECTION>_<KEY>` environment
  variables. This gives a frozen `Config`.
- `settings.py`: the catalog's `settings` table, which overrides `Config` for everything the Settings page edits
  (`settings.current(con, cfg)`, each key validated by `_CHECKS`). The worker and the web UI read it again as they
  go, so a change applies within seconds.

## The web app

`lineagebox/web/app.py` puts the app together in `create_app(cfg)`: the checks every request goes through, the error
pages, and the routers in `web/routes/`, one module per part of the site (browse, faces, cleanup, settings...).
Templates are in `web/templates`, and `web/static/app.js` drives the pages.

- `web/site.py`: the `Site`, what the app keeps between requests (the config, rate limits, caches), found by routes
  through `s: Site = Depends(site)`.
- `web/deps.py`: what routes depend on: `viewer`, `con` (the viewer's connection), `writer`, and `page`, which renders
  a template.
- `web/common.py`: what more than one routes module uses: the request bodies, the access classes and the id checks.

- Pages render through `page(request, "x.html", ...)`. Every page template has an entry in `help.py` `PAGES`, which
  the help panel shows.
- Every route has an entry in its module's `ACCESS` (`public`, `member` or `admin`). A route without one answers 500.
- Every POST API depends on `_from_app`, which requires the header `X-Requested-With: lineagebox`. With SameSite
  cookies, that stops cross-site requests.
- `app.js` works through data attributes, so most UI needs no new JavaScript:
  - `data-api` (with `data-body`) posts. `data-reload`, `data-remove`, `data-go`, `data-show`, `data-html` and
    `data-busy` say what happens around it;
  - `data-key` is a keyboard shortcut, which the help panel lists;
  - `data-names` is the person-name autocomplete;
  - `form.select-form` with the `bulk_bar` macro is the tick-photos bar.
- Without family accounts, only private-network clients and allowed host names are answered. With them, every route
  but signing in needs a session. See [access control](access-control.md).

## Code map

| Area | Modules |
|---|---|
| Entry points | `cli.py` (commands), `worker.py`, `web/app.py`, `copies.py`, `health.py` (Docker health check), `updater.py` (the update service) |
| Config and storage | `config.py`, `settings.py`, `db.py`, `migrations/`, `mounts.py` (refuses a catalog on a network share) |
| Reading files | `scan.py`, `ingest.py`, `media.py` (decoding, thumbnails, previews), `video.py` (ffmpeg), `check.py` |
| Understanding photos | `dates.py`, `dedup.py`, `folders.py`, `faces.py`, `people.py`, `describe.py`, `llm.py`, `speech.py`, `quality.py`, `orientation.py`, `places.py` |
| Finding photos | `search.py`, `catalog.py` (shared read queries), `tagging.py`, `tours.py`, `tour_suggestions.py` |
| Family | `family.py` (relationship words from a `Graph`), `events.py`, `documents.py`, `family_check.py`, `gedcom.py`, `gedcom_import.py` |
| Cleanup | `cleanup.py` (moves to Removed, restore, empty), `writeback.py` (ExifTool) |
| Accounts | `accounts.py`, `access.py`, `changes.py`, `uploads.py`, `downloads.py`, `https.py` (certificates), `serving.py` (the web server following them) |
| Status | `progress.py`, `downloads.py`, `activity.py` (activity lines, services' problems), `retry.py`, `updates.py` (new releases) |
| Shared | `errors.py` (`Problem`: what can't be done, in words for the person), `tools.py` (running ffmpeg and ExifTool), `power.py` (keeps Windows awake through a long stage) |
| Whisper service | `whisper_service/` (FastAPI in front of whisper.cpp; its own image) |

## Family

`family.py` stores only facts: parents, siblings, partners and other relationships. Kin words (niece, in-law,
step-brother, "wife's ex-husband") are worked out from a `Graph`, relative to the person marked "me" (or whoever is
signed in). Siblings entered without parents share a stand-in parent with a negative id. Life events and names follow
GEDCOM (`events.py`), with import and export in `gedcom*.py`.
