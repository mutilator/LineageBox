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
| `[scan] image_extensions` | `.jpg .jpeg .png .nef` | | Photo file types to catalogue. |
| `[scan] video_extensions` | `.mts .m2ts .mp4 .m4v .mov .3gp .3g2 .avi .wmv .mpg .mpeg .mkv` | | Video file types. |
| `[scan] ignore_dirs` | `Thumbs Previews .thumbnails` (Docker adds NAS recycle bins and thumbnail folders) | Page | Folder names (case-insensitive patterns) whose files are listed but never read. |

### Reading photos

| Setting | Default | Page | What it is |
|---|---|---|---|
| `[ingest] threads` | 8 (Docker: 4) | Page | Photos read at a time. Fewer for a slow disk or share. |
| `[ingest] video_threads` | 2 | | Of those, how many may be videos (long reads). |
| `[ingest] thumb_size` | 400 | | Thumbnail size in pixels (WebP). |
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
| `[whisper] enabled` | true | Page | Transcripts of videos. |
| `[whisper] url` | none | Page | The speech-to-text service's address, e.g. `http://pc:9300`. |
| `[whisper] language` | `en` | | The language spoken in videos, or `auto`. |
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
| `WHISPER_LANGUAGE` | `[whisper] language` | The language spoken in the videos. |
| `LIBRARY_ABOUT` | `[library] about` | Whose photos these are. |
| `NETWORK_PATHS` | `[web] network_paths` | Where PCs find each source. |
| `ALLOWED_NETWORKS` | `[web] allowed_networks` | Who may use it. |
| `ALLOWED_HOSTS` | `[web] allowed_hosts` | Names to answer to. |
| `TRUSTED_PROXIES` | `[web] trusted_proxies` | The reverse proxy's address or network. |
| `TLS_CERT`, `TLS_KEY` | `[web] tls_cert`, `tls_key` | HTTPS without a proxy. |
| `SMB_SHARE`, `SMB_USERNAME`, `SMB_PASSWORD`, `SMB_VERSION`, `SMB_OPTIONS` | | A network share, with `compose.smb.yaml`. |

Any other setting can be passed by adding its `LINEAGEBOX_...` variable to the services in a `compose.override.yaml`.
