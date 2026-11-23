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
- The `updater` service in LineageBox's compose file has the Docker socket, which is the same as root on that machine. It has no
  network and reads no settings or catalog: it acts only when the web app has created the empty file
  `update/requested` in the catalog folder, and then only updates the containers labeled `lineagebox.update` in its own
  compose project, each to the newest copy of the image it already runs. A way to make it do anything else, run
  anything, or touch other containers is a vulnerability. To do without it, see
  [Updating and backups](docs/install.md#updating-and-backups).
- The setup wizard's container ([the setup wizard](docs/install.md#the-setup-wizard): the repository's
  `compose.yaml`, or `lineagebox setup`) runs as root, with only the root powers it needs to look in every folder and
  to give what it saves to LineageBox's user. It sees the machine's files through a read-only mount, to show folders
  and who owns them. That mount doesn't stop root reaching Docker's socket, and with Docker older than 25 the disks
  mounted under `/` stay writable in it: the wizard's own code keeps it to reading, and writes only in the folder it
  was started from. Looking at the machine and saving need the key it prints; without the key it only makes files
  from the answers it is sent. It stops after an hour unused. A way to read a file's contents through it, to make it
  write anywhere else or use Docker's socket, or to look at the machine or save without the key is a vulnerability.
  The hosted wizard keeps nothing it is sent, and the page never sends it passwords.
- Don't expose it to the internet with family accounts off. Use a VPN, or turn accounts on and put a reverse proxy
  with HTTPS in front ([remote access](docs/remote-access.md)).
- Set **Reverse proxy** on the Settings page when you use one. Without it, LineageBox takes every visitor for the
  proxy's own private address.
- The catalog folder holds the Google client secret and the admins' sign-in code secrets. Keep it, and its backups,
  as private as the photos.
