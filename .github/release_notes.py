"""A release's notes on GitHub: its version's section of CHANGELOG.md, which CI copies onto the release of a tag.

    python .github/release_notes.py 1.6.0 [--repo owner/name]

prints that version's section, without its heading and with each paragraph and list item on one line, and with --repo
a link to every commit since the release before it. A pre-release (1.6.0-rc.1) takes its own section if there is one, or else that of the version it leads up to.
Without a section, or with an empty one, it says so and exits with 1, so CI refuses the tag before publishing images.
"""
import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

# "## 1.6.0 (2026-11-11)"; any other "## " heading (such as "## Unreleased") holds notes not yet released
HEADING = re.compile(r"## v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)(?=\s|$)")


def sections(text: str) -> list[tuple[str, str]]:
    """Each released version's (version, notes), in the changelog's order: the newest first."""
    found: list[tuple[str | None, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            match = HEADING.match(line)
            found.append((match.group(1) if match else None, []))
        elif found:
            found[-1][1].append(line)
    return [(version, "\n".join(lines).strip()) for version, lines in found if version]


def unwrapped(text: str) -> str:
    """Each paragraph and list item on one line. The changelog is wrapped like the rest of the docs, but a release's
    notes on GitHub show every line break as one, which would break its lines part way across the page."""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        fence = line.lstrip().startswith("```")
        starts_block = fence or not line.strip() or re.match(r"\s*([#>|]|[-*+] |\d+[.)] )", line)
        after_block = not out or not out[-1].strip() or out[-1].lstrip().startswith(("#", "|", "```"))
        if fenced or starts_block or after_block:
            out.append(line)
        else:
            out[-1] += " " + line.strip()
        if fence:
            fenced = not fenced
    return "\n".join(out)


def notes(version: str, text: str, repo: str | None = None) -> str:
    """The release notes of `version` (no "v"), from a changelog's `text`. LookupError when it has none."""
    listed = sections(text)
    versions = [v for v, _ in listed]
    wanted = version if version in versions else version.split("-")[0]
    if wanted not in versions:
        raise LookupError(f"CHANGELOG.md has no section for {version}: add one, headed '## {wanted} (date)'")
    at = versions.index(wanted)
    body = unwrapped(listed[at][1])
    if not body:
        raise LookupError(f"CHANGELOG.md's section for {wanted} is empty")
    if repo:
        before = next((v for v in versions[at + 1:] if "-" not in v), None)
        link = (f"https://github.com/{repo}/compare/v{before}...v{version}" if before
                else f"https://github.com/{repo}/commits/v{version}")
        body += f"\n\n**Full Changelog**: {link}"
    return body + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a version's release notes from CHANGELOG.md.")
    parser.add_argument("version", help="the version, such as 1.6.0 (a leading v is dropped)")
    parser.add_argument("--repo", help="owner/name on GitHub, to end with a link to the commits since the last release")
    args = parser.parse_args()
    try:
        text = notes(args.version.removeprefix("v"), CHANGELOG.read_text(encoding="utf-8"), args.repo)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
