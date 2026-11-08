"""The first steps on a new LineageBox, on an admin's Home until they are done: watch a folder, check the AI, name
yourself (This is me), name the largest groups of faces, start the family tree, invite someone. Each step is worked
out from the catalog as it is now, so one done another way (the config file, a family tree brought in, the accounts
command) is ticked too, and nothing is stored but the admin's choice to put the list away for good (HIDDEN).

With family accounts off, inviting someone becomes turning them on when the family is ready: optional, so the list
goes once the rest is done."""
import sqlite3
from dataclasses import dataclass

from lineagebox import family, settings
from lineagebox.config import Config

HIDDEN = "first_run_hidden"  # the settings key: the list put away for good, by an admin
# Name the largest groups: done once this many people have faces named, and no group still unnamed is larger than the
# faces named as the last of them (so the people in the most photos are named first). A catalog the family has
# worked on for a while has it done many times over: on one of 58,000 faces, 127 people were named and the largest
# unnamed group left had 17 faces, against hundreds for the fifth person.
GROUPS_PEOPLE = 5


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    about: str
    href: str
    done: bool
    optional: bool = False
    note: str = ""  # what stands in the way just now (the AI not answering), or how far it is


def steps(con: sqlite3.Connection, now: Config) -> list[Step]:
    """Every step, in the order they are best done. con: an admin's connection (con.me is their account's person);
    now: the settings as changed on the Settings page."""
    photos = con.execute("SELECT count(*) FROM image").fetchone()[0]
    found = [
        Step("folder", "Watch your photos folder",
             "Pick a folder to watch: the one your photos are in, on this computer or the network. LineageBox reads "
             "them in the background and never changes them.",
             "/settings#folders", bool(now.folders),
             note=f"{photos:,} photo{'' if photos == 1 else 's'} read so far." if now.folders else ""),
        _ai(con, now),
        _me(con, now),
        _groups(con, now),
        _tree(con),
        _invite(con, now),
    ]
    return found


def checklist(con: sqlite3.Connection, now: Config) -> dict | None:
    """What Home shows: the steps, and how many of those that count are done; None once they all are, or once an
    admin put the list away. All done is noted as put away, so a LineageBox past its first days (or one that had
    them before this list) asks one setting instead of counting faces on every visit to Home."""
    if settings.get(con, HIDDEN):
        return None
    found = steps(con, now)
    needed = [s for s in found if not s.optional]
    if all(s.done for s in needed):
        settings.put(con, HIDDEN, "done")
        return None
    return {"steps": found, "done": sum(s.done for s in needed), "needed": len(needed)}


def hide(con: sqlite3.Connection) -> None:
    settings.put(con, HIDDEN, "hidden")


def _ai(con: sqlite3.Connection, now: Config) -> Step:
    """Done once the AI has described a photo, or when it is turned off on purpose."""
    described = con.execute("SELECT 1 FROM analysis LIMIT 1").fetchone() is not None
    problem = settings.problems(con).get("ollama")
    note = f"It doesn't answer just now: {problem['message']}" if problem and not described else ""
    return Step("ai", "Check the AI",
                "The AI describes each photo (what is in it, the occasion) so that search finds it. It runs on a "
                "computer in the house with Ollama: Settings → AI, speech and faces checks that it answers.",
                "/settings#ollama", described or not now.describe_enabled, note=note)


def _me(con: sqlite3.Connection, now: Config) -> Step:
    if now.accounts_enabled:
        about = ("Name your own face, then pick yourself as the person of your account on Family accounts: "
                 "relationships, and Home, are worked out from you.")
        href = "/accounts"
    else:
        about = ("Name your own face (Name faces asks), open your page and press This is me: relationships (your "
                 "niece, your grandfather) are worked out from you.")
        href = "/people/name"
    return Step("me", "Name yourself and press This is me", about, href, family.me(con) is not None)


def _groups(con: sqlite3.Connection, now: Config) -> Step:
    """Done when the GROUPS_PEOPLE people in the most photos are named (see there), or faces are not looked for."""
    named = [r[0] for r in con.execute("""SELECT count(*) FROM face WHERE person_id IS NOT NULL
                                          AND assigned_by = 'user' GROUP BY person_id ORDER BY 1 DESC""")]
    largest = con.execute("""SELECT count(*) FROM face WHERE cluster_id IS NOT NULL AND person_id IS NULL
                             GROUP BY cluster_id ORDER BY 1 DESC LIMIT 1""").fetchone()
    faces = con.execute("SELECT count(*) FROM face").fetchone()[0]
    done = not now.faces_enabled or (len(named) >= GROUPS_PEOPLE and (not largest or largest[0] <= named[
        GROUPS_PEOPLE - 1]))
    note = ""
    if now.faces_enabled and not faces:
        note = "No faces found yet: they are looked for as the photos are read."
    elif not done:
        note = f"{len(named):,} of {GROUPS_PEOPLE} named so far." if len(named) < GROUPS_PEOPLE else ""
    return Step("groups", "Name the largest groups of faces",
                "Faces that look like one person are grouped, the largest first: they are the people in the most "
                "photos. Name them, and recognition finds them in the rest.",
                "/people/name?only=groups", done, note=note)


def _tree(con: sqlite3.Connection) -> Step:
    started = con.execute("""SELECT EXISTS (SELECT 1 FROM parent) OR EXISTS (SELECT 1 FROM partner)
                             OR EXISTS (SELECT 1 FROM sibling)""").fetchone()[0]
    me = family.me(con)
    return Step("tree", "Start the family tree",
                "Add your parents, partner, brothers and sisters and children on your own page; cousins, in-laws and "
                "the rest are worked out from them. A tree from Ancestry or another program can be brought in on the "
                "Family page.", f"/person/{me}" if me else "/family", bool(started))


def _invite(con: sqlite3.Connection, now: Config) -> Step:
    if not now.accounts_enabled:
        return Step("invite", "Turn on family accounts when you're ready",
                    "Until then everyone on your home network sees everything, as you do. With accounts, each "
                    "relative signs in from their own phone and sees their part of the family.",
                    "/accounts", False, optional=True)
    invited = con.execute("""SELECT 1 FROM account WHERE role = 'member' AND disabled_at IS NULL
                             LIMIT 1""").fetchone() is not None
    return Step("invite", "Invite someone in the family",
                "Add an account for a relative, tied to who they are in the tree: they sign in with Google or a "
                "passkey and see their own part of the family.", "/accounts", invited)
