# Quick start: everything on one machine

This guide sets up LineageBox and its AI helpers together in Docker, on one NAS, home server or PC. It takes about 15
minutes, plus the downloads. Then the library is catalogued in the background.

If you also have a desktop PC with a graphics card, the AI runs much faster there. See
[A NAS, plus a PC with a graphics card](nas-and-pc.md) instead.

## 1. Install Docker

You need Docker with Compose v2, on x86-64 or 64-bit ARM:

- **Linux:** [Docker Engine](https://docs.docker.com/engine/install/).
- **Synology, QNAP, Unraid, TrueNAS:** the NAS's own Docker or Container Manager package, with SSH turned on.
- **Windows or macOS:** [Docker Desktop](https://www.docker.com/products/docker-desktop/). It works, but every
  client looks like the same internal address, so the home-network check can't tell devices apart. A Linux machine is
  better for a long-running install.

Check that it works: `docker compose version`.

## 2. Get LineageBox

```sh
git clone https://github.com/mutilator/LineageBox lineagebox
cd lineagebox
cp .env.example .env
```

## 3. Say where your photos are

Open `.env` in a text editor and set:

```sh
PHOTOS=/volume1/photo        # the folder with your photos and videos (default: ./photos, next to compose.yaml)
TZ=Europe/London             # your time zone, for dates and the describing hours
PUID=1000                    # who owns those photos: `id -u` and `id -g` give your own
PGID=1000
```

Keep all your photos and videos inside that one folder. If they are in several places, see
[a folder of this machine](../install.md#a-folder-of-this-machine). LineageBox runs as that user, not as root, and
only reads this folder, except when you ask Cleanup to move a photo to the Removed folder. The folder must be there
already ([users and permissions](../install.md#users-and-permissions)).

To try LineageBox out first, leave `PHOTOS` as it is and copy a few hundred photos into the `photos` folder next to
`compose.yaml`.

Photos on another NAS or a Windows share? See [network shares](../install.md#a-network-share-smbcifs).

## 4. Turn on the AI helpers

Still in `.env`, add Ollama (photo descriptions) and Whisper (what is said in videos) to the compose files:

```sh
COMPOSE_FILE=compose.yaml:compose.ollama.yaml:compose.whisper.yaml
```

On Windows, separate them with `;` instead of `:`.

- **With an NVIDIA graphics card:** install the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  then add `:compose.ollama-nvidia.yaml` to the end.
- **With an AMD graphics card** (x86-64, supported by ROCm): add `:compose.ollama-amd.yaml` instead.
- **Without a graphics card:** this still works, but slowly. Pick a small model in step 6.
- **No AI at all:** leave `COMPOSE_FILE` out. Everything except descriptions and transcripts still works.

## 5. Start it

```sh
docker compose up -d
```

The first start downloads the images, which takes a few minutes. Later starts take seconds. New versions are installed
from the Status page ([updating](../install.md#updating-and-backups)).

Open **http://&lt;this machine's address&gt;:8000** in a browser. Use `http://localhost:8000` on the machine itself.
It opens on **Getting started**: the steps below and the next ones, each ticked off once it is done.

## 6. Pick your photos and the AI model

On the **Settings** page:

1. **Watched folders → Watch another folder:** open `local`, go to the folder with your photos and press **Watch**.
   You can watch more than one folder.
2. **AI (Ollama):** press **Download ministral-3 to Ollama**. That model is about 6 GB and wants a graphics card with
   8 GB of memory. On a processor alone, set **Model** to `gemma3:4b` first, then download that. It is smaller and
   much faster, and wrong a little more often. [Which model](../ollama.md#which-model) compares them.
3. Optional: **Whose photos**, e.g. "a family in Oregon, USA". This helps the AI read place names in folder names.

## 7. Let it work

The **Status** page shows what the worker is doing and how far it has got. It refreshes itself. The first pass:

1. **Reads every photo once** to make previews and thumbnails. Afterwards LineageBox works from those, not from the
   originals. A big library takes hours. Photos appear in **Folders** and **Timeline** as they are read.
2. **Looks for faces**, on this machine's processor: about one photo a second on a small ARM board, faster on a PC.
   The face models (about 280 MB) are downloaded the first time.
3. **Describes each photo** with the AI. With a graphics card this takes a few seconds a photo. On a processor alone it
   can take a minute or more, so a big library takes weeks. **Settings → Schedule** can limit it to the night.
4. **Transcribes videos** once Whisper has downloaded its models.

After that, the worker looks for new photos every hour. Every stage can be stopped and picks up where it left off.

## 8. What to do next

1. **Name people.** The **People** page shows groups of faces that look like one person. Type a name, then press
   **Find more photos of these people**. Start with the people who appear most.
2. **Start the family tree.** On your own person's page, mark **This is me**, then add parents, partners, children
   and siblings. LineageBox works out everyone else's relationship to you. Already have a tree in Ancestry, FamilySearch
   or Gramps? Export it as GEDCOM and use **Family → Bring in a family tree**.
3. **Invite the family.** Give LineageBox a public name with HTTPS ([remote access](../remote-access.md)), then set up
   [family accounts](../family-accounts.md). Each relative signs in with Google and sees their own branch and its
   photos.

Also try **Search** ("dog", "snow 2014", someone's name), and **Cleanup** for duplicates and blurry shots. Press **?** on
any page to see what it does and its keyboard shortcuts. The [user guide](../user-guide.md) covers every page. Before
updating, [back up the catalog](../install.md#updating-and-backups).

## If something goes wrong

- `docker compose ps` shows whether the containers are running and healthy.
- `docker compose logs -f worker` (or `web`, `ollama`, `whisper`) shows what they are doing.
- A red note on the Status page names the problem. **Check the connection** on the Settings page tells you more.
- See [troubleshooting](../troubleshooting.md).
