# Quick start: from source, without Docker

Run LineageBox straight from the code with Python, on Windows, macOS or Linux. This is the quickest way to try it on a
PC, and the setup for working on the code. For a long-running install, Docker is easier: see
[everything on one machine](docker.md).

## What you need

- **Python 3.12** or newer.
- **ffmpeg and ffprobe** on the `PATH`, for videos. Without them, photos still work. `LINEAGEBOX_FFMPEG` can name the
  folder they are in instead.
- **ExifTool** on the `PATH` (optional). LineageBox uses it to write confirmed rotations and locations into photo files.
- **Ollama** (optional), for AI descriptions: [ollama.com/download](https://ollama.com/download), then
  `ollama pull ministral-3`. See [docs/ollama.md](../ollama.md).

## 1. Install

```sh
git clone https://github.com/mutilator/LineageBox lineagebox
cd lineagebox
python -m venv .venv
```

Then, on Windows (PowerShell):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[faces,dev]"
```

On macOS or Linux:

```sh
.venv/bin/python -m pip install -e ".[faces,dev]"
```

`faces` adds InsightFace and onnxruntime for face recognition, and `dev` adds the test tools. Leave out `faces` to try
LineageBox without face recognition.

**With an NVIDIA graphics card**, faces are found about 30 times faster with the GPU build of onnxruntime:

```sh
python -m pip uninstall -y onnxruntime
python -m pip install "onnxruntime-gpu[cuda,cudnn]"
```

## 2. Configure

```sh
cp config.example.toml config.toml
```

In `config.toml`, set `media_root` to the folder your photo folders are in, and `data_dir` to where the catalog should
go (about 0.5 MB per photo, on a local disk). Both are relative to where you run LineageBox, so absolute paths are
safer. Everything else can stay as it is. [docs/configuration.md](../configuration.md) lists every setting.

## 3. Run

The examples below use the Windows path to Python. On macOS and Linux, use `.venv/bin/python` instead.

Start the web UI:

```powershell
.\.venv\Scripts\python.exe -m lineagebox serve
```

Open **http://127.0.0.1:8000**. On **Settings → Watched folders**, pick a folder and press **Watch**.

Then start the worker in a second terminal. It takes new photos through every stage, then looks again every hour:

```powershell
.\.venv\Scripts\python.exe -m lineagebox worker
```

Every stage is safe to stop with Ctrl+C. The next run carries on where it stopped. The stages can also be run one at
a time (`scan`, `ingest`, `faces`, `describe` and so on). See the [command line](../command-line.md).

`serve` answers this machine only. `--host 0.0.0.0` lets other devices on your home network in, and LineageBox still
refuses anyone outside it.

## On a PC with a graphics card

Finding faces and describing photos both use the graphics card. On a card with 12 GB, faces and one Ollama slot fit
together, but two Ollama slots leave no room for faces. If faces fail for lack of memory, run them first
(`lineagebox faces`) and describe afterwards, or use `lineagebox faces --cpu`, which is much slower.

## Next

- [User guide](../user-guide.md): what every page does.
- [Development](../development.md): tests, how the code is laid out, and conventions.
