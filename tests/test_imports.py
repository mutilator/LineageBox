"""Bringing in what other apps gave the photos (N5): faces named in XMP (MWG and Windows' regions), Picasa's
.picasa.ini, Google Takeout's JSON, keywords, captions, dates and places, always below what was set here."""
import dataclasses
import json
import struct

import numpy as np
import pytest
from PIL import Image

from lineagebox import comments, db, imports, settings, sidecars, xmp
from lineagebox.faces import STAGE_FACES
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from tests.conftest import rel, write_jpeg

NS = ('xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
      'xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/" '
      'xmlns:stArea="http://ns.adobe.com/xmp/sType/Area#" xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'xmlns:exif="http://ns.adobe.com/exif/1.0/"')


def packet(regions=(), keywords=(), caption=None, extra=""):
    """An XMP packet as digiKam or Lightroom write one: regions as (name, centre x, centre y, w, h)."""
    items = "".join(f'<rdf:li><rdf:Description mwg-rs:Name="{n}" mwg-rs:Type="Face"><mwg-rs:Area stArea:x="{x}" '
                    f'stArea:y="{y}" stArea:w="{w}" stArea:h="{h}" stArea:unit="normalized"/></rdf:Description>'
                    f'</rdf:li>' for n, x, y, w, h in regions)
    body = f"<mwg-rs:Regions rdf:parseType='Resource'><mwg-rs:RegionList><rdf:Bag>{items}</rdf:Bag></mwg-rs:RegionList>" \
           f"</mwg-rs:Regions>" if regions else ""
    if keywords:
        body += "<dc:subject><rdf:Bag>" + "".join(f"<rdf:li>{k}</rdf:li>" for k in keywords) + "</rdf:Bag></dc:subject>"
    if caption:
        body += f"<dc:description><rdf:Alt><rdf:li xml:lang='x-default'>{caption}</rdf:li></rdf:Alt></dc:description>"
    return (f"<x:xmpmeta {NS}><rdf:RDF><rdf:Description rdf:about='' {extra}>{body}</rdf:Description></rdf:RDF>"
            f"</x:xmpmeta>").encode()


def photo(path, size=(400, 300), meta=None, orientation=None, taken="2004:07:15 13:20:00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    if orientation:
        exif[0x0112] = orientation
    Image.new("RGB", size, (120, 140, 160)).save(path, "JPEG", exif=exif, **({"xmp": meta} if meta else {}))
    return path


def faces(con, image_id, *boxes):
    """Faces found here (as faces.py would), and the photo marked as looked at for faces."""
    ids = [con.execute("""INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding)
                          VALUES (?, ?, ?, ?, ?, 0.9, 25, ?)""",
                       (image_id, *b, np.zeros(512, np.float32).tobytes())).lastrowid for b in boxes]
    con.execute(f"UPDATE image SET stage = stage | {STAGE_FACES} WHERE id = ?", (image_id,))
    con.commit()
    return ids


def image_of(con, *parts):
    return con.execute("SELECT image_id FROM file WHERE path = ?", (rel(*parts),)).fetchone()[0]


def read(con, cfg):
    scan(con, cfg)
    ingest(con, cfg)


def test_names_keywords_and_people_from_a_photos_own_xmp(con, cfg, library):
    dora = con.execute("INSERT INTO person(name) VALUES ('Dora Miller')").lastrowid
    con.commit()
    photo(library / "Lake" / "a.jpg", meta=packet(
        [("Ann Miller", 0.25, 0.4, 0.2, 0.3), ("Carl Baker", 0.8, 0.4, 0.1, 0.2)],
        ["Beach", "Ann Miller", "Dora Miller", "Summer-Holiday"], "OLYMPUS DIGITAL CAMERA"))
    read(con, cfg)
    image = image_of(con, "Lake", "a.jpg")
    ann_face, other = faces(con, image, (0.16, 0.26, 0.34, 0.54), (0.5, 0.5, 0.6, 0.6))
    stats = imports.run(con, cfg)
    assert stats.read >= 1 and stats.found == 1 and stats.named == 3
    ann = con.execute("SELECT id FROM person WHERE name = 'Ann Miller'").fetchone()[0]  # someone new for the name
    assert tuple(con.execute("SELECT person_id, assigned_by FROM face WHERE id = ?", (ann_face,)).fetchone()) == (ann,
                                                                                                         "user")
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (other,)).fetchone()[0] is None
    carl = con.execute("SELECT id FROM person WHERE name = 'Carl Baker'").fetchone()[0]
    added = {r[0] for r in con.execute("SELECT person_id FROM image_person WHERE image_id = ?", (image,))}
    assert added == {carl, dora}  # no face of Carl's was found; Dora's name was a keyword
    tags = {r[0]: r[1] for r in con.execute("""SELECT t.name, it.source FROM image_tag it JOIN tag t ON t.id = it.tag_id
                                               WHERE it.image_id = ?""", (image,))}
    assert tags == {"beach": "user", "summer holiday": "user"}  # names are people, not tags
    assert comments.caption(con, image) is None  # the camera's own words are no caption
    summary = imports.summary(con)
    assert (summary["faces"], summary["photos"], summary["tags"], summary["people"]) == (1, 2, 2, 2)
    assert {p["name"] for p in summary["added"]} == {"Ann Miller", "Carl Baker"}
    assert imports.run(con, cfg).read == 0  # each photo is read once


def test_names_wait_for_the_faces_to_be_found(con, cfg, library):
    photo(library / "Lake" / "a.jpg", meta=packet([("Ann Miller", 0.25, 0.4, 0.2, 0.3)]))
    read(con, cfg)
    image = image_of(con, "Lake", "a.jpg")
    imports.run(con, cfg)
    assert con.execute("SELECT state FROM imported WHERE kind = 'name'").fetchone()[0] == "waiting"
    assert not con.execute("SELECT 1 FROM image_person").fetchone()
    face, = faces(con, image, (0.15, 0.25, 0.35, 0.55))
    imports.run(con, cfg)
    assert tuple(con.execute("SELECT state, face_id FROM imported WHERE kind = 'name'").fetchone()) == ("face", face)


def test_what_was_set_here_wins(con, cfg, library):
    bob = con.execute("INSERT INTO person(name) VALUES ('Bob Baker')").lastrowid
    ann = con.execute("INSERT INTO person(name) VALUES ('Ann Miller')").lastrowid
    con.commit()
    photo(library / "Lake" / "a.jpg", meta=packet([("Ann Miller", 0.25, 0.4, 0.2, 0.3), ("Carl Baker", 0.75, 0.4, 0.2, 0.3)],
                                                  ["beach"], "Our trip"))
    read(con, cfg)
    image = image_of(con, "Lake", "a.jpg")
    mine, not_carl = faces(con, image, (0.15, 0.25, 0.35, 0.55), (0.65, 0.25, 0.85, 0.55))
    con.execute("UPDATE face SET person_id = ?, assigned_by = 'user' WHERE id = ?", (bob, mine))
    carl = con.execute("INSERT INTO person(name) VALUES ('Carl Baker')").lastrowid
    con.execute("INSERT INTO face_not_person(face_id, person_id) VALUES (?, ?)", (not_carl, carl))
    tag = con.execute("INSERT INTO tag(name) VALUES ('beach')").lastrowid
    con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, ?, 'not')", (image, tag))
    comments.set_caption(con, image, "Written here", None)
    con.commit()
    imports.run(con, cfg)
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (mine,)).fetchone()[0] == bob
    assert con.execute("SELECT person_id FROM face WHERE id = ?", (not_carl,)).fetchone()[0] is None
    assert {r[0] for r in con.execute("SELECT state FROM imported WHERE kind = 'name'")} == {"kept"}
    assert con.execute("SELECT source FROM image_tag WHERE image_id = ?", (image,)).fetchone()[0] == "not"
    assert comments.caption(con, image) == "Written here"
    assert ann not in {r[0] for r in con.execute("SELECT person_id FROM image_person")}


def test_a_name_two_people_have_is_left_to_pick(con, cfg, library):
    for _ in range(2):
        con.execute("INSERT INTO person(name, uid) VALUES ('Ann Miller', hex(randomblob(8)))")
    con.commit()
    photo(library / "Lake" / "a.jpg", meta=packet([("Ann Miller", 0.25, 0.4, 0.2, 0.3)]))
    read(con, cfg)
    faces(con, image_of(con, "Lake", "a.jpg"), (0.15, 0.25, 0.35, 0.55))
    imports.run(con, cfg)
    assert con.execute("SELECT state FROM imported WHERE kind = 'name'").fetchone()[0] == "unsure"
    assert imports.summary(con)["unsure"] == 1


def test_a_box_drawn_before_the_photo_is_turned_still_finds_its_face(con, cfg, library):
    # stored 400 wide and 300 high, shown turned a quarter clockwise (orientation 6): 300 wide, 400 high. The other
    # app drew the box on the picture as stored, near its top-left corner, which is the top right as shown.
    photo(library / "Lake" / "turned.jpg", meta=packet([("Ann Miller", 0.15, 0.2, 0.2, 0.2)]), orientation=6)
    read(con, cfg)
    image = image_of(con, "Lake", "turned.jpg")
    assert tuple(con.execute("SELECT width, height FROM image WHERE id = ?", (image,)).fetchone()) == (300, 400)
    face, decoy = faces(con, image, (0.70, 0.05, 0.90, 0.25), (0.05, 0.75, 0.25, 0.95))
    imports.run(con, cfg)
    assert con.execute("SELECT face_id FROM imported WHERE kind = 'name'").fetchone()[0] == face


def test_picasa_faces_and_keywords(con, cfg, library):
    photo(library / "Picasa" / "IMG_1.JPG")
    # rect64: left, top, right, bottom as 16-bit fractions: 0.1, 0.2, 0.3, 0.5 of the picture
    rect, carl = ("".join(f"{round(v * 65535):04x}" for v in box) for box in ((0.1, 0.2, 0.3, 0.5), (0.6, 0.2, 0.8, 0.5)))
    (library / "Picasa" / ".picasa.ini").write_text(
        f"[IMG_1.JPG]\nfaces=rect64({rect}),abc123;rect64({rect}),ffffffffffffffff;rect64({carl}),99\n"
        f"keywords=Birthday,Cake\n[Contacts2]\nabc123=Ann Miller;;\n", encoding="utf-8")
    (library / "contacts.xml").write_text('<contacts><contact id="99" name="Carl Baker"/></contacts>')
    read(con, cfg)
    image = image_of(con, "Picasa", "IMG_1.JPG")
    face, = faces(con, image, (0.1, 0.2, 0.3, 0.5))
    imports.run(con, cfg)
    assert con.execute("SELECT p.name FROM face f JOIN person p ON p.id = f.person_id WHERE f.id = ?",
                       (face,)).fetchone()[0] == "Ann Miller"
    assert {r[0] for r in con.execute("SELECT source FROM imported")} == {"picasa"}
    assert {r[0] for r in con.execute("""SELECT t.name FROM image_tag it JOIN tag t ON t.id = it.tag_id
                                          WHERE it.image_id = ?""", (image,))} == {"birthday", "cake"}
    names = {r[0] for r in con.execute("SELECT value FROM imported WHERE kind = 'name'")}
    assert names == {"Ann Miller", "Carl Baker"}  # Carl through contacts.xml, a folder above


def test_google_takeout(con, cfg, library):
    photo(library / "Takeout" / "IMG_20.jpg", taken=None)
    (library / "Takeout" / "IMG_20.jpg.supplemental-metadata.json").write_text(json.dumps({
        "title": "IMG_20.jpg", "description": "First day of school",
        "photoTakenTime": {"timestamp": "1104580800", "formatted": "1 Jan 2005"},
        "geoData": {"latitude": 39.8, "longitude": -89.64, "altitude": 0},
        "people": [{"name": "Ann Miller"}]}))
    read(con, cfg)
    image = image_of(con, "Takeout", "IMG_20.jpg")
    assert con.execute("SELECT taken_at_source FROM image WHERE id = ?", (image,)).fetchone()[0] == "mtime"
    con.execute(f"UPDATE image SET stage = stage | {STAGE_FACES} WHERE id = ?", (image,))
    con.commit()
    imports.run(con, cfg)
    row = con.execute("SELECT taken_at, taken_at_source, gps_lat, gps_lon FROM image WHERE id = ?", (image,)).fetchone()
    assert row["taken_at"][:4] == "2005" or row["taken_at"][:10] == "2004-12-31"  # the install's own time zone
    assert row["taken_at_source"] == "import" and (row["gps_lat"], row["gps_lon"]) == (39.8, -89.64)
    assert comments.caption(con, image) == "First day of school"
    assert con.execute("""SELECT p.name FROM image_person l JOIN person p ON p.id = l.person_id
                          WHERE l.image_id = ?""", (image,)).fetchone()[0] == "Ann Miller"


def test_takeouts_names_for_its_files():
    long = "PXL_20230105_123456789.MP.jpg"  # its JSON's whole name is cut to 51 characters
    names = {n.lower(): n for n in ("IMG_1.jpg.json", "IMG_2.jpg(1).json", "IMG_3.JPG.supplemental-metadata.json",
                                    f"{long}.supplemental-metadata"[:46] + ".json", "IMG_4.jpg.json")}
    assert imports.takeout_json("IMG_1.jpg", names) == "IMG_1.jpg.json"
    assert imports.takeout_json("IMG_2(1).jpg", names) == "IMG_2.jpg(1).json"
    assert imports.takeout_json("img_3.JPG", names) == "IMG_3.JPG.supplemental-metadata.json"
    assert imports.takeout_json(long, names) == f"{long}.supplemental-metadata"[:46] + ".json"
    assert imports.takeout_json("IMG_4-edited.jpg", names) == "IMG_4.jpg.json"
    assert imports.takeout_json("IMG_5.jpg", names) is None


def test_iptc_keywords_and_caption(con, cfg, library):
    path = photo(library / "Old" / "scan.jpg", taken=None)
    iim = b"".join(b"\x1c\x02" + bytes([k]) + struct.pack(">H", len(v)) + v
                   for k, v in ((25, b"Wedding"), (25, b"Church"), (120, "Grandpa’s wedding".encode()),
                                (55, b"19560602")))
    block = b"8BIM\x04\x04\x00\x00" + struct.pack(">I", len(iim)) + iim + (b"\x00" if len(iim) % 2 else b"")
    app13 = b"Photoshop 3.0\x00" + block
    data = path.read_bytes()
    path.write_bytes(data[:2] + b"\xff\xed" + struct.pack(">H", len(app13) + 2) + app13 + data[2:])
    read(con, cfg)
    image = image_of(con, "Old", "scan.jpg")
    imports.run(con, cfg)
    assert comments.caption(con, image) == "Grandpa’s wedding"
    assert tuple(con.execute("SELECT taken_at, taken_at_source FROM image WHERE id = ?", (image,)).fetchone()) == (
        "1956-06-02T00:00:00", "import")
    assert {r[0] for r in con.execute("SELECT t.name FROM image_tag it JOIN tag t ON t.id = it.tag_id")} == \
        {"wedding", "church"}


def test_a_sidecar_lineagebox_wrote_is_not_taken_back_in(con, cfg, library):
    path = photo(library / "Lake" / "a.jpg")
    meta = xmp.Metadata(width=400, height=300, faces=[("Ann Miller", 0.1, 0.2, 0.3, 0.5)], people=["Ann Miller"],
                        caption="Ours", tags=["ours"])
    sidecars.path_for(path).write_bytes(xmp.packet(meta))
    (library / "Lake" / "a.xmp").write_bytes(packet(keywords=["theirs"]))  # Lightroom's name for a sidecar
    read(con, cfg)
    imports.run(con, cfg)
    assert [r[0] for r in con.execute("SELECT value FROM imported")] == ["theirs"]


def test_off_reads_nothing(con, cfg, library):
    photo(library / "Lake" / "a.jpg", meta=packet(keywords=["beach"]))
    read(con, cfg)
    assert imports.run(con, dataclasses.replace(cfg, import_enabled=False)).read == 0
    assert not con.execute("SELECT 1 FROM metadata_read").fetchone()


def test_a_catalog_with_photos_already_keeps_it_off_until_asked(cfg, tmp_path):
    old = dataclasses.replace(cfg, data_dir=tmp_path / "older")
    c = db.connect(old, migrate_schema=False)
    db.migrate(c, old, upto=28)
    c.execute("INSERT INTO image(sha256, format) VALUES ('x', 'jpeg')")
    c.commit()
    db.migrate(c, old)
    assert settings.current(c, old).import_enabled is False
    c.close()
    assert settings.current(db.connect(dataclasses.replace(cfg, data_dir=tmp_path / "new")), cfg).import_enabled


def test_xmp_in_its_other_shapes():
    windows = (b"<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
               b"<rdf:Description xmlns:MP='http://ns.microsoft.com/photo/1.2/' "
               b"xmlns:MPRI='http://ns.microsoft.com/photo/1.2/t/RegionInfo#' "
               b"xmlns:MPReg='http://ns.microsoft.com/photo/1.2/t/Region#' "
               b"xmlns:exif='http://ns.adobe.com/exif/1.0/' exif:GPSLatitude='45,30.5N' exif:GPSLongitude='122,30,30W'>"
               b"<MP:RegionInfo rdf:parseType='Resource'><MPRI:Regions><rdf:Bag><rdf:li rdf:parseType='Resource'>"
               b"<MPReg:Rectangle>0.1, 0.2, 0.3, 0.4</MPReg:Rectangle><MPReg:PersonDisplayName>Ann Miller"
               b"</MPReg:PersonDisplayName></rdf:li></rdf:Bag></MPRI:Regions></MP:RegionInfo>"
               b"</rdf:Description></rdf:RDF></x:xmpmeta>")
    found = xmp.parse(b"JPEG bytes before it" + windows + b"and after")
    assert found.regions == [("Ann Miller", 0.1, 0.2, pytest.approx(0.4), pytest.approx(0.6))]
    assert found.gps == (pytest.approx(45.508333, abs=1e-5), pytest.approx(-122.508333, abs=1e-5))
    assert xmp.read_date("1987") == "1987-07-02T00:00:00" and xmp.read_date("1987-07") == "1987-07-15T00:00:00"
    assert xmp.read_date("2004:07:15 13:20:05+02:00") == "2004-07-15T13:20:05"
    assert xmp.read_date("0000:00:00 00:00:00") is None
    assert xmp.parse(b"<!DOCTYPE x [<!ENTITY a 'b'>]><x:xmpmeta xmlns:x='adobe:ns:meta/'>&a;</x:xmpmeta>").empty()
    assert xmp.parse(b"<x:xmpmeta broken").empty()
    off_the_map = windows.replace(b"45,30.5N", b"95,30.5N")  # a latitude over 90: no place on the map
    assert xmp.parse(off_the_map).gps is None and xmp.parse(off_the_map).regions


def test_odd_and_hostile_files_beside_the_photos_never_stop_the_reading(con, cfg, library):
    """What other apps (or anyone) left beside the photos is read leniently: JSON of the wrong shape or nested too deep
    to read, a caption longer than captions are here, a .picasa.ini of garbage, thousands of keywords. None may stop
    the stage: it would stop on the same photo every pass, and every photo's names after it would wait."""
    odd = library / "Odd"
    photo(odd / "a.jpg", size=(401, 300), taken=None)  # each its own bytes
    (odd / "a.jpg.json").write_text(json.dumps({"photoTakenTime": {"timestamp": "1104580800"}, "people": 5,
                                                "geoData": [39.8, -89.64], "description": ["not", "text"]}))
    photo(odd / "b.jpg", size=(402, 300), taken=None)
    (odd / "b.jpg.json").write_text('{"photoTakenTime": ' + "[" * 100000 + "]" * 100000 + "}")
    photo(odd / "c.jpg", size=(403, 300), meta=packet(caption="A long story about the farm. " * 300))
    photo(odd / "d.jpg", size=(404, 300))
    (odd / ".picasa.ini").write_bytes(b"[d.jpg]\nfaces=rect64(zz),1;rect64(;;\x00\xff\xfe;rect64(0000ffff0000ffff),2\n"
                                      b"keywords=,,\n[[[\n=\n[]\n")
    (odd / "contacts.xml").write_bytes(b'<!DOCTYPE c [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
                                       b'<contacts><contact id="2" name="&b;"/></contacts>')  # entities: refused
    photo(odd / "f.jpg", size=(406, 300), taken=None)
    (odd / "f.jpg.json").write_bytes(b'{"photoTakenTime": {"timestamp": "1104580800"}, "x": "' + b"a" * (2 << 20)
                                     + b'"}')  # far bigger than Takeout's: something else
    photo(odd / "e.jpg", size=(405, 300))
    (odd / "e.jpg.xmp").write_bytes(packet(keywords=[f"word {n}" for n in range(3000)]))  # another app's
    photo(library / "Lake" / "named.jpg", meta=packet([("Ann Miller", 0.25, 0.4, 0.2, 0.3)]))
    read(con, cfg)
    con.execute(f"UPDATE image SET stage = stage | {STAGE_FACES}")  # no faces found: names go on the photos
    con.commit()
    imports.run(con, cfg)
    images = {n: image_of(con, "Odd", n) for n in ("a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg")}
    read_ids = {r[0] for r in con.execute("SELECT image_id FROM metadata_read")}
    assert set(images.values()) <= read_ids and image_of(con, "Lake", "named.jpg") in read_ids
    assert not con.execute("SELECT 1 FROM retry WHERE stage = 'metadata'").fetchone()
    assert not con.execute("SELECT 1 FROM imported WHERE image_id IN (?, ?)", (images["d.jpg"], images["f.jpg"])) \
        .fetchone()  # nobody named from a contacts.xml with entities, nor a date from an outsized JSON
    a = con.execute("SELECT taken_at_source, gps_lat FROM image WHERE id = ?", (images["a.jpg"],)).fetchone()
    assert tuple(a) == ("import", None)  # the date in it was fine; the place of the wrong shape is left out
    assert comments.caption(con, images["c.jpg"]).startswith("A long story about the farm.")
    assert len(comments.caption(con, images["c.jpg"])) <= comments.MOST
    assert con.execute("SELECT count(*) FROM image_tag WHERE image_id = ?", (images["e.jpg"],)).fetchone()[0] == \
        imports.MOST_EACH
    assert con.execute("""SELECT p.name FROM image_person l JOIN person p ON p.id = l.person_id
                          WHERE l.image_id = ?""", (image_of(con, "Lake", "named.jpg"),)).fetchone()[0] == "Ann Miller"


def test_a_photo_that_cant_be_read_waits_and_the_rest_go_on(con, cfg, library, monkeypatch):
    """Something no reader expected (a file that is gone, or a bug) puts that photo aside to try again later, as a
    file the share can't give just now is, while the others are read and their names brought in."""
    photo(library / "Lake" / "a.jpg", meta=packet(keywords=["beach"]))
    photo(library / "Lake" / "b.jpg", meta=packet(keywords=["lake"]))
    read(con, cfg)
    bad, good = image_of(con, "Lake", "a.jpg"), image_of(con, "Lake", "b.jpg")
    real = imports.Reader.read

    def read_one(self, rel_path, photo):
        if rel_path.endswith("/a.jpg"):
            raise ValueError("something unexpected")
        return real(self, rel_path, photo)
    monkeypatch.setattr(imports.Reader, "read", read_one)
    stats = imports.run(con, cfg)
    assert stats.failed == 1 and stats.read >= 1
    assert con.execute("SELECT error FROM retry WHERE stage = 'metadata' AND item_id = ?",
                       (bad,)).fetchone()[0].startswith("ValueError")
    assert {r[0] for r in con.execute("SELECT image_id FROM metadata_read")} >= {good} and \
        bad not in {r[0] for r in con.execute("SELECT image_id FROM metadata_read")}


def test_a_raw_files_own_xmp_is_found_in_its_first_part(tmp_path):
    """Most raw files are nothing Pillow opens: their XMP packet is looked for in the start of the file. A file that
    can't be read at all is an error (the photo is tried again later), not an empty answer."""
    raw = tmp_path / "DSC_0001.NEF"
    raw.write_bytes(b"II*\x00" + bytes(5000) + packet([("Ann Miller", 0.5, 0.5, 0.2, 0.2)], ["Beach"]) + bytes(99))
    found, orientation = xmp.embedded(raw)
    assert [r[0] for r in found.regions] == ["Ann Miller"] and found.keywords == ["Beach"] and orientation == 1
    with pytest.raises(OSError):
        xmp.embedded(tmp_path / "gone.NEF")
    late = tmp_path / "late.NEF"  # a packet past the part looked through is not read
    late.write_bytes(b"II*\x00" + b"\x00" * xmp.HEAD_BYTES + packet(keywords=["Beach"]))
    assert xmp.embedded(late)[0].empty()


def test_a_name_written_on_a_mac_is_the_same_name():
    """macOS writes accented letters decomposed (NFD): the same name, not someone new."""
    from lineagebox import imports
    assert imports._usable_name("Rene\u0301e  Miller") == "Ren\u00e9e Miller"
