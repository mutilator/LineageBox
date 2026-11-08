# LineageBox

**Your family's history, photos and home videos in one private place, shared with the family.**

<br>

<h2 align="center"><a href="https://linbox.g-tech.dev">▶&nbsp;&nbsp;Try the live demo: linbox.g-tech.dev</a></h2>

<p align="center">A made-up family's photos, papers and family tree to look around in and change.<br>
Sign in with one click, as a family member or as an admin. It all goes back as it was twice a day.</p>

<br>

LineageBox is a self-hosted family archive. It joins two things that usually live apart:

- **the family tree:** who everyone is, how they are related, the events of their lives, and the documents that prove
  them;
- **the photos and videos they appear in.**

It reads the photo folders you already have and recognizes the faces in them. Every photo is then linked to the people
in your tree: open a great-grandmother's page and see her family, her life events, her birth certificate and every
photo she's in.

Then the family comes in. Relatives sign in with their Google account or a passkey and see **their own branch**: their ancestors,
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

Then open `http://<that machine>:8000`. **Getting started**, on the first page, lists these steps and ticks each off
once it is done:

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
  view. Relationships work in search: "cousins 2019", "grandma christmas". The whole line down from any ancestor
  can be moved around and zoomed, and saved as one large picture.
- **Each person's life story.** Their page holds the story the family writes, a portrait you choose, and their life
  by age: life events, the documents and sources that bear them out, and their photos at each age. Adoption and
  fostering are kept as such ("adoptive mother", "foster brother").
- **Life events, documents and sources.** Births, marriages, moves and deaths, adoptions, bar and bat mitzvahs,
  censuses and more, with dates as exact as you know them ("about 1950"). Mark a photo of a certificate, an obituary
  or a letter as a document, cite a book, a website or a census page, and link each to the person or event it
  records.
- **Reads obituaries and notes into the tree.** Paste an obituary, an announcement or notes, or add photos of one:
  the local AI lists the people it names and what it says of them, matched to the people you have, as changes you
  tick one by one.
- **Works with genealogy software.** It imports and exports GEDCOM 7.0 and 5.5.1, for Ancestry, FamilySearch,
  MyHeritage, Gramps and the like, with portraits cropped from faces, sources, adoptions and places' coordinates.
  Imported people are matched to the people you already have, and you check every match; their photos are found in
  your folders by name.
- **Checks the tree for mistakes:** a missing parent, dates that can't be right, two entries that may be one person,
  and photos that don't fit the dates (someone named in a photo from before they were born).
- **A family book.** A person, or everyone descended from them, as pages to print or save as a PDF: portraits, life
  stories, life events, family, documents and the best photos year by year. A gift for a reunion or a memorial.

### Shared with the family

- **Sign in with Google, or a passkey.** You invite relatives by email address. Those without a Google account get a
  link that makes a passkey: they sign in with their phone's or computer's own lock. No passwords to manage, and no
  sign-up page.
- **Everyone sees their own branch.** Each relative sees their part of the tree and the photos its people are in.
  Partners see the same family. Admins see everything and can make exceptions.
- **The whole family contributes.** Relatives name faces, fix dates, add relatives and events, and upload photos,
  videos and whole folders from the browser. **Name faces** puts one face at a time in front of them, with Yes, No and
  Skip, easy on a phone. **Who is this?** asks the family about a face nobody has named yet.
- **The family's own words.** Anyone who can see a photo can write its caption, who, where and what happened, and
  comment under it. Their words show above the AI's, in search and in slideshows.
- **Recorded stories.** Older relatives tell instead of typing: a voice note recorded in the browser or on the phone,
  about a photo, a person or one of their life events. When Whisper is set up, what they say is written down and
  found by search.
- **Everything can be undone.** Recent changes lists what anyone changed, admins included, with Undo; a change that
  would let someone see more of the family is flagged.
- **New for you.** A bell at the top counts what happened since you last looked: something shared with you, a face
  someone asked about in your photos, new photos of your family, words written on your photos. Admins are also told
  of damaged files, a disk nearly full, a service with a problem and a new version. It is all inside the site:
  nothing is emailed or sent anywhere.
- **On the phone's home screen.** Over HTTPS, LineageBox installs as an app (no app store). On Android, photos shared
  from the gallery go straight to Upload.
- **Photo frames and TVs.** An admin makes a link for an old tablet, a smart TV or a Home Assistant dashboard that
  shows memories, a tour or an album full screen, a photo at a time, without anyone signing in on it. The link opens nothing
  else and can be turned off at any time.
- At home, without accounts, LineageBox simply answers the home network, with no sign-in.

### Photos and home videos

- **Recognizes faces.** Name a group of faces once, and LineageBox finds that person in the rest of the library, across
  decades. It also suggests who the unnamed people in a photo might be, who a group of unnamed faces looks like, and
  people named twice; and it lists names that can't fit someone's age and group photos with faces missed.
- **Search in plain words.** "Birthday cake", "beach 2014", someone's name, with suggestions as you type and filters
  (people together, years, a place, photos or videos, faces to name). Search covers the family's captions and
  comments, descriptions written by a local AI ([Ollama](https://ollama.com)), text on signs and cakes, tags, places
  and events read from folder names (correctable on each folder's page), dates, documents, and what is said in videos
  (transcribed with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)). It also finds what the words mean:
  "kids sledding" finds children on a snowy hill, and each photo shows **more like this**. Whenever the AI's computer
  is off, search finds words alone.
- **The AI on request.** Have a photo, or a whole selection, described again, by a better model if you like; captions
  and tags people gave stay. Rename a tag across the library, merge two, or take one off every photo for good.
- **Made for phones too.** Swipe through photos, pinch to zoom, tap a face on the photo to name it. A short menu (the
  rest under More), light or dark as each browser chooses, and a person's page and the family that print cleanly.
  Big folders and years load as you scroll, with the years and months at the side to jump to, and thumbnails stay
  sharp on a phone's screen. Tick photos on any list (Shift and a click for a run of them) to tag, date or file them
  together.
- **For the keyboard and screen readers.** Every thumbnail says who is in it and when; the slideshow, the map and
  uploading work from the keyboard; and keys of one letter can be turned off for speech software.
- **Ready for other languages.** It comes in English, and its pages, scripts, help, dates and numbers are all ready to
  be translated: a family abroad adds a translation file, and each relative sees the site in their browser's
  language ([translating](docs/translating.md)).
- **Gets the dates right.** It reads camera dates, folder names and file names. You can date old scans as loosely as
  you know them ("summer 1987", "the 1980s"), or take its suggestion from the photos beside them and who is in them.
  Copies take their original's camera date, and a camera whose clock was wrong is put right at once.
- **Timeline, map and tours.** Browse by year or on a map of where photos were taken and where family events happened.
  Photos with GPS are named after the nearest town without going online, and anyone can put a photo on the map by
  hand (an admin, a whole folder).
  Play slideshows such as "the grandchildren, summer 2021", with a title card, the family's captions and dates as
  exact as they are known ("Summer 1987"), and save one as a video file to send. Home opens on memories: this week in
  earlier years, a birthday coming up, someone through the years.
- **Albums and favourites.** Pick photos by hand for a wedding or a reunion, put them in order, choose a cover, caption
  each and share the album with the family. A heart on a photo makes it a favourite, which tours and memories show
  first.
- **Tidies up safely.** It lists duplicate copies, burst shots, smaller copies, blank and blurry shots, photos on their
  side, and files a disk has damaged. Nothing is removed until you pick it, and removed files can be restored.
- **Plays every video in the browser,** making 720p copies of camcorder and old phone formats that browsers can't
  play. iPhone photos (HEIC), most cameras' raw files, and Live Photos (shown as one photo that moves) are read too.
- **Long tapes and sound.** A digitised tape gets chapters, where each recording on it starts, and frames all through
  it. Cassettes and voice memos are catalogued as sound recordings, played and transcribed. Any line Whisper misheard
  can be corrected, and it hears whichever language is spoken.
- **Keeps the family's work safe.** Every night it makes a checked copy of the catalog (the names, the tree, the words)
  where your backup program can take it, and **Download everything** gives one file of it that other programs read.
- **Works with other photo programs.** A library already tagged in Picasa, digiKam, Lightroom, Windows Photo Gallery or
  Google Photos (Takeout) starts out named: faces, keywords, captions and dates come in, below anything set here.
  The other way, the names, captions and places the family gives go into XMP files beside the photos once you turn
  that on, and downloads always carry them.

## Requirements

- **Docker** with Compose v2, on x86-64 or 64-bit ARM. A small ARM board is enough for LineageBox itself. Without
  Docker, you need Python 3.12 and ffmpeg.
- **Disk for the catalog:** about 0.5 MB per photo (10 GB for 20,000 photos), on the machine's own disk. It can't be
  on a network share.
- **For AI descriptions (optional):** Ollama with a vision model. A graphics card with 8 GB or more makes it fast. On a
  processor alone it works, but slowly. Search by meaning adds a small text model (nomic-embed-text, 274 MB), which a
  processor alone runs well.
- **Photos:** JPEG, PNG, TIFF (scans, shown through a JPEG copy browsers can display), HEIC (iPhones' photos, with
  Live Photos kept as one photo) and most cameras' raw files
  (Nikon, Canon, Sony, Fujifilm, Olympus, Panasonic, Pentax, DNG and more), plus most video formats (MP4, MOV, MTS,
  3GP, AVI, WMV, MKV and more) and sound recordings (MP3, M4A, WAV, WMA, Ogg, FLAC, AMR). They can be in a local
  folder or on a network share (SMB).
- **For family accounts:** a domain name that reaches your home, HTTPS (a reverse proxy such as Caddy is easiest),
  and a free Google sign-in client from Google Cloud, or passkeys alone. [Family accounts](docs/family-accounts.md)
  walks through it.

Every AI feature can be switched off, and the rest keeps working.

## Privacy

- Photos, faces, descriptions, recorded stories and the catalog stay on your machines. The AI models run locally:
  what a search means is read by your own Ollama too.
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
- Naming photos with GPS after a town uses a list of towns that comes with LineageBox
  ([GeoNames](https://www.geonames.org/), CC BY 4.0): nothing is looked up online for it.
- LineageBox writes in the photo folders only when asked: Cleanup, uploads, 720p copies, turning a photo, and, once you
  turn them on, XMP sidecars beside the photos (IMG_1234.JPG.xmp) with the names, captions and places the family gave.
  It writes those into a photo file itself only when an admin asks, on that photo's page.
- Nothing is emailed or pushed to phones: what is new for each person (and admins' alerts) is shown inside the site.
- A photo frame's link works without signing in, so whoever has it sees what it shows (one view of memories or a
  tour, nothing else). By default it works only on the home network; an admin can turn any link off.

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
[Leaflet](https://leafletjs.com), [OpenStreetMap](https://www.openstreetmap.org) and
[GeoNames](https://www.geonames.org).
