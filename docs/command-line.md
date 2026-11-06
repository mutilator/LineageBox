# Command line

Everything LineageBox does is also a command, `python -m lineagebox <command>`. The worker runs them all on its own, so
you only need these to debug, to run one stage by hand, or to manage accounts and HTTPS.

- **In Docker:** `docker compose exec worker python -m lineagebox <command>`. Use `web` instead of `worker` for
  `accounts` and `https`.
- **From source:** `.venv/bin/python -m lineagebox <command>`, or `.\.venv\Scripts\python.exe -m lineagebox <command>`
  on Windows.

`--config <file>` reads another config file. `python -m lineagebox <command> --help` lists a command's options.

Every long command is incremental and safe to stop with Ctrl+C. Running it again carries on where it stopped, and
failures are recorded per photo without stopping the run.

## Services

| Command | What it does |
|---|---|
| `serve [--host H] [--port P]` | The web UI. It binds `127.0.0.1:8000` by default (`0.0.0.0` in Docker). |
| `worker [--once]` | Runs every stage for new photos, then again every interval. `--once` runs one pass and exits. |
| `copies [--once] [--limit N]` | Makes 720p copies of videos browsers can't play. |

## Stages, in the order the worker runs them

| Command | What it does |
|---|---|
| `scan` | Records folders and files in the watched folders. Files that disappeared are flagged missing, never deleted. |
| `ingest [--folder F] [--limit N] [--retry-errors]` | Reads each new file once: hash, metadata, fingerprint, thumbnail and preview. `--retry-errors` reads files that failed to decode again. |
| `dedup` | Groups near-duplicates: edited copies, smaller copies, burst shots. |
| `folders` | Reads places, events and dates from folder names (uses Ollama). |
| `faces [--limit N] [--cpu]` | Finds faces in the previews. `--cpu` leaves a graphics card to Ollama. |
| `people [--identify]` | Groups unnamed faces for naming. `--identify` first names faces that clearly match named people. |
| `sharpness` | Measures sharpness, for the Blank and blurry list. |
| `check-files [--all]` | Reads every file whole for damage, and has missing previews made again. `--all` checks files already checked. |
| `index` | Rebuilds the search index. |
| `transcribe [--limit N]` | Transcribes videos with the Whisper service. |
| `describe [--folder F] [--limit N] [--videos]` | Describes photos with Ollama. `--folder "Photos/2012/Zoo trip"` does one album. |

## Looking and managing

| Command | What it does |
|---|---|
| `status` | Catalog counts: watched folders, files read and waiting, unique photos, videos, formats and where dates came from. The Status page shows the rest. |
| `https [off]` | Shows the HTTPS certificate in use, or turns HTTPS off. |
| `accounts list` | Lists family accounts. |
| `accounts add <email> [--admin] --person "<name>"` | Adds an account tied to a person in the tree. |
| `accounts disable\|enable\|remove <email>` | Switches an account off or on, or removes it. |
| `accounts sign-out <email>` | Ends an account's sessions. |
| `accounts local-code <email>` | Makes an admin's code for signing in at home without the internet. |
| `accounts invite <email>` | Makes a link for making the account's passkey (one use; 7 days, a day for an admin). |
| `accounts passkeys <email>` | Lists the account's passkeys. |
| `accounts on\|off` | Turns family accounts on or off. |

## Running stages by hand, from source

On a first run from source, without the worker:

```sh
python -m lineagebox scan        # seconds
python -m lineagebox ingest      # reads every photo once: the longest step over a network share
python -m lineagebox dedup
python -m lineagebox folders     # needs Ollama
python -m lineagebox faces       # GPU if onnxruntime-gpu is installed, else the processor
python -m lineagebox people
python -m lineagebox describe    # needs Ollama; a few seconds a photo with a graphics card
python -m lineagebox serve
```

On one graphics card, run `faces` and `describe` one after the other: with Ollama running two photos at a time there
may not be room for both models.
