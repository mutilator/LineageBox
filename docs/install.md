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

## Quick start

```sh
git clone https://github.com/mutilator/LineageBox lineagebox && cd lineagebox
cp .env.example .env      # set PHOTOS, TZ, and PUID/PGID to the photos' owner; see below for the rest
docker compose up -d
```

Open `http://<that machine>:8000`, go to **Settings → Watched folders → Watch another folder**, open `local`, pick the
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
720p copies, written next to each video as `name.720p.mp4`) can write to them.

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
| Descriptions of photos and videos, and reading folder names | Ollama with a vision model: on another machine with a graphics card, or in Docker next to LineageBox | [ollama.md](ollama.md) |
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

A new version may change the catalog. Before it does, LineageBox copies the catalog as it was into the catalog folder's
`backups` folder (for example `catalog-v15-20261103-101500.db`) and keeps the last three. An older version won't open a
catalog a newer one has changed. To go back: stop LineageBox, put the copy back as `lineagebox.db`, delete
`lineagebox.db-wal` and `lineagebox.db-shm`, set `LINEAGEBOX_VERSION` to the version you had, and start it again.

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

Everything LineageBox knows is in the catalog folder (`DATA`, `./data` by default). The photos themselves are only read,
except by Cleanup, uploads and the 720p copies. To back up the catalog:

- **Stopped:** `docker compose stop`, copy the whole catalog folder, then `docker compose start`.
- **Running:** make a consistent copy of the database, then copy the rest of the folder:

  ```sh
  docker compose exec web python -c "import sqlite3; d = sqlite3.connect('/data/lineagebox-backup.db'); sqlite3.connect('/data/lineagebox.db').backup(d); d.close()"
  ```

  Thumbnails, previews and video frames can be made again from the photos, but that takes as long as the first pass.

## Logs and commands

- Logs: `docker compose logs -f worker` (or `web`, `copies`, `ollama`, `whisper`).
- A one-off command runs in a container, e.g. `docker compose exec worker python -m lineagebox status`. The
  [command line](command-line.md) lists them all.

## Uninstalling

`docker compose down` stops and removes the containers. Your photos are untouched, apart from any Removed folders and
720p copies LineageBox made. To remove everything LineageBox made, also delete the catalog folder and, if you used them,
the Docker volumes: `docker volume rm lineagebox_ollama lineagebox_whisper lineagebox_nas`.
