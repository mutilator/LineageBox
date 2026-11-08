# Setting up Whisper: what is said in videos, recordings and stories

LineageBox takes the sound out of each video and sound recording (a digitised cassette, a voice memo), and of each story the family records on a photo or a person's page, and sends it to its speech-to-text service (`whisper_service/`, built on [whisper.cpp](https://github.com/ggml-org/whisper.cpp)). What was said becomes searchable ("happy birthday", a name) and shows on the page, with each line linked to its moment in the video or recording. Wind, music and the usual Whisper inventions ("Thank you.", "you") are filtered out. Recorded stories go first: they are short, and the family's own words.

A line Whisper misheard can be put right on the page ("fix"), and a story's words by whoever recorded it. Corrections are kept as the family's: hearing the video again, or filtering it under better rules, never writes over them.

The service can run in three places:

| Where | Good for |
|---|---|
| [A Windows PC with an NVIDIA graphics card](#on-a-windows-pc-with-an-nvidia-graphics-card) | Fast: tens of times faster than the videos play. |
| [In Docker, next to LineageBox](#in-docker-next-to-lineagebox) | No other machine. Runs on the processor, so it is slow, but it gets through a library in the background. |
| [Nowhere: turned off](#turning-it-off) | Everything else works; videos, recordings and stories just have no transcripts (stories can still be listened to, and their words typed by hand). |

Transcribing runs during the same hours as AI descriptions (**When the AI may describe** on the Settings page).

The service downloads its models from Hugging Face the first time it starts: the Whisper models (by OpenAI, MIT licence) and the Silero VAD model that finds the speech (MIT licence). LineageBox doesn't ship them. Each comes from a fixed version of its repository and is used only when its SHA-256 matches the one in `whisper_service/checksums.py`, which lists every model there. To use a model that isn't listed, put the file in the models folder yourself.

## On a Windows PC with an NVIDIA graphics card

1. **Get LineageBox's code** on the PC, and a Python 3.12 environment with what the service needs. From LineageBox's
   folder:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install fastapi uvicorn httpx
   ```
2. **Get whisper.cpp:** from [its releases](https://github.com/ggml-org/whisper.cpp/releases), download the 64-bit Windows build with CUDA support. Unpack it into `%LOCALAPPDATA%\lineagebox-whisper\bin`, so that `whisper-server.exe` and `whisper-vad-speech-segments.exe` are directly in that folder.
3. **Start it** and let it download its models (about 600 MB, into `%LOCALAPPDATA%\lineagebox-whisper\models`):

   ```powershell
   .\whisper_service\start.ps1 --download
   ```

   Its log is `%LOCALAPPDATA%\lineagebox-whisper\service.log`. `stop.ps1` stops it, and `autostart.ps1` makes it start whenever you log on.
4. **Open the firewall** for TCP port 9300, for your home network only. In an administrator PowerShell, using your own network's range:

   ```powershell
   New-NetFirewallRule -DisplayName 'LineageBox Whisper (TCP 9300)' -Direction Inbound -Protocol TCP -LocalPort 9300 -Profile Private -RemoteAddress 192.168.1.0/24 -Action Allow
   ```
5. **Point LineageBox at it:** `WHISPER_URL=http://<the PC's address>:9300` in `.env`, or the **Address** on the Settings page. **Check the connection** says which model it uses, and whether it runs on the GPU or the CPU.

**Sharing the graphics card with Ollama:** the service starts Whisper only while there are videos to transcribe. It stops Whisper after 10 idle minutes (`--idle-minutes`), and uses the card only when about 1.6 GB of its memory is free; otherwise it uses the processor. `python -m whisper_service --help` lists the options. `start.ps1` passes them on, e.g. `.\whisper_service\start.ps1 --download --idle-minutes 5`.

On **Linux with an NVIDIA card**, build whisper.cpp with CUDA (`cmake -B build -DGGML_CUDA=1`). Set `WHISPER_BIN` to its `build/bin` folder and run `python -m whisper_service --download` in the same way.

## In Docker, next to LineageBox

1. In `.env`, add the Whisper file to the compose files. On Windows, separate them with `;` instead of `:`. With Ollama in Docker too, list both: `compose.yaml:compose.ollama.yaml:compose.whisper.yaml`.

   ```sh
   COMPOSE_FILE=compose.yaml:compose.whisper.yaml
   ```
2. `docker compose up -d`. The first time, this builds whisper.cpp for this machine's processor, which takes several minutes. Then the service downloads its models; the Status page's **Downloads** shows how far it is. Videos are transcribed once the downloads have finished.
3. LineageBox uses it at `http://whisper:9300`, unless an address was already saved on the Settings page. If one was, change it there.

**How fast:** on the processor, the default model `ggml-large-v3-turbo-q5_0.bin` runs a few times faster than the video plays on a desktop processor. A small NAS processor can take longer than the video itself. Only stretches with speech are transcribed, so quiet videos go faster. To go faster, set a smaller model in `.env` and run `docker compose up -d`:

| `WHISPER_MODEL` | Size | Notes |
|---|---|---|
| `ggml-large-v3-turbo-q5_0.bin` | 550 MB | The default. Most accurate; LineageBox's filtering was measured with it. |
| `ggml-small.bin` | 470 MB | Faster, more mistakes. |
| `ggml-base.bin` | 140 MB | Faster still; fine for clear speech. |
| `ggml-small.en.bin`, `ggml-base.en.bin` | | English only, a little more accurate in English. |

The models are kept in the Docker volume `lineagebox_whisper`.

**Language:** LineageBox asks Whisper to tell the language from the speech itself (`auto`), each video, recording and story on its own, so a family that speaks two languages gets both. Short, noisy clips can be guessed wrong; to have one language always, set it under **Settings → AI, speech and faces** (**Language spoken**, a code such as `en` or `de`), or `WHISPER_LANGUAGE` in `.env`. The English-only models (`.en`) always hear English.

**Who may use it:** like LineageBox, the service only answers the home network (private IPv4 and IPv6 addresses). If LineageBox reaches it from another range, such as a VPN's `100.64.0.0/10`, set `WHISPER_ALLOWED_NETWORKS=100.64.0.0/10` (comma-separated) where the service runs.

## Turning it off

Untick **Transcribe what is said in videos, sound recordings and recorded stories** on the Settings page. Transcripts already made stay, and remain searchable. Tick it again to carry on.

## When something is wrong

| You see | What to do |
|---|---|
| Status: *Whisper not answering* | The PC is asleep or off, the service isn't running, the address is wrong, or the firewall is in the way. **Check the connection** on the Settings page tells you more. |
| A red note: **Whisper: Transcribing failed: …** | The service's own error. Its log says more: `service.log` on Windows, `docker compose logs whisper` in Docker. |
| Check the connection: *not ready: downloading …* | It is still downloading its model; see **Downloads** on the Status page. |
| *not a model this service can check* | The model in `WHISPER_MODEL` isn't in `whisper_service/checksums.py`, so it isn't downloaded. Pick one from the table above, or put the file in the models folder yourself. |
| *not the file expected (its SHA-256 differs)* | What Hugging Face sent isn't the model this version expects, so it wasn't used. Try again later, or update LineageBox. |
| Check the connection: *could not download …* | No internet on that machine, or Hugging Face was unreachable. Restart the service to try again. |
