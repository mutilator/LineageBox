# LineageBox

**Your family's history, photos and home videos in one private place, shared with the family.**

LineageBox is a self-hosted family archive. It joins two things that usually live apart:

- **the family tree:** who everyone is, how they are related, the events of their lives, and the documents that prove
  them;
- **the photos and videos they appear in.**

It reads the photo folders you already have and recognizes the faces in them. Every photo is then linked to the people
in your tree: open a great-grandmother's page and see her family, her life events, her birth certificate and every
photo she's in.

Then the family comes in. Relatives sign in with their Google account and see **their own branch**: their ancestors,
their blood relatives and those relatives' partners, and the photos those people are in. They can name faces, fill in
what they remember, add relatives and upload their own photos. Everything stays on your own hardware. The AI runs
locally, nothing is uploaded to a cloud service, and LineageBox never changes your files unless you ask.

## Quick start

On any machine with Docker (a NAS, a home server, a PC):

```sh
git clone https://github.com/mutilator/LineageBox lineagebox && cd lineagebox
cp .env.example .env          # set PHOTOS to your photo folder, TZ to your time zone, PUID/PGID to its owner
docker compose up -d
```

LineageBox runs as `PUID`'s user (`id -u`, `id -g` give yours), not as root, so the photo folder must already be
there and be theirs ([users and permissions](docs/install.md#users-and-permissions)).

To run the AI next to it as well, add this line to `.env` before starting (use `;` instead of `:` on Windows):

```sh
COMPOSE_FILE=compose.yaml:compose.ollama.yaml:compose.whisper.yaml
```

Then open `http://<that machine>:8000`:

1. **Settings → Watched folders:** open `local`, go to your photos and press **Watch**.
2. **Settings → AI (Ollama):** press **Download ministral-3 to Ollama**. Without a graphics card, set the model to
   `gemma3:4b` first.
3. **Status** shows the progress. Every photo is read once, then faces are found and the AI describes each photo.
4. Meanwhile, name the people on the **People** page, press **This is me** on your own page, and start the family
   tree. When you're ready to bring the family in, set up [family accounts](docs/family-accounts.md).

The guides go step by step:

| Guide | For |
|---|---|
| [Everything on one machine](docs/quick-start/docker.md) | A NAS, home server or PC running LineageBox, with the AI in Docker next to it |
| [A NAS, plus a PC with a graphics card](docs/quick-start/nas-and-pc.md) | LineageBox beside the photos, with the AI on a desktop PC that has a GPU (much faster) |
| [From source, without Docker](docs/quick-start/from-source.md) | Trying it on a PC, or working on the code |

## What it does

### Family history

- **A family tree built from facts.** Enter parents, partners and siblings. LineageBox works out every other
  relationship (great-aunt, second cousin once removed, step-brother, former sister-in-law) from anyone's point of
  view. Relationships work in search: "cousins 2019", "grandma christmas".
- **Life events and documents.** Births, marriages, moves and deaths, with dates as exact as you know them ("about
  1950"). Mark a photo of a certificate, an obituary or a letter as a document, and link it to the person or event it
  records.
- **Works with genealogy software.** It imports and exports GEDCOM 7.0 and 5.5.1, for Ancestry, FamilySearch,
  MyHeritage, Gramps and the like, with portraits cropped from faces. Imported people are matched to the people you
  already have, and you check every match.
- **Checks the tree for mistakes:** a missing parent, dates that can't be right, two entries that may be one person.

### Shared with the family

- **Sign in with Google.** You invite relatives by email address. No passwords to manage, and no sign-up page.
- **Everyone sees their own branch.** Each relative sees their part of the tree and the photos its people are in.
  Partners see the same family. Admins see everything and can make exceptions.
- **The whole family contributes.** Relatives name faces, fix dates, add relatives and events, and upload photos,
  videos and whole folders from the browser. **Who is this?** asks the family about a face nobody has named yet.
- **Admins can undo anything a member changed.** Changes that would let someone see more of the family are flagged.
- At home, without accounts, LineageBox simply answers the home network, with no sign-in.

### Photos and home videos

- **Recognizes faces.** Name a group of faces once, and LineageBox finds that person in the rest of the library, across
  decades. It also suggests who the unnamed people in a photo might be.
- **Search in plain words.** "Birthday cake", "beach 2014", someone's name. Search covers descriptions written by a
  local AI ([Ollama](https://ollama.com)), tags, places and events read from folder names, dates, and what is said in
  videos (transcribed with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)).
- **Gets the dates right.** It reads camera dates, folder names and file names. You can date old scans as loosely as
  you know them ("summer 1987", "the 1980s").
- **Timeline, map and tours.** Browse by year or on a map of where photos were taken and where family events happened.
  Play slideshows such as "the grandchildren, summer 2021". Home opens on memories: this week in earlier years, a
  birthday coming up, someone through the years.
- **Tidies up safely.** It lists duplicate copies, burst shots, smaller copies, blank and blurry shots, photos on their
  side, and files a disk has damaged. Nothing is removed until you pick it, and removed files can be restored.
- **Plays every video in the browser,** making 720p copies of camcorder and old phone formats that browsers can't
  play.

## Requirements

- **Docker** with Compose v2, on x86-64 or 64-bit ARM. A small ARM board is enough for LineageBox itself. Without
  Docker, you need Python 3.12 and ffmpeg.
- **Disk for the catalog:** about 0.5 MB per photo (10 GB for 20,000 photos), on the machine's own disk. It can't be
  on a network share.
- **For AI descriptions (optional):** Ollama with a vision model. A graphics card with 8 GB or more makes it fast. On a
  processor alone it works, but slowly.
- **Photos:** JPEG, PNG and Nikon NEF raw files, plus most video formats (MP4, MOV, MTS, 3GP, AVI, WMV, MKV and more).
  They can be in a local folder or on a network share (SMB). HEIC photos (iPhones' default) and other cameras' raw
  formats aren't read yet.
- **For family accounts:** a domain name that reaches your home, HTTPS (a reverse proxy such as Caddy is easiest),
  and a free Google sign-in client from Google Cloud. [Family accounts](docs/family-accounts.md) walks through it.

Every AI feature can be switched off, and the rest keeps working.

## Privacy

- Photos, faces, descriptions and the catalog stay on your machines. The AI models run locally.
- LineageBox downloads its face models (about 280 MB) the first time it looks for faces. If you use them, Ollama and
  Whisper download their models too.
- Outside services, and when they are used:
  - **The map's background** comes from OpenStreetMap's tile servers. Whoever opens the Map page loads it in their
    browser from tile.openstreetmap.org. The tile server can be changed in the config.
  - **Place names on the map** are looked up with OpenStreetMap's Nominatim, only once you turn it on. Only the place
    names are sent, a few a minute, under its [usage policy](https://operations.osmfoundation.org/policies/nominatim/).
  - **Family accounts** sign people in with Google, only once you turn them on.
  - **Updates:** once a day LineageBox asks GitHub for the newest release's version number, so admins can be told about
    an update. Nothing is sent but the question. **Settings → Updates** turns it off.
- Without family accounts, LineageBox only answers devices on your home network.

## Documentation

**Using and running LineageBox**

- [Installation guide](docs/install.md): photo sources, network shares, permissions, updates and backups
- [User guide](docs/user-guide.md): the family tree, naming people, searching, dates, tours and cleanup
- [Family accounts](docs/family-accounts.md): inviting relatives, who sees what, uploads and undo
- [Reaching it by name, HTTPS and from outside](docs/remote-access.md), which family accounts need
- [AI descriptions with Ollama](docs/ollama.md) and [video transcripts with Whisper](docs/whisper.md)
- [Troubleshooting](docs/troubleshooting.md)

**Reference and development**

- [Configuration reference](docs/configuration.md) and [command line](docs/command-line.md)
- [Architecture](docs/architecture.md), [design notes and measurements](docs/design-notes.md), and
  [access control](docs/access-control.md)
- [Development](docs/development.md): setting up, tests and conventions

## Updating

When a new version is out, admins see **Update available** at the top of every page. **Status → Version and updates →
Update now** installs it: LineageBox downloads the new version and restarts, in a few minutes. Before a new version
changes the catalog, LineageBox keeps a copy of it as it was. To update by hand instead:

```sh
docker compose pull && docker compose up -d
```

[Updating and backups](docs/install.md#updating-and-backups) has the details, and how to stay on one version.

## Contributing

Bug reports, ideas and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to get set up and run
the tests.

## License

Copyright © 2026 Scott Gusler.

LineageBox is free software under the [GNU Affero General Public License, version 3](LICENSE) (`AGPL-3.0-only`), and
comes with no warranty. You may use, change and share it. If you share a copy, changed or not, or let people use a
changed copy over a network, you must offer them its source code under the same licence.
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md#additional-permission-for-openssl) adds one permission, for the
OpenSSL libraries that come with OpenCV. The licence doesn't cover the name: a changed copy you share needs another one
([TRADEMARKS.md](TRADEMARKS.md)).

LineageBox finds and recognizes faces with InsightFace's `buffalo_l` models, which it downloads the first time it looks
for faces. They are not part of LineageBox, and InsightFace licenses them for **non-commercial research purposes only**.
Check that your use fits, or turn faces off (**Settings → Faces**). For other licensing, InsightFace can be reached at
recognition-oss-pack@insightface.ai.

[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists the software LineageBox and its Docker images include, and their
licences.

## Acknowledgements

LineageBox is built on [InsightFace](https://github.com/deepinsight/insightface), [Ollama](https://ollama.com),
[whisper.cpp](https://github.com/ggml-org/whisper.cpp), [FFmpeg](https://ffmpeg.org),
[ExifTool](https://exiftool.org), [FastAPI](https://fastapi.tiangolo.com), [SQLite](https://sqlite.org),
[Leaflet](https://leafletjs.com) and [OpenStreetMap](https://www.openstreetmap.org).
