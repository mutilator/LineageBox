# Quick start: a NAS, plus a PC with a graphics card

This is the fastest setup for a big library:

- **LineageBox** runs in Docker next to the photos, on a NAS or home server that is always on. It reads the photos,
  finds faces and serves the web page.
- **The AI** runs on a desktop PC with a graphics card: Ollama for descriptions, and optionally Whisper for video
  transcripts. LineageBox sends it previews over the network. While the PC is asleep or off, the AI work waits and
  everything else carries on.

A graphics card with 8 GB or more describes a photo in a few seconds, against a minute or more on a NAS processor.

## On the PC: Ollama

1. **Install Ollama** from [ollama.com/download](https://ollama.com/download).
2. **Let the NAS reach it.** By default Ollama only answers its own machine. Set the environment variable
   `OLLAMA_HOST` to `0.0.0.0`, then quit Ollama and start it again.
   - **Windows:** *Settings → System → About → Advanced system settings → Environment Variables*, then add a user
     variable.
   - **macOS:** `launchctl setenv OLLAMA_HOST 0.0.0.0`.
   - **Linux:** `sudo systemctl edit ollama`, then add `Environment="OLLAMA_HOST=0.0.0.0"` under `[Service]`.
3. **Open the firewall** for TCP port 11434, for your home network only. On Windows, in an administrator PowerShell
   (with your own network's range):

   ```powershell
   New-NetFirewallRule -DisplayName 'Ollama (TCP 11434)' -Direction Inbound -Protocol TCP -LocalPort 11434 -Profile Private -RemoteAddress 192.168.1.0/24 -Action Allow
   ```
4. **Get the model:** `ollama pull ministral-3`. You can also press a button for it in LineageBox later.
5. Note the PC's address on your network (e.g. `192.168.1.10`). A fixed address, set on your router, saves trouble
   later.

[docs/ollama.md](../ollama.md#on-another-machine) has the details, other models, and running two photos at a time.

**Video transcripts (optional):** the Whisper service can run on the same PC. It needs an NVIDIA card on Windows or
Linux. Follow [docs/whisper.md](../whisper.md#on-a-windows-pc-with-an-nvidia-graphics-card). It listens on port 9300.

## On the NAS: LineageBox

1. **Install Docker** with Compose v2, using the NAS's Docker or Container Manager package, and turn on SSH.
2. **Get LineageBox**, over SSH:

   ```sh
   git clone https://github.com/mutilator/LineageBox lineagebox
   cd lineagebox
   cp .env.example .env
   ```
3. **Fill in `.env`:**

   ```sh
   PHOTOS=/volume1/photo                  # the one folder that holds all your photos and videos
   DATA=./data                            # the catalog: about 0.5 MB per photo, on this machine's own disk
   TZ=America/Denver                      # your time zone
   OLLAMA_URL=http://192.168.1.10:11434   # the PC from above
   #WHISPER_URL=http://192.168.1.10:9300  # if you set up Whisper on the PC
   PUID=1000                              # who owns the photos on the NAS (`id -u`, `id -g` for you):
   PGID=1000                              #   LineageBox runs as them (Synology: usually 1026 and 100)
   ```

   The photo and catalog folders must be there already, and that user must be able to write to the catalog
   folder. `git clone` made `./data` as you; for a `DATA` elsewhere, make it first (`mkdir -p`).
4. **Start it:**

   ```sh
   docker compose up -d
   ```

   The first start downloads the images, which takes a few minutes.

## In the browser

Open **http://&lt;the NAS's address&gt;:8000** (it opens on **Getting started**, the first steps, each ticked off once
it is done), then on the **Settings** page:

1. **Watched folders → Watch another folder:** open `local`, go to your photos and press **Watch**.
2. **AI (Ollama):** press **Check the connection**. It should find the PC and say the model can look at pictures. If
   the model is missing, press **Download ministral-3 to Ollama**.
3. **Speech in videos (Whisper):** **Check the connection**, if you set it up.
4. **Schedule → When the AI may describe:** if the PC is used for other things, limit describing to hours when it is
   free, such as nights.

The **Status** page shows the progress. First every photo is read and its faces found, on the NAS. Then the PC
describes them. New photos are always described before the backlog.

## Good to know

- **When the PC sleeps,** descriptions and transcripts wait. LineageBox checks every 5 minutes whether it is back.
- **Photos on a Windows PC or another NAS:** LineageBox can mount the share itself. See
  [network shares](../install.md#a-network-share-smbcifs).
- **"Copy network path"** on each photo can give the path people paste into File Explorer. Set it under
  **Settings → On the network**.
- **Next:** name people on the **People** page, start the family tree from your own person's page (or bring one in
  as GEDCOM), and [invite the family](../family-accounts.md) once LineageBox has a public name with HTTPS. The
  [user guide](../user-guide.md) covers every page.
