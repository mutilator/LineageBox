# LineageBox documentation

LineageBox is a private family archive: the family tree, with its people's life events and documents, linked to the
photos and home videos they appear in, and shared with relatives who sign in with Google.

## Getting started

- [Quick start: everything on one machine](quick-start/docker.md): Docker, with the AI in containers next to LineageBox.
- [Quick start: a NAS, plus a PC with a graphics card](quick-start/nas-and-pc.md): the fastest setup for a big library.
- [Quick start: from source](quick-start/from-source.md): Python, without Docker.

## Running LineageBox

- [Installation guide](install.md): photo sources, network shares, permissions, host networking, updates and backups.
- [AI descriptions with Ollama](ollama.md): models, another machine or Docker, graphics cards.
- [Video transcripts with Whisper](whisper.md): on a PC with a graphics card or in Docker.
- [Reaching it by name, HTTPS and from outside](remote-access.md).
- [Family accounts](family-accounts.md): relatives signing in with Google, who sees what, uploads.
- [Troubleshooting](troubleshooting.md).

## Using LineageBox

- [User guide](user-guide.md): search, people, the family tree, dates, tours, cleanup, Status and Settings.

## Reference

- [Configuration](configuration.md): every setting, config file and environment variable.
- [Command line](command-line.md): every `python -m lineagebox` command.

## Development

- [Architecture](architecture.md): the processes, the pipeline, the catalog and the code.
- [Design notes and measurements](design-notes.md): why it is built this way.
- [Access control](access-control.md): sign-in, who sees what, and how it is enforced.
- [Development](development.md): setting up, tests, conventions, and adding pages, settings and migrations.
