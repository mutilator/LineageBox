# Design notes and measurements

Why LineageBox is built the way it is. Most choices were measured on a real family library of a few tens of thousands
of photos and over a thousand videos, from DSLRs, compact cameras, a camcorder and two decades of phones. The tests ran
on a desktop PC with a mid-range graphics card, and on a small ARM board for the Docker install. The numbers are a
guide, not a promise for other libraries.

## Principles

- **The photos are read-only.** All derived data (database, thumbnails, previews, face crops, frames) lives in the
  catalog folder. Only Cleanup, uploads, 720p copies and confirmed rotations and locations write to the photo
  folders, and only when asked.
- **Read each original once.** Ingest computes the hash, EXIF, perceptual hash, thumbnail and preview from one read.
  Faces, the AI and the web UI work from the local preview, so they don't depend on a slow or sleeping share. 2048 px
  is the size face detection was tested at.
- **Every stage is idempotent and resumable.** Stop it at any time. Failures are recorded per photo.
- **People's edits always win.** Anything a person set by hand is never overwritten by a re-run.
- **The AI never sees names.** Descriptions are kept apart from naming, so renaming or merging people never needs a
  re-run, which would take days for a big library.
- **AI output is versioned.** Each description records its model and prompt version, so a subset can be described again
  with a better model and compared. Going back to an earlier model doesn't redo its photos.
- **Nothing is specific to one library.** Suggestions, defaults and thresholds come from counts over the installer's
  own catalog at run time, never from one family's names, places, years or tags. A library may be used to measure a
  rule, but the result must be a rule. Suggested tours work this way (`tour_suggestions.py`).

## What a family library looks like

- **Duplicates are everywhere.** About 30% of files were extra copies: whole albums copied under several folders.
  Same name and size matched identical SHA-256 in every sampled group, so exact hashing removes them.
- **Near-duplicates need perceptual hashing:** resized copies in `Previews`/`Thumbs` folders, and edited copies
  (`DSC_8627.jpg` and `DSC_8627-2.jpg`) that differ in bytes.
- **GPS is rare** (2% here), so places mostly come from folder names. **Folder names are rich**: places ("Grand Canyon
  AZ"), events ("5th Birthday", "Halloween"), people, dates in many formats ("2007-03-08", "Easter13"), and junk
  (`Thumbs`, `.thumbnails`, `AVCHD`, `*_files`).
- **Capture dates:** 92% had EXIF dates. The rest come from folder names, file names (`IMG_20160811_182239`) or file
  times, and the source is recorded, so dates from file times can be listed and fixed.

## Faces: InsightFace `buffalo_l`

SCRFD detection with an ArcFace R50 embedding, on onnxruntime. It was chosen as the most accurate open-source model
that installs without compiling anything. `face_recognition`/dlib is older and markedly worse on children and turned
faces. DeepFace pulls in TensorFlow.

- **Speed:** 36–57 ms a photo on the GPU, about 2.3 s on a desktop CPU, and 0.75 s median on the ARM board, with
  the same faces found. The embeddings matched the GPU's to ≥ 0.9997, so faces run fine on a NAS.
- **Junk faces** (backs of heads, stuffed toys, a dog) have a low ArcFace embedding norm: a median of 12.1 against 21.5
  overall. A cut-off around 15 drops them and keeps about 90% of faces.
- **Grouping:** HDBSCAN over unnamed faces, on 128 PCA dimensions. That gave the purest groups when measured. Random
  samples of the largest groups were each one person.
- **The hard limit:** two brothers as toddlers scored 0.50 against each other, the same as one baby at two different
  events (0.51). No single threshold separates them. So recognition is conservative: the top-3 similarity must be
  ≥ 0.5 and 0.1 ahead of the runner-up, with one person per photo. A person confirms the rest. **Seen together** uses
  the "nobody is in a photo twice" rule to suggest the closest person not already named in the photo.
- InsightFace licenses these models for **non-commercial research purposes only**. LineageBox downloads them; it doesn't
  include them.

## Descriptions: a vision model through Ollama

`ministral-3` (8.9B, Q4_K_M), asked for a JSON-schema answer at temperature 0.1.

- **Image size:** 512 px costs about 830 prompt tokens, and 1024 px about 1,520. It caps around 1,650. 1024 px is
  enough, and the server's default 8k context is plenty.
- **Speed:** about 4 s a photo for the full record on that graphics card, and 2.4–2.6 s with two Ollama slots and four
  requests in flight (1.7× one slot). `gemma3:12b` was more accurate but more than twice as slow.
- **Accuracy:** 11 of 15 varied photos were fully right. Typical mistakes were a fawn called a goat and a red rake
  called an umbrella. Descriptions are labelled as AI-made in the UI, and people's tags override them.
- **Folder context helps a lot.** With the folder's place and event in the prompt, the model names landmarks it would
  otherwise miss. An eclipse photo went from "a firework explodes" to "partial solar eclipse".
- **Don't send `num_ctx`.** When another client (Open WebUI, say) shares the Ollama server, a context size
  different from the server's default makes Ollama reload the model every time the two clients alternate.
- **Folder names in batches** of 25, matched by id, because the model rewrites paths.
- Near-duplicate groups are described once, from their largest member, and the result is copied to the others.
- The LLM's own "blurry" and "document/screenshot" flags were unreliable (blurry on 74% of photos), so Cleanup doesn't
  use them. Blur is a pixel sharpness score: the lowest 1% is almost all black frames, blank sky and lens caps. Papers
  and screens are specific phrases in descriptions of photos without people.

## The database: SQLite with FTS5, embeddings in NumPy

- FTS5 with Porter stemming ranked the right photo first in every test query, and handles plurals and verb forms.
- Nearest faces by brute force in NumPy took 5.7 ms a query over 100,000 faces (200 MB of memory), against 245 ms
  with sqlite-vec. A batch of 1,000 queries took 0.4 s.
- PostgreSQL with pgvector would suit many concurrent writers. For a household it only adds a service to run.

## The web UI

FastAPI with Jinja2 templates and a few hundred lines of plain JavaScript. There is no Node build step. Pages rendered
in 16–130 ms on the development PC, including a folder of over a thousand photos and a year of several thousand, and
in 36–350 ms on the ARM board.

## Videos

- **ffmpeg** (the CLI, not PyAV), because the copies need the CLI anyway.
- **Frames** every ~10 s (3–20 a video). Blank and near-identical frames are dropped, and the clearest becomes the
  preview. A frame takes 0.3–0.7 s on the ARM board. Faces are found on every frame, and each person counts once per
  video. Four frames go to the AI in one request (about 3,200 prompt tokens).
- **Dates:** a camcorder .MTS file's date comes from the H.264 stream, else the container's (UTC), else the file name.
- **Playable copies:** 95% of videos were H.264, but browsers can't play about a quarter of them as they are (AVCHD
  .MTS, MOV with PCM sound, 3GP, AVI, WMV, HEVC). These get a 720p copy next to the original, marked in its
  comment. A copy is never a catalog entry, and Cleanup moves it with its video.
- **Transcripts:** whisper.cpp large-v3-turbo with Silero VAD first, 26–29× real time on the GPU. Only stretches
  with speech are transcribed. Wind, music and the usual Whisper inventions ("Thank you.") are filtered out.

## Rotation and locations written into files

When someone confirms a photo's rotation, or a series of photos' location on the map, LineageBox also writes it into the
file where the format allows: a JPEG's orientation tag and GPS, without re-compressing, with ExifTool, for JPEG, TIFF
and NEF. The photo's faces, names and tags carry over to the changed file. A rotation is suggested only when the faces
in a photo agree on it, never when some faces are upright (a child hanging upside down among people standing). Places
read from folder names stay in the catalog only, because they are a town's or landmark's centre, not where each photo
was taken.

## Family

- Only facts are entered: parents, children, siblings, partners (current or former), sex, and other relationships.
  Everything else is worked out, relative to "me".
- Step-family comes only from current partners. After a divorce, people are described by the way to them instead
  ("ex-husband's wife's daughter").
- Names aren't unique: two people can share a name, and a typed name is looked up among the people the viewer can
  see.
