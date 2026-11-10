"""GEDCOM: the family tree as genealogy programs read and write it (gedcom.io).

Export writes GEDCOM 7.0, the current version, by default, or 5.5.1 for programs that don't read 7.0 yet: a .ged file,
or a GEDZIP (.gdz: the file plus media) with each person's portrait, cropped to their face.

How LineageBox's family maps onto GEDCOM:
- each person is an INDI, with their names (main and other), sex, events, notes and UID; their life story is a NOTE
  too, the first, marked in 7.0 with the extension tag _BIO (declared in HEAD.SCHMA) so that it comes back as their
  story, while other programs show it as a note;
- each couple, and each set of parents with children, is a FAM: partners as HUSB and WIFE (no more than a word in 7.0:
  a couple of one sex is fine), MARR when they married, DIV when that marriage is over, children as CHIL with how
  they belong (FAMC.PEDI: BIRTH, ADOPTED, FOSTER). A child's birth parents and adoptive parents are two FAMs, each
  with its PEDI; siblings whose parents aren't known share a FAM with no partners;
- attributes (OCCU, EDUC, RESI, RELI...) carry their value as the payload ("1 OCCU Teacher"), as 7.0 requires, and an
  EVEN its description; an event's place has its coordinates (PLAC.MAP) when they were set by hand, confirmed on the
  map, or came from a family tree;
- documents (photos of records) and written sources (books, websites, records: documents.py) are SOUR records, cited
  by the people and events they bear out, a written source's citation with where in it (PAGE). Each has a UID, so an
  export brought back in finds them again;
- files another tree linked to someone or to a source, not in the library, are OBJE records with the file as it was
  written, so the next program gets them back;
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

from lineagebox import __version__, events, family
from lineagebox.config import Config

VERSIONS = ("7.0", "5.5.1")
PET_URI = "urn:uuid:6a1d3c55-9f43-4f0e-8c2b-6f4d2b0a7e11"  # documents LineageBox's _PET extension tag
GROUP_URI = "urn:uuid:2f6e9b0a-41c7-4d38-9a5e-8c1f0d7b3e24"  # and _GROUP: a group someone is in (Teachers)
BIO_URI = "urn:uuid:9c4e2b17-6d3a-4f85-b0e1-7a5d3c8f2e46"  # and _BIO: the NOTE that is someone's life story
UID_SPACE = uuid.UUID("0b8f3c1e-5a52-4b8e-9d0f-2a7c6e4d1b93")  # stable UIDs of families with no partner record
ROLES = {"friend": "FRIEND", "best friend": "FRIEND", "family friend": "FRIEND", "neighbor": "NGHBR",
         "neighbour": "NGHBR", "godparent": "GODP", "godfather": "GODP", "godmother": "GODP"}
NAME_TYPES = {"birth": "BIRTH", "married": "MARRIED", "aka": "AKA"}
PEDI = {"birth": "BIRTH", "adopted": "ADOPTED", "foster": "FOSTER"}
SEX = {"male": "M", "female": "F"}
MEDIA_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "jpe": "image/jpeg", "jfif": "image/jpeg", "png": "image/png",
               "gif": "image/gif", "tif": "image/tiff", "tiff": "image/tiff", "bmp": "image/bmp", "pdf": "application/pdf", "heic": "image/heic",
               "mp4": "video/mp4", "mov": "video/quicktime", "mp3": "audio/mpeg", "wav": "audio/wav",
               "txt": "text/plain", "htm": "text/html", "html": "text/html"}
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
        # cut into CONC lines of up to 200 characters, never beside a space (a space ending a line is lost: other
        # programs strip it, and so does the rstrip below), and each @ doubled within its own line (cut between the
        # two of a doubled @, it would come back as two)
        chunks, start = [], 0
        while start < len(text) or not chunks:
            end = cut = min(start + 200, len(text))
            while start < cut < len(text) and " " in (text[cut - 1], text[cut]):
                cut -= 1
            end = cut if cut > start else end  # all spaces: cut anyway
            chunks.append(text[start:end])
            start = end
        if not _is_pointer(text):
            chunks = [chunk.replace("@", "@@") for chunk in chunks]
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
        self.text_cites = defaultdict(list)  # the same, of written sources: [(source id, where in it)]
        for sid, pid, eid, page in con.execute("""SELECT source_id, person_id, event_id, page FROM source_citation
                                                  ORDER BY id"""):
            self.text_cites[("p", pid) if pid else ("e", eid)].append((sid, page))
        sources = con.execute("SELECT * FROM source ORDER BY id").fetchall()
        self.coords = {r["name"]: (r["lat"], r["lon"]) for r in con.execute(
            """SELECT name, lat, lon FROM place WHERE lat IS NOT NULL AND lon IS NOT NULL
               AND (source IN ('user', 'gedcom') OR confirmed_at IS NOT NULL)""")}
        self.tree_media = defaultdict(list)  # ('p', person) or ('s', source) -> [tree_media row]
        for m in con.execute("SELECT * FROM tree_media ORDER BY id"):
            self.tree_media[("p", m["person_id"]) if m["person_id"] else ("s", m["source_id"])].append(m)
        partners = {(r["a"], r["b"]): r for r in con.execute("SELECT * FROM partner")}
        pedi = {(r["child_id"], r["parent_id"]): r["pedi"] for r in con.execute("SELECT * FROM parent")}
        living = {p for p, r in people.items() if self._living(r, evts.get(p, ()))}

        # families: each couple, and each set of (effective) parents of one kind with children: a child adopted by
        # a couple is a CHIL of their FAM, adopted (PEDI), and a child of the birth parents in theirs
        fams: dict[frozenset, dict] = {}
        for (a, b), r in partners.items():
            fams[frozenset((a, b))] = {"partners": (a, b), "children": [], "couple": r, "how": {}}
        for child in sorted(people):
            by_kind = defaultdict(set)
            for parent in g.effective_parents.get(child, ()):
                by_kind[pedi.get((child, parent), "birth")].add(parent)
            for kind in family.PEDI:
                if ps := frozenset(by_kind.get(kind, ())):
                    fam = fams.setdefault(ps, {"partners": tuple(sorted(p for p in ps if p > 0)), "children": [],
                                               "couple": None, "how": {}})
                    fam["children"].append(child)
                    fam["how"][child] = kind if any(p > 0 for p in ps) else None  # stand-ins: parents unknown
        fam_ids = {key: f"@F{n}@" for n, key in enumerate(sorted(fams, key=lambda k: sorted(k)), 1)}
        famc, fams_of = defaultdict(list), defaultdict(list)
        for key, fam in fams.items():
            for p in fam["partners"]:
                fams_of[p].append(fam_ids[key])
            for c in fam["children"]:
                how = fam["how"].get(c)
                famc[c].append((fam_ids[key], None if how is None else PEDI[how] if self.v7 else how))
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
            self.put(1, "UID" if self.v7 else "_UID", document_uid(d["sha256"]))
        for src in sources:
            self.put(0, f"@T{src['id']}@", "SOUR")
            self.put(1, "TITL", src["title"])
            for tag, column in (("AUTH", "author"), ("PUBL", "publication"), ("NOTE", "note")):
                if src[column]:
                    self.put(1, tag, src[column])
            for m in self.tree_media.get(("s", src["id"]), ()):
                self.put(1, "OBJE", f"@X{m['id']}@")
            if src["uid"]:
                self.put(1, "UID" if self.v7 else "_UID", src["uid"])
        for m in (m for ms in self.tree_media.values() for m in ms):
            if m["person_id"] in living and self.private_living:
                continue
            self.put(0, f"@X{m['id']}@", "OBJE")
            # 7.0 writes a file as a URL: a Windows path ("C:/Users/...") as file:///C:/Users/...
            windows = self.v7 and re.match(r"^[A-Za-z]:/", m["file"])
            self.put(1, "FILE", "file:///" + m["file"] if windows else m["file"])
            self.put(2, "FORM", self._form(m["form"], m["file"]))
            if m["title"]:
                self.put(2, "TITL", m["title"])
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
            self.put(2, "TAG", f"_BIO {BIO_URI}")
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
            for m in self.tree_media.get(("p", pid), ()):
                self.put(1, "OBJE", f"@X{m['id']}@")
            if r["biography"]:
                self.put(1, "NOTE", r["biography"])
                if self.v7:
                    self.put(2, "_BIO", "Y")
            if r["notes"]:
                self.put(1, "NOTE", r["notes"])
            for group in self.groups.get(pid, ()):
                self.put(1, "_GROUP", group)
            self._sources(1, ("p", pid))
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
        attribute = e["tag"] in events.ATTRIBUTES
        details = [(t, e[k]) for t, k in (("TYPE", "type"), ("DATE", "date"), ("PLAC", "place"), ("NOTE", "note"))
                   if e[k] and not (attribute and t == "TYPE")]
        key = ("e", e["id"])
        cited = self.cites.get(key) or self.text_cites.get(key)
        if attribute:  # its value is the payload, which 7.0 requires: the place lived in, if nothing else
            payload = e["type"] or e["place"] or events.PERSON_EVENTS.get(e["tag"], e["tag"])
        elif e["tag"] == "EVEN" and self.v7:
            payload = e["type"] or "Event"
        else:
            payload = None if details or cited else "Y"
        self.put(level, e["tag"], payload)
        for tag, value in details:
            self.put(level + 1, tag, value)
            if tag == "PLAC" and (at := self.coords.get(value.strip())):
                self.put(level + 2, "MAP")
                self.put(level + 3, "LATI", f"{'N' if at[0] >= 0 else 'S'}{abs(at[0]):.6f}")
                self.put(level + 3, "LONG", f"{'E' if at[1] >= 0 else 'W'}{abs(at[1]):.6f}")
        self._sources(level + 1, key)

    def _sources(self, level: int, key) -> None:
        """The citations of a person or an event: documents, then written sources with where in them."""
        for image_id in self.cites.get(key, ()):
            self.put(level, "SOUR", f"@S{image_id}@")
        for sid, page in self.text_cites.get(key, ()):
            self.put(level, "SOUR", f"@T{sid}@")
            if page:
                self.put(level + 1, "PAGE", page)

    def _form(self, form: str | None, file: str) -> str:
        """A file's format as each version writes it: a media type in 7.0 (image/jpeg), a word in 5.5.1 (jpg)."""
        form = (form or "").strip().lower()
        ending = file.rsplit(".", 1)[-1].lower() if "." in file.rsplit("/", 1)[-1] else ""
        if self.v7:
            if "/" in form:
                return form
            return MEDIA_TYPES.get(form) or MEDIA_TYPES.get(ending) or "application/octet-stream"
        if form and "/" not in form:
            return form
        back = {v: k for k, v in reversed(MEDIA_TYPES.items())}
        return back.get(form) or ending or "jpg"

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
        """Each person's portrait: the preview of the photo with the face chosen for them, else their clearest
        hand-named face, cropped around it."""
        out = {}
        for r in self.con.execute("""
                SELECT f.person_id, f.x1, f.y1, f.x2, f.y2, i.sha256, i.taken_at, p.name FROM face f
                JOIN image i ON i.id = f.image_id JOIN person p ON p.id = f.person_id
                WHERE f.id = (SELECT c.id FROM face c WHERE c.person_id = f.person_id
                              ORDER BY c.id IS (SELECT o.cover_face_id FROM person o WHERE o.id = c.person_id) DESC,
                                       c.assigned_by = 'user' DESC, c.quality DESC LIMIT 1)"""):
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


def document_uid(sha256: str) -> str:
    """A document's UID in exports: the same for the same photo, so a tree brought back in finds it."""
    return str(uuid.uuid5(UID_SPACE, f"document:{sha256}"))


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
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UNICODE: UTF-16, as some programs save 5.5.1
            data = data.decode("utf-16", errors="replace")
        else:
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
