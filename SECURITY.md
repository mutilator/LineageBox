# Security

LineageBox holds a family's photos, names, birth dates and family links, so problems that expose them matter even when
they look small.

## Reporting a vulnerability

Please report it privately, not in a public issue: on GitHub, open the repository's
[Security tab](https://github.com/mutilator/LineageBox/security) and choose **Report a vulnerability**, or email
scott@g-tech.dev. Include what an attacker needs (on the home network, a family member's account, or nothing at all
from the internet), the steps or requests that show it, and what they get.

You should hear back within a week. Once a fix is released, the advisory says what was affected and which version
fixes it.

## What counts

The intended rules are in [docs/access-control.md](docs/access-control.md). In short:

- **Family accounts off** (the default): only devices on private networks are answered, and everyone there can see
  and change everything. "Anyone on my home network can see my photos" is how it is meant to work. A way for anything
  *outside* the home network to get in is a vulnerability.
- **Family accounts on**: everyone signs in. A family member seeing photos, people or details outside their own
  branch, changing anything outside it, or reaching an admin page is a vulnerability. So is getting in without
  signing in.
- The photo folders must never be written to except by Cleanup, uploads, 720p copies and confirmed rotations, and
  never outside the photo folders at all.

## Running it safely

- Keep LineageBox up to date: **Status → Version and updates → Update now**, or `docker compose pull && docker compose
  up -d`.
- The `updater` service in `compose.yaml` has the Docker socket, which is the same as root on that machine. It has no
  network and reads no settings or catalog: it acts only when the web app has created the empty file
  `update/requested` in the catalog folder, and then only updates the containers labelled `lineagebox.update` in its own
  compose project, each to the newest copy of the image it already runs. A way to make it do anything else, run
  anything, or touch other containers is a vulnerability. To do without it, see
  [Updating and backups](docs/install.md#updating-and-backups).
- Don't expose it to the internet with family accounts off. Use a VPN, or turn accounts on and put a reverse proxy
  with HTTPS in front ([remote access](docs/remote-access.md)).
- Set **Reverse proxy** on the Settings page when you use one. Without it, LineageBox takes every visitor for the
  proxy's own private address.
- The catalog folder holds the Google client secret and the admins' sign-in code secrets. Keep it, and its backups,
  as private as the photos.
