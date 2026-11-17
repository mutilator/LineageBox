"""Boyfriends and girlfriends: a couple going out together, rather than married or sharing a life as partners
(`partner.dating`). Each can be current or former, like any other couple, and their children belong to both of them.

Until now a person's page offered only husband, wife and partner, so a boyfriend or a girlfriend could be entered
only as another relationship ("Jake is Tom's girlfriend"), a label beside friend and coworker that the family tree
knew nothing of. Those labels become real couples here, with the sex the word says, unless the two are a couple in
the tree already or one of them has another current partner: those are left as they are, to be put right by hand.
"""
import uuid

# What was typed as another relationship before there was an option for it -> (the sex it says, still together)
WORDS = {"boyfriend": ("male", True), "girlfriend": ("female", True),
         "ex-boyfriend": ("male", False), "ex-girlfriend": ("female", False),
         "ex boyfriend": ("male", False), "ex girlfriend": ("female", False),
         "former boyfriend": ("male", False), "former girlfriend": ("female", False)}


def upgrade(con, cfg):
    con.execute("ALTER TABLE partner ADD COLUMN dating INTEGER NOT NULL DEFAULT 0")
    couples_from_labels(con)


def couples_from_labels(con) -> None:
    """Every "X is Y's girlfriend" entered as another relationship becomes the couple it says."""
    for who, of, kind in con.execute("SELECT person_id, of_id, kind FROM relation").fetchall():
        if (word := WORDS.get(" ".join(kind.lower().split()))) is None:
            continue
        sex, current = word
        a, b = min(who, of), max(who, of)
        if con.execute("SELECT 1 FROM partner WHERE a = ? AND b = ?", (a, b)).fetchone():
            continue  # a couple in the tree already: the label is left for whoever entered it to take away
        if current and con.execute("SELECT 1 FROM partner WHERE current AND (a IN (?, ?) OR b IN (?, ?))",
                                   (who, of, who, of)).fetchone():
            continue  # one of them has another current partner: the two of them would have to be sorted out by hand
        con.execute("INSERT INTO partner(a, b, current, married, dating, uid) VALUES (?, ?, ?, 0, 1, ?)",
                    (a, b, int(current), str(uuid.uuid4())))
        con.execute("UPDATE person SET sex = ? WHERE id = ? AND sex IS NULL", (sex, who))
        con.execute("DELETE FROM relation WHERE person_id = ? AND of_id = ? AND kind = ?", (who, of, kind))
