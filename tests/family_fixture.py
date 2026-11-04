"""A family for the access tests, for the rules in docs/access-control.md (made-up names), with every
case the rules name, outsiders whose photos carry markers that must never reach a member, and photos on disk."""
import numpy as np

from lineagebox import accounts, search
from lineagebox.ingest import ingest
from lineagebox.scan import scan
from tests.conftest import write_jpeg

#   Old Rex + Old Nell        Ned + Nan
#     |-- Al -- Cal              |
#     Rex ---------------------- Nell
#      |-- Cora + Milo (Milo's parents Mel + Meg, his sister Mia)
#      |-- Bart + Bea -- Bella;  Bart + Eve (former) -- Ellis;  Bart + Fay (former, no child)
#      |-- Sam + Tina -- Gus;    Tina + Ed (former) -- Jake
#   Tina's parents Hank + Hazel, her sister Rosa; Hank's brother Hal (siblings entered with no parents)
#   Jim is Cora's friend, Rufus is Sam's dog. Zed and Zoe are nobody's family.
PEOPLE = ["Old Rex", "Old Nell", "Al", "Cal", "Ned", "Nan", "Rex", "Nell", "Cora", "Milo", "Mel", "Meg", "Mia", "Bart",
          "Bea", "Bella", "Eve", "Ellis", "Fay", "Sam", "Tina", "Gus", "Ed", "Jake", "Hank", "Hazel", "Rosa", "Hal",
          "Jim", "Rufus", "Zed", "Zoe"]
PARENTS = {"Al": ("Old Rex", "Old Nell"), "Rex": ("Old Rex", "Old Nell"), "Cal": ("Al",), "Nell": ("Ned", "Nan"),
           "Cora": ("Rex", "Nell"), "Bart": ("Rex", "Nell"), "Sam": ("Rex", "Nell"), "Milo": ("Mel", "Meg"),
           "Mia": ("Mel", "Meg"), "Bella": ("Bart", "Bea"), "Ellis": ("Bart", "Eve"), "Gus": ("Sam", "Tina"),
           "Jake": ("Tina", "Ed"), "Tina": ("Hank", "Hazel"), "Rosa": ("Hank", "Hazel")}
PARTNERS = [("Old Rex", "Old Nell", 1), ("Ned", "Nan", 1), ("Rex", "Nell", 1), ("Cora", "Milo", 1), ("Mel", "Meg", 1),
            ("Bart", "Bea", 1), ("Bart", "Eve", 0), ("Bart", "Fay", 0), ("Sam", "Tina", 1), ("Tina", "Ed", 0),
            ("Hank", "Hazel", 1), ("Zed", "Zoe", 1)]
SIBLINGS = [("Hank", "Hal")]
RELATIONS = [("Jim", "friend", "Cora"), ("Rufus", "pet", "Sam")]

REX_SIDE = {"Old Rex", "Old Nell", "Al", "Cal", "Ned", "Nan", "Rex", "Nell", "Cora", "Bart", "Sam", "Bella", "Ellis",
            "Gus", "Milo", "Bea", "Eve", "Tina", "Jake"}
TINA_SIDE = {"Tina", "Hank", "Hazel", "Rosa", "Hal", "Jake", "Gus", "Sam", "Ed"}
SEES = {
    "Cora": REX_SIDE | {"Mel", "Meg", "Mia"},  # and her husband's side
    "Sam": REX_SIDE | TINA_SIDE,
    "Tina": REX_SIDE | TINA_SIDE,
}

# Photos, each in a folder of its own: (folder, file, [(person, assigned_by)], people added by hand, hidden)
PHOTOS = [
    ("Family/Picnic", "cora_bart.jpg", [("Cora", "user"), ("Bart", "user")], [], False),
    ("Family/Old", "rex.jpg", [("Rex", "auto")], [], False),
    ("Bakers", "hank.jpg", [("Hank", "user")], [], False),
    ("Bakers", "hank_sam.jpg", [("Hank", "user"), ("Sam", "user")], [], False),
    ("ZedSecret", "zed.jpg", [("Zed", "user")], [], False),
    ("Pets", "rufus.jpg", [("Rufus", "user")], [], False),
    ("Friends", "jim.jpg", [], ["Jim"], False),
    ("Trips", "scenery.jpg", [], [], False),
    ("Family/Private", "hidden.jpg", [("Cora", "user")], [], True),
    ("Family/Aside", "aside.jpg", [(None, "rejected")], [], False),
]
SECRET = "zebrazed"  # in Zed's caption, tag, transcript... : never on a page a member gets


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def build(con, cfg, library) -> dict:
    """The family, the photos on disk (scanned and ingested), their faces and Zed's markers. Returns
    {"people": name -> id, "photos": file name -> image id, "faces": file name -> [face ids]}."""
    ids = {n: con.execute("INSERT INTO person(name) VALUES (?)", (n,)).lastrowid for n in PEOPLE}
    for child, parents in PARENTS.items():
        for p in parents:
            con.execute("INSERT INTO parent(child_id, parent_id) VALUES (?, ?)", (ids[child], ids[p]))
    for a, b, current in PARTNERS:
        x, y = sorted((ids[a], ids[b]))
        con.execute("INSERT INTO partner(a, b, current, married) VALUES (?, ?, ?, 1)", (x, y, current))
    for a, b in SIBLINGS:
        x, y = sorted((ids[a], ids[b]))
        con.execute("INSERT INTO sibling(a, b) VALUES (?, ?)", (x, y))
    for who, kind, of in RELATIONS:
        con.execute("INSERT INTO relation(person_id, of_id, kind) VALUES (?, ?, ?)", (ids[who], ids[of], kind))
    con.execute("INSERT INTO event(person_id, tag, date, sort_date, place) VALUES (?, 'BIRT', '1950', '1950', ?)",
                (ids["Zed"], f"{SECRET}ville"))
    con.execute("INSERT INTO person_name(person_id, name, type) VALUES (?, ?, 'nickname')", (ids["Zed"], f"{SECRET}nick"))
    con.commit()

    for n, (folder, name, *_rest) in enumerate(PHOTOS):
        write_jpeg(library / folder / name, size=(200, 160), color=(20 * n, 100, 255 - 20 * n),
                   taken=f"20{10 + n}:05:0{1 + n % 8} 10:00:00")
    scan(con, cfg)
    ingest(con, cfg)
    photos, faces = {}, {}
    for n, (folder, name, named, added, hidden) in enumerate(PHOTOS):
        image = con.execute("SELECT image_id FROM file WHERE path = ?",
                            (f"{library.name}/{folder}/{name}",)).fetchone()[0]
        photos[name] = image
        faces[name] = []
        for k, (who, how) in enumerate(named):
            faces[name].append(con.execute(
                """INSERT INTO face(image_id, x1, y1, x2, y2, det_score, quality, embedding, person_id, assigned_by)
                   VALUES (?, ?, 0.1, ?, 0.6, 0.9, 25, ?, ?, ?)""",
                (image, 0.1 + 0.4 * k, 0.4 + 0.4 * k, unit(n * 10 + k).tobytes(), ids.get(who), how)).lastrowid)
        for who in added:
            con.execute("INSERT INTO image_person(image_id, person_id, added_at) VALUES (?, ?, '2026-01-01')",
                        (image, ids[who]))
        if hidden:
            con.execute("UPDATE image SET hidden = 1 WHERE id = ?", (image,))
    zed = photos["zed.jpg"]
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, caption, raw_json)
                   VALUES (?, 'test', 1, '2026-01-01', ?, '{}')""", (zed, f"A {SECRET} at the beach"))
    con.execute("INSERT INTO tag(name) VALUES (?)", (f"{SECRET}tag",))
    con.execute("INSERT INTO image_tag(image_id, tag_id, source) VALUES (?, last_insert_rowid(), 'user')", (zed,))
    con.execute("INSERT INTO document(image_id, kind, title, created_at) VALUES (?, 'Letter', ?, '2026-01-01')",
                (zed, f"{SECRET} letter"))
    con.execute("UPDATE folder SET event = ? WHERE path LIKE '%ZedSecret'", (f"{SECRET} party",))
    con.commit()
    search.rebuild(con)
    return {"people": ids, "photos": photos, "faces": faces}


def account(con, person_id: int, email: str, role: str = "member") -> dict:
    account_id = accounts.add(con, email, person_id, role)
    con.commit()
    return dict(accounts.get(con, account_id))
