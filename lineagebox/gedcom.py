"""GEDCOM: the family tree as genealogy programs read and write it (gedcom.io).

Export writes GEDCOM 7.0, the current version, by default, or 5.5.1 for programs that don't read 7.0 yet: a .ged file,
or a GEDZIP (.gdz: the file plus media) with each person's portrait, cropped to their face.

How LineageBox's family maps onto GEDCOM:
- each person is an INDI, with their names (main and other), sex, events, notes and UID;
- each couple, and each set of parents with children, is a FAM: partners as HUSB and WIFE (no more than a word in 7.0:
  a couple of one sex is fine), MARR when they married, DIV when that marriage is over, children as CHIL with how
  they belong (FAMC.PEDI); siblings whose parents aren't known share a FAM with no partners;
- other relationships (friend, neighbor...) are ASSO: FRIEND and NGHBR where GEDCOM has a word, else OTHER with the
  word as its PHRASE;
- pets are INDI records marked with the extension tag _PET (declared in HEAD.SCHMA, as 7.0 asks), with an ASSO
  "Pet" from their owner; programs that don't know _PET still keep them as individuals;
- groups of people who aren't family (Teachers, a band) are the extension tag _GROUP, one per group.
"""
import io
import re
import sqlite3
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

from lineagebox import __version__, family
from lineagebox.config import Config

VERSIONS = ("7.0", "5.5.1")
PET_URI = "urn:uuid:6a1d3c55-9f43-4f0e-8c2b-6f4d2b0a7e11"  # documents LineageBox's _PET extension tag
GROUP_URI = "urn:uuid:2f6e9b0a-41c7-4d38-9a5e-8c1f0d7b3e24"  # and _GROUP: a group someone is in (Teachers)
UID_SPACE = uuid.UUID("0b8f3c1e-5a52-4b8e-9d0f-2a7c6e4d1b93")  # stable UIDs of families with no partner record
ROLES = {"friend": "FRIEND", "best friend": "FRIEND", "family friend": "FRIEND", "neighbor": "NGHBR",
         "neighbour": "NGHBR", "godparent": "GODP", "godfather": "GODP", "godmother": "GODP"}
NAME_TYPES = {"birth": "BIRTH", "married": "MARRIED", "aka": "AKA"}
PEDI = {"birth": "BIRTH", "adopted": "ADOPTED", "foster": "FOSTER"}
SEX = {"male": "M", "female": "F"}
LIVING_YEARS = 110  # someone born less long ago, with no death, may be alive


def export(con: sqlite3.Connection, cfg: Config, version: str = "7.0", photos: bool = False,
           private_living: bool = False) -> tuple[bytes, str]:
    """(file contents, file name): a .ged, or with photos a .gdz holding gedcom.ged and the portraits."""
    data, media, name = export_parts(con, cfg, version, photos, private_living)
    if not photos:
        return data, name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("gedcom.ged", data)
        for path, source in media.items():
            z.write(source, path, compress_type=zipfile.ZIP_STORED)  # JPEGs are compressed already
    return buf.getvalue(), name


def export_parts(con: sqlite3.Connection, cfg: Config, version: str = "7.0", photos: bool = False,
                 private_living: bool = False) -> tuple[bytes, dict[str, Path], str]:
    """(gedcom.ged's contents, {path in the zip: file on disk} of the portraits and documents it names, file name): for
    sending the .gdz as it is made (the web app) instead of whole in memory."""
    if version not in VERSIONS:
        raise ValueError(f"unknown GEDCOM version {version}")
    media = {}
    text = _Writer(con, cfg, version, photos, private_living, media).write()
    day = datetime.now().strftime("%Y-%m-%d")
    ending = "ged" if not photos else "gdz" if version == "7.0" else "zip"
    return ("﻿" + text).encode("utf-8"), media, f"family-tree-{day}.{ending}"


class _Writer:
    def __init__(self, con, cfg, version, photos, private_living, media):
        self.con, self.cfg, self.v7 = con, cfg, version == "7.0"
        self.photos, self.private_living, self.media = photos, private_living, media
        self.lines: list[str] = []

    def put(self, level: int, tag: str, payload: str | None = None) -> None:
        """One structure, with a multi-line payload continued in CONT lines (and long 5.5.1 lines in CONC)."""
        if payload is None or payload == "":
            self.lines.append(f"{level} {tag}")
            return
        first, *more = str(payload).split("\n")
        self._line(level, tag, first)
        for line in more:
            self._line(level + 1, "CONT", line)

    def _line(self, level: int, tag: str, text: str) -> None:
        if self.v7:
            text = "@" + text if text.startswith("@") and not _is_pointer(text) else text
            self.lines.append(f"{level} {tag} {text}".rstrip())
            return
        if not _is_pointer(text):
            text = text.replace("@", "@@")
        chunks = [text[i:i + 200] for i in range(0, len(text), 200)] or [""]
        self.lines.append(f"{level} {tag} {chunks[0]}".rstrip())
        for chunk in chunks[1:]:
            self.lines.append(f"{level + 1} CONC {chunk}")

    def write(self) -> str:
        con = self.con
        g = family.load(con)
        people = {r["id"]: r for r in con.execute("SELECT * FROM person ORDER BY id")}
        names = defaultdict(list)
        for r in con.execute("SELECT * FROM person_name ORDER BY id"):
            names[r["person_id"]].append(r)
        evts = defaultdict(list)
        for r in con.execute("SELECT * FROM event ORDER BY sort_date IS NULL, sort_date, id"):
            evts[r["person_id"] if r["person_id"] is not None else (r["couple_a"], r["couple_b"])].append(r)
        pets = {r[0] for r in con.execute("SELECT person_id FROM relation WHERE kind = 'pet'")}
        groups = defaultdict(list)
        for pid, name in con.execute("SELECT person_id, name FROM person_group ORDER BY name"):
            groups[pid].append(name)
        self.groups = groups
        self.cites = defaultdict(list)  # ('p', person) or ('e', event) -> documents (image ids)
        for image_id, pid, eid in con.execute("SELECT image_id, person_id, event_id FROM citation ORDER BY id"):
            self.cites[("p", pid) if pid else ("e", eid)].append(image_id)
        docs = con.execute("""SELECT d.*, i.sha256 FROM document d JOIN image i ON i.id = d.image_id
                              ORDER BY d.image_id""").fetchall()
        partners = {(r["a"], r["b"]): r for r in con.execute("SELECT * FROM partner")}
        pedi = {(r["child_id"], r["parent_id"]): r["pedi"] for r in con.execute("SELECT * FROM parent")}
        living = {p for p, r in people.items() if self._living(r, evts.get(p, ()))}

        # families: each couple, and each set of (effective) parents with children
        fams: dict[frozenset, dict] = {}
        for (a, b), r in partners.items():
            fams[frozenset((a, b))] = {"partners": (a, b), "children": [], "couple": r}
        for child in sorted(people):
            ps = frozenset(g.effective_parents.get(child, ()))
            if ps:
                fam = fams.setdefault(ps, {"partners": tuple(sorted(p for p in ps if p > 0)), "children": [],
                                           "couple": None})
                fam["children"].append(child)
        fam_ids = {key: f"@F{n}@" for n, key in enumerate(sorted(fams, key=lambda k: sorted(k)), 1)}
        famc, fams_of = defaultdict(list), defaultdict(list)
        for key, fam in fams.items():
            for p in fam["partners"]:
                fams_of[p].append(fam_ids[key])
            for c in fam["children"]:
                famc[c].append((fam_ids[key], self._pedi(c, fam["partners"], pedi)))
        covers = self._covers() if self.photos else {}

        self._head()
        for pid, r in people.items():
            self._indi(pid, r, names[pid], evts.get(pid, ()), famc[pid], fams_of[pid], pid in pets,
                       pid in living, covers.get(pid), g, people)
        for key, fam in sorted(fams.items(), key=lambda kv: fam_ids[kv[0]]):
            self._fam(fam_ids[key], fam, people, evts, living)
        for pid, cover in sorted(covers.items()):
            if pid in living and self.private_living:
                continue
            self.put(0, f"@O{pid}@", "OBJE")
            self.put(1, "FILE", cover["path"])
            self.put(2, "FORM", "image/jpeg" if self.v7 else "jpg")
            if not self.v7:
                self.put(3, "TYPE", "photo")
            self.put(2, "TITL", cover["title"])
        for d in docs:
            self.put(0, f"@S{d['image_id']}@", "SOUR")
            self.put(1, "TITL", d["title"])
            self.put(1, "NOTE", d["kind"])
            path = self.cfg.preview_path(d["sha256"])
            if self.photos and path.is_file():
                self.media[f"media/{d['sha256']}.jpg"] = path
                self.put(1, "OBJE", f"@M{d['image_id']}@")
        for d in docs:
            if self.photos and self.cfg.preview_path(d["sha256"]).is_file():
                self.put(0, f"@M{d['image_id']}@", "OBJE")
                self.put(1, "FILE", f"media/{d['sha256']}.jpg")
                self.put(2, "FORM", "image/jpeg" if self.v7 else "jpg")
                if not self.v7:
                    self.put(3, "TYPE", "document")
                self.put(2, "TITL", d["title"])
        self.put(0, "TRLR")
        return "\r\n".join(self.lines) + "\r\n"

    def _head(self) -> None:
        now = datetime.now()
        self.put(0, "HEAD")
        if self.v7:
            self.put(1, "GEDC")
            self.put(2, "VERS", "7.0")
            self.put(1, "SCHMA")
            self.put(2, "TAG", f"_PET {PET_URI}")
            self.put(2, "TAG", f"_GROUP {GROUP_URI}")
        self.put(1, "SOUR", "LINEAGEBOX")
        self.put(2, "VERS", __version__)
        self.put(2, "NAME", "LineageBox")
        self.put(1, "DATE", f"{now.day} {now:%b}".upper() + f" {now.year}")
        self.put(2, "TIME", f"{now:%H:%M:%S}")
        if not self.v7:
            self.put(1, "SUBM", "@U1@")
            self.put(1, "GEDC")
            self.put(2, "VERS", "5.5.1")
            self.put(2, "FORM", "LINEAGE-LINKED")
            self.put(1, "CHAR", "UTF-8")
            self.put(0, "@U1@", "SUBM")
            self.put(1, "NAME", "LineageBox")

    def _indi(self, pid, r, other_names, evts, famc, fams_of, pet, living, cover, g, people) -> None:
        private = living and self.private_living
        self.put(0, f"@I{pid}@", "INDI")
        given = r["given_name"] if r["given_name"] is not None else r["name"]
        surname = r["surname"]
        self.put(1, "NAME", _name("Living" if private else given, surname))
        if r["name_type"] in NAME_TYPES and surname:
            self.put(2, "TYPE", NAME_TYPES[r["name_type"]] if self.v7 else r["name_type"])
        if not private:
            if given:
                self.put(2, "GIVN", given)
            for n in other_names:
                if n["type"] == "nickname":
                    self.put(2, "NICK", n["name"])
        if surname:
            self.put(2, "SURN", surname)
        if not private:
            for n in other_names:
                if n["type"] != "nickname":
                    self.put(1, "NAME", _name(n["given_name"] or n["name"], n["surname"]))
                    self.put(2, "TYPE", NAME_TYPES[n["type"]] if self.v7 else n["type"])
        self.put(1, "SEX", SEX.get(r["sex"], "U"))
        if private:
            self.put(1, "RESN", "PRIVACY" if self.v7 else "privacy")
        else:
            for e in evts:
                self._event(1, e)
        for fam, how in famc:
            self.put(1, "FAMC", fam)
            if how:
                self.put(2, "PEDI", how)
        for fam in fams_of:
            self.put(1, "FAMS", fam)
        if not private:
            for other, kind in self._associations(pid):
                self.put(1, "ASSO", f"@I{other}@")
                role = ROLES.get(kind.lower())
                if self.v7:
                    self.put(2, "ROLE", role or "OTHER")
                    if not role:
                        self.put(3, "PHRASE", kind.capitalize())
                else:
                    self.put(2, "RELA", kind.capitalize())
            if cover:
                self.put(1, "OBJE", f"@O{pid}@")
                if self.v7:
                    self.put(2, "CROP")
                    for tag in ("TOP", "LEFT", "HEIGHT", "WIDTH"):
                        self.put(3, tag, str(cover["crop"][tag]))
            if r["notes"]:
                self.put(1, "NOTE", r["notes"])
            for group in self.groups.get(pid, ()):
                self.put(1, "_GROUP", group)
            for image_id in self.cites.get(("p", pid), ()):
                self.put(1, "SOUR", f"@S{image_id}@")
        if r["uid"]:
            self.put(1, "UID" if self.v7 else "_UID", r["uid"])
        if pet:
            self.put(1, "_PET", "Y")

    def _fam(self, xref, fam, people, evts, living) -> None:
        self.put(0, xref, "FAM")
        partners = list(fam["partners"])
        if len(partners) == 2 and (people[partners[0]]["sex"] == "female" or people[partners[1]]["sex"] == "male"):
            partners.reverse()  # a husband as HUSB and a wife as WIFE, when the sexes say which
        for tag, p in zip(("HUSB", "WIFE"), partners):
            self.put(1, tag, f"@I{p}@")
        for c in fam["children"]:
            self.put(1, "CHIL", f"@I{c}@")
        couple = fam["couple"]
        private = self.private_living and any(p in living for p in fam["partners"])
        if couple is not None and not private:
            own = evts.get((couple["a"], couple["b"]), [])
            tags = {e["tag"] for e in own}
            if couple["married"] and "MARR" not in tags:
                self.put(1, "MARR", "Y")
            for e in own:
                self._event(1, e)
            if couple["married"] and not couple["current"] and "DIV" not in tags:
                self.put(1, "DIV", "Y")
        uid = couple["uid"] if couple is not None and couple["uid"] else str(uuid.uuid5(
            UID_SPACE, "|".join(sorted(people[p]["uid"] or str(p) for p in [*fam["partners"], *fam["children"]]))))
        self.put(1, "UID" if self.v7 else "_UID", uid)

    def _event(self, level: int, e) -> None:
        details = [(t, e[k]) for t, k in (("TYPE", "type"), ("DATE", "date"), ("PLAC", "place"), ("NOTE", "note"))
                   if e[k]]
        sources = self.cites.get(("e", e["id"]), ())
        self.put(level, e["tag"], None if details or sources else "Y")
        for tag, value in details:
            self.put(level + 1, tag, value)
        for image_id in sources:
            self.put(level + 1, "SOUR", f"@S{image_id}@")

    def _pedi(self, child, partners, pedi) -> str | None:
        kinds = {pedi[(child, p)] for p in partners if (child, p) in pedi}
        if not kinds:
            return None  # siblings whose parents aren't known
        kind = kinds.pop() if len(kinds) == 1 else "birth"  # one family holds the child: by birth when they differ
        return PEDI.get(kind, "BIRTH") if self.v7 else kind

    def _associations(self, pid) -> list[tuple[int, str]]:
        """Who is what to pid: (other person, relationship word)."""
        return [(r[0], r[1]) for r in self.con.execute(
            "SELECT person_id, kind FROM relation WHERE of_id = ? ORDER BY kind, person_id", (pid,))]

    def _living(self, r, evts) -> bool:
        if any(e["tag"] in ("DEAT", "BURI", "CREM") for e in evts):
            return False
        born = next((e["sort_date"] for e in evts if e["tag"] == "BIRT" and e["sort_date"]), None)
        return born is None or int(born[:4]) > datetime.now().year - LIVING_YEARS

    def _covers(self) -> dict[int, dict]:
        """Each person's portrait: the preview of the photo with their clearest hand-named face, cropped around it."""
        out = {}
        for r in self.con.execute("""
                SELECT f.person_id, f.x1, f.y1, f.x2, f.y2, i.sha256, i.taken_at, p.name FROM face f
                JOIN image i ON i.id = f.image_id JOIN person p ON p.id = f.person_id
                WHERE f.id = (SELECT c.id FROM face c WHERE c.person_id = f.person_id
                              ORDER BY c.assigned_by = 'user' DESC, c.quality DESC LIMIT 1)"""):
            path = self.cfg.preview_path(r["sha256"])
            try:
                with Image.open(path) as im:
                    w, h = im.size
            except OSError:
                continue
            cx, cy = (r["x1"] + r["x2"]) / 2 * w, (r["y1"] + r["y2"]) / 2 * h
            half = max((r["x2"] - r["x1"]) * w, (r["y2"] - r["y1"]) * h) * 0.9  # the face and some of the head
            left, top = max(0, int(cx - half)), max(0, int(cy - half))
            right, bottom = min(w, int(cx + half)), min(h, int(cy + half))
            zip_path = f"media/{r['sha256']}.jpg"
            self.media[zip_path] = path
            year = f", {r['taken_at'][:4]}" if r["taken_at"] else ""
            out[r["person_id"]] = {"path": zip_path, "title": f"{r['name']}{year}",
                                   "crop": {"TOP": top, "LEFT": left, "HEIGHT": bottom - top, "WIDTH": right - left}}
        return out


def _name(given: str | None, surname: str | None) -> str:
    return f"{given or ''} /{surname}/".strip() if surname else (given or "")


def _is_pointer(text: str) -> bool:
    return len(text) > 2 and text.startswith("@") and text.endswith("@") and " " not in text


# ---- reading -------------------------------------------------------------------------------------------------------

@dataclass
class Node:
    """One GEDCOM structure: '1 BIRT' with its substructures ('2 DATE 1984')."""
    level: int
    tag: str
    payload: str = ""
    xref: str | None = None
    children: list["Node"] = field(default_factory=list)

    def first(self, tag: str) -> "Node | None":
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag: str) -> list["Node"]:
        return [c for c in self.children if c.tag == tag]

    def value(self, tag: str, default: str = "") -> str:
        node = self.first(tag)
        return node.payload if node is not None else default


_LINE = re.compile(r"^\s*(\d+)\s+(?:(@[^@\s]+@)\s+)?(\S+)(?: (.*))?$")


def parse(data: bytes | str) -> list[Node]:
    """A GEDCOM file's records (level 0), 7.0 or 5.5.1, with CONT and CONC lines joined into their payloads."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:  # an old 5.5 file in ANSI
            data = data.decode("cp1252", errors="replace")
    records: list[Node] = []
    stack: list[Node] = []
    v55 = "\n2 VERS 5." in data[:2000]
    for raw in data.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        level, xref, tag, payload = int(m[1]), m[2], m[3].upper(), m[4] or ""
        payload = payload.replace("@@", "@") if v55 else (payload[1:] if payload.startswith("@@") else payload)
        if tag in ("CONT", "CONC"):
            if 0 < level <= len(stack):
                stack[level - 1].payload += ("\n" if tag == "CONT" else "") + payload
            continue
        node = Node(level, tag, payload, xref)
        if level == 0:
            records.append(node)
            stack = [node]
        elif level <= len(stack):
            stack = stack[:level]
            stack[-1].children.append(node)
            stack.append(node)
    return records
