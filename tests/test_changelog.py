"""CHANGELOG.md, whose section for a version CI copies onto that version's GitHub Release (.github/release_notes.py)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

import lineagebox

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "release_notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

SAMPLE = """# Changelog

## Unreleased

### New

- Not out yet.

## 1.1.0 (2026-02-01)

### Fixed

- The second.

## 1.0.0-rc.1 (2026-01-20)

### New

- A try.

## 1.0.0 (2026-01-15)

### New

- The first.

## 0.9.0 (2026-01-01)
"""


def test_this_version_has_release_notes():
    """CI refuses a version's tag without its notes, so this says so before it is tagged."""
    done = subprocess.run([sys.executable, str(SCRIPT), lineagebox.__version__, "--repo", "example/lineagebox"],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("### ")
    assert "**Full Changelog**: https://github.com/example/lineagebox/" in done.stdout


def test_a_section_and_the_link_to_what_changed_since_the_release_before():
    assert release_notes.notes("1.1.0", SAMPLE) == "### Fixed\n\n- The second.\n"
    # the release before is the last full one, not a pre-release
    assert release_notes.notes("1.1.0", SAMPLE, "example/lineagebox").endswith(
        "- The second.\n\n**Full Changelog**: https://github.com/example/lineagebox/compare/v1.0.0...v1.1.0\n")
    assert release_notes.notes("1.0.0", SAMPLE, "example/lineagebox").endswith(
        "https://github.com/example/lineagebox/compare/v0.9.0...v1.0.0\n")


def test_a_pre_release_has_its_own_notes_or_those_of_the_version_it_leads_to():
    assert release_notes.notes("1.0.0-rc.1", SAMPLE) == "### New\n\n- A try.\n"
    assert release_notes.notes("1.1.0-rc.1", SAMPLE) == "### Fixed\n\n- The second.\n"


def test_the_first_release_links_to_its_commits():
    assert release_notes.notes("1.0.0", "## 1.0.0\n\n- The first.\n", "example/lineagebox") == (
        "- The first.\n\n**Full Changelog**: https://github.com/example/lineagebox/commits/v1.0.0\n")


def test_lines_wrapped_in_the_changelog_are_one_line_on_github():
    """GitHub shows each line break in a release's notes, so a wrapped item would break part way across the page."""
    text = ("## 2.0.0\n\nA catalog update,\nwrapped.\n\n### New\n\n- **One:** wrapped\n  in two.\n- Two.\n"
            "  1. A step\n     wrapped.\n\n```\nkept\nas is\n```\n")
    assert release_notes.notes("2.0.0", text) == ("A catalog update, wrapped.\n\n### New\n\n- **One:** wrapped in two.\n"
                                                   "- Two.\n  1. A step wrapped.\n\n```\nkept\nas is\n```\n")


@pytest.mark.parametrize("version", ["1.2.0", "0.9.0"])
def test_no_notes_is_an_error(version):
    """No section for 1.2.0 (Unreleased isn't one), and 0.9.0's is empty."""
    with pytest.raises(LookupError):
        release_notes.notes(version, SAMPLE)
