# Architecture

How LineageBox is put together: the processes, the pipeline, the catalog, and the code. This is for developers and for
debugging. [Design notes](design-notes.md) explain why it is built this way, with the measurements behind the choices.

## The pieces

```
        photo folders (local or a network share; read-only except Cleanup, uploads, copies, rotations)
                                  │
     ┌────────────────────────────▼─────────────────────────────┐
     │ worker          python -m lineagebox worker                  │──► Ollama        descriptions, folder names
     │ scan → ingest → chapters → dedup → folders → faces →     │──► Whisper svc   transcripts of videos, stories
     │ people → places → sharpness → check files → index →      │
     │ transcribe → describe                                    │
     └────────────────────────────┬─────────────────────────────┘
                                  │
         data_dir:  lineagebox.db (SQLite, WAL) · thumbs/ · thumbs2x/ · previews/ · frames/ · stories/ · insightface/
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
   become one `image`), EXIF, a perceptual hash, a 400 px thumbnail and a 2048 px preview. JPEG, PNG, TIFF (16-bit
   scans scaled to 8 bits; a file of several pages by its first), HEIC (through pillow-heif) and cameras' raw files
   (through rawpy) are read; whatever the kind, browsers are shown the JPEG preview and thumbnail. Videos get ffprobe metadata and sampled frames (one
   every 10 s for the first 20, then one a minute, up to 120), and the clearest frame becomes the preview; after a
   frame that takes too long (a tape with no index, decoded from its start) the video keeps the frames it has. A sound
   file (a digitised cassette, a voice memo, a "video" with no picture) is a recording, `image.media = 'audio'`:
   its preview is a picture of its sound wave, and the faces and describing stages skip it; one tagged as a song
   from an album is left out as music. Later stages use the previews, never the originals. At the end, a Live
   Photo's short clip is paired with its still (`live.py`, `image.live_of`), so it shows only through the still.
   Then **chapters** (`chapters.py`): a video over 10 minutes gets chapters where a recording on the tape starts or
   the picture changes completely, found from its keyframes, with a frame at each (`video_chapters`).
3. **dedup** (`dedup.py`) groups near-duplicates by perceptual hash, and a copy dated only by its file's time or a
   folder takes its group's camera date (`share_dates`, source `copy`). **folders** (`folders.py`) reads place,
   event, people hints and dates from folder names, by regex and the LLM. Then, in the describing hours while Ollama
   answers, **describe again** (`describe.describe_requests`: the photos people asked to have described again, with
   the model asked for) and **meaning** (`embeddings.embed_new`: new and changed descriptions and captions read by the
   embedding model for search by meaning), each for 20 minutes a pass at most (`worker.ASKED_MINUTES`), so that a
   thousand photos asked for leave the stages after them their time; the next pass starts at once while some are
   left. **dates** (`dates.py`) picks each photo's
   date from EXIF, else the folder, else the file name, else the file time. A camera whose clock was put right
   (`camera_clock.py`, Cleanup → Camera clocks) has its new photos' dates moved as they are read.
4. **faces** (`faces.py`) runs InsightFace on the previews. **people** (`people.py`) recognises faces of named people
   (`identify`) and groups the rest with HDBSCAN (`cluster`), then the faces it leaves alone in twos and threes
   where they are plainly one person (`_small_groups`). Then **other apps' names** (`imports.py`, when `[xmp] import`
   is on): each photo is read once (`metadata_read`) for what other apps left, from the start of the original
   (`xmp.embedded`: XMP and IPTC), other programs' XMP sidecars, Picasa's `.picasa.ini` and Google Takeout's JSON;
   keywords, captions, dates and places come in at once, and names on faces (`imported`, state `waiting`) once the
   photo's faces are found, on the face their box covers. A photo whose files can't be read, or hold something no
   reader expected, waits in `retry` while the others are read. Then **XMP sidecars** (`sidecars.py`, when `[xmp]
   sidecars` is on): the photos in `sidecar_stale` get their sidecars written, a few minutes a pass; a worker whose
   photo folders are read-only (Docker) leaves them to the copies service, which runs the same stage between its
   720p copies.
5. **places** (`places.py`), **sharpness** (`quality.py`), **check files** (`check.py`: each original read whole once,
   for disk damage, up to 20 minutes a pass), the **search index** (`search.py`), **news** (`outbox.after_pass`: new
   photos once their faces were looked for, and admins' alerts, for New for you), **transcribe** (`speech.py`: the
   family's recorded stories first, then videos and recordings) and **describe** (`describe.py`). Before describing,
   **document dates** (`scan_dates.read_waiting`): the AI reads the dates written on each scan Cleanup → Suggested
   dates lists, once, documents first, for 20 minutes a pass at most.

Before ingest, **slideshow videos** (`slideshow.make_waiting`): the tours and albums someone asked to have as one
video file, the oldest first, so whoever asked waits minutes, not hours. Asking sets a settings key that starts a
pass, as Check now does. Each frame is drawn by Pillow (photo, caption, date, names) and piped to ffmpeg as it is drawn,
with the videos' own sound added after; nothing but the finished MP4 is kept. A video started is finished; between
them the stage gives way after 30 minutes. One that can't be made, whatever the reason, is marked failed and the next
is made; one cut off by a restart is made again from the start, and what it was writing (`videos/.making-*`) is
deleted. Each pass also deletes the videos older than 30 days.

Describing goes newest first, and stops after one interval so that a scan can run. New photos never wait behind a
backlog. Describing and transcribing keep to the describing schedule. Both wait while their service is unreachable,
and the worker checks every 5 minutes whether it is back.

Once a day, before ingest, previews, thumbnails and video frames that have gone missing get their photo's ingest bit
cleared, and ingest makes them again from the original (`check.rebuild_derivatives`). On the first pass of each day,
right after the scan, `backup.py` makes a copy of the catalog with SQLite's backup API, checks it whole, and only then
deletes the copies no longer kept ([backups](install.md#updating-and-backups)); the recorded stories' sound files go
beside it. Once a day too, recorded stories taken away a month ago are deleted for good, with their files
(`stories.purge`), and sound files no story refers to; but never one whose story undoing a change would bring back
(someone removed, with the stories taken away from them).

## The catalog

SQLite in WAL mode, in `data_dir/lineagebox.db`, with FTS5 for search (`search_fts`). Face embeddings are 512 float32
values stored as BLOBs and compared in NumPy; so are the descriptions' (768 with nomic-embed-text), which the web app
keeps in memory and tops up with what the worker added (`embeddings._Index`).

The main tables:

| Table | Holds |
|---|---|
| `folder`, `file` | Every folder and file under the watched folders. Paths are relative to the media root, with `/`. |
| `image` | One row per unique content (SHA-256): format, size, date and its source, GPS, stage bits, errors. |
| `face` | Box, landmarks, quality, embedding, cluster, person, and who assigned it (`assigned_by`). |
| `person`, `parent`, `partner`, `sibling`, `relation`, `event`, `person_name` | People and the family tree. Only facts are stored. `parent.pedi` says how a child belongs to a parent (birth, adopted, foster); `person.biography` is the life story the family writes, `person.notes` where things came from, `person.cover_face_id` the portrait chosen. |
| `document`, `citation`, `source`, `source_citation`, `tree_media` | What bears the tree out: photos of records (documents) and written sources (books, websites, records), each cited for a person or a life event, a source with where in it (`documents.py`); and files another family tree linked to someone or a source that aren't in the library. |
| `analysis`, `tag`, `image_tag` | AI descriptions, kept per model and prompt version, and tags. `analysis.asked` marks a description someone asked for (Describe again), kept when the Settings page's model changes. |
| `embedding`, `embed_stale` | Search by meaning (`embeddings.py`): each photo's words (its description and the family's caption) as a float32 vector from Ollama's embedding model, one per photo search shows, with the text's SHA-256; and the photos whose words changed since (filled by triggers). |
| `describe_request` | Photos someone asked to have described again, with the model asked for (NULL: the Settings page's), for the worker. |
| `tag_alias` | Tags renamed, merged or taken off every photo (`tagging.rename`, `remove_everywhere`): the AI's old word, and the tag it means now (NULL: none). |
| `comment` | The family's words on a photo: its caption and the comments under it (`comments.py`). |
| `transcript`, `video_frame`, `video_chapters` | Videos and recordings: what is said (with the lines people corrected, `transcript.edits`), the frames looked at, long videos' chapters. |
| `story` | Recorded stories (`stories.py`): a voice note about a photo, or a person or one of their life events. The sound is a file in the data folder's `stories/`, never on the photo share. |
| `album`, `album_photo`, `album_passed` | Albums picked by hand (`albums.py`): who made each (NULL: the admins'), its cover and words; its photos in order, each with the album's own caption; and, of a shared album, the photos its maker may pass on (as `tour_photo` for a tour). |
| `favourite` | Hearts on photos (`favourites.py`), one per account (NULL: given at home without accounts). Tours and memories prefer them. |
| `slideshow_video` | A tour or album someone asked to have as one video file (`slideshow.py`): the slides as they saw them, its state for the page, and the file's name in the data folder's `videos/`. |
| `settings` | Everything the Settings page changes, and the worker's state for the Status page. |
| `account`, `session`, `upload` | Family accounts. |
| `change_log` | Every change made through the site, by anyone, with what it altered before and after, for undo (`changes.py`). |
| `outbox`, `outbox_seen` | New for you (`outbox.py`): each event written once (a share, a "Who is this?", new photos, a comment, an admin's alert), checked against each viewer as it is shown; each account's mark of what it has seen (0: the house, without accounts). Kept 90 days. |
| `device_link` | Photo frames' links (`devices.py`): the one view each shows, the SHA-256 of its token, whether it works at home only, when it was turned off and last used. |
| `image_place`, `folder_place` | Photos and folders put on the map by hand (`places.py`): a photo's wins over its GPS, which wins over its folder's (by hand, then a place its name gives). Changes to them are tracked for undo. |
| `sidecar`, `sidecar_stale` | XMP sidecars LineageBox wrote beside photo files (`sidecars.py`), with the SHA-256 of what each says (`other`: one another program made, or wrote into since, left alone); and the photos whose sidecars may be out of date, filled by triggers on what a sidecar holds. |
| `metadata_read`, `imported` | Other apps' metadata (`imports.py`): the photos read, and everything brought in from them (names on faces, people added, tags, captions, dates, places) for Settings → Other photo programs. |
| `scan_reading` | The dates the AI found written on a scan (`scan_dates.py`, Cleanup → Suggested dates): `dates`, JSON `[{"written", "what"}]` as it copied them, the document's own first; one row a scan, read again for a new model or prompt `version`. |
| `camera_clock` | A camera's clock put right (`camera_clock.py`): a stretch of its photos, from the first it dated wrong to the last (by the dates it wrote; or on to every later one), and `pins`, the photos in it someone said were really taken when. One pin moves them all as much; two or more spread a drift over the photos between. `image.camera_taken_at` keeps what the camera wrote. |

`VISIBLE` (`catalog.py`) is the SQL filter for images that still have a file present and are their own item (not a
Live Photo's clip). `ON_SHARE` is the same without the clip rule, for what clips need too (720p copies, rebuilt
previews).

The search index (`search_fts`) has a column for each kind of words, weighted: the family's captions and comments
above the AI's description, then tags, names, places and events, folder words, dates, what is said in videos, text the
AI read off the photo, and documents' text. `search_stale` (filled by triggers) lists photos whose words changed
outside a request, for the worker to index again. A search with words is also read for its meaning, when Ollama
answers in time (`search._with_meaning`): the photos closest by meaning, held to the same filters and to the words
that name people or years, are blended with the word matches by their places in both lists (reciprocal rank fusion).

**Migrations** are `lineagebox/migrations/NNN_name.sql`, or `NNN_name.py` with an `upgrade(con, cfg)` function.
`db.migrate` applies those above `PRAGMA user_version`, each under the write lock. The web UI and the worker start
together and both run it, so the second process finds the work done. A migration is never changed once released.

**Write in short transactions.** A long write locks out the web UI, which then answers "busy" (503).

## People's edits always win

Re-runs never overwrite what a person decided:

- `face.assigned_by`: `user` (named by hand) beats `auto` (recognised). `rejected` and `not_a_face` faces are set
  aside.
- `face_not_person` records "this face is not that person". Recognition and suggestions respect it.
- `image_tag.source`: `user` marks a tag given by hand, and `not` marks one taken off for good. Describing a photo
  again (on request too) replaces only the AI's own tags; a tag renamed or taken off everywhere by an admin is read
  through `tag_alias`, so the AI's old word gives the tag as it is now, or nothing.
- `image.taken_at_source = 'user'` marks a date set by hand. `date_text`, `date_from` and `date_to` hold its span, and
  the automatic date stays in `auto_taken_at`. A camera's clock put right moves only that automatic date, and a copy
  takes its group's camera date (`copy`) only over a file's time or a folder's date. A date another app gave
  (`import`) replaces only a file's time, a folder's date or (from XMP and IPTC) a file name's.
- What other apps left (`imports.py`) never overwrites what was set here: a face named by hand or set aside keeps its
  name, a tag taken off stays off, a caption stays, and `face_not_person` is respected.
- A place put on the map by hand (`image_place`) wins over a photo's GPS, and is what its XMP sidecar says. A
  folder's (`folder_place`) is for its photos without GPS, below no nearer folder whose name gives a place.
- `folder.source = 'user'` marks what a folder means (place, occasion, date, names), set by hand on its page: the AI
  never reads that folder's name again, and its photos' dates and search are worked out again from it
  (`folders.set_meaning`, `reflow`).
- `transcript.edits` holds the lines of a transcript people put right, by the second each starts at: hearing the video
  again or filtering it under better rules keeps them. `story.words` is a story's words as someone wrote them;
  Whisper's are kept apart, in `story.heard`.
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
- `web/pieces.py`: long pages of photos a piece at a time (folder, year, tag, tour): the part a request asks for
  (`?skip=`, `?upto=`; `?piece=1` answers with only the tiles, `_piece.html`) and the scrubber's years and months
  (`marks`), shown by `_pieces.html`. A photo's ← → don't depend on it: the photo page works the whole list out from
  its `ctx`.
- `web/tiles.py`: what grid tiles say and show. `tile_alts` (a template global) names who is in each photo for its alt
  text, in one query per grid, on the viewer's own connection, which `page()` sets for the template (`reading`), so a
  member's tiles name only people they see. `sharp` makes the 2× tile `/t2/<sha>` (`routes/files.py`) from the preview
  the first time a sharp screen asks for it (the grids' `srcset`), into `thumbs2x/`, and again when the preview is
  newer; two at once (`MAKING`), each request waiting at most `WAIT` seconds for its turn. With no preview, the disk
  nearly full or that wait over, the thumbnail stands in, sent `no-cache` so the browser asks again next time (the
  tile itself is kept a year, as thumbnails are).
- `web/frames.py`: photo frames' device links (`/d/<token>/...`): `DevicePath` takes the token off the address before
  the gate, which looks it up; `ROUTES` are the only routes a link may open; `PICKERS` gives each view's photos
  (`memories`, `tour`, `album`).

- Pages render through `page(request, "x.html", ...)`. Every page template has an entry in `help.py` `PAGES`, which
  the help panel shows.
- Every route has an entry in its module's `ACCESS` (`public`, `member` or `admin`). A route without one answers 500.
  A photo frame's device link may open only `frames.ROUTES`, whatever their class.
- Every POST API depends on `_from_app`, which requires the header `X-Requested-With: lineagebox`. With SameSite
  cookies, that stops cross-site requests.
- Every change goes through `changes.tracking`, which keeps it on Recent changes with what it altered. A route
  returns its change's id as `undo`, and `app.js` shows a note at the foot of the page with an Undo button
  (`notice()`); errors show there too, never in a browser alert.
- `app.js` works through data attributes, so most UI needs no new JavaScript:
  - `data-api` (with `data-body`) posts. `data-reload`, `data-remove`, `data-go`, `data-show`, `data-html` and
    `data-busy` say what happens around it;
  - `data-key` is a keyboard shortcut, which the help panel lists;
  - `data-names` is the person-name autocomplete;
  - `form.select-form` with the `bulk_bar` macro is the tick-photos bar. Its ticks are kept in sessionStorage for the
    list (its address without page, part or select), so they survive more of it loading and its other pages;
    Shift and a click ticks a range; `script[data-everything]` (the whole list's ids) is what Select all ticks;
  - `[data-pieces]` (`_pieces.html`) loads the next piece as it is scrolled near (IntersectionObserver) and the piece
    before on Show earlier photos, keeps `?skip=`/`?upto=` in the address (`history.replaceState`) so Back and a reload
    show the same tiles, and `[data-scrubber]` marks the year and month in view. Grids' parts are
    `data-grid-part`/`data-grid-head` (not `data-part`, which is the Settings page's groups);
  - keys of one letter or sign can be turned off in the help (`lettersOff()`, `localStorage` `lineagebox-letter-keys`);
    every handler of such keys asks it. The slideshow (`playTour`) is a modal `<dialog>`;
  - `data-viewer` is the photo page: faces outlined on the photo, swipe and zoom, only the photo, the details sheet
    on a phone; a photo opened from a list remembers it (sessionStorage) for ← → and Back.
- Without family accounts, only private-network clients and allowed host names are answered. With them, every route
  but signing in needs a session. See [access control](access-control.md).
- The count by the bell at the top (New for you) comes from `deps.news_count`, worked out only by pages that show it
  and kept 30 seconds. `routes/news.py` is the page, and the alerts the Status page loads after itself.
- The installable app: `routes/phone.py` serves the web manifest (with Android's share target) and the service worker
  (`static/sw.js`, with the version number put in), which keeps only the site's own static files and an offline page;
  the icons are in `static/icons/`. `routes/frame.py` is the photo frame (`photo_frame.html`, `static/frame.js`,
  written for old browsers) and Settings → Photo frames.
- The menu (`base.html`): the pages used most up front, the rest under More (a `data-panel` menu, as ☰ and the
  profile button are), all folded into ☰ when they don't fit. `help.py` `MENU` has a line for each of its pages, in
  its order, for the help's "Every page" list (`tests/test_help.py` checks they match). Light or dark: `static/theme.js`
  (a file, as the Content-Security-Policy allows no inline script) puts the browser's choice (`localStorage`
  `lineagebox-theme`) on `<html data-theme>` before the page is drawn, and `app.css` defines the colours for
  `prefers-color-scheme` and for `data-theme`; a person's page (`body.person-page`) and Family (`body.family-page`)
  have a print style, and app.js unfolds their panels for printing.
- The first steps (`first_run.py`, `_first_run.html`): Home (and the empty start page) works out each step from the
  catalog for an admin; all done, or put away (`routes/first_run.py`), is kept as the setting `first_run_hidden`, so
  Home stops counting.
- AI on request: `routes/ai.py` puts photos on the worker's list to describe again (never in a request), lists
  Ollama's models for admins, and answers a photo's More like this (`_like_this.html`, loaded after the page);
  `routes/tags.py` renames, merges and removes tags across the library for admins, each through `changes.tracking`.

## Other languages

LineageBox is written in English and ships only English, but everything the web app shows goes through gettext, so a
translation is a catalog added under `lineagebox/locale/<language>/LC_MESSAGES/` ([translating](translating.md)).
`i18n.py` holds it together:

- **Which language**: the gate in `web/app.py` picks one for each request (`i18n.choose`: the browser's own choice
  from More → Language, a `lang` cookie set by `web/routes/language.py`; else the first language its Accept-Language
  asks for that is installed; else `[web] language`; else English) and sets it for the request (`i18n.speaking`, a
  context variable), so templates, the help and any Python code that runs for the request speak it. With English
  only, every visitor gets English and nothing in a page changes.
- **Templates**: Jinja's i18n extension (`i18n.install`, from `site.py`), with `trimmed` blocks. `{% trans %}` holds
  the page's HTML text; its translation is HTML too, and quotes in it outside tags become character references, so a
  translation can't end an attribute. `_()` is plain text (escaped where it is shown, right inside `|tojson`), for
  attributes and expressions; `ngettext` gives plurals in an attribute.
- **Formats**: the `date`, `number` and `size` filters, `month_names` and `weekday_names` read their patterns,
  separators and names from the catalog (messages with a context), so English is as before (Jun 3, 2012 10:05;
  12,345; 74.8 GB) and a translation sets its own.
- **Scripts**: `app.js` `t()`, `tn()` and `num()` (`frame.js` has its own `t()`). A page in another language carries
  that language's script catalog as JSON (`#lineagebox-text`, `i18n.script_catalog`) with each plural form's number for
  0-199, worked out from the catalog's rule, since the pages' scripts can't evaluate one (no `eval` under the
  Content-Security-Policy). English pages carry nothing.
- **The help** (`web/help.py`) marks its texts `N_()` and translates them as it hands them to `_help.html`.
- **Not yet**: the messages the Python code makes (`errors.Problem`, answers' `message`, activity lines, kinship and
  GEDCOM date words) are English; [development](development.md#text-people-read) says how they would be wrapped.

## Code map

| Area | Modules |
|---|---|
| Entry points | `cli.py` (commands), `worker.py`, `web/app.py`, `copies.py`, `health.py` (Docker health check), `updater.py` (the update service) |
| Config and storage | `config.py`, `settings.py`, `db.py`, `migrations/`, `mounts.py` (refuses a catalog on a network share) |
| Reading files | `scan.py`, `ingest.py`, `media.py` (decoding, thumbnails, previews), `video.py` (ffmpeg: frames, chapters, sound waves, copies), `chapters.py` (long videos' chapters), `check.py` |
| Understanding photos | `dates.py`, `dedup.py`, `folders.py`, `faces.py`, `people.py`, `describe.py`, `llm.py`, `speech.py`, `quality.py`, `orientation.py`, `places.py` (the map, places by hand), `towns.py` (the nearest town, from `data/towns.tsv.gz`), `face_ages.py` (what faces' ages can say: names that don't fit, a birth-year hint), `date_hints.py` (a probable date for a scan), `camera_clock.py`, `scan_dates.py` (scans dated by the day they were scanned: what can tell their real date) |
| Finding photos | `search.py` (words, filters, suggestions), `embeddings.py` (search by meaning, More like this), `catalog.py` (shared read queries), `tagging.py` (also tags across the library), `tours.py`, `tour_suggestions.py`, `albums.py` (albums picked by hand), `favourites.py` (hearts), `slideshow.py` (a tour or album saved as a video), `comments.py` (captions and comments), `stories.py` (recorded stories), `live.py` (Live Photos) |
| Family | `family.py` (relationship words from a `Graph`), `events.py`, `documents.py` (documents and written sources), `family_check.py`, `gedcom.py`, `gedcom_import.py`, `life.py` (a person's life by age, for their page), `book.py` (a family book to print or save as a PDF, `static/book.css`) |
| Cleanup | `cleanup.py` (moves to Removed, restore, empty; a photo's sidecar goes with it), `writeback.py` (ExifTool: a rotation, and what the family gave a photo, into its file) |
| Other photo programs | `xmp.py` (XMP written and read, IPTC read), `sidecars.py` (XMP sidecars beside the photos, in zips and the export), `imports.py` (names, tags and dates from other apps, Picasa and Google Takeout) |
| Keeping the family's work | `backup.py` (the nightly copy of the catalog), `export.py` (the portable export: GEDCOM plus `photos.json`, [export](export.md)) |
| Accounts | `accounts.py`, `passkeys.py`, `access.py`, `changes.py`, `uploads.py`, `downloads.py`, `https.py` (certificates), `serving.py` (the web server following them) |
| Status | `progress.py`, `downloads.py`, `activity.py` (activity lines, services' problems), `retry.py`, `updates.py` (new releases), `first_run.py` (the first steps on a new LineageBox) |
| Telling the family | `outbox.py` (New for you: events, who may see each, admins' alerts), `devices.py` (photo frames' device links), `web/frames.py` |
| Shared | `errors.py` (`Problem`: what can't be done, in words for the person), `i18n.py` (other languages: the request's language, the templates' and help's gettext, the formats of dates and numbers; `web/routes/language.py` keeps a browser's choice), `tools.py` (running ffmpeg and ExifTool), `power.py` (keeps Windows awake through a long stage) |
| Whisper service | `whisper_service/` (FastAPI in front of whisper.cpp; its own image) |

## Family

`family.py` stores only facts: parents, siblings, partners and other relationships. Kin words (niece, in-law,
step-brother, "wife's ex-husband") are worked out from a `Graph`, relative to the person marked "me" (or whoever is
signed in). Siblings entered without parents share a stand-in parent with a negative id. Life events and names follow
GEDCOM (`events.py`), with import and export in `gedcom*.py`.

A child belongs to each parent by birth, by adoption or as a foster child (`parent.pedi`, GEDCOM's FAMC.PEDI): two
birth parents and two adoptive ones at most, foster parents besides. The `Graph` walks every kind for ancestors and
the tree; kin words go up birth and adoptive links (`kin_ancestors`), so an adoptive mother's mother is a grandmother,
and anyone reached only through a foster link is "foster" (foster brother, foster grandmother). The link itself
says so: "adoptive mother", "birth mother" (for someone with adoptive parents too), "adopted son".

A person's page (`life.py`) groups their photos and life events by how old they were, from the photos' dates alone in
one query, and loads each group's photos when it is opened (`/person/{id}/life/{group}`), so someone in thousands of
photos still has a quick page. `family_check.py` also checks the tree against the photos: someone named in photos
from before their birth, or from more than a year after the year they died, or in a photo from before a parent was
born.
