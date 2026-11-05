# Troubleshooting

Start with the **Status** page. A red note there names most problems, and **Check the connection** on the Settings page
tests Ollama and Whisper. Then look at the logs:

```sh
docker compose ps                  # running and healthy?
docker compose logs -f worker      # or web, copies, ollama, whisper
docker compose exec worker python -m lineagebox status
```

## The web page doesn't open

- **"Only available on the home network"**: the request didn't come from a private address. If you go through a
  reverse proxy, set it under **Settings → Address and HTTPS → Reverse proxy** (or `TRUSTED_PROXIES`). If you use
  Docker Desktop, every client looks like one internal address. See [remote access](remote-access.md).
- **"Not set up to be reached by this name"**: LineageBox only answers addresses, home-network names and the host names
  you list. Open it by address, then add the name under **Address and HTTPS → Host names**.
- **Nothing answers after turning HTTPS on**: use `https://`. `docker compose exec web python -m lineagebox https off`
  turns it off.
- **`bind source path does not exist`** when starting: the folder in `PHOTOS` or `DATA` isn't there. Correct the path,
  or make the folder as `PUID`'s user (`mkdir -p`). Docker would otherwise make it as root.
- **The container keeps restarting**: `docker compose logs web` usually says why. Two common reasons:
  - the catalog folder is on a network share, which LineageBox refuses because SQLite databases get corrupted there;
  - the catalog folder belongs to another user than `PUID`'s. The message says what to change
    ([users and permissions](install.md#users-and-permissions));
  - a setting in `.env` or a config file can't be used. The message names it.
- **Signed out of everything after turning accounts on**: everyone signs in once accounts are on, at home too.
  `docker compose exec web python -m lineagebox accounts off` turns them off.

## No photos appear

- Is a folder watched? **Settings → Watched folders**. The first pass starts within a minute of saving.
- Does the container see the photos? `docker compose exec worker ls /photos/local`. If that is empty, check `PHOTOS`
  in `.env`, then run `docker compose up -d` again.
- **A network share** that can't be reached at startup stops the containers from starting, and one that drops
  later makes the worker treat its photos as offline. Nothing is marked missing until it is back.
- Folders named like thumbnail caches and recycle bins (`Thumbs`, `@eaDir`, `#recycle`) and folders ending in
  `-Removed` are skipped on purpose.
- Only the file types in `image_extensions` and `video_extensions` are read ([configuration](configuration.md)).

## Descriptions or transcripts don't happen

- **Status says Ollama is not answering:** the machine is asleep or off, the address is wrong, `OLLAMA_HOST` isn't
  set to `0.0.0.0` on it, or a firewall is in the way. The worker checks again every 5 minutes.
- **"Ollama has no model named …"**: press **Download … to Ollama** on the Settings page.
- **"… can't look at pictures"**: the model isn't a vision model. Pick one from [the list](ollama.md#which-model).
- **"Describing failed: Ollama answered 500"**: most often the model doesn't fit in memory. Pick a smaller one, or send
  fewer photos at a time.
- **Nothing happens during the day**: check **Settings → Schedule → When the AI may describe**.
- Whisper: see [when something is wrong](whisper.md#when-something-is-wrong).

## Faces

- **No faces yet**: faces come after the photos are read. The first time, the face models (about 280 MB) are
  downloaded, which needs internet access. **Status → Downloads** shows them. If Status says they *are not the ones
  this version of LineageBox expects*, what GitHub sent didn't match their SHA-256 and wasn't used; it is tried again
  once a day, and an update of LineageBox may be needed.
- **Out of graphics memory** when run from source: run `faces` while Ollama is idle, or with `--cpu`.
- **Two people confused** (young siblings, most often): name a few more photos of each by hand. Recognition only
  names faces that clearly match one person, and never overwrites a name given by hand. "Fix" on a face named
  wrongly in a photo puts the right name on it, and that face is never matched to the wrong person again.

## "The catalog is busy" (503)

The database was locked by a long write for longer than the web UI waits. Try again in a moment. It can happen while
the worker writes a big batch. If it happens often, `docker compose logs worker` shows what it was doing.

## Pages are slow

**Status → Slow pages** lists the pages that took LineageBox more than a second to answer since it last started, and
`docker compose logs web` has a line for each (`slow: GET /tags took 1.8 s`). The browser's developer tools show how
long each answer took on the server (`Server-Timing`). Counts that take a while on a big library (tags, people, the
faces to confirm, Cleanup's numbers) are kept for a minute, so a page is slow the first time and quick after that,
until something is changed on the site. The first look at a big library also takes longer while the worker is still
reading it.

## Dates are wrong

Photos without a camera date are dated by folder name, then file name, then the file's time. **Status → Where dates
come from** lists the ones dated by file time. Fix them one by one under **Taken** on the photo's page, or many at once
with **Select photos** ([dates](user-guide.md#dates)).

## Something else

Open an issue with:

- what you did, what you expected and what happened;
- the end of `docker compose logs worker` and `docker compose logs web`;
- how LineageBox runs: Docker or from source, the machine, and where Ollama runs.

Leave out photos, names and addresses you don't want public.
