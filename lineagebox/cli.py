"""Command line: python -m lineagebox <command>  (or `LineageBox <command>` once installed)."""
import argparse
import contextlib
import os
import sqlite3
import sys
from pathlib import Path

from lineagebox import __version__, catalog, config, db, settings
from lineagebox.power import keep_awake
from lineagebox.ingest import ingest
from lineagebox.scan import scan


def cmd_scan(con, cfg, args):
    s = scan(con, cfg)
    print(f"folders {s.folders}  image files {s.files}  new {s.new}  changed {s.changed}  newly missing {s.missing}")
    if s.errors:
        print(f"{len(s.errors)} errors (files were not marked missing because of them):")
        for e in s.errors[:20]:
            print("  ", e)


def cmd_ingest(con, cfg, args):
    s = ingest(con, cfg, limit=args.limit, retry_errors=args.retry_errors, folder=args.folder)
    if not s.pending:
        print("nothing to ingest")
        return
    rate = s.processed / s.seconds if s.seconds else 0
    print(f"processed {s.processed}/{s.pending} in {s.seconds:.0f}s ({rate:.1f} files/s): "
          f"new images {s.new_images}, duplicates {s.duplicates}, "
          f"decode errors {s.decode_errors}, read errors {s.read_errors}"
          + ("  [interrupted; run again to continue]" if s.interrupted else ""))


def cmd_folders(con, cfg, args):
    from lineagebox.folders import date_from_folders, parse_folders
    from lineagebox.llm import Ollama

    parsed, missing = parse_folders(con, Ollama(cfg),
                                    progress=lambda done, total: print(f"  {done}/{total}", flush=True),
                                    about=cfg.library_about)
    print(f"parsed {parsed} folders" + (f", {missing} not answered (run again to retry)" if missing else ""))
    print(f"dated {date_from_folders(con)} photos from folder names (they only had a file time)")


def cmd_dedup(con, cfg, args):
    from lineagebox import dedup

    groups, members = dedup.group(con)
    print(f"{groups} near-duplicate groups covering {members} images")


def cmd_describe(con, cfg, args):
    from lineagebox.describe import describe

    s = describe(con, cfg, folder=args.folder, limit=args.limit, media="video" if args.videos else None)
    if not s.pending:
        print("nothing to describe")
        return
    rate = s.seconds / s.done if s.done else 0
    print(f"described {s.done}/{s.pending} in {s.seconds:.0f}s ({rate:.1f} s/photo), failed {s.failed}"
          + ("  [interrupted; run again to continue]" if s.interrupted else "")
          + ("  [Ollama stopped answering; run again to continue]" if s.gave_up else ""))


def cmd_faces(con, cfg, args):
    from lineagebox.faces import detect

    s = detect(con, cfg, limit=args.limit, gpu=not args.cpu)
    if not s.pending:
        print("no photos waiting for face detection")
        return
    print(f"{s.faces} faces in {s.images}/{s.pending} photos in {s.seconds:.0f}s "
          f"({s.seconds / max(s.images, 1) * 1000:.0f} ms/photo, {s.provider})"
          + ("  [interrupted; run again to continue]" if s.interrupted else ""))


def cmd_people(con, cfg, args):
    from lineagebox import people

    if args.identify:
        print(f"recognised {people.identify(con)} more faces of named people")
    clusters, faces = people.cluster(con)
    print(f"{clusters} groups of unnamed faces ({faces} faces) ready to name on the People page")


def cmd_sharpness(con, cfg, args):
    from lineagebox import quality

    print(f"measured {quality.measure(con, cfg)} photos")


def cmd_transcribe(con, cfg, args):
    from lineagebox import speech

    if not speech.Whisper(cfg).reachable():
        print(f"the Whisper service at {cfg.whisper_url or '(none set)'} is not answering")
        return
    s = speech.transcribe(con, cfg, limit=args.limit)
    if not s.pending:
        print("no videos, recordings or stories waiting for a transcript")
        return
    print(f"transcribed {s.done}/{s.pending} ({s.stories} of them recorded stories; {s.audio_seconds / 60:.0f} min "
          f"of sound) in "
          f"{s.seconds / 60:.1f} min, {s.failed} failed" + ("  [Whisper stopped answering]" if s.gave_up else ""))


def cmd_copies(con, cfg, args):
    from lineagebox import copies

    if args.once:
        made = copies.make(con, cfg, limit=args.limit)
        print(f"made {made} 720p copies; {copies.count_pending(con)} still to make")
    else:
        copies.run_forever(con, cfg)  # the Docker copies service


def cmd_index(con, cfg, args):
    from lineagebox import search

    print(f"indexed {search.rebuild(con)} images for search")


def cmd_status(con, cfg, args):
    s = catalog.stats(con)
    print(f"watching  {', '.join(str(cfg.share_root / f) for f in cfg.folders)}\ndata      {cfg.data_dir}")
    print(f"folders   {s['folders']} ({s['folders_ignored']} ignored)")
    print(f"files     {s['files']}: {s['files_ingested']} ingested, {s['files_pending']} pending, "
          f"{s['files_ignored']} in ignored folders, {s['files_missing']} missing, "
          f"{s['files_read_errors']} read errors")
    print(f"images    {s['images']} unique, {s['images_undecodable']} undecodable, {s['images_with_gps']} with GPS")
    print(f"videos    {s['videos']} ({s['video_hours']:.1f} hours)")
    print("formats   " + ", ".join(f"{k}: {n}" for k, n in s["formats"].items()))
    print("dates     " + ", ".join(f"{k}: {n}" for k, n in s["date_sources"].items()))


def _stop_on_sigterm() -> None:
    """Docker stops a container with SIGTERM, which Python ignores as a container's first process unless it is told what
    to do: end now, as a stage would end mid-pass (what it read is saved as it goes, and read again next time),
    instead of being killed when Docker's time runs out. SystemExit, which no stage catches, unlike Ctrl+C."""
    import signal

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)


def cmd_worker(con, cfg, args):
    from lineagebox import worker

    if args.once:
        worker.run_once(con, cfg)
    else:
        worker.run_forever(con, cfg)  # reads the Settings page's changes before every pass


def cmd_serve(con, cfg, args):
    import uvicorn

    from lineagebox import serving
    from lineagebox.web.app import create_app

    con.close()
    app = create_app(cfg)
    while True:
        tls, problem = serving.tls(cfg)  # None: plain HTTP (HTTPS off, or its certificate can't be loaded)
        print(f"LineageBox on {'https' if tls else 'http'}://{args.host}:{args.port}"
              + (f" ({problem})" if problem else ""))
        # uvicorn's own proxy headers are off: the app believes them only from trusted_proxies (see create_app), so
        # the network allow list sees the real client address
        server = uvicorn.Server(uvicorn.Config(
            app, host=args.host, port=args.port, log_level="warning", proxy_headers=False,
            timeout_graceful_shutdown=5, **({"ssl_certfile": tls[0], "ssl_keyfile": tls[1]} if tls else {})))
        changed = serving.watch(cfg, server)  # stops it when HTTPS is switched or the certificate is renewed
        server.run()
        if not changed.is_set():
            return  # stopped for good (Ctrl+C, docker stop)


def cmd_https(con, cfg, args):
    from lineagebox import https

    if args.action == "off":
        settings.save(con, cfg, {"tls_cert": "", "tls_key": ""})
        print("HTTPS is off: the web UI serves plain HTTP again within a minute.")
        return
    if not cfg.tls_cert:
        print("HTTPS is off. It is turned on on the Settings page, under Address and HTTPS.")
        return
    print(f"certificate  {cfg.tls_cert}\nkey          {cfg.tls_key}")
    try:
        cert = https.check(cfg.tls_cert, cfg.tls_key)
    except https.Problem as e:
        sys.exit(f"LineageBox: {e} The web UI serves plain HTTP until that is fixed.")
    print(f"for          {', '.join(cert.names) or '(no names)'}")
    print(f"issuer       {cert.issuer}{' (self-signed)' if cert.self_signed else ''}")
    if cert.expires:
        print(f"good until   {cert.expires:%Y-%m-%d}{' (expired)' if cert.expired else ''}")


def cmd_check_files(con, cfg, args):
    from lineagebox import check

    n = check.rebuild_derivatives(con, cfg)
    print(f"previews: {n} photos and videos have theirs gone; the worker's next pass makes them again" if n
          else "previews: none gone")
    checked, damaged = check.check(con, cfg, every=args.all, threads=cfg.ingest_threads,
                                   progress=lambda done, total: done % 1000 < check.COMMIT_EVERY
                                   and print(f"  {done}/{total}", flush=True))
    p = check.progress(con)
    print(f"read {checked} files whole: {damaged} damaged. {p['checked']} of {p['files']} files checked so far, "
          f"{p['damaged']} damaged (Cleanup → Damaged files)")


def cmd_backup(con, cfg, args):
    """A copy of the catalog now, as the worker makes once a day (backup.py)."""
    from lineagebox import backup
    from lineagebox.storage import fmt_size

    try:
        state = backup.make(con, cfg, why="command")
    except backup.Failed as e:
        sys.exit(f"LineageBox: {e}")
    last = state["last"]
    print(f"made {Path(last['folder']) / last['file']} ({fmt_size(last['bytes'])}) and checked it; {state['kept']} "
          f"copies kept there ({fmt_size(state['kept_bytes'])})")


def check_backup(path: str) -> None:
    """Read a copy of the catalog whole and say what it holds, before it is put back: needs no config and no catalog,
    which may be what was lost."""
    from lineagebox import backup
    from lineagebox.storage import fmt_size

    try:
        found = backup.check(Path(path))
    except backup.Failed as e:
        sys.exit(f"LineageBox: {e}")
    newest = backup.newest_version()
    print(f"{path} is whole (SQLite's integrity check found nothing wrong).")
    if found["version"] > newest:
        print(f"Catalog version {found['version']}: newer than this LineageBox ({__version__}) knows ({newest}). "
              "Put it back with the version that made it, or a newer one.")
    elif found["version"] < newest:
        print(f"Catalog version {found['version']}: this LineageBox ({__version__}) upgrades it to {newest} when it "
              "starts with it, keeping a copy as it was first.")
    else:
        print(f"Catalog version {found['version']}, as this LineageBox ({__version__}) makes them.")
    print(f"{found['photos']:,} photos and videos, {found['people']:,} people, {found['named_faces']:,} named faces; "
          f"{fmt_size(found['bytes'])}.")


def cmd_demo(con, cfg, args):
    """A public demo (demo.py, docs/demo.md): keep it as it is now, or put it back."""
    from lineagebox import demo

    try:
        if args.action == "save":
            done = demo.save(con, cfg)
            print(f"saved the demo as it is now in {demo.seed_dir(cfg)} (catalog version {done['catalog_version']}, "
                  f"{len(done['folders'])} watched folders; {done['copied']} files copied, {done['removed']} "
                  "deleted there): `lineagebox demo reset` puts this back")
        else:
            done = demo.reset(cfg)
            print(f"put the demo back as it was saved {done['saved_at']}: {done['copied']} files copied back, "
                  f"{done['removed']} files and folders made since deleted")
    except demo.Problem as e:
        sys.exit(f"LineageBox: {e}")


def cmd_export(con, cfg, args):
    from lineagebox import export

    path = Path(args.file)
    if path.is_dir():
        path = path / export.file_name()
    try:
        counts = export.write(con, cfg, path)
    except BaseException:
        path.unlink(missing_ok=True)  # not half a file that looks like an export
        raise
    print(f"wrote {path}: the family tree ({counts['people']:,} people) and {counts['photos']:,} photos and videos, "
          f"with {counts['named_faces']:,} named faces, {counts['tags']:,} tags given by hand, {counts['captions']:,} "
          f"captions, {counts['comments']:,} comments and {counts['documents']:,} documents (docs/export.md)")


def cmd_accounts(con, cfg, args):
    """Family accounts from the command line: the first admin, and getting in when the web page can't be used."""
    from lineagebox import accounts, passkeys

    def account():
        row = accounts.by_email(con, args.email or "")
        if row is None:
            sys.exit(f"LineageBox: no account for {args.email}")
        return row

    try:
        if args.action == "list":
            rows = accounts.everyone(con)
            for r in rows:
                state = " (switched off)" if r["disabled_at"] else ""
                print(f"{r['email']:40} {r['role']:7} {r['person_name'] or '-'}{state}")
            print(f"Family accounts are {'on' if cfg.accounts_enabled else 'off'}." if rows else "No accounts yet.")
        elif args.action == "add":
            pid = None
            if args.person:
                found = [r[0] for r in con.execute("SELECT id FROM person WHERE lower(name) = lower(?)",
                                                   (" ".join(args.person.split()),))]
                if len(found) != 1:
                    sys.exit(f"LineageBox: {len(found) or 'nobody'} in the family tree "
                             f"{'is' if len(found) < 2 else 'are'} called {args.person}")
                pid = found[0]
            accounts.add(con, args.email or "", pid, "admin" if args.admin else "member")
            con.commit()
            print(f"Added {args.email} as {'an admin' if args.admin else 'a member'}.")
        elif args.action in ("disable", "enable"):
            accounts.update(con, account()["id"], disabled=args.action == "disable")
            con.commit()
            print(f"{args.email} is {'switched off' if args.action == 'disable' else 'switched back on'}.")
        elif args.action == "remove":
            accounts.remove(con, account()["id"])
            con.commit()
            print(f"Removed {args.email}.")
        elif args.action == "sign-out":
            print(f"Signed {args.email} out of {accounts.end_sessions(con, account()['id'])} browsers.")
        elif args.action == "local-code":
            row = account()
            secret = accounts.set_local_secret(con, row["id"])
            print(f"Code for signing in at home without the internet, for {row['email']}:\n"
                  f"  In Google Authenticator: + → Enter a setup key. Account: {row['email']}  Key: "
                  f"{' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))}  (time based)\n"
                  f"  Or as a link: {accounts.setup_uri(secret, row['email'])}\n"
                  "Keep the key somewhere safe; running this again makes a new one and the old one stops working.")
        elif args.action == "invite":
            row = account()
            token, until = passkeys.make_invite(con, row["id"], None, cfg)
            print(f"Link for making a passkey for {row['email']}, until {until:%Y-%m-%d %H:%M}:\n"
                  f"  {passkeys.invite_link(cfg, token)}\n"
                  "Open it on their own phone or computer. It works once; running this again makes a new one and "
                  "this one stops working.")
        elif args.action == "passkeys":
            row = account()
            keys = passkeys.of_account(con, row["id"])
            for k in keys:
                used = f", last used {k['last_used_at'][:16]}" if k["last_used_at"] else ""
                print(f"{k['name']:30} made {k['created_at'][:16]}{used}, for {k['rp_id']}")
            print(f"{len(keys)} passkey{'' if len(keys) == 1 else 's'} for {row['email']}. Take one away on Settings → "
                  "Family accounts, or switch the account off.")
        elif args.action in ("on", "off"):
            settings.save(con, cfg, {"accounts_enabled": args.action == "on"})
            print(f"Family accounts are {args.action}: the web app follows within a few seconds.")
    except (accounts.Problem, passkeys.Problem, settings.Invalid) as e:
        sys.exit(f"LineageBox: {e}")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # some library paths are not cp1252-safe
    parser = argparse.ArgumentParser(prog="lineagebox")
    parser.add_argument("--version", action="version", version=f"LineageBox {__version__}")
    parser.add_argument("--config", help="path to config.toml (default: LINEAGEBOX_CONFIG, else the project root's); "
                                         "LINEAGEBOX_* environment variables override it")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="record folders, photos and videos on the share")
    p = sub.add_parser("ingest", help="hash, dedupe, read metadata, write previews and thumbnails")
    p.add_argument("--limit", type=int, help="process at most this many files")
    p.add_argument("--retry-errors", action="store_true", help="re-read files that previously failed to decode")
    p.add_argument("--folder", help="only files under this folder, e.g. \"local/Photos/2012\"")
    sub.add_parser("folders", help="read places, events and dates from folder names (uses the LLM)")
    sub.add_parser("dedup", help="group near-duplicates (edited copies, burst shots)")
    p = sub.add_parser("describe", help="describe photos with the vision LLM (Ctrl+C safe, resumable)")
    p.add_argument("--folder", help="only photos under this folder, e.g. \"Photos/2012/Zoo trip\"")
    p.add_argument("--limit", type=int, help="describe at most this many photos")
    p.add_argument("--videos", action="store_true", help="only videos")
    p = sub.add_parser("faces", help="find faces in photos (GPU; Ctrl+C safe, resumable)")
    p.add_argument("--limit", type=int, help="process at most this many photos")
    p.add_argument("--cpu", action="store_true", help="run on the CPU (slower; leaves the GPU to Ollama)")
    p = sub.add_parser("people", help="group unnamed faces for naming")
    p.add_argument("--identify", action="store_true", help="first label faces that clearly match named people")
    sub.add_parser("sharpness", help="measure sharpness, for the Blank and blurry cleanup list")
    p = sub.add_parser("transcribe", help="transcribe what is said in videos, sound recordings and recorded "
                                          "stories (the Whisper service)")
    p.add_argument("--limit", type=int, help="transcribe at most this many")
    p = sub.add_parser("copies", help="make 720p copies of videos browsers cannot play (the Docker copies service)")
    p.add_argument("--once", action="store_true", help="make the copies that are needed now, then exit")
    p.add_argument("--limit", type=int, help="with --once: make at most this many")
    sub.add_parser("index", help="rebuild the search index")
    sub.add_parser("status", help="show catalog counts")
    p = sub.add_parser("worker", help="run every stage now and then every interval (the Docker worker service)")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p = sub.add_parser("serve", help="run the web UI")
    p.add_argument("--host", default=os.environ.get("LINEAGEBOX_HOST", "127.0.0.1"),
                   help="interface to bind (default: LINEAGEBOX_HOST, else this machine only; 0.0.0.0 in Docker)")
    p.add_argument("--port", type=int, default=os.environ.get("LINEAGEBOX_PORT", "8000"))  # argparse checks it
    p = sub.add_parser("https",
                       help="show the web UI's HTTPS certificate, or turn HTTPS off (the Settings page sets it)")
    p.add_argument("action", nargs="?", choices=["off"], help="off: serve plain HTTP again")
    p = sub.add_parser("check-files", help="read every file whole for damage a disk did (Cleanup → Damaged files), "
                                           "and have previews that are gone made again")
    p.add_argument("--all", action="store_true", help="check again files already checked")
    sub.add_parser("updater", help="install newer images when an admin asks on the Status page (the Docker updater "
                                   "service; it needs the Docker socket)")
    p = sub.add_parser("accounts", help="family accounts: list, add, switch off, sign out; an admin's code for "
                                        "signing in at home without the internet; a link for making a passkey, "
                                        "and the passkeys made; turn accounts on or off")
    p.add_argument("action", choices=["list", "add", "disable", "enable", "remove", "sign-out", "local-code", "invite",
                                      "passkeys", "on", "off"])
    p.add_argument("email", nargs="?", help="the account's email address")
    p.add_argument("--admin", action="store_true", help="with add: an admin, who sees and changes everything")
    p.add_argument("--person", help="with add: who they are in the family tree, by name")
    p = sub.add_parser("backup", help="copy the catalog now, checked, into the backup folder (the worker does it once "
                                      "a day); --check reads a copy whole and says what it holds")
    p.add_argument("--check", metavar="FILE", help="check this copy of the catalog instead (needs no config or "
                                                   "catalog)")
    p = sub.add_parser("export", help="the family tree (GEDCOM) and every photo's dates, tags, names on faces, "
                                      "captions and comments, as one zip (docs/export.md)")
    p.add_argument("file", help="the zip to write, or a folder to write lineagebox-export-<date>.zip in")
    p = sub.add_parser("demo", help="a public demo (docs/demo.md): save keeps the catalog, the data folder and the "
                                    "watched folders as they are now; reset, with web and worker stopped, puts them "
                                    "back, deleting everything made since")
    p.add_argument("action", choices=["save", "reset"])
    args = parser.parse_args(argv)
    if args.command in ("worker", "copies", "updater") and not getattr(args, "once", False):
        _stop_on_sigterm()
    if args.command == "updater":  # reads no config and no catalog: see updater.py
        from lineagebox import updater
        return updater.main()
    if args.command == "backup" and args.check:  # nor this: the catalog may be what was lost
        return check_backup(args.check)

    try:
        base = config.load(args.config)
        if problem := db.unwritable(base):  # before migrations, which would fail on it less plainly
            sys.exit(f"LineageBox: {problem}")
        con = db.connect(base)
    except (config.ConfigError, sqlite3.DatabaseError, OSError) as e:  # a catalog damaged or a folder not there
        sys.exit(f"LineageBox: {e}")
    # with the Settings page's changes; the web app and a running worker look them up again as they go
    runs_on = args.command == "serve" or (args.command in ("worker", "copies") and not args.once)
    cfg = base if runs_on else settings.current(con, base)
    awake = keep_awake() if args.command in ("ingest", "faces", "describe") else contextlib.nullcontext()
    commands = {"scan": cmd_scan, "ingest": cmd_ingest, "folders": cmd_folders, "dedup": cmd_dedup,
                "describe": cmd_describe, "faces": cmd_faces, "people": cmd_people, "sharpness": cmd_sharpness,
                "transcribe": cmd_transcribe, "copies": cmd_copies, "index": cmd_index,
                "status": cmd_status, "worker": cmd_worker, "serve": cmd_serve,
                "https": cmd_https, "accounts": cmd_accounts, "check-files": cmd_check_files, "backup": cmd_backup,
                "export": cmd_export, "demo": cmd_demo}
    try:
        with awake:
            commands[args.command](con, cfg, args)
    except (sqlite3.DatabaseError, OSError) as e:  # the catalog or a disk: said, not a traceback
        sys.exit(f"LineageBox: {type(e).__name__}: {e}")
    finally:
        con.close()
