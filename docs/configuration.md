# Configuration reference

Most people never need this page: the Settings page in the web UI changes everything day to day, and `.env` gives the
starting values for Docker. This is the full list, for debugging and unusual setups.

## Where settings come from

Each layer overrides the one before:

1. **Defaults** in `lineagebox/config.py`.
2. **A config file:** the one named by `LINEAGEBOX_CONFIG`, else `config.toml` in the project root. In Docker,
   `LINEAGEBOX_CONFIG` points at the image's `docker/config.toml`. Run from source, copy `config.example.toml` to
   `config.toml`.
3. **`config.toml` in the catalog folder** (`data_dir`), if there is one. It's handy on a NAS, where you don't want to
   touch the container. It can't move `data_dir` itself.
4. **Environment variables** `LINEAGEBOX_<SECTION>_<KEY>`, e.g. `LINEAGEBOX_OLLAMA_URL` for `url` under `[ollama]`, or
   `LINEAGEBOX_MEDIA_ROOT` for the top-level `media_root`. `compose.yaml` passes the common ones from `.env` under short
   names ([below](#env-names-for-docker)). Empty values are ignored.
5. **The Settings page**, for the settings it shows. They are saved in the catalog's `settings` table, so the web UI and
   the worker both see them, and they win over everything above. **Once a setting has been saved on the page, changing
   it in `.env` or a config file has no effect.** Change it on the page.

Some settings are deliberately not on the page: the photo folders, the catalog folder and the network allow list. A
mistake there could lock everyone out.

A few choices belong to each browser, not to the install, and are kept only there (its `localStorage`): light or dark
(More → Colours), the map's ticked layers, and whether keys of one letter work. The catalog's `settings` table also
keeps what LineageBox notes for itself, never set by hand; one of them, `first_run_hidden`, is set once the first steps
on an admin's Home are all done or put away.

Value formats in environment variables: lists are separated by commas (`LINEAGEBOX_SCAN_IGNORE_DIRS=Thumbs,@eaDir`), and
pairs are written `folder=path,folder=path` (`LINEAGEBOX_WEB_NETWORK_PATHS=local=\\nas\photo`). Booleans are `true` or
`false`.

## Every setting

"Page" marks the settings the Settings page can change. The Docker default is the image's `docker/config.toml`, where
it differs.

### Folders

| Setting | Default | Page | What it is |
|---|---|---|---|
| `media_root` | required (Docker: `/photos`) | | The folder every photo source is in. Every catalog path starts here. |
| `data_dir` | required (Docker: `/data`) | | The catalog: database, thumbnails, previews. Must be on a local disk. |
| `[scan] folders` | none | Page | Watched folders inside `media_root`, `/`-separated (`local/Pictures`). |
| `[scan] image_extensions` | `.jpg .jpeg .png .tif .tiff .heic .heif .hif` and cameras' raw files (`.nef .nrw .cr2 .cr3 .crw .arw .srf .sr2 .dng .raf .orf .rw2 .rwl .pef .srw .3fr .iiq .mos .mef .mrw .erf .kdc .dcr`) | | Photo file types to catalogue: every kind LineageBox reads. Leave a kind out to skip those files. |
| `[scan] video_extensions` | `.mts .m2ts .mp4 .m4v .mov .3gp .3g2 .avi .wmv .mpg .mpeg .mkv` | | Video file types. A video file with sound and no picture (a phone's voice note) is catalogued as a sound recording. |
| `[scan] audio_extensions` | `.mp3 .m4a .aac .wav .wma .ogg .oga .opus .flac .amr .aif .aiff` | | Sound recordings to catalogue: digitised cassettes, voice memos. A file tagged as a song from an album (artist, album and track number) is left out as music. Set it to an empty list (`[]`) to catalogue no sound files. |
| `[scan] ignore_dirs` | `Thumbs Previews .thumbnails` (Docker adds NAS recycle bins and thumbnail folders) | Page | Folder names (case-insensitive patterns) whose files are listed but never read. |

### Reading photos

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[ingest] threads` | 8 (Docker: 4) | Page | Photos read at a time. Fewer for a slow disk or share. |
| `[ingest] video_threads` | 2 | | Of those, how many may be videos (long reads). |
| `[ingest] thumb_size` | 400 | | Thumbnail size in pixels (WebP). Sharp screens get tiles of twice this size, made from the preview when first shown and kept in `data_dir/thumbs2x` (no setting). |
| `[ingest] preview_size` | 2048 | | Preview size in pixels (JPEG), also used for faces and the AI. |
| `[copies] threads` | 4 (Docker: 2) | | ffmpeg threads per 720p video copy. |
| `[worker] interval_minutes` | 60 | Page | How often to look for new photos. Describing a backlog also pauses this often to look. |

### AI

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[ollama] enabled` | true | Page | Descriptions and reading folder names. |
| `[ollama] url` | `http://localhost:11434` (Docker: `http://host.docker.internal:11434`) | Page | Ollama's address. |
| `[ollama] model` | `ministral-3` | Page | A vision model. Changing it describes every photo again (results are kept per model). |
| `[ollama] workers` | 3 (Docker: 2) | Page | Photos sent at a time. One more than Ollama's `OLLAMA_NUM_PARALLEL` keeps it busy. |
| `[ollama] image_size` | 1024 | | Longest side sent to the model (about 1,500 prompt tokens). |
| `[ollama] keep_alive` | `30m` | | How long Ollama keeps the model loaded between photos. |
| `[ollama] embed_model` | `""` (Docker and `config.example.toml`: `nomic-embed-text`) | Page | Search by meaning: the model that reads descriptions and searches ([docs/ollama.md](ollama.md#search-by-meaning)). `""` finds words only. Changing it reads every description again. |
| `[whisper] enabled` | true | Page | Transcripts of videos, sound recordings and recorded stories. |
| `[whisper] url` | none | Page | The speech-to-text service's address, e.g. `http://pc:9300`. |
| `[whisper] language` | `auto` | Page | The language spoken: `auto` lets Whisper tell from the speech, video by video; or a code such as `en` or `de` when it guesses wrong. |
| `[faces] enabled` | true | Page | Finding and recognizing faces. |
| `[faces] models_dir` | `~/.insightface` (Docker: `/data/insightface`) | | Where the face models are downloaded. |
| `[library] about` | none | Page | Whose photos these are ("a family in Oregon, USA"). Helps the AI read places in folder names. |
| `[places] lookup` | false | Page | Find place names on the map with OpenStreetMap's Nominatim. This sends the names (nothing else) to `[places] url`, at most 4 a minute from the worker. |
| `[places] url` | `https://nominatim.openstreetmap.org/search` | | The Nominatim search address. Point it at your own Nominatim, or another provider that answers the same way, if OpenStreetMap asks or you look up a lot. |
| `[places] contact` | none | | An email address sent with each lookup (Nominatim's `email` parameter), so OpenStreetMap can reach you about this install's lookups rather than block it. |
| `[map] tiles` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | | The map's background tiles. The visitor's browser loads them from this server. |
| `[map] attribution` | © OpenStreetMap contributors, linked to openstreetmap.org/copyright | | The credit shown on the map. Keep OpenStreetMap's when the tiles are drawn from its data. |

The describing schedule (any time, certain hours and days, or paused) is set only on the Settings page. So is
**South of the equator**, which moves the seasons six months on (summer is December to February) for dates typed
like "summer 1987", searching for a season, and season tours. A season that runs into the next year counts for the
year it starts in: winter 2023 is December 2023 to February 2024. And **Dates in numbers are day first**: 5/6/1990
is then 5 June, and a folder named "Party 5.6.98" is from 5 June 1998 (unticked, month first, as in the US).

### Cleanup

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[cleanup] blurry_below` | 30 | Page | Sharpness cut-off for the Blank and blurry list. |
| `[cleanup] turn_suggest` | `sure` | Page | Turned photos: `sure`, `more` (suggest less certain ones too) or `off`. |

### Web and network

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[web] share_path` | `media_root` | | `media_root` as people's PCs see it, for Copy network path. |
| `[web] network_paths` | none | Page | `folder=path` pairs: where PCs find each source (`local=\\nas\photo`). |
| `[web] allowed_networks` | every private range | | Who may use the web UI when family accounts are off. |
| `[web] allowed_hosts` | none | Page | Host names to answer to besides addresses and home-network names (`photos.example.com`, `*.example.com`). |
| `[web] trusted_proxies` | none | Page | Reverse proxies whose `X-Forwarded-For` and `X-Forwarded-Proto` are believed. |
| `[web] tls_cert`, `[web] tls_key` | none | Page | HTTPS without a proxy: certificate (full chain) and key, PEM. Both or neither. |
| `[web] hover_preview_ms` | 500 | Page | Milliseconds of pointing at a photo before it shows larger; 0 never. |
| `[web] language` | none (English) | | Once a translation is installed ([translating](translating.md)): the pages' language for a browser that asks for none LineageBox has (`de`, `pt_BR`). A browser's own choice (More → Language) and its languages come first. |

### Family accounts

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[accounts] enabled` | false | Page | Everyone signs in. Refused until an admin account exists. |
| `[accounts] google_client_id`, `google_client_secret` | none | Page | The Google OAuth client. |
| `[accounts] public_url` | none | Page | `https://photos.example.com`: where Google sends people back to. |
| `[accounts] session_days` | 90 | Page | A session lasts this long after it was last used. |
| `[accounts] uploads_folder` | `Family uploads` | Page | Members' uploads go to `<watched folder>/<this>/<person id>`. |
| `[accounts] uploads_into` | the first watched folder | Page | The watched folder that holds it. |
| `[accounts] upload_max_gb` | 4 | Page | The largest single uploaded file. |
| `[updates] check` | true | Page | Ask GitHub once a day for the newest release's version, to show admins that an update is out. |
| `[updates] repository` | `mutilator/LineageBox` | | Where releases are published (`owner/name` on GitHub), and where the help panel's About links for the source code. A fork's images name their own. |

Photo frames' device links are made and turned off on **Settings → Photo frames**, and kept in the catalog
(`device_link`), not in a config file: each is a name, the one view it shows, how long each photo stays (8 to 60
seconds), and whether it works only on the home network. A link away from home uses `[accounts] public_url`. New for
you has no settings: it is shown inside the site only, and nothing is ever sent.

### Backups

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[backups] enabled` | true | Page | A checked copy of the catalog on the worker's first pass of each day ([Backups](install.md#backups)). |
| `[backups] folder` | `data_dir/backups/nightly` | Page | Where the copies go: a folder outside the watched folders that the web app and the worker can write to (in Docker, one mounted into both). In a config file it may be relative to `data_dir`; the page takes a whole path. |
| `[backups] keep_daily` | 7 | Page | Keep the newest copy of each of this many days (1 to 90)... |
| `[backups] keep_weekly` | 4 | Page | ...and of each of this many weeks before those (0 to 104). |

### Other photo programs

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[xmp] sidecars` | false | Page | Write an XMP sidecar beside each photo the family gave names, a caption, tags given by hand, a date set by hand or a place put on the map by hand (`IMG_1234.JPG.xmp` beside `IMG_1234.JPG`), for digiKam, darktable, Lightroom and ExifTool. The worker writes them (in Docker, the copies service, as the worker only reads the photos), a few minutes at a time, and keeps them up to date; one another program made is left alone. Downloads and the export carry them either way. |
| `[xmp] import` | true | Page | Bring in what other apps left with the photos: faces named in XMP (digiKam, Lightroom, Picasa, Windows Photo Gallery) and in Picasa's `.picasa.ini`, keywords, captions, dates and places, and Google Takeout's JSON beside the photos. Below anything set here, each photo read once. A catalog that had photos before this setting existed starts with it off. |

Environment variables: `LINEAGEBOX_XMP_SIDECARS`, `LINEAGEBOX_XMP_IMPORT`. Naming photos with GPS after the nearest
town has no setting: it uses the list of towns that comes with LineageBox, and asks nothing online.

### A public demo

For a copy anyone may try, with a made-up family's photos ([A public demo](demo.md)). None of these are on the
Settings page.

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[demo] enabled` | false | - | One button to sign in, no other way in, the AI, Whisper and everything else that reaches the network off, and `lineagebox demo reset` allowed. |
| `[demo] person` | "" | - | Who visitors are in the family tree: a name, or a person's number. |
| `[demo] uploads` | 5 | - | Uploads from each address until the next reset (0 to 1000; 0 for none). |
| `[demo] upload_max_mb` | 10 | - | The most one of them may be, in MB (1 to 4096). |
| `[demo] resets` | every day | - | When it goes back as it was, in words, for the note at the top of every page: "every day at 4:00 UTC". |
| `[demo] preview` | "" | - | The photo link previews show (chat apps, social sites), by its path in the catalog: `local/Holidays/2014/beach.jpg`. Served to anyone at `/demo-preview.jpg`. Needs `[accounts] public_url`, as previews need whole addresses. |

Environment variables: `LINEAGEBOX_DEMO_ENABLED`, `LINEAGEBOX_DEMO_PERSON`, `LINEAGEBOX_DEMO_UPLOADS`,
`LINEAGEBOX_DEMO_UPLOAD_MAX_MB`, `LINEAGEBOX_DEMO_RESETS`, `LINEAGEBOX_DEMO_PREVIEW`.

### Other environment variables

| Variable | What it is |
|---|---|
| `LINEAGEBOX_CONFIG` | The config file to read instead of the project root's `config.toml`. |
| `LINEAGEBOX_FFMPEG` | The folder holding `ffmpeg` and `ffprobe`, when they aren't on the `PATH`. |
| `LINEAGEBOX_HOST`, `LINEAGEBOX_PORT` | Defaults for `lineagebox serve --host` and `--port`. |

## `.env` names for Docker

`compose.yaml` maps these `.env` names onto the settings above, and also uses some itself:

| `.env` | Setting | What it is |
|---|---|---|
| `PHOTOS` | | The folder mounted as `local` (default `./photos`). |
| `DATA` | | The catalog folder (default `./data`). |
| `PORT` | | The web UI's port on this machine (default 8000). |
| `BIND` | | The address of this machine to answer on (default all its IPv4 addresses). |
| `TZ` | | Time zone, for dates and the describing hours (default `UTC`). |
| `PUID`, `PGID` | | User and group the containers run as: whoever owns the photos and the catalog folder (default 1000). |
| `COMPOSE_FILE` | | Extra compose files: Ollama, Whisper or a network share in Docker. |
| `LINEAGEBOX_VERSION` | | One release to run (`1.2.3`), instead of the newest (`latest`). |
| `LINEAGEBOX_IMAGE`, `LINEAGEBOX_WHISPER_IMAGE` | | Another image to run, such as a fork's: its name without the version. |
| `DOCKER_SOCKET` | | Docker's socket, for the updater service (default `/var/run/docker.sock`). |
| `OLLAMA_URL` | `[ollama] url` | Ollama on another machine. |
| `OLLAMA_VERSION`, `OLLAMA_NUM_PARALLEL` | | Ollama in Docker: its version, and photos at once. |
| `WHISPER_URL` | `[whisper] url` | The speech-to-text service on another machine. |
| `WHISPER_MODEL` | | Whisper in Docker: its model. |
| `WHISPER_LANGUAGE` | `[whisper] language` | The language spoken in videos, recordings and stories (`auto` by default). |
| `LIBRARY_ABOUT` | `[library] about` | Whose photos these are. |
| `NETWORK_PATHS` | `[web] network_paths` | Where PCs find each source. |
| `ALLOWED_NETWORKS` | `[web] allowed_networks` | Who may use it. |
| `ALLOWED_HOSTS` | `[web] allowed_hosts` | Names to answer to. |
| `TRUSTED_PROXIES` | `[web] trusted_proxies` | The reverse proxy's address or network. |
| `TLS_CERT`, `TLS_KEY` | `[web] tls_cert`, `tls_key` | HTTPS without a proxy. |
| `SMB_SHARE`, `SMB_USERNAME`, `SMB_PASSWORD`, `SMB_VERSION`, `SMB_OPTIONS` | | A network share, with `compose.smb.yaml`. |

Any other setting can be passed by adding its `LINEAGEBOX_...` variable to the services in a `compose.override.yaml`.
