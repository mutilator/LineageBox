# Setting up Ollama: descriptions of photos and videos

LineageBox sends each photo, and a few frames of each video, to [Ollama](https://ollama.com) running a model that can look at pictures. It keeps what the model answers: a description, tags, the setting, the season, any text in the picture. That is what lets a search for "snow", "birthday cake" or "goat" find photos. Ollama also reads folder names ("Emma5thBirthday", "GrandCanyon2019") for places, events and dates.

Ollama can run in three places:

| Where | Good for |
|---|---|
| [Another machine with a graphics card](#on-another-machine) | Fast. A desktop PC that is on in the evenings is enough. |
| [In Docker, next to LineageBox](#in-docker-next-to-lineagebox) | No other machine. Fast with a graphics card in the LineageBox machine, slow without one. |
| [Nowhere: turned off](#turning-it-off) | Everything else works; there are just no descriptions. |

## Which model

The model must be able to look at pictures (a "vision" model). LineageBox checks this:

- **Check the connection** on the Settings page says whether Ollama has the model and whether the model can look at pictures.
- The Status page shows a red note when it can't, or when Ollama answers every photo with an error.

| Model | Download | Licence | Notes |
|---|---|---|---|
| `ministral-3` (the default) | about 6 GB | Apache-2.0 | What LineageBox was tuned with. About 6.5 GB of graphics memory with one photo at a time; with a desktop graphics card, a few seconds a photo. |
| `gemma3:12b` | about 8 GB | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) | More accurate than ministral-3, about half as fast. |
| `ministral-3:3b` | about 3 GB | Apache-2.0 | For smaller graphics cards, or a processor without one. Faster, and wrong more often. |
| `gemma3:4b` | about 3.5 GB | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) | The same, from Google. |

Each model comes with its own licence, which applies to how you use it. The model's page on [ollama.com](https://ollama.com/library) names it.

You set the model on the Settings page (**AI (Ollama)**, **Model**). Changing it describes every photo again with the new one. Descriptions are kept per model, so going back to the old model doesn't redo them.

## On another machine

1. **Install Ollama** from [ollama.com/download](https://ollama.com/download) (Windows, macOS or Linux).
2. **Let other machines reach it.** By default Ollama only answers its own machine. Set the environment variable `OLLAMA_HOST` to `0.0.0.0`, then restart Ollama:
   - **Windows:** *Settings → System → About → Advanced system settings → Environment Variables*. Add a user variable `OLLAMA_HOST` = `0.0.0.0`, then quit Ollama from the tray and start it again.
   - **macOS:** `launchctl setenv OLLAMA_HOST 0.0.0.0`, then restart the Ollama app.
   - **Linux** (installed as a service): `sudo systemctl edit ollama`, add the two lines below, then `sudo systemctl restart ollama`.

     ```ini
     [Service]
     Environment="OLLAMA_HOST=0.0.0.0"
     ```
3. **Open the firewall** for TCP port 11434, for your home network only. On Windows, in an administrator PowerShell (use your own network's range):

   ```powershell
   New-NetFirewallRule -DisplayName 'Ollama (TCP 11434)' -Direction Inbound -Protocol TCP -LocalPort 11434 -Profile Private -RemoteAddress 192.168.1.0/24 -Action Allow
   ```
4. **Point LineageBox at it:** `OLLAMA_URL=http://<that machine's address>:11434` in `.env`, or the **Address** on the Settings page.
5. **Download the model:** **Download ministral-3 to Ollama** on the Settings page, or `ollama pull ministral-3` on that machine. The Status page's **Downloads** shows progress for downloads started from LineageBox.

Good to know:

- **More photos at once.** Set `OLLAMA_NUM_PARALLEL=2` on the Ollama machine, the same way as `OLLAMA_HOST`, and **Photos at a time** to 3 in LineageBox. When this was tested, 2 slots described photos about 1.7 times as fast as one; each slot needs some more graphics memory.
- **When the machine sleeps** or is off, descriptions wait. LineageBox checks every 5 minutes whether Ollama is back. **When the AI may describe** on the Settings page can limit describing to the hours the machine is free, such as nights.
- **Sharing with other programs** (such as Open WebUI) is fine. LineageBox leaves Ollama's context size at its default, so Ollama doesn't reload the model each time a request comes from the other program.

## In Docker, next to LineageBox

1. In `.env`, add the Ollama file to the compose files. On Windows, separate them with `;` instead of `:`.

   ```sh
   COMPOSE_FILE=compose.yaml:compose.ollama.yaml
   ```
2. `docker compose up -d`. LineageBox uses this Ollama, at `http://ollama:11434`, unless an address was already saved on the Settings page. If one was, change it there to `http://ollama:11434`.
3. **Download the model:** press **Download ministral-3 to Ollama** on the Settings page and follow it under **Downloads** on the Status page. Or run `docker compose exec ollama ollama pull ministral-3`. The models are kept in the Docker volume `lineagebox_ollama`.

This Ollama is not published on your network; only LineageBox's containers can reach it. To use it from other machines too, add `ports: ["11434:11434"]` to it in `compose.ollama.yaml`.

**Without a graphics card**, Ollama runs on the processor. That works, but slowly: a NAS processor can take minutes per photo. A small model (`ministral-3:3b`, `gemma3:4b`) is much faster. Leave `OLLAMA_NUM_PARALLEL` at 1 and **Photos at a time** at 1 or 2.

**With an NVIDIA graphics card:**

1. Install the NVIDIA driver and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on the LineageBox machine, and restart Docker.
2. Add the NVIDIA file:

   ```sh
   COMPOSE_FILE=compose.yaml:compose.ollama.yaml:compose.ollama-nvidia.yaml
   ```
3. `docker compose up -d`. `docker compose logs ollama` says which graphics card Ollama found ("inference compute").

**With an AMD graphics card** (x86-64, a card Ollama supports with ROCm), add `compose.ollama-amd.yaml` instead.

**Updating Ollama:** set `OLLAMA_VERSION` in `.env` to a newer version from [Ollama's releases](https://github.com/ollama/ollama/releases), then `docker compose up -d`.

## Turning it off

Untick **Describe photos and videos, and read folder names, with AI** on the Settings page. Then:

- nothing is sent to Ollama;
- descriptions already made stay, and search keeps using them;
- search still finds dates, places and events in folder names, people, and what is said in videos.

Tick it again to carry on where it stopped.

## When something is wrong

The Status page shows a red note starting with **Ollama:**. The same note is on the Settings page. What the notes mean:

| The note says | What to do |
|---|---|
| Ollama has no model named … | Press **Download … to Ollama** on the Settings page, or pick a model it has. **Check the connection** lists them. |
| … can't look at pictures | Pick a vision model (see [Which model](#which-model)). Folder names are still read with it. |
| Describing failed: Ollama answered 500: … | Ollama's own error. Most often the model doesn't fit in memory: pick a smaller one, or fewer **Photos at a time**. Ollama's log has more: `docker compose logs ollama`, or *server.log* in Ollama's folder on Windows. |
| (Status shows Ollama *not answering*) | The machine is asleep or off, the address is wrong, `OLLAMA_HOST` isn't set, or a firewall is in the way. |

The note disappears by itself once a photo is described again.
