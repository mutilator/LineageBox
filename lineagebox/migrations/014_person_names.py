"""Two people may have the same name.

Family members who sign in see only their own part of the family tree, so a name they type has to be able to become a
new person even when someone outside their part already has it (two John Smiths). person.name was UNIQUE: the table
is built again without that, keeping every id, and an index on lower(name) keeps finding people by name quick.
"""
import re

FOREIGN_KEYS_OFF = True  # db.migrate: dropping the old table must not delete the family links and faces that point to it


def upgrade(con, cfg):
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'person'").fetchone()[0]
    new, n = re.subn(r"(\bname\s+TEXT\s+NOT\s+NULL)\s+UNIQUE", r"\1", sql, count=1, flags=re.IGNORECASE)
    if not n:
        raise RuntimeError(f"person.name is not declared as expected: {sql}")
    new = re.sub(r"^CREATE TABLE\s+\"?person\"?", "CREATE TABLE person_new", new, count=1, flags=re.IGNORECASE)
    indexes = [r[0] for r in con.execute("""SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'person'
                                            AND sql IS NOT NULL""")]
    con.execute(new)
    con.execute("INSERT INTO person_new SELECT * FROM person")
    con.execute("DROP TABLE person")
    con.execute("ALTER TABLE person_new RENAME TO person")
    for index in indexes:
        con.execute(index)
    con.execute("CREATE INDEX person_lower_name ON person(lower(name))")
