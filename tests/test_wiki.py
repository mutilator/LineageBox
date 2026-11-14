"""The GitHub wiki, which CI writes over with the docs on each release (.github/wiki.py): the repository's docs make
one whose every link leads somewhere, and each document becomes its page as the wiki has always had it."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("wiki", ROOT / ".github" / "wiki.py")
wiki = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wiki)


def test_the_docs_make_a_wiki_whose_links_all_lead_somewhere():
    made = wiki.Wiki(ROOT, "example/lineagebox")
    pages = made.build()
    assert made.problems == []
    assert {"Home", "User-guide", "Getting-around", "Keyboard-shortcuts", "Changelog", "_Sidebar",
            "_Footer"} <= set(pages)
    # the changelog is a page of its own, linked from Home, the sidebar and where the docs speak of it
    assert pages["Changelog"].startswith("What's new in each release")
    for page in ("Home", "_Sidebar", "Development", "Contributing"):
        assert "[Changelog](Changelog)" in pages[page], page
    assert "](Development#releases)" in pages["Changelog"]
    assert "https://github.com/example/lineagebox/blob/main/LICENSE" in pages["Home"]


SAMPLE = {
    "README.md": """# Sample

See [the guide](docs/user-guide.md#naming-faces), [the docs](docs/README.md) and [LICENSE](LICENSE).

## Documentation

- The old list, which the docs' own index replaces.

## Updating

[CHANGELOG.md](CHANGELOG.md) says what is new.
""",
    "docs/README.md": """# The docs

Words Home leaves out.

## Using it

- [User guide](user-guide.md): every page.
- [Installing](install.md#ports).
""",
    "docs/install.md": """# Installing

Read about [tours](user-guide.md#tours), [people](user-guide.md#people) and [SECURITY.md](../SECURITY.md), but not
[docs/user-guide.md](../SECURITY.md).

```sh
echo "[not a link](nowhere.md)"
```

## Ports

Port 8000.
""",
    "docs/user-guide.md": """# User guide

What each page does.

## People

### Naming faces

See [tours](#tours) and [the tree](#the-family-tree).

### The family tree

The tree.

## Tours

Back to [naming faces](#naming-faces).
""",
    "SECURITY.md": "# Security\n\nPrivately.\n",
    "CHANGELOG.md": "# Changelog\n\n## 1.0.0 (2026-01-01)\n\n- The first, [installed](docs/install.md#ports).\n",
    "LICENSE": "The licence.\n",
}


@pytest.fixture
def sample(tmp_path, monkeypatch):
    for path, text in SAMPLE.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    monkeypatch.setattr(wiki, "PAGES", {"README.md": "Home", "docs/README.md": "Home",
                                        "docs/install.md": "Installation-guide", "docs/user-guide.md": "User-guide",
                                        "SECURITY.md": "Security", "CHANGELOG.md": "Changelog"})
    monkeypatch.setattr(wiki, "GUIDE_PAGES", {})
    monkeypatch.setattr(wiki, "GUIDE_ABOUT", {"Tours": "slideshows"})
    monkeypatch.setattr(wiki, "NOTES", {"Security": "> A copy."})
    monkeypatch.setattr(wiki, "SIDEBAR",
                        "- [Overview](User-guide)\n{guide}\n- [Changelog](Changelog)\n- [Code]({github})\n")
    return tmp_path


def test_each_document_becomes_its_page(sample):
    made = wiki.Wiki(sample, "example/lineagebox")
    pages = made.build()
    assert made.problems == []
    assert pages["Home"] == """# Sample

See [the guide](People#naming-faces), [the docs](Home) and [LICENSE](https://github.com/example/lineagebox/blob/main/LICENSE).

## Documentation

### Using it

- [User guide](User-guide): every page.
- [Installing](Installation-guide#ports).

## Updating

[Changelog](Changelog) says what is new.
"""
    # its title gone (the wiki shows the page's name), links to the user guide's sections on their pages, and words that
    # were only the file's name are the page's title; code is left alone
    assert pages["Installation-guide"] == """Read about [tours](Tours), [people](People) and [Security](Security), but not
[docs/user-guide.md](Security).

```sh
echo "[not a link](nowhere.md)"
```

## Ports

Port 8000.
"""
    assert pages["Security"] == "> A copy.\n\nPrivately.\n"
    assert pages["Changelog"] == "## 1.0.0 (2026-01-01)\n\n- The first, [installed](Installation-guide#ports).\n"


def test_the_user_guide_is_a_page_per_section(sample):
    pages = wiki.Wiki(sample, "example/lineagebox").build()
    assert pages["User-guide"] == """What each page does.

- **[People](People)**: [naming faces](People#naming-faces), [the family tree](People#the-family-tree)
- **[Tours](Tours)**: slideshows
"""
    assert pages["People"] == """## Naming faces

See [tours](Tours) and [the tree](#the-family-tree).

## The family tree

The tree.

---

[User guide](User-guide) · [Tours →](Tours)
"""
    assert pages["Tours"] == ("Back to [naming faces](People#naming-faces).\n\n---\n\n"
                              "[← People](People) · [User guide](User-guide)\n")
    assert pages["_Sidebar"] == ("- [Overview](User-guide)\n- [People](People)\n- [Tours](Tours)\n"
                                 "- [Changelog](Changelog)\n- [Code](https://github.com/example/lineagebox)\n")


def test_what_the_wiki_couldnt_show_stops_it(sample):
    with (sample / "docs/install.md").open("a", encoding="utf-8") as f:
        f.write("\nSee [ports](#port) and [the guide](user-guide.md#nowhere).\n")
    (sample / "docs/extra.md").write_text("# Extra\n", encoding="utf-8")
    with (sample / "docs/user-guide.md").open("a", encoding="utf-8") as f:
        f.write("\n## Maps\n\nThe map.\n")
    made = wiki.Wiki(sample, "example/lineagebox")
    made.build()
    assert made.problems == [
        "The user guide's section 'Maps' has no parts to list: say what it is about in GUIDE_ABOUT in .github/wiki.py",
        "Installation-guide: [ports](#port): Installation-guide has no heading #port",
        "Installation-guide: [the guide](User-guide#nowhere): User-guide has no heading #nowhere",
        "docs/extra.md has no page in the wiki: add it to PAGES in .github/wiki.py",
    ]


def test_anchors_are_githubs():
    assert wiki.slug('"The catalog is busy" (503)') == "the-catalog-is-busy-503"
    assert wiki.slug("Faces: InsightFace `buffalo_l`") == "faces-insightface-buffalo_l"
    assert wiki.slug("1.7.1 (2026-11-12)") == "171-2026-11-12"
    assert wiki.slug("A person's page") == "a-persons-page"
    assert [a for _, _, a in wiki.headings("## Tips\n\n```\n## not one\n```\n\n### Tips\n")] == ["tips", "tips-1"]


def test_a_long_page_of_many_sections_lists_them_first():
    sections = "".join(f"## Part {n}\n\n{'Words. ' * 120}\n\n" for n in range(1, 6))
    listed = wiki.contents("The start.\n\n" + sections)
    assert listed.startswith("The start.\n\n**On this page:** [Part 1](#part-1) · [Part 2](#part-2) · ")
    assert "[Part 5](#part-5)\n\n## Part 1\n" in listed
    assert wiki.contents("The start.\n\n" + sections[:sections.index("## Part 5")]).count("On this page") == 0


def test_writing_replaces_the_pages(tmp_path):
    (tmp_path / "Old-page.md").write_text("gone", encoding="utf-8")
    (tmp_path / "picture.png").write_bytes(b"kept")
    wiki.write({"Home": "Hello\n"}, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Home.md", "picture.png"]
    assert (tmp_path / "Home.md").read_bytes() == b"Hello\n"
