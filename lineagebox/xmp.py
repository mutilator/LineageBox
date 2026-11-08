"""XMP, the metadata other photo programs read and write: writing what the family gave a photo as an XMP sidecar (see
sidecars.py), and reading the names, keywords, captions, dates and places other apps left in a photo's XMP or IPTC
(see imports.py).

What is written, and where other programs look for it:
- names on faces: MWG regions (mwg-rs:Regions, the Metadata Working Group's, which digiKam, Lightroom, Picasa 3.9 and
  ExifTool read), each a box in fractions of the picture as it is shown (turned upright), and the names of everyone in
  the photo as IPTC's PersonInImage and as keywords;
- the family's caption as dc:description, which nearly everything shows as the photo's caption or description;
- tags given by hand as keywords (dc:subject);
- a date set by hand as exif:DateTimeOriginal (its middle, as the catalog keeps it) and photoshop:DateCreated (as
  exactly as it is known: "1987", "1987-07" or the day), and the words it was given ("summer 1987") in LineageBox's
  own namespace;
- a place put on the map by hand as exif:GPSLatitude and exif:GPSLongitude.

Every packet LineageBox writes says so (lineagebox:Written), so that reading other apps' metadata never takes its own
back in. Reading is lenient: other programs write XMP in several valid shapes (values as attributes or elements,
structures as rdf:parseType="Resource" or as an rdf:Description), and one that can't be read is skipped, never an
error.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
OURS = "https://github.com/mutilator/LineageBox/ns/1.0/"
DC = "http://purl.org/dc/elements/1.1/"
XMP = "http://ns.adobe.com/xap/1.0/"
EXIF = "http://ns.adobe.com/exif/1.0/"
PHOTOSHOP = "http://ns.adobe.com/photoshop/1.0/"
IPTC_EXT = "http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
MWG_RS = "http://www.metadataworkinggroup.com/schemas/regions/"
ST_AREA = "http://ns.adobe.com/xmp/sType/Area#"
ST_DIM = "http://ns.adobe.com/xap/1.0/sType/Dimensions#"
MP = "http://ns.microsoft.com/photo/1.2/"  # Windows Live Photo Gallery's people tags
MP_RI = "http://ns.microsoft.com/photo/1.2/t/RegionInfo#"
MP_REG = "http://ns.microsoft.com/photo/1.2/t/Region#"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
HEAD_BYTES = 1 << 20  # of a file Pillow can't open, the part looked through for an XMP packet
MAX_PACKET = 4 << 20  # bigger than any packet of names and keywords: something else, not read
# What cameras put in a photo's description by themselves, which some programs copy into XMP: not a caption
CAMERA_WORDS = re.compile(r"^(?:(?:olympus|sony|kodak|samsung|nikon|canon|pentax|fujifilm|panasonic|minolta|"
                          r"konica|casio|ricoh|sanyo|hp|lg|huawei)\b.*|digital camera|dcim.*|default|untitled|"
                          r"image|photo|picture|description|no description|ok|exif_jpeg_\w*|<.*>|[\W\d_]*)$",
                          re.IGNORECASE)


@dataclass
class Metadata:
    """What the family gave a photo, as written into its sidecar."""
    width: int | None = None                 # the picture as shown, which the boxes are fractions of
    height: int | None = None
    faces: list[tuple[str, float, float, float, float]] = field(default_factory=list)  # name, x1, y1, x2, y2
    people: list[str] = field(default_factory=list)  # everyone in it: named on a face, or added by hand
    caption: str | None = None
    tags: list[str] = field(default_factory=list)
    taken_at: str | None = None              # a date set by hand: the middle of its span, 1987-07-16T00:00:00
    date_created: str | None = None          # as exactly as it is known: 1987, 1987-07 or 1987-07-16
    date_text: str | None = None             # as it was said: summer 1987
    gps: tuple[float, float] | None = None   # placed on the map by hand

    def empty(self) -> bool:
        return not (self.faces or self.people or self.caption or self.tags or self.taken_at or self.gps)


@dataclass
class Found:
    """What another app left in a photo's metadata."""
    ours: bool = False                       # LineageBox wrote it: nothing to take in
    regions: list[tuple[str, float, float, float, float]] = field(default_factory=list)  # name, x1, y1, x2, y2
    people: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    caption: str | None = None
    taken_at: str | None = None
    gps: tuple[float, float] | None = None

    def empty(self) -> bool:
        return not (self.regions or self.people or self.keywords or self.caption or self.taken_at or self.gps)

    def add(self, other: "Found") -> None:
        """Another source's findings: names and keywords from both, the first one's caption, date and place."""
        if other.ours:
            return
        seen = {r[0].casefold() for r in self.regions}
        self.regions += [r for r in other.regions if r[0].casefold() not in seen]
        seen = {q.casefold() for q in self.people}  # once, not for each name: a packet may hold thousands
        self.people += [p for p in other.people if p.casefold() not in seen]
        seen = {q.casefold() for q in self.keywords}
        self.keywords += [k for k in other.keywords if k.casefold() not in seen]
        self.caption = self.caption or other.caption
        self.taken_at = self.taken_at or other.taken_at
        self.gps = self.gps or other.gps


# ---- writing ------------------------------------------------------------------------------------------------------

_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml(text: str) -> str:
    """Text as XML can hold it: a control character (pasted with a name or caption) would make the whole sidecar one
    that strict readers refuse, so it is left out."""
    return escape(_NOT_XML.sub("", text))


def packet(meta: Metadata, written_at: str | None = None) -> bytes:
    """An XMP sidecar holding what the family gave the photo (UTF-8, as the XMP specification asks of sidecars)."""
    attrs = {"lineagebox:Written": written_at or datetime.now().isoformat(timespec="seconds")}
    body = []
    if meta.caption:
        body.append(f'   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{_xml(meta.caption)}</rdf:li>'
                    f'</rdf:Alt></dc:description>')
    keywords = list(dict.fromkeys([*meta.tags, *meta.people]))
    if keywords:
        body.append("   <dc:subject><rdf:Bag>" + "".join(f"<rdf:li>{_xml(k)}</rdf:li>" for k in keywords)
                    + "</rdf:Bag></dc:subject>")
    if meta.people:
        body.append("   <Iptc4xmpExt:PersonInImage><rdf:Bag>"
                    + "".join(f"<rdf:li>{_xml(p)}</rdf:li>" for p in meta.people)
                    + "</rdf:Bag></Iptc4xmpExt:PersonInImage>")
    if meta.faces and meta.width and meta.height:
        regions = []
        for name, x1, y1, x2, y2 in meta.faces:
            w, h = _fraction(x2 - x1), _fraction(y2 - y1)
            regions.append(f'      <rdf:li rdf:parseType="Resource"><mwg-rs:Name>{_xml(name)}</mwg-rs:Name>'
                           f'<mwg-rs:Type>Face</mwg-rs:Type><mwg-rs:Area stArea:x="{_fraction((x1 + x2) / 2):.6f}" '
                           f'stArea:y="{_fraction((y1 + y2) / 2):.6f}" stArea:w="{w:.6f}" stArea:h="{h:.6f}" '
                           f'stArea:unit="normalized"/></rdf:li>')
        body.append('   <mwg-rs:Regions rdf:parseType="Resource">\n'
                    f'    <mwg-rs:AppliedToDimensions stDim:w="{meta.width}" stDim:h="{meta.height}" '
                    'stDim:unit="pixel"/>\n    <mwg-rs:RegionList><rdf:Bag>\n' + "\n".join(regions)
                    + "\n    </rdf:Bag></mwg-rs:RegionList>\n   </mwg-rs:Regions>")
    if meta.taken_at:
        attrs["exif:DateTimeOriginal"] = meta.taken_at[:19]
        attrs["photoshop:DateCreated"] = meta.date_created or meta.taken_at[:19]
        if meta.date_text:
            attrs["lineagebox:DateText"] = _NOT_XML.sub("", meta.date_text)
    if meta.gps:
        attrs["exif:GPSVersionID"] = "2.2.0.0"
        attrs["exif:GPSLatitude"] = _dms(meta.gps[0], "NS")
        attrs["exif:GPSLongitude"] = _dms(meta.gps[1], "EW")
        attrs["exif:GPSMapDatum"] = "WGS-84"
    namespaces = {"lineagebox": OURS, "dc": DC, "exif": EXIF, "photoshop": PHOTOSHOP, "Iptc4xmpExt": IPTC_EXT,
                  "mwg-rs": MWG_RS, "stArea": ST_AREA, "stDim": ST_DIM}
    head = "".join(f"\n    xmlns:{k}={quoteattr(v)}" for k, v in namespaces.items())
    head += "".join(f"\n    {k}={quoteattr(v)}" for k, v in attrs.items())
    text = ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="LineageBox">\n'
            f' <rdf:RDF xmlns:rdf="{RDF}">\n'
            f'  <rdf:Description rdf:about=""{head}>\n'
            + "".join(line + "\n" for line in body)
            + "  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n<?xpacket end=\"w\"?>\n")
    return text.encode("utf-8")


def date_created(date_from: str | None, date_to: str | None, taken_at: str | None) -> str | None:
    """A date set by hand as exactly as it is known, as IPTC's Date Created allows: the year for a span within one
    whole year, the month for one whole month, else the day it is placed at."""
    if not taken_at:
        return None
    if date_from and date_to and date_from[:4] == date_to[:4]:
        if date_from[5:] == "01-01" and date_to[5:] == "12-31":
            return date_from[:4]
        if date_from[5:7] == date_to[5:7] and date_from[8:] == "01" and _last_day(date_to):
            return date_from[:7]
    return taken_at[:10]


def _last_day(day: str) -> bool:
    y, m, d = (int(p) for p in day.split("-"))
    nxt = datetime(y + (m == 12), m % 12 + 1, 1)
    return (nxt - datetime(y, m, d)).days == 1


def _fraction(v: float) -> float:
    return min(1.0, max(0.0, float(v)))


def _dms(value: float, refs: str) -> str:
    """XMP's way of writing a GPS coordinate: degrees, minutes with decimals, and N/S or E/W ("45,30.123456N")."""
    ref = refs[0] if value >= 0 else refs[1]
    value = abs(value)
    degrees = int(value)
    return f"{degrees},{(value - degrees) * 60:.6f}{ref}"


# ---- reading ------------------------------------------------------------------------------------------------------

def find_packet(data: bytes) -> bytes | None:
    """The XMP packet in a file's bytes: XMP is plain text with a wrapper made to be found this way in any file."""
    start = data.find(b"<x:xmpmeta")
    end = data.find(b"</x:xmpmeta>", start) if start >= 0 else -1
    if start < 0 or end < 0:
        start = data.find(b"<rdf:RDF")
        end = data.find(b"</rdf:RDF>", start) if start >= 0 else -1
        if start < 0 or end < 0:
            return None
        return data[start:end + len(b"</rdf:RDF>")]
    return data[start:end + len(b"</x:xmpmeta>")]


def parse(data: bytes | str | None) -> Found:
    """What an XMP packet (or a whole sidecar) holds of names, keywords, caption, date and place."""
    found = Found()
    if not data:
        return found
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_PACKET:
        return found
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16, as a few old programs wrote it
        data = data.decode("utf-16", "replace").encode("utf-8")
    packet_bytes = find_packet(data)
    if packet_bytes is None or b"<!DOCTYPE" in packet_bytes or b"<!ENTITY" in packet_bytes:
        return found  # XMP never declares entities: refusing them keeps a crafted file from expanding in memory
    try:
        root = ET.fromstring(packet_bytes)
    except ET.ParseError:
        return found
    rdf = root if root.tag == f"{{{RDF}}}RDF" else root.find(f"{{{RDF}}}RDF")
    if rdf is None:
        return found
    for desc in rdf.findall(f"{{{RDF}}}Description"):
        if desc.get(f"{{{OURS}}}Written") is not None or any(
                k.startswith(f"{{{OURS}}}") for k in desc.attrib) or any(
                c.tag.startswith(f"{{{OURS}}}") for c in desc):
            found.ours = True
        _read_description(desc, found)
    names = {r[0].casefold() for r in found.regions} | {p.casefold() for p in found.people}
    found.keywords = [k for k in dict.fromkeys(found.keywords) if k.casefold() not in names]
    return found


def _read_description(desc: ET.Element, found: Found) -> None:
    if (caption := _text(_prop(desc, DC, "description"))) and not CAMERA_WORDS.match(caption.strip()):
        found.caption = found.caption or caption.strip()
    found.keywords += [k.strip() for k in _items(_prop(desc, DC, "subject")) if k.strip()]
    found.people += [p.strip() for p in _items(_prop(desc, IPTC_EXT, "PersonInImage")) if p.strip()]
    for ns, name in ((EXIF, "DateTimeOriginal"), (PHOTOSHOP, "DateCreated"), (XMP, "CreateDate")):
        if not found.taken_at and (when := read_date(_text(_prop(desc, ns, name)))):
            found.taken_at = when
    lat, lon = _coordinate(_text(_prop(desc, EXIF, "GPSLatitude"))), _coordinate(_text(_prop(desc, EXIF,
                                                                                             "GPSLongitude")))
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0, 0) \
            and not found.gps:  # a place on the map: a latitude over 90 is something else (or a damaged packet)
        found.gps = (lat, lon)
    found.regions += _mwg_regions(desc) + _mp_regions(desc)
    found.people = list(dict.fromkeys(found.people))


def _mwg_regions(desc: ET.Element) -> list:
    regions = _prop(desc, MWG_RS, "Regions")
    if not isinstance(regions, ET.Element):
        return []
    info = _struct(regions)
    dims = _prop(info, MWG_RS, "AppliedToDimensions")
    dim_w = dim_h = None
    if isinstance(dims, ET.Element):
        d = _struct(dims)
        dim_w, dim_h = _number(_text(_prop(d, ST_DIM, "w"))), _number(_text(_prop(d, ST_DIM, "h")))
    out = []
    for item in _elements(_prop(info, MWG_RS, "RegionList")):
        r = _struct(item)
        name = " ".join((_text(_prop(r, MWG_RS, "Name")) or "").split())
        kind = (_text(_prop(r, MWG_RS, "Type")) or "Face").strip()
        area = _prop(r, MWG_RS, "Area")
        if not name or kind.lower() != "face" or not isinstance(area, ET.Element):
            continue
        a = _struct(area)
        x, y, w, h = (_number(_text(_prop(a, ST_AREA, k))) for k in ("x", "y", "w", "h"))
        if None in (x, y, w, h):
            continue
        if (_text(_prop(a, ST_AREA, "unit")) or "normalized").strip().lower() == "pixel":
            if not dim_w or not dim_h:
                continue
            x, w, y, h = x / dim_w, w / dim_w, y / dim_h, h / dim_h
        if box := _box(x - w / 2, y - h / 2, x + w / 2, y + h / 2):
            out.append((name, *box))
    return out


def _mp_regions(desc: ET.Element) -> list:
    """Windows Live Photo Gallery's people tags: a top-left corner and a size, in fractions."""
    info = _prop(desc, MP, "RegionInfo")
    if not isinstance(info, ET.Element):
        return []
    out = []
    for item in _elements(_prop(_struct(info), MP_RI, "Regions")):
        r = _struct(item)
        name = " ".join((_text(_prop(r, MP_REG, "PersonDisplayName")) or "").split())
        parts = [_number(p) for p in (_text(_prop(r, MP_REG, "Rectangle")) or "").split(",")]
        if not name or len(parts) != 4 or None in parts:
            continue
        x, y, w, h = parts
        if box := _box(x, y, x + w, y + h):
            out.append((name, *box))
    return out


def _box(x1, y1, x2, y2):
    x1, y1, x2, y2 = (min(1.0, max(0.0, v)) for v in (x1, y1, x2, y2))
    return (x1, y1, x2, y2) if x2 - x1 > 0.002 and y2 - y1 > 0.002 else None


def _prop(node: ET.Element, ns: str, name: str):
    """A property of a description or structure: given as an attribute (a simple value) or as an element."""
    value = node.get(f"{{{ns}}}{name}")
    if value is not None:
        return value
    return node.find(f"{{{ns}}}{name}")


def _struct(el: ET.Element) -> ET.Element:
    """The node a structure's fields are on: the element itself (rdf:parseType="Resource", or fields as its
    attributes), or the rdf:Description inside it."""
    if el.get(f"{{{RDF}}}parseType") == "Resource":
        return el
    inner = el.find(f"{{{RDF}}}Description")
    return inner if inner is not None else el


def _elements(value) -> list[ET.Element]:
    """The items of an array (rdf:Bag, rdf:Seq or rdf:Alt)."""
    if not isinstance(value, ET.Element):
        return []
    for kind in ("Bag", "Seq", "Alt"):
        container = value.find(f"{{{RDF}}}{kind}")
        if container is not None:
            return container.findall(f"{{{RDF}}}li")
    return []


def _items(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [li.text or "" for li in _elements(value) if li.text]


def _text(value) -> str | None:
    """A simple value, or the default language's text of a language alternative."""
    if value is None or isinstance(value, str):
        return value
    items = _elements(value)
    if items:
        default = next((li for li in items if li.get(XML_LANG) == "x-default"), items[0])
        return default.text
    return value.text


def _number(text) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


_DATE = re.compile(r"^\s*(\d{4})(?:[-:](\d{2})(?:[-:](\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?)?)?")


def read_date(text: str | None) -> str | None:
    """A date as XMP or EXIF writes it, as the catalog keeps dates (local time, no zone): 2004-07-15T13:20:00. A year
    alone is put at the middle of it, a month at its middle day, as a date set by hand would be. The time zone, if
    given, is left as written: a camera's local time is what the catalog shows."""
    m = _DATE.match(text or "")
    if not m:
        return None
    year = int(m[1])
    if not 1826 <= year <= datetime.now().year + 1:  # before the first photograph, or a placeholder like 0000
        return None
    month, day = int(m[2] or 0), int(m[3] or 0)
    hour, minute, second = int(m[4] or 0), int(m[5] or 0), int(m[6] or 0)
    if month == 0:
        month, day = 7, 2
    elif day == 0:
        day = 15
    try:
        return datetime(year, month, day, hour, minute, second).isoformat(timespec="seconds")
    except ValueError:
        return None


def _coordinate(text: str | None) -> float | None:
    """45,30.12N, 45,30,7N or 45.5 as a signed decimal coordinate."""
    if not text:
        return None
    text = text.strip()
    ref = text[-1:].upper()
    sign = -1 if ref in ("S", "W") else 1
    if ref in ("N", "S", "E", "W"):
        text = text[:-1]
    try:
        parts = [float(p) for p in text.split(",")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    value = parts[0] + (parts[1] / 60 if len(parts) > 1 else 0) + (parts[2] / 3600 if len(parts) > 2 else 0)
    return sign * value if value <= 180 else None


# ---- what a photo file holds --------------------------------------------------------------------------------------

def embedded(path: Path) -> tuple[Found, int]:
    """The XMP and IPTC in a photo file, and its EXIF orientation (1 when it has none). Only the start of the file is
    read: Pillow reads a file's headers when it opens it, and the picture only when asked."""
    from PIL import Image, IptcImagePlugin

    from lineagebox import media  # noqa: F401  (opens HEIC through pillow-heif)
    found, orientation = Found(), 1
    try:
        with Image.open(path) as im:
            raw = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
            if not raw and hasattr(im, "tag_v2"):
                raw = im.tag_v2.get(700)
            if isinstance(raw, (bytes, str)):
                found = parse(raw)
            try:
                found.add(_iptc(IptcImagePlugin.getiptcinfo(im) or {}))
            except Exception:  # an IPTC block that can't be read: the XMP still counts
                pass
            try:
                orientation = int(im.getexif().get(0x0112) or 1)
            except Exception:
                orientation = 1
    except Exception:  # not a picture Pillow opens (most raw files): look for the packet in the file's first part
        try:
            with open(path, "rb") as f:
                found = parse(f.read(HEAD_BYTES))
        except OSError:
            raise
        except Exception:
            found = Found()
    return found, orientation if 1 <= orientation <= 8 else 1


def _iptc(info: dict) -> Found:
    """IPTC's keywords (2:25), caption (2:120) and date created (2:55, 2:60), as Picasa and older programs wrote
    them."""
    found = Found()
    utf8 = info.get((1, 90)) in (b"\x1b%G", [b"\x1b%G"])

    def text(raw) -> str:
        if utf8:
            return raw.decode("utf-8", "replace")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", "replace")

    def values(key) -> list[str]:
        raw = info.get(key)
        if raw is None:
            return []
        return [text(r).strip() for r in (raw if isinstance(raw, list) else [raw]) if r]

    found.keywords = [k for k in values((2, 25)) if k]
    caption = " ".join(values((2, 120))).strip()
    if caption and not CAMERA_WORDS.match(caption):
        found.caption = caption
    if day := values((2, 55)):
        d = day[0]
        clock = (values((2, 60)) or ["000000"])[0]
        if len(d) == 8 and d.isdigit():
            found.taken_at = read_date(f"{d[:4]}-{d[4:6]}-{d[6:]}T{clock[:2] or '00'}:{clock[2:4] or '00'}:"
                                       f"{clock[4:6] or '00'}")
    return found
