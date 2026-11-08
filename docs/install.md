# Installation guide

This guide covers a Docker install in full: where photos can come from, network shares, permissions, the AI helpers,
updating and backups. For the short version, start with a [quick start guide](README.md#getting-started).

LineageBox runs as three containers from one image:

- **web**: the web UI, on port 8000.
- **worker**: looks for new photos every hour and takes them through every step: reading, duplicates, dates, faces,
  places, descriptions and search.
- **copies**: makes 720p copies of the videos browsers can't play.

AI descriptions need [Ollama](https://ollama.com), and video transcripts need LineageBox's Whisper service. Each can run
on another machine with a graphics card, or in Docker next to LineageBox ([below](#ollama-whisper-and-faces)). Face
recognition runs on the LineageBox machine's own processor.

## What you need

- **Docker** with Compose v2, on x86-64 or 64-bit ARM.
- **Room for the catalog** (database, thumbnails, previews): about 0.5 MB per photo, so 10 GB for 20,000 photos. It
  must be on the machine's own disk, because SQLite databases get corrupted on network shares. LineageBox refuses to
  start when it can tell the folder is on one.
- **Ollama with a vision model** (optional), on any machine LineageBox can reach or in Docker next to it. Without it,
  everything except descriptions and reading folder names works.
- **Internet access the first time faces are looked for.** The face models (InsightFace `buffalo_l`, about 280 MB) are
  downloaded into the catalog folder. InsightFace licenses them for non-commercial research purposes only: check
  that your use fits, or turn faces off on the Settings page.
- **HTTPS by a name** (optional), for putting LineageBox on phones' home screens and sharing photos to it from them:
  phones install a site only over HTTPS with a certificate they trust ([remote access](remote-access.md)). Over plain
  http everything else works.

## Quick start

```sh
git clone https://github.com/mutilator/LineageBox lineagebox && cd lineagebox
cp .env.example .env      # set PHOTOS, TZ, and PUID/PGID to the photos' owner; see below for the rest
docker compose up -d
```

Open `http://<that machine>:8000`: it opens on **Getting started**, the first steps (watch a folder, check the AI,
name yourself, name the largest groups of faces, start the tree, invite someone), each a link to where it is done and
ticked off once it is. Go to **Settings → Watched folders → Watch another folder**, open `local`, pick the
folder with your photos and press **Watch**. The worker starts within a minute, and **Status** shows how far it has
got. The first pass reads every photo once, which takes a while for a big library. After that, only new photos are
read.

With no `.env` at all, LineageBox uses the `photos` and `data` folders next to `compose.yaml`, port 8000 and UTC, and
runs as user 1000 ([users and permissions](#users-and-permissions)).

This downloads LineageBox's published image (for x86-64 and 64-bit ARM). To build it here from this folder's code
instead, run `docker compose up -d --build`. **Update now** on the Status page then replaces it with the published
image, so set `LINEAGEBOX_IMAGE` to a name of your own (for example `lineagebox-local`) to keep your build.

## Where the photos come from

Inside the containers, every photo source is a folder of `/photos`. The Settings page browses `/photos`, so all
sources appear there as top-level folders. A source's name starts every path in the catalog, so choose it once. If you
rename it later, its photos are read again. Their descriptions and faces are kept, because those belong to each photo's
contents, not its path.

### A folder of this machine

`PHOTOS` in `.env` is mounted as `local`, and that one folder is usually all LineageBox needs:

- **Keep the photos and videos together, in one folder** (with as many folders inside it as you like), and set `PHOTOS`
  to it. If they are spread over several folders now, moving them under one is worth it: one place to back up, and
  nothing more to set up here.
- **If they have to stay where they are**, on the same disk, set `PHOTOS` to a folder that holds them all, such as
  `/volume1/family`, rather than mounting each one. Then watch only the folders with photos on the Settings page:
  LineageBox never looks in the others.

Photos on another disk of this machine need a mount of their own. Give each service one more line in a
`compose.override.yaml` next to `compose.yaml`. Docker compose reads that file by itself, unless `COMPOSE_FILE` is set
in `.env`: then add `:compose.override.yaml` to the end of it. Make the folder writable for `web` and `copies`, and
read-only (`:ro`) for `worker`. The folder must be there already: Docker would make a missing one as root.

```yaml
services:
  web:
    volumes:
      - /volume2/scans:/photos/scans
  worker:
    volumes:
      - /volume2/scans:/photos/scans:ro
  copies:
    volumes:
      - /volume2/scans:/photos/scans
```

If LineageBox must never write to a source, mount it `:ro` in `web` and `copies` too. Cleanup and 720p copies are then
unavailable for that source, and Settings says so.

### A network share (SMB/CIFS)

Docker mounts the share itself, with a login. In `.env`:

```sh
COMPOSE_FILE=compose.yaml:compose.smb.yaml      # on Windows: compose.yaml;compose.smb.yaml
SMB_SHARE=//192.168.1.20/photos
SMB_USERNAME=me
SMB_PASSWORD='my password'
```

The share appears as `nas`. Things to know:

- **Server name or address:** recent Docker versions look up a server name such as `//mynas/photos` themselves. If
  the share doesn't mount, use the address.
- **The password:** put it in single quotes. It can't contain a comma, because the mount options are separated by
  commas.
- **Older servers:** set `SMB_VERSION=2.1`, or `1.0` for very old NASes. Further options go in `SMB_OPTIONS`, starting
  with a comma, e.g. `SMB_OPTIONS=,seal`.
- **Changing the share or login later:** Docker keeps the options it was first given. Run
  `docker compose down && docker volume rm lineagebox_nas`, then `docker compose up -d`. That only unmounts the share,
  and nothing on it is touched.
- **If the share is down at startup,** Docker doesn't start the containers, so the web UI is down too. To keep
  LineageBox up without the share, mount it on the host instead (an `/etc/fstab` line with `nofail`) and add it as a
  folder of this machine.
- **If the share drops while LineageBox runs,** the worker treats an empty or unreadable source as offline. Nothing is
  marked missing until it is back.
- **A second share:** copy the `nas` volume in `compose.smb.yaml`, and its three service lines, under another name.
- **Ownership:** files on the share appear to belong to `PUID`/`PGID` ([below](#users-and-permissions)).

### Copy network path

Each photo's page has **Copy network path**, for pasting into File Explorer. Tell LineageBox where people's PCs find
each source:

```sh
NETWORK_PATHS=local=\\mynas\photo,nas=\\192.168.1.20\photos
```

Or set them on **Settings → On the network**: one field for the photos folder and one for each source. Once saved
there, the page's values are used instead of `NETWORK_PATHS`. With a network path set, you can also paste such a path
on the Settings page to watch a folder.

### Cleanup and the Removed folder

Cleanup only moves files, and only when you ask it to. Removed files go to a Removed folder in the same source, so they
can be restored:

- next to the watched folder (`Pictures-Removed` for `local/Pictures`);
- inside it, as `LineageBox-Removed`, when the watched folder is a whole source (you watched `nas` itself). That's
  because a file can't be moved onto another mount.

The scan never reads folders whose names end in `-Removed`. **Empty the Removed folder** is the only thing in LineageBox
that deletes files.

The worker mounts the photos read-only. Only the web container (for Cleanup and uploads) and the copies container (for
720p copies, written next to each video as `name.720p.mp4`) can write to them. Once **XMP sidecars** are turned on
(**Settings → Other photo programs**), the copies container also writes one beside each photo the family gave names,
a caption, tags, a date or a place (`IMG_1234.JPG.xmp`): a few minutes at a time, between its copies. A share mounted
read-only for every container leaves them waiting, and the Settings page says so. From source, the worker writes
them itself.

## Users and permissions

LineageBox runs as the user and group in `PUID` and `PGID` (1000 unless set), with no root powers and no setuid
programs, so a bug in a program that reads photos and videos (ffmpeg, say) can write only what that user may. Set them
in `.env` to whoever owns your photos: `id -u` and `id -g` give your own. On a Synology, the first user is usually
1026, in group 100 (`users`).

```sh
PUID=1000
PGID=1000
```

That user must be able to write to the catalog folder, and to the photo folders for Cleanup, uploads and 720p copies.
What LineageBox writes there (the catalog, Removed folders, 720p copies) belongs to them.

**The folders must be there before the first start.** Docker would make a missing one itself, as root, which
LineageBox couldn't write, so `compose.yaml` stops at `bind source path does not exist` instead. `git clone` makes the
default `data` and `photos` folders, as whoever clones. For a `DATA` or `PHOTOS` somewhere else, make the folder first,
as that user: `mkdir -p /volume1/docker/lineagebox`.

- **"... can't be written by the user LineageBox runs as"** when it starts: the folder belongs to someone else, often
  root, because Docker or an earlier install made it. Set `PUID`/`PGID` to its owner, or give it to theirs:
  `sudo chown -R 1000:1000 data`.
- **Rootless Docker or Podman:** the containers' root is you, so set `PUID=0` and `PGID=0`.
- **Photos that belong to several users**, so that no one user can write them all: run as root, with only the root
  powers writing for other users needs. Set `PUID=0` and `PGID=0`, and give the three services these in
  `compose.override.yaml`. `DAC_OVERRIDE` reads and writes other users' files, `CHOWN` gives 720p copies, Removed
  folders and turned photos to the original's owner, and `FOWNER` works in folders with the sticky bit.

  ```yaml
  services:
    web:
      cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]
    worker:
      cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]
    copies:
      cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]
  ```

## Ollama, Whisper and faces

Three kinds of AI work are done for LineageBox. Each can be switched off on the Settings page, and the rest keeps
working:

| What | Done by | Set it up |
|---|---|---|
| Descriptions of photos and videos, and reading folder names; search by meaning (a small second model) | Ollama with a vision model: on another machine with a graphics card, or in Docker next to LineageBox | [ollama.md](ollama.md) |
| Transcripts of what is said in videos (optional) | LineageBox's speech-to-text service: on a PC with an NVIDIA card, or in Docker next to LineageBox | [whisper.md](whisper.md) |
| Finding and recognizing faces | The LineageBox machine's processor | Nothing to set up |

With no other machine, everything can run in Docker on the LineageBox machine:

```sh
COMPOSE_FILE=compose.yaml:compose.ollama.yaml:compose.whisper.yaml
```

That works without a graphics card too, but slowly.

The Status page shows:

- under **Downloads**, the face models, a model Ollama is downloading for LineageBox, and the speech-to-text service's
  models;
- a red note when Ollama or Whisper can't do the work, for example a model that can't look at pictures, or an error it
  answers every request with. **Check the connection** on the Settings page tells you more.

While the AI machine is asleep or off, descriptions and transcripts wait. The worker notices within five minutes when
it is back.

## Docker Desktop, routers and host networking

**Docker Desktop on Windows or a Mac** shows every client as the same internal address. Run LineageBox on Linux if the
home-network check needs to tell clients apart.

**On a router, or wherever containers on Docker's bridge network can't reach the DNS server** (OpenWrt, for example),
use host networking. Put this in a `compose.override.yaml` next to `compose.yaml` (and add it to `COMPOSE_FILE` if
that is set):

```yaml
services:
  web:
    build:
      network: host
    network_mode: host
    ports: !reset []
    command: ["serve", "--host", "192.168.1.1", "--port", "8000"]   # the LAN address to answer on
  worker:
    network_mode: host
  copies:
    network_mode: host
    cpus: 2                 # leave the rest of the processor for routing
```

With host networking, LineageBox also sees each client's real address for its home-network check.

## Updating and backups

Every release is published as its version (`1.2.3`), as `1.2`, and as `latest`, at the same time. `compose.yaml` runs
`latest`, so updating means downloading `latest` again and restarting the containers with it:

- **From the browser:** admins see **Update available** at the top of the page when a new version is out. **Status →
  Version and updates → Update now** installs it. The `updater` service downloads the new images and restarts
  LineageBox's containers with them, the web UI last; the page waits, and shows the new version when it is back. If a
  new version doesn't start, the one before is put back. Ollama, and anything else on the machine, is left alone.
- **By hand:** `docker compose pull && docker compose up -d` in LineageBox's folder.
- **One version only:** set `LINEAGEBOX_VERSION=1.2.3` in `.env` and run `docker compose up -d`. LineageBox stays on
  that version, and Update now only reinstalls it. Remove the line to go back to the newest.

The updater has the Docker socket, which is the same as root on this machine. It has no network and reads nothing
but whether the web app made the file `update/requested`; [SECURITY.md](../SECURITY.md) says what it may and may not
do. To do without it, add this to `compose.override.yaml` (or one of your compose files) and update by hand:

```yaml
services:
  updater:
    deploy:
      replicas: 0
```

With rootless Docker the socket is elsewhere: set `DOCKER_SOCKET` in `.env` (for example
`/run/user/1000/docker.sock`).

Once a day LineageBox asks GitHub for the newest release's version number, to know that an update is out. **Settings →
Updates** turns that off.

### Backups

Everything LineageBox knows is in the catalog folder (`DATA`, `./data` by default), and what only your family knows
(the names on faces, the family tree, dates and tags given by hand, captions and comments) is in one file of it: the
catalog, `lineagebox.db`. The one other thing there that can't be made again is the **stories** folder: the sound of
the stories the family records on photos and people's pages, kept as they were recorded (a few MB each), never in
the photo folders. Thumbnails, previews and video frames can be made again from the photos, though that takes
as long as the first pass. The **thumbs2x** folder holds thumbnails at twice the size, made from the previews when a
phone or another sharp screen first shows them, and kept: they need no backup, and the folder can be emptied at any
time (they are made again as they are wanted). The **videos** folder holds tours and albums saved as a video (Save as a video), each kept
30 days; they can be made again from their tour or album at any time, so the backups leave them out. Albums,
their captions and the hearts on photos are in the catalog. The photos themselves are only read, except by Cleanup, uploads and the 720p copies.
The XMP sidecars beside the photos, when they are on, are made from the catalog and written again whenever it
changes, so a restored catalog brings them up to date by itself: they need no backup of their own.

**Nightly copies.** On its first pass of each day, the worker copies the catalog into the catalog folder's
`backups/nightly` (as `lineagebox-20261107-001500.db`). It copies it with SQLite's backup API while LineageBox runs,
opens the copy again and reads it whole to check it, and only then gives it its name. Then it deletes the copies no
longer kept: by default the newest of each of the last 7 days, and of each of 4 weeks before those. So a copy that
fails never costs the last good one. **Status → Backups** shows the last good copy, and the Status page says so when
a copy failed or none has been made for two days. **Settings → Backup** switches the copies off, picks another folder
and how many to keep, and makes one now, as `python -m lineagebox backup` does.

A backup program that copies the catalog folder (a NAS's backup app, a sync to the cloud) should take these copies, not
`lineagebox.db` itself. That one changes all the time, and its latest changes are in `lineagebox.db-wal` beside it, so
a copy of it made while LineageBox runs may not open. Each nightly copy is as big as the catalog, about 8 KB a photo,
and isn't compressed: it is ready to put back as it is, and a backup program that deduplicates sends only what changed
since the night before. Each holds everything the catalog does, accounts and admins' sign-in codes too: keep the
folder as private as the catalog folder. (New for you's events and photo frames' links are in the catalog as well;
of a link only its token's SHA-256, so a copy opens no frame.)

**Recorded stories** are copied beside the copies too, into their `stories` folder: each recording once (they never
change), and deleted there only once the catalog no longer has it (a story taken away is kept a month, so that Undo
can bring it back, then deleted for good). A backup program that takes the copies should take that folder with them.

**On another disk.** A copy on the catalog's disk is lost with that disk. To keep the copies somewhere else (a second
disk, a NAS share, or a folder that the Dropbox or Google Drive app syncs), mount that folder into the web and worker
containers, for example in `compose.override.yaml`:

```yaml
services:
  web:
    volumes:
      - /mnt/usb/lineagebox-backups:/backups
  worker:
    volumes:
      - /mnt/usb/lineagebox-backups:/backups
```

Then, after `docker compose up -d`, give the folder as the containers see it (`/backups`) under **Settings → Backup →
Folder**. It must exist and be writable by PUID's user, and be outside the photo folders. The first copy goes there
right away. For a folder a cloud app syncs, fewer copies (3 a day and 2 a week) keep within its storage. A
`config.toml` in the catalog folder, if you have one, is copied beside the copies too.

**Copies before an upgrade.** A new version may change the catalog. Before it does, LineageBox copies the catalog as
it was into the catalog folder's `backups` folder (for example `catalog-v15-20261103-101500.db`) and keeps the last
three. An older version won't open a catalog a newer one has changed. To go back to the version you had: put that copy
back as below, and set `LINEAGEBOX_VERSION` to the version you had before starting LineageBox again.

**Putting a copy back:**

1. Check the copy: `docker compose run --rm --no-deps worker backup --check
   /data/backups/nightly/lineagebox-20261107-001500.db` (any path the container sees). It says whether the copy is
   whole, which catalog version it is, and how many photos, people and named faces it holds. It needs no catalog, so
   it works when the catalog is what was lost.
2. Stop LineageBox: `docker compose stop`.
3. In the catalog folder, move `lineagebox.db`, `lineagebox.db-wal` and `lineagebox.db-shm` aside (keep them until the
   copy works), and copy the backup in their place as `lineagebox.db`. If the catalog folder's `stories` folder was
   lost too, copy the backup folder's `stories` folder in its place (as it is: two-letter folders of recordings).
4. Start it again: `docker compose start`. A copy from an older version is upgraded as it starts, after being copied
   as it was.

What was done after the copy was made is gone; photos added since are found again by the next scan. Stories recorded
since then are not deleted: the catalog no longer knows them, so the next day LineageBox moves their sound into the
`stories/set-aside` folder, where they stay. If the whole
catalog folder was lost, the worker makes the previews and thumbnails again from the photos (it looks for missing ones
once a day) and downloads the face models again. From source, stop `serve` and `worker` and do the same in the data
folder; `python -m lineagebox backup --check <file>` checks a copy.

**Leaving LineageBox.** **Settings → Backup → Download everything** (or `python -m lineagebox export <file>`) makes one
zip that other programs read without LineageBox: the family tree as GEDCOM, and each photo's paths, dates, tags, names
on faces, captions and comments as JSON. [The export format](export.md) describes it.

## Logs and commands

- Logs: `docker compose logs -f worker` (or `web`, `copies`, `ollama`, `whisper`).
- A one-off command runs in a container, e.g. `docker compose exec worker python -m lineagebox status`. The
  [command line](command-line.md) lists them all.

## Uninstalling

`docker compose down` stops and removes the containers. Your photos are untouched, apart from any Removed folders and
720p copies LineageBox made. To remove everything LineageBox made, also delete the catalog folder and, if you used them,
the Docker volumes: `docker volume rm lineagebox_ollama lineagebox_whisper lineagebox_nas`.
