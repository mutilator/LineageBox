# LineageBox documentation

LineageBox is a private family archive: the family tree, with its people's life events and documents, linked to the
photos and home videos they appear in, and shared with relatives who sign in with Google.

## Getting started

- [Quick start: everything on one machine](quick-start/docker.md): Docker, with the AI in containers next to LineageBox.
- [Quick start: a NAS, plus a PC with a graphics card](quick-start/nas-and-pc.md): the fastest setup for a big library.
- [Quick start: from source](quick-start/from-source.md): Python, without Docker.

## Running LineageBox

- [Installation guide](install.md): photo sources, network shares, permissions, host networking, updates and backups.
- [AI descriptions with Ollama](ollama.md): models, describing again, search by meaning, another machine or Docker,
  graphics cards.
- [Transcripts with Whisper](whisper.md): what is said in videos, sound recordings and recorded stories; on a PC
  with a graphics card or in Docker.
- [Reaching it by name, HTTPS and from outside](remote-access.md), and putting it on phones' home screens.
- [Family accounts](family-accounts.md): relatives signing in with Google, who sees what, uploads, New for you, and
  photo frames' device links.
- [Troubleshooting](troubleshooting.md).

## Using LineageBox

- [User guide](user-guide.md): the first steps, the menu, light or dark and printing; search (by words and by
  meaning), people, the family tree and its book, New for you,
  the app on a phone, photo frames, the map and putting photos on it, other photo programs (their names brought in,
  XMP sidecars), dates, tags, tours, albums and favourites, cleanup, Status and Settings.

## Reference

- [Configuration](configuration.md): every setting, config file and environment variable.
- [Command line](command-line.md): every `python -m lineagebox` command.
- [The export format](export.md): the zip that Settings → Backup → Download everything makes, file by file. The
  nightly copies of the catalog, and putting one back, are in the [installation guide](install.md#updating-and-backups).

## Development

- [Architecture](architecture.md): the processes, the pipeline, the catalog and the code.
- [Design notes and measurements](design-notes.md): why it is built this way.
- [Access control](access-control.md): sign-in, who sees what, and how it is enforced.
- [Development](development.md): setting up, tests, conventions, writing text that can be translated, and adding
  pages, settings and migrations.
- [Translating](translating.md): adding a language: extracting the text, translating it, compiling and choosing it.
