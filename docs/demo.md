# A public demo

LineageBox can run as a demo that anyone on the internet may try: a made-up family's photos, papers and family tree,
which visitors look around in and change, and which go back as they were every day.

With `[demo] enabled` on (or `LINEAGEBOX_DEMO_ENABLED=true`):

- **One button to sign in.** The sign-in page has only **Sign in to demo**. Each click makes a member account of its
  own, as the person `[demo] person` names in the family tree, and signs that browser in. No email, Google account,
  passkey or code is asked for, and those ways in are switched off, as are an admin's codes: there is no admin on a
  demo.
- **Members only.** Visitors see and do what a [family member](family-accounts.md) does, from that person's place in
  the tree. Settings, Status and the admins' Cleanup pages aren't reachable.
- **Shared changes, private uploads.** Names, dates, captions, comments and people added to the tree are seen by
  everyone trying the demo, until it goes back as it was. Uploads are seen by their uploader alone, whoever is named
  in them. Each address may upload `[demo] uploads` files (5 by default) of at most `[demo] upload_max_mb` (10 MB)
  each until the next reset. Sharing and recording stories are off: what one visitor made would reach the others.
- **Nothing reached on the network.** The AI ([Ollama](ollama.md)), transcripts ([Whisper](whisper.md)), looking
  places up online, bringing in other programs' metadata, sidecars, nightly backups and asking GitHub for updates are
  off, whatever the catalog's settings say. Faces are still found in uploads, on the machine itself.
- **A note on every page** says the family is made up and when everything goes back as it was (`[demo] resets`).

Every setting is in [Configuration](configuration.md#a-public-demo).

## Making the demo

1. Run LineageBox as usual, **without** `[demo] enabled`, on a library of made-up photos: describe them with Ollama
   and transcribe the recordings with Whisper, name the faces you want named, build the family tree and add its
   documents. Leave some faces unnamed, so that visitors have something to name.
2. Pick the person visitors will be, someone in the middle of the tree: their Home page shows their close family
   first.
3. Turn the demo on (`[demo] enabled = true`, `[demo] person = "<their name>"`), and say when it resets
   (`[demo] resets = "every day at 4:00 UTC"`).
4. Keep it as it is now:

   ```sh
   docker compose run --rm --no-deps worker demo save
   ```

   This copies the catalog (checked), the data folder (previews, thumbnails, faces, stories) and every watched folder
   into `data/demo-seed`. Run it again whenever you change the demo itself.

## Putting it back

`demo reset` puts back what `demo save` kept: the catalog, the data folder (files made since are deleted, files
changed are copied back) and the watched folders (uploads are deleted). Every visitor's account, change and upload is
gone. The web app and the worker must be stopped meanwhile, so that they don't go on with what they had in memory:

```sh
docker compose stop web worker copies
docker compose run --rm --no-deps worker demo reset
docker compose start web worker copies
```

Run those three lines from cron as often as the demo should reset, once or twice a day. The site is away for the
half minute or so they take. A reset refuses to run unless `[demo] enabled` is on, as it deletes every photo added
since the last save. The catalog's own backups, the face models, HTTPS certificates and the data folder's
`config.toml` are left alone.

## Putting it on the internet

- Serve it over HTTPS through a reverse proxy, and list the proxy in `[web] trusted_proxies`
  ([Remote access](remote-access.md)). Uploads are counted by the visitor's address, which LineageBox takes from the
  proxy only when it trusts it; otherwise every visitor is counted as the proxy, and the uploads run out for everyone
  at once.
- List the demo's name in `[web] allowed_hosts`.
- Give the demo's containers no way into the network they run in: with the AI and Whisper off, they need nothing
  but the proxy.
- The updater service isn't needed: there is no admin to ask for an update. Update the demo by hand, then run
  `demo save` again, so that each reset doesn't upgrade the old catalog anew.
- Use photos and papers you made up or may share. Everything in the demo is public: the made-up family's tree, the
  papers' text, and what visitors type.
