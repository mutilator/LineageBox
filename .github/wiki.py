"""The GitHub wiki, made from the documentation in the repository. CI publishes it on each release
(.github/workflows/image.yml), so the wiki tells of the version people install. The files here stay the ones to edit:
each release writes the wiki over.

    python .github/wiki.py WIKI [--repo owner/name]

replaces the pages in the folder WIKI (a clone of the wiki, or any folder) with: Home (README.md, with the docs' own
index as its Documentation section), a page for each document, the user guide as a page for each of its sections and
one that lists them, the changelog, and the sidebar and footer. Links between documents become links between pages,
and a link to another file of the repository points at it on GitHub. Every link between pages is then checked: one to
a page or a heading that isn't there, or a document left without a page, is an error (exit 1, and nothing is written),
so a release never publishes it. tests/test_wiki.py makes the same checks on every change.
"""
import argparse
import os
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "mutilator/LineageBox"

# Each document's page. A link whose words are only the document's file name ("CONTRIBUTING.md") takes the page's
# title instead: its name with spaces, or as TITLES has it.
PAGES = {
    "README.md": "Home",
    "docs/README.md": "Home",  # the docs' index: Home's Documentation section
    "docs/quick-start/docker.md": "Quick-start-on-one-machine",
    "docs/quick-start/nas-and-pc.md": "Quick-start-with-a-NAS-and-a-PC",
    "docs/quick-start/from-source.md": "Quick-start-from-source",
    "docs/install.md": "Installation-guide",
    "docs/ollama.md": "AI-descriptions-with-Ollama",
    "docs/whisper.md": "Transcripts-with-Whisper",
    "docs/remote-access.md": "Remote-access-and-HTTPS",
    "docs/family-accounts.md": "Family-accounts",
    "docs/demo.md": "Public-demo",
    "docs/troubleshooting.md": "Troubleshooting",
    "docs/user-guide.md": "User-guide",  # and a page for each of its sections, below
    "docs/configuration.md": "Configuration-reference",
    "docs/command-line.md": "Command-line",
    "docs/export.md": "Export-format",
    "docs/architecture.md": "Architecture",
    "docs/design-notes.md": "Design-notes",
    "docs/access-control.md": "Access-control",
    "docs/development.md": "Development",
    "docs/translating.md": "Translating",
    "CONTRIBUTING.md": "Contributing",
    "SECURITY.md": "Security",
    "TRADEMARKS.md": "Trademarks",
    "THIRD_PARTY_NOTICES.md": "Third-party-notices",
    "CHANGELOG.md": "Changelog",
}
TITLES = {"Third-party-notices": "Third-party notices"}
# Said at the top of a page, before the document's own words: what it is a copy of, or the words a section of the user
# guide that starts with a list needs in place of its heading.
NOTES = {
    "Third-party-notices": "> This page is a copy of `THIRD_PARTY_NOTICES.md` in the repository, which both Docker "
                           "images also carry in `/app`.",
    "Status-page": "The **Status** page shows:",
}

GUIDE = "docs/user-guide.md"
# The user guide's sections ("## ..."), a page each, named after its heading but for these.
GUIDE_PAGES = {
    "Getting started and getting around": "Getting-around",
    "Status": "Status-page",
    "Settings": "Settings-page",
    "Keyboard": "Keyboard-shortcuts",
}
# What the guide's own page says of a section that has no parts ("### ...") to list instead. A new section without
# parts needs its words here.
GUIDE_ABOUT = {
    "Other photo programs": "names, tags, captions and dates brought in from other apps, and XMP sidecars for them",
    "Dates": "where dates come from, dating photos by hand, cameras whose clock was wrong, and dating scans",
    "Working on many photos at once": "ticking photos to tag, date, describe, file or download them together",
    "Tours": "slideshows picked by rules, played with captions or saved as a video",
    "Albums and favourites": "photos picked by hand and put in order, and the photos you love",
    "Cleanup": "copies, similar shots, damaged, blurry and turned photos, checks of the tree, and Recent changes",
    "Status": "what the worker is doing, what is waiting, storage, backups and alerts",
    "Settings": "each part of the Settings page",
    "Keyboard": "the keys on every page, and turning the one-letter keys off",
}

# "On this page:", before the first section of a page (Home aside) with at least this many sections and characters.
CONTENTS_SECTIONS = 5
CONTENTS_LENGTH = 4000

SIDEBAR = """\
**[LineageBox](Home)**

[▶ Try the live demo](https://linbox.g-tech.dev)

**Getting started**

- [On one machine](Quick-start-on-one-machine)
- [A NAS, plus a PC with a graphics card](Quick-start-with-a-NAS-and-a-PC)
- [From source, without Docker](Quick-start-from-source)

**Running LineageBox**

- [Installation guide](Installation-guide)
- [AI descriptions with Ollama](AI-descriptions-with-Ollama)
- [Transcripts with Whisper](Transcripts-with-Whisper)
- [Remote access and HTTPS](Remote-access-and-HTTPS)
- [Family accounts](Family-accounts)
- [A public demo](Public-demo)
- [Troubleshooting](Troubleshooting)

**User guide**

- [Overview](User-guide)
{guide}

**Reference**

- [Configuration reference](Configuration-reference)
- [Command line](Command-line)
- [Export format](Export-format)

**Development**

- [Architecture](Architecture)
- [Design notes and measurements](Design-notes)
- [Access control](Access-control)
- [Development](Development)
- [Translating](Translating)
- [Contributing](Contributing)

**The project**

- [Changelog](Changelog)
- [Security](Security)
- [The LineageBox name](Trademarks)
- [Third-party notices](Third-party-notices)
- [Licence (AGPL-3.0)]({github}/blob/main/LICENSE)
- [Source code]({github})
"""
FOOTER = ("LineageBox is free software under the [GNU AGPL, version 3]({github}/blob/main/LICENSE). · "
          "[Source code]({github}) · [Live demo](https://linbox.g-tech.dev) · [Report a problem]({github}/issues)\n")

FENCE = re.compile(r"\s*(```|~~~)")
LINK = re.compile(r"\[([^\]]*)\]\(([^()\s]+)\)")
HEADING = re.compile(r"(#{1,6}) +(.+?) *")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")  # https:, mailto:


def runs(text: str) -> list[tuple[bool, list[str]]]:
    """The text's lines in runs, each (in a code block, its lines): links and headings are only looked for outside."""
    out: list[tuple[bool, list[str]]] = []
    fenced = False
    for line in text.split("\n"):
        fence = bool(FENCE.match(line))
        code = fenced or fence
        if not out or out[-1][0] != code:
            out.append((code, []))
        out[-1][1].append(line)
        if fence:
            fenced = not fenced
    return out


def lines_outside_code(text: str):
    """Each line of the text, with whether it is outside code blocks."""
    for code, lines in runs(text):
        for line in lines:
            yield line, not code


def in_prose(text: str, change) -> str:
    """The text with `change` made to each stretch of it outside code blocks."""
    return "\n".join("\n".join(lines) if code else change("\n".join(lines)) for code, lines in runs(text))


def plain(words: str) -> str:
    """A heading's words to link to it with: without its links' addresses or code's backticks."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", words).replace("`", "")


def slug(words: str) -> str:
    """GitHub's anchor for a heading: its words in lower case, without punctuation, each space a hyphen."""
    words = re.sub(r"<[^>]+>", "", plain(words))
    return re.sub(r"[^\w\- ]", "", words.lower()).replace(" ", "-")


def headings(text: str) -> list[tuple[int, str, str]]:
    """Each heading outside code blocks: (level, words, anchor), a repeated anchor numbered -1, -2 as GitHub does."""
    found: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for line, prose in lines_outside_code(text):
        match = HEADING.fullmatch(line) if prose else None
        if match:
            anchor = slug(match.group(2))
            n = seen.get(anchor, 0)
            seen[anchor] = n + 1
            found.append((len(match.group(1)), match.group(2), f"{anchor}-{n}" if n else anchor))
    return found


def sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The text before its first "## " heading, and each such heading's (words, what follows it)."""
    intro: list[str] = []
    found: list[tuple[str, list[str]]] = []
    for line, prose in lines_outside_code(text):
        match = HEADING.fullmatch(line) if prose else None
        if match and len(match.group(1)) == 2:
            found.append((match.group(2), []))
        else:
            (found[-1][1] if found else intro).append(line)
    return "\n".join(intro), [(words, "\n".join(body)) for words, body in found]


def untitled(text: str) -> str:
    """Without its "# Title" line: the wiki shows the page's name above it."""
    first, _, rest = text.partition("\n")
    return rest.lstrip("\n") if first.startswith("# ") else text


def shifted(text: str, by: int) -> str:
    """Its headings a level deeper (by=1) or higher (by=-1)."""
    if by > 0:
        return in_prose(text, lambda t: re.sub(r"(?m)^(#{1,5}) ", r"#\1 ", t))
    return in_prose(text, lambda t: re.sub(r"(?m)^#(#{1,6}) ", r"\1 ", t))


def contents(text: str) -> str:
    """With "On this page:" and a link to each section before the first, on a page with many sections and long."""
    found = [(words, anchor) for level, words, anchor in headings(text) if level == 2]
    if len(found) < CONTENTS_SECTIONS or len(text) < CONTENTS_LENGTH:
        return text
    lines = text.split("\n")
    at = next(i for i, (line, prose) in enumerate(lines_outside_code(text))
              if prose and line.startswith("## ") and HEADING.fullmatch(line))
    lines[at:at] = ["**On this page:** " + " · ".join(f"[{plain(words)}](#{anchor})" for words, anchor in found), ""]
    return "\n".join(lines)


def title(page: str) -> str:
    return TITLES.get(page, page.replace("-", " "))


def guide_page(heading: str) -> str:
    return GUIDE_PAGES.get(heading) or re.sub(r"\W+", "-", heading).strip("-")


def in_a_sentence(words: str) -> str:
    """A heading's words to list mid-sentence: "A person's page" as "a person's page", but "LineageBox on your
    phone" as it is."""
    first = words.split(" ", 1)[0]
    return words[0].lower() + words[1:] if first[1:] == first[1:].lower() else words


class Wiki:
    """The wiki's pages, made from the documents in the repository at `root`, linking to it on GitHub as `repo`."""

    def __init__(self, root: Path = ROOT, repo: str = REPO):
        self.root = Path(root)
        self.github = f"https://github.com/{repo}"
        self.pages: dict[str, str] = {}
        self.problems: list[str] = []
        self.guide_anchors: dict[str, tuple[str, str]] = {}  # an anchor of the user guide -> (its page, its anchor)

    def read(self, path: str) -> str:
        file = self.root / path
        if not file.is_file():
            self.problems.append(f"{path} isn't there: give PAGES in .github/wiki.py its new name")
            return ""
        return file.read_text(encoding="utf-8").replace("\r\n", "\n")

    def build(self) -> dict[str, str]:
        """Every page, by its name. Anything wrong is in `problems`."""
        guide_intro, guide = self.guide_sections()
        for path, page in PAGES.items():
            if path not in ("docs/README.md", GUIDE):
                self.pages[page] = self.document(path, page)
        self.user_guide(guide_intro, guide)
        listed = "\n".join(f"- [{title(page)}]({page})" for _, page, _ in guide)
        self.pages["_Sidebar"] = SIDEBAR.format(guide=listed, github=self.github)
        self.pages["_Footer"] = FOOTER.format(github=self.github)
        self.check()
        return self.pages

    def document(self, path: str, page: str) -> str:
        text = self.links(self.read(path), path, page)
        if page == "Home":
            text = self.with_index(text)
        else:
            text = contents(untitled(text))
            if page in NOTES:
                text = f"{NOTES[page]}\n\n{text}"
        return text.strip("\n") + "\n"

    def with_index(self, text: str) -> str:
        """README.md with the docs' index (docs/README.md, from its first section) as its Documentation section."""
        index = self.links(self.read("docs/README.md"), "docs/README.md", "Home")
        _, parts = sections(index)
        listed = shifted("\n".join(f"## {words}\n{body}" for words, body in parts), 1).strip("\n")
        lines = text.split("\n")
        starts = [i for i, (line, prose) in enumerate(lines_outside_code(text))
                  if prose and line.startswith("## ") and HEADING.fullmatch(line)]
        at = next((i for i in starts if lines[i] == "## Documentation"), None)
        if at is None:
            self.problems.append("README.md has no '## Documentation' section to hold the docs' index")
            return text
        end = next((i for i in starts if i > at), len(lines))
        lines[at + 1:end] = ["", *listed.split("\n"), ""]
        return "\n".join(lines)

    def guide_sections(self) -> tuple[str, list[tuple[str, str, str]]]:
        """The user guide's words before its first section, and each section's (heading, page, words), its parts a
        level higher. Notes where each of the guide's anchors went, for the links to them."""
        text = untitled(self.read(GUIDE))
        intro, parts = sections(text)
        pages = [guide_page(words) for words, _ in parts]
        inside: list[list[str]] = []  # each section's anchors in the whole guide, its own heading's aside
        for level, _, anchor in headings(text):
            if level == 2:
                self.guide_anchors[anchor] = (pages[len(inside)], "")
                inside.append([])
            elif inside:
                inside[-1].append(anchor)
            else:
                self.guide_anchors[anchor] = ("User-guide", anchor)
        found = []
        for (words, body), page, anchors in zip(parts, pages, inside):
            body = shifted(body.strip("\n"), -1)
            for anchor, there in zip(anchors, (a for _, _, a in headings(body))):
                self.guide_anchors[anchor] = (page, there)
            found.append((words, page, body))
        headed = {words for words, _ in parts}
        for name, table in (("GUIDE_PAGES", GUIDE_PAGES), ("GUIDE_ABOUT", GUIDE_ABOUT)):
            for heading in sorted(set(table) - headed):
                self.problems.append(f"{name} in .github/wiki.py names '{heading}', which the user guide has no "
                                     "section of")
        return intro, found

    def user_guide(self, intro: str, guide: list[tuple[str, str, str]]):
        """A page for each section of the user guide, with links to the one before and after, and the guide's own
        page listing them, with their parts."""
        listed = []
        for k, (words, page, body) in enumerate(guide):
            nav = ["[User guide](User-guide)"]
            if k:
                nav.insert(0, f"[← {title(guide[k - 1][1])}]({guide[k - 1][1]})")
            if k + 1 < len(guide):
                nav.append(f"[{title(guide[k + 1][1])} →]({guide[k + 1][1]})")
            text = contents(self.links(body, GUIDE, page)).strip("\n")
            if page in NOTES:
                text = f"{NOTES[page]}\n\n{text}"
            self.pages[page] = f"{text}\n\n---\n\n{' · '.join(nav)}\n"
            parts = [(w, a) for level, w, a in headings(body) if level == 2]
            if parts:
                about = ", ".join(f"[{in_a_sentence(plain(w))}]({page}#{a})" for w, a in parts)
            elif words in GUIDE_ABOUT:
                about = GUIDE_ABOUT[words]
            else:
                about = ""
                self.problems.append(f"The user guide's section '{words}' has no parts to list: say what it is about "
                                     "in GUIDE_ABOUT in .github/wiki.py")
            listed.append(f"- **[{title(page)}]({page})**: {about}")
        intro = self.links(intro.strip("\n"), GUIDE, "User-guide")
        self.pages["User-guide"] = intro + "\n\n" + "\n".join(listed) + "\n"

    def links(self, text: str, source: str, here: str) -> str:
        """The text's links as links in the wiki, written on the page `here` from the document `source`."""
        def swap(match):
            words, href = match.groups()
            new = self.target(href, source, here)
            path = href.partition("#")[0]
            if path and new != href and not SCHEME.match(new) and words in (path, posixpath.basename(path)):
                words = title(new.partition("#")[0] or here)
            return f"[{words}]({new})"
        return in_prose(text, lambda t: LINK.sub(swap, t))

    def target(self, href: str, source: str, here: str) -> str:
        """Where a link to `href` in the document `source` goes from the page `here`: a page (and its heading), an
        address on GitHub for another file of the repository, or as it was (an address, or what leads nowhere)."""
        if SCHEME.match(href):
            return href
        path, _, anchor = href.partition("#")
        if path:
            path = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
        elif source == GUIDE:
            path = GUIDE  # a heading in another section may be on another page now
        else:
            return href
        if path == GUIDE and anchor:
            page, anchor = self.guide_anchors.get(anchor, ("User-guide", anchor))
        elif path in PAGES:
            page = PAGES[path]
        elif (self.root / path).exists():
            return f"{self.github}/blob/main/{path}" + (f"#{anchor}" if anchor else "")
        else:
            return href
        if page == here and anchor:
            return f"#{anchor}"
        return page + (f"#{anchor}" if anchor else "")

    def check(self):
        """Every link between pages leads to a page and, where it names one, a heading of it; and every document in
        docs/ has a page."""
        anchors = {page: {anchor for _, _, anchor in headings(text)} for page, text in self.pages.items()}
        for page, text in self.pages.items():
            for code, lines in runs(text):
                for words, href in [] if code else LINK.findall("\n".join(lines)):
                    if SCHEME.match(href):
                        continue
                    to, _, anchor = href.partition("#")
                    to = to or page
                    said = f"{page}: [{' '.join(words.split())}]({href})"
                    if to not in anchors:
                        self.problems.append(f"{said} leads to no page")
                    elif anchor and anchor not in anchors[to]:
                        self.problems.append(f"{said}: {to} has no heading #{anchor}")
        for file in sorted((self.root / "docs").rglob("*.md")):
            path = file.relative_to(self.root).as_posix()
            if path not in PAGES:
                self.problems.append(f"{path} has no page in the wiki: add it to PAGES in .github/wiki.py")


def write(pages: dict[str, str], folder: Path):
    """The pages into `folder`, in place of the pages in it."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.md"):
        old.unlink()
    for page, text in pages.items():
        (folder / f"{page}.md").write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Make the GitHub wiki's pages from the repository's documentation.")
    parser.add_argument("wiki", type=Path, help="the folder to write the pages in (its .md files are replaced)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", REPO),
                        help="owner/name on GitHub, for links to the repository's other files")
    parser.add_argument("--root", type=Path, default=ROOT, help="the repository to read (this one by default)")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    wiki = Wiki(args.root, args.repo)
    pages = wiki.build()
    if wiki.problems:
        print("The wiki wasn't written:", *wiki.problems, sep="\n- ", file=sys.stderr)
        return 1
    write(pages, args.wiki)
    print(f"{len(pages)} pages written to {args.wiki}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
