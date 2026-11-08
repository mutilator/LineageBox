"""New for you: what happened that someone would want to know, kept once as an event, and shown inside the site to each
account as they may see it. Nothing is sent outside the house.

Each event is written once, by the code that knows of it:
- sharing.share: something shared with someone (or with everyone);
- routes/home.py: "Who is this?" asked about a face, for everyone who sees its photo;
- comments.add_comment: a comment on a photo, for whoever uploaded it, whoever is in it and whoever wrote on it before;
- the worker (after_pass): new photos, once their faces are looked for; and, for admins, files found damaged, a disk
  nearly full, Ollama away for days and a newer release;
- activity.note_problem: a service with a problem (the model missing, describing failing...), for admins.

Who sees an event is worked out each time it is shown, from what they see then (for_viewer): a member is told only of
photos, faces and shares that are theirs to see now, however it was when the event was written; what someone did
themselves is never news to them; alerts are for admins. Without family accounts the house has one list, as an admin.
Each account's seen mark (outbox_seen) is the newest event they have seen: opening New for you moves it.

Email or Web Push, should they come, would read the same events with the same check, each keeping a mark of its own
for what it has sent (as outbox_seen does for the page). Neither is built: nothing leaves the house.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta

from lineagebox import settings
from lineagebox.catalog import VISIBLE

KEEP_DAYS = 90  # events older than this are deleted
LOOKED_AT = 400  # events looked at for one viewer, newest first: their list shows those they may see of these
SHOWN = 6  # pictures shown with an event
PHOTOS_MOST = 2000  # photos one "new photos" event names: more at once is a library being read, told in parts
WAITING_MOST = 5000  # new photos kept waiting for their faces to be looked for, the newest
OLLAMA_AWAY = timedelta(days=3)  # Ollama not answering this long is told to admins (a PC asleep for a night isn't)
DISK_AGAIN = timedelta(days=7)  # a disk still nearly full is told again after this
SERVICE_AGAIN = timedelta(days=1)  # the same problem with a service, again after this
PHOTOS_AFTER, PHOTOS_WAITING = "outbox_photos_after", "outbox_photos_waiting"  # settings: new photos told up to here
DAMAGE_AFTER = "outbox_damage_after"  # settings: damaged files told of, by when they were checked
OLLAMA_UP = "outbox_ollama_up"  # settings: when Ollama last answered the worker
DISKS_ON = "outbox_disks_on"  # settings: the day the disks were last looked at
ALERTS = ("damaged", "disk_low", "service", "update")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---- writing -------------------------------------------------------------------------------------------------------

def add(con: sqlite3.Connection, kind: str, *, admins: bool = False, to: int | None = None, by: int | None = None,
        image_id: int | None = None, face_id: int | None = None, share_id: int | None = None,
        comment_id: int | None = None, data: dict | None = None, key: str | None = None,
        again_after: timedelta | None = None) -> int | None:
    """Write one event; doesn't commit, so it goes with the change it is about. key: an alert told once (again_after
    None) or again only after again_after. Returns its id, or None when it wasn't written (told already, or the
    catalog busy: an event is never worth failing the change it is about)."""
    try:
        if key is not None:
            last = con.execute("SELECT max(at) FROM main.outbox WHERE key = ?", (key,)).fetchone()[0]
            if last and (again_after is None or datetime.now() - datetime.fromisoformat(last) < again_after):
                return None
        return con.execute("""INSERT INTO main.outbox(at, kind, admins, to_account, by_account, image_id, face_id,
                                                      share_id, comment_id, data, key)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (_now(), kind, int(admins), to, by, image_id, face_id, share_id, comment_id,
                            json.dumps(data or {}, separators=(",", ":")), key)).lastrowid
    except sqlite3.OperationalError:
        return None


def shared(con: sqlite3.Connection, share_id: int) -> None:
    """Something was shared (sharing.share): news for whoever it is shared with, or for everyone."""
    row = con.execute("SELECT account_id, with_id FROM main.share WHERE id = ?", (share_id,)).fetchone()
    if row is not None:
        add(con, "share", to=row["with_id"], by=row["account_id"], share_id=share_id)


def asked(con: sqlite3.Connection, face_id: int, account_id: int | None) -> None:
    """Who is this? (routes/home.py): news for everyone who sees the photo, but the one who asked."""
    row = con.execute("SELECT image_id FROM main.face WHERE id = ?", (face_id,)).fetchone()
    if row is not None:
        add(con, "who_is_this", by=account_id, face_id=face_id, image_id=row["image_id"])


def commented(con: sqlite3.Connection, comment_id: int) -> None:
    """A comment on a photo (comments.add_comment): news for whoever uploaded it, the accounts of the people in it,
    and whoever wrote on it before, each once, never its writer. Without family accounts nobody is told."""
    row = con.execute("SELECT image_id, account_id FROM main.comment WHERE id = ?", (comment_id,)).fetchone()
    if row is None:
        return
    image_id, writer = row["image_id"], row["account_id"]
    why: dict[int, str] = {}
    for (a,) in con.execute("SELECT account_id FROM main.comment WHERE image_id = ? AND kind = 'comment' AND id != ?"
                            " AND account_id IS NOT NULL", (image_id, comment_id)):
        why[a] = "wrote"
    for (a,) in con.execute("""SELECT a.id FROM main.account a WHERE a.disabled_at IS NULL AND a.person_id IN (
                                   SELECT person_id FROM main.face
                                   WHERE image_id = :i AND assigned_by IN ('user', 'auto')
                                   UNION SELECT person_id FROM main.image_person WHERE image_id = :i)""",
                            {"i": image_id}):
        why[a] = "in"
    for (a,) in con.execute("SELECT account_id FROM main.upload WHERE image_id = ?", (image_id,)):
        why[a] = "uploaded"
    for account_id, reason in why.items():
        if account_id != writer:
            add(con, "comment", to=account_id, by=writer, image_id=image_id, comment_id=comment_id,
                data={"why": reason})


def service_problem(con: sqlite3.Connection, name: str, message: str) -> None:
    """A service has a problem (activity.note_problem): told to admins, the same one again only after a day."""
    if add(con, "service", admins=True, data={"service": name, "message": message[:500]},
           key=f"service:{name}:{message[:200]}", again_after=SERVICE_AGAIN) is not None:
        con.commit()


def after_pass(con: sqlite3.Connection, cfg) -> int:
    """What the worker tells after its pass: new photos, and admins' alerts. Then events older than KEEP_DAYS go.
    Returns how many events were written. Commits."""
    n = new_photos(con, cfg) + damaged_files(con) + disks(con, cfg) + ollama_away(con, cfg) + update(con)
    con.execute("DELETE FROM main.outbox WHERE at < ?", ((datetime.now() - timedelta(days=KEEP_DAYS)).isoformat(),))
    con.commit()
    return n


def new_photos(con: sqlite3.Connection, cfg) -> int:
    """New photos and videos, once their faces have been looked for (so that members whose family is in them can be
    told), in events of PHOTOS_MOST at most; one of each group of similar shots. Those whose faces are still to be
    looked for wait for a later pass. The first time (a new catalog, read whole) nothing is told: it is all new."""
    from lineagebox.faces import STAGE_FACES
    after = int(settings.get(con, PHOTOS_AFTER) or 0)
    newest = con.execute("SELECT coalesce(max(id), 0) FROM image").fetchone()[0]
    if not after:
        settings.put(con, PHOTOS_AFTER, newest)
        return 0
    waiting = settings.get(con, PHOTOS_WAITING) or []
    rows = con.execute(f"""SELECT i.id, i.stage FROM image i
                           WHERE (i.id > :after OR i.id IN (SELECT value FROM json_each(:waiting))) AND {VISIBLE}
                             AND (i.dup_group_id IS NULL OR i.dup_group_id = i.id) ORDER BY i.id""",
                       {"after": after, "waiting": json.dumps(waiting)}).fetchall()
    ready = [r["id"] for r in rows if not cfg.faces_enabled or r["stage"] & STAGE_FACES]
    told = set(ready)
    still = [r["id"] for r in rows if r["id"] not in told][-WAITING_MOST:]
    written = 0
    for start in range(0, len(ready), PHOTOS_MOST):
        part = ready[start:start + PHOTOS_MOST]
        written += add(con, "new_photos", data={"ids": part}) is not None
    con.commit()
    settings.put(con, PHOTOS_WAITING, still)
    settings.put(con, PHOTOS_AFTER, max(newest, after))
    return written


def damaged_files(con: sqlite3.Connection) -> int:
    """Files the check found damaged since the last told (check.py), as one alert."""
    after = settings.get(con, DAMAGE_AFTER) or ""
    rows = con.execute("""SELECT count(*), max(checked_at) FROM main.file
                          WHERE damage IS NOT NULL AND NOT missing AND checked_at > ?""", (after,)).fetchone()
    if not rows[0]:
        return 0
    written = add(con, "damaged", admins=True, data={"files": rows[0]}) is not None
    con.commit()
    settings.put(con, DAMAGE_AFTER, rows[1])
    return written


def disks(con: sqlite3.Connection, cfg) -> int:
    """Once a day: a disk nearly full (storage.LOW_SHARE or LOW_BYTES left), told again each week while it is."""
    today = date.today().isoformat()
    if settings.get(con, DISKS_ON) == today:
        return 0
    from lineagebox import storage
    written = 0
    for d in storage.disks(con, cfg):
        if d["low"]:
            where = ", ".join(d["folders"]) + (" and LineageBox's files" if d.get("data") and d["folders"]
                                               else "LineageBox's files" if d.get("data") else "")
            written += add(con, "disk_low", admins=True, data={"free": d["free"], "total": d["total"], "where": where},
                           key=f"disk_low:{where}", again_after=DISK_AGAIN) is not None
    con.commit()
    settings.put(con, DISKS_ON, today)
    return written


def ollama_away(con: sqlite3.Connection, cfg) -> int:
    """Ollama not answering the worker for OLLAMA_AWAY or more (the PC it runs on off, or moved): photos wait to be
    described. A PC asleep overnight is usual, so only days count; told once for each time it goes away."""
    if not cfg.describe_enabled:
        return 0
    state = settings.get(con, "worker", {}) or {}
    if state.get("ollama"):
        settings.put(con, OLLAMA_UP, _now())
        return 0
    up = settings.get(con, OLLAMA_UP)
    if state.get("ollama") is not False or not up or datetime.now() - datetime.fromisoformat(up) < OLLAMA_AWAY:
        return 0
    written = add(con, "service", admins=True, key=f"away:ollama:{up}",
                  data={"service": "Ollama", "message": f"It hasn't answered since {up[:16].replace('T', ' ')}, so "
                                                        f"photos wait to be described. Is the PC it runs on on?"})
    con.commit()
    return written is not None


def update(con: sqlite3.Connection) -> int:
    """A newer release (updates.py found it): told to admins once for each version."""
    from lineagebox import updates
    release = updates.available(con)
    if not release:
        return 0
    written = add(con, "update", admins=True, data={"version": release["version"], "url": release.get("url", "")},
                  key=f"update:{release['version']}")
    con.commit()
    return written is not None


# ---- reading -------------------------------------------------------------------------------------------------------

def _mark_key(v) -> int:
    return v.account_id or 0


def seen_id(con: sqlite3.Connection, v) -> int:
    row = con.execute("SELECT seen_id FROM outbox_seen WHERE account_id = ?", (_mark_key(v),)).fetchone()
    return row[0] if row else 0


def mark_seen(con: sqlite3.Connection, v, upto: int) -> None:
    """They have seen every event up to this one (on a connection that may write: a member's writer)."""
    if upto <= 0 or v.seeing_as:
        return
    try:
        con.execute("""INSERT INTO main.outbox_seen(account_id, seen_id) VALUES (?, ?)
                       ON CONFLICT(account_id) DO UPDATE SET seen_id = max(seen_id, excluded.seen_id)""",
                    (_mark_key(v), upto))
        con.commit()
    except sqlite3.OperationalError:  # busy: marked the next time
        con.rollback()


def for_viewer(con: sqlite3.Connection, v, most: int = 100) -> list[dict]:
    """The events this viewer may see now, newest first, at most `most`: each with its words, where it leads, the
    pictures to show with it and whether it is new to them. con: the viewer's own connection, or one that sees
    everything; either way each event is checked against v, now."""
    me = v.account_id
    rows = con.execute("""SELECT * FROM outbox WHERE (:admin OR NOT admins) AND (to_account IS NULL OR to_account = :me)
                          AND coalesce(by_account, 0) != :me ORDER BY id DESC LIMIT :n""",
                       {"admin": int(v.admin), "me": me if me is not None else -1, "n": LOOKED_AT}).fetchall()
    seen, out = seen_id(con, v), []
    check = _Checks(con, v)
    for r in rows:
        item = check.event(r)
        if item is None:
            continue
        item.update(id=r["id"], at=r["at"], kind=r["kind"], new=r["id"] > seen, alert=bool(r["admins"]))
        out.append(item)
        if len(out) >= most:
            break
    return out


def unseen(con: sqlite3.Connection, v) -> int:
    """How many events are new to this viewer (the count at the top of each page)."""
    return sum(e["new"] for e in for_viewer(con, v))


def event_for(con: sqlite3.Connection, v, event_id: int) -> dict | None:
    """One event as this viewer may see it, or None."""
    row = con.execute("""SELECT * FROM outbox WHERE id = ? AND (? OR NOT admins)
                         AND (to_account IS NULL OR to_account = ?) AND coalesce(by_account, 0) != ?""",
                      (event_id, int(v.admin), v.account_id or -1, v.account_id or -1)).fetchone()
    item = _Checks(con, v).event(row) if row is not None else None
    if item is not None:
        item.update(id=row["id"], at=row["at"], kind=row["kind"], alert=bool(row["admins"]))
    return item


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


class _Checks:
    """Whether the viewer may see each event now, and what it says to them. Looks things up once for all events."""

    def __init__(self, con: sqlite3.Connection, v):
        self.con, self.v = con, v
        self._shares = self._names = None

    def names(self) -> dict[int, tuple]:
        """account id -> (person id, name, email)."""
        if self._names is None:
            self._names = {r[0]: (r[1], r[2], r[3]) for r in self.con.execute(
                """SELECT a.id, a.person_id, p.name, a.email FROM main.account a
                   LEFT JOIN main.person p ON p.id = a.person_id""")}
        return self._names

    def who(self, account_id: int | None, nobody: str = "Someone in the family") -> str:
        """Who did it, as the viewer may be told: an admin sees names and emails, a member names of people in their
        part of the family (sharing.who)."""
        from lineagebox import sharing
        if account_id is None or account_id not in self.names():
            return nobody
        person, name, email = self.names()[account_id]
        if self.v.admin:
            return name or email or nobody
        said = sharing.who(self.v, person, name, email)
        return nobody if said.startswith("someone outside") else said

    def shares(self) -> set[int]:
        """The shares that still hold for a member (access.active_shares); an admin: every share there is."""
        if self._shares is None:
            from lineagebox import access
            if self.v.admin or self.v.account_id is None:
                self._shares = {r[0] for r in self.con.execute("SELECT id FROM main.share")}
            else:
                self._shares = {r["id"] for r in access.active_shares(self.con, self.v.account_id)}
        return self._shares

    def pictures(self, ids) -> list[dict]:
        """Of these photos, those still there that the viewer sees: [{id, sha256, media}], in this order."""
        ids = [i for i in ids if self.v.sees_image(i)]
        if not ids:
            return []
        found = {r[0]: r for r in self.con.execute(f"""SELECT i.id, i.sha256, i.media FROM main.image i
                                                       WHERE i.id IN (SELECT value FROM json_each(?)) AND {VISIBLE}""",
                                                  (json.dumps(ids),))}
        return [{"id": i, "sha256": found[i]["sha256"], "media": found[i]["media"]} for i in ids if i in found]

    def event(self, r) -> dict | None:
        kind, v = r["kind"], self.v
        data = json.loads(r["data"] or "{}")
        if r["admins"] and not v.admin:
            return None
        if kind == "share":
            return self.share(r)
        if kind == "who_is_this":
            return self.who_is_this(r)
        if kind == "new_photos":
            return self.new_photos(r, data)
        if kind == "comment":
            return self.comment(r, data)
        if kind in ALERTS:
            return alert(kind, data) if v.admin else None
        return None  # a kind this version doesn't know: written by a newer one

    def share(self, r) -> dict | None:
        if r["share_id"] not in self.shares():
            return None
        s = self.con.execute("""SELECT s.*, f.name AS folder_name, t.name AS tour_name FROM main.share s
                                LEFT JOIN main.folder f ON f.id = s.folder_id LEFT JOIN main.tour t ON t.id = s.tour_id
                                WHERE s.id = ?""", (r["share_id"],)).fetchone()
        if s is None:
            return None
        whom = "with you" if s["with_id"] is not None else "with everyone in the family"
        by = self.who(s["account_id"])
        pictures = []
        if s["image_id"] is not None:
            if not (pictures := self.pictures([s["image_id"]])):
                return None
            what, href = "a photo", f"/image/{s['image_id']}"
        elif s["tour_id"] is not None:
            what, href = f"the tour “{s['tour_name']}”", f"/tour/{s['tour_id']}"
        elif s["folder_id"] is not None:
            what, href = (f"the folder “{s['folder_name']}”" if s["folder_name"] else "a folder"), "/folders"
        else:  # an album, or whatever else can be shared by then
            what, href = "an album", "/folders"
        return {"text": f"{by} shared {what} {whom}", "href": href, "photos": pictures, "faces": []}

    def who_is_this(self, r) -> dict | None:
        if not self.v.sees_image(r["image_id"]):
            return None
        open_face = self.con.execute("""SELECT 1 FROM main.face f JOIN main.face_question q ON q.face_id = f.id
                                        WHERE f.id = ? AND f.person_id IS NULL
                                          AND (f.assigned_by IS NULL OR f.assigned_by = 'rejected')
                                          AND f.id NOT IN (SELECT face_id FROM main.face_question_skip
                                                           WHERE account_id = ?)""",
                                     (r["face_id"], self.v.account_id or 0)).fetchone()
        if open_face is None or not self.pictures([r["image_id"]]):
            return None  # named since, or they said they don't know
        asker = self.who(r["by_account"], "Someone")
        return {"text": f"{asker} asked who this is. Do you know?", "href": f"/image/{r['image_id']}", "photos": [],
                "faces": [r["face_id"]]}

    def new_photos(self, r, data) -> dict | None:
        ids = list(data.get("ids") or [])
        if self.v.account_id is not None and ids:  # their own uploads are no news to them
            own = {i for (i,) in self.con.execute("""SELECT image_id FROM main.upload WHERE account_id = ?
                                                     AND image_id IN (SELECT value FROM json_each(?))""",
                                                  (self.v.account_id, json.dumps(ids)))}
            ids = [i for i in ids if i not in own]
        pictures = self.pictures(ids)
        if not pictures:
            return None
        n, videos = len(pictures), sum(p["media"] == "video" for p in pictures)
        what = (_plural(n, "new video") if videos == n else
                f"{_plural(n - videos, 'new photo')} and {_plural(videos, 'video')}" if videos else
                _plural(n, "new photo"))
        text = f"{what} of your family" if self.v.limited else what
        return {"text": text, "href": f"/news/{r['id']}", "photos": pictures[:SHOWN], "more": max(0, n - SHOWN),
                "faces": [], "ids": [p["id"] for p in pictures]}

    def comment(self, r, data) -> dict | None:
        # the comment it was, by whom: a comment taken away leaves its number to the next one written (by anyone, or a
        # caption), whose words would otherwise be shown as this writer's
        row = self.con.execute("""SELECT text FROM main.comment WHERE id = ? AND image_id = ? AND kind = 'comment'
                                  AND account_id IS ?""",
                               (r["comment_id"], r["image_id"], r["by_account"])).fetchone()
        if row is None or not (pictures := self.pictures([r["image_id"]])):
            return None
        words = row["text"] if len(row["text"]) <= 140 else row["text"][:139].rstrip() + "…"
        on = {"uploaded": "your photo", "in": "a photo you're in"}.get(data.get("why"), "a photo you wrote on")
        return {"text": f"{self.who(r['by_account'])} wrote on {on}: “{words}”", "href": f"/image/{r['image_id']}",
                "photos": pictures, "faces": []}


def alert(kind: str, data: dict) -> dict:
    """An admin's alert in words, and where to see about it."""
    from lineagebox.storage import fmt_size
    if kind == "damaged":
        n = data.get("files", 0)
        text = f"{_plural(n, 'file')} found damaged on the photo disk: a copy from a backup may be needed"
        return {"text": text, "href": "/cleanup/damaged", "photos": [], "faces": []}
    if kind == "disk_low":
        return {"text": f"A disk is nearly full: {fmt_size(data.get('free'))} left of {fmt_size(data.get('total'))} on "
                        f"the one with {data.get('where') or 'the photos'}", "href": "/status#storage",
                "photos": [], "faces": []}
    if kind == "update":
        return {"text": f"LineageBox {data.get('version', '')} is out", "href": "/status#updates", "photos": [],
                "faces": []}
    return {"text": f"{data.get('service', 'A service')}: {data.get('message', '')}", "href": "/status",
            "photos": [], "faces": []}
