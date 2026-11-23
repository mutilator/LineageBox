"""The checks git runs before each commit (.githooks/check_commit.py): no secrets, private words or files that stay on
this machine. The made-up secrets here are put together while the test runs, so that this file passes the check."""
import importlib.util
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_commit", ROOT / ".githooks" / "check_commit.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

GITHUB = "gh" + "p_" + "A1b2" * 9
AWS = "AK" + "IA" + "QWERTYUIOPASDFGH"
ANTHROPIC = "sk-" + "ant-" + "x9Yz" * 10
GOOGLE = "AI" + "za" + "Sy" + "B7" * 16 + "c"
OAUTH = "GOC" + "SPX-" + "a1B2" * 7
KEY_BODY = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB"
PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + KEY_BODY + "\n-----END " + "OPENSSH PRIVATE KEY-----\n"
URL_PASSWORD = "Tr0ub4dor3"
ASSIGNED = "q8Zr2Lm4Np6Tx0Vb"
ENV_SECRET = "h7Gk2pQ9zW"


def rules(found):
    return sorted({f.rule for f in found})


@pytest.mark.parametrize("path, rule", [
    (".env", "env-file"), ("deploy/.env", "env-file"), ("router.env", "env-file"), (".env.local", "env-file"),
    ("scratch/try.py", "local-only"), (".claude/rules/web.md", "local-only"), ("CLAUDE.md", "local-only"),
    (".vscode/settings.json", "local-only"), ("data/lineagebox.db", "local-only"),
    ("photos/IMG_0001.JPG", "local-only"), ("config.toml", "local-only"),
    ("id_ed25519", "key-file"), ("certs/site.pem", "key-file"), ("deploy/home.key", "key-file"),
    ("client_secret_123.json", "key-file"), (".netrc", "key-file"),
    ("backups/lineagebox.db", "catalog"), ("lineagebox.db-wal", "catalog"), ("catalog.sqlite", "catalog"),
    ("worker.log", "log"), ("photos.zip", "archive"), ("backup.tar.gz", "archive"),
    ("models/buffalo_l/det_10g.onnx", "model"),
    ("tests/fixtures/DSC_0042.NEF", "picture-elsewhere"), ("lineagebox/holiday.mp4", "picture-elsewhere"),
    ("README-picture.jpg", "picture-elsewhere"), ("tests/voice.m4a", "picture-elsewhere"),
])
def test_files_that_stay_on_this_machine_are_refused(path, rule):
    assert cc.never(path)[0] == rule


@pytest.mark.parametrize("path", [
    ".env.example", "config.example.toml", "compose.yaml", "data/.gitignore", "photos/.gitignore",
    "docs/screenshots/home.jpg", "lineagebox/web/static/icons/icon-192.png", "lineagebox/setup/static/icon.svg",
    "lineagebox/data/towns.tsv.gz", "lineagebox/environment.py", "docs/keys.md", "tests/test_backup.py",
])
def test_the_repository_s_own_files_may_be_committed(path):
    assert cc.never(path) is None


def test_secrets_are_found_but_never_shown():
    text = "\n".join([
        f"token = {GITHUB}",
        f"aws = {AWS}",
        f"claude = {ANTHROPIC}",
        f"maps = {GOOGLE}",
        f"oauth = {OAUTH}",
        PRIVATE_KEY,
        f"share = smb://nas-user:{URL_PASSWORD}@nas.example.com/photos",
        f'api_key = "{ASSIGNED}"',
        f"ROUTER_PASSWORD={ENV_SECRET}",
    ])
    found = cc.scan_text("notes.txt", text, cc.Private())
    assert rules(found) == ["api-key", "assigned-secret", "aws-key", "github-token", "google-key", "google-secret",
                            "password-in-url", "private-key"]
    assert {"notes.txt:11", "notes.txt:12"} <= {f.where for f in found if f.rule == "assigned-secret"}
    shown = " ".join(f.what for f in found)
    for secret in (GITHUB, AWS, ANTHROPIC, GOOGLE, OAUTH, KEY_BODY[:20], URL_PASSWORD, ASSIGNED, ENV_SECRET):
        assert secret not in shown
    assert "notes.txt:6" in {f.where for f in found if f.rule == "private-key"}
    assert "ROUTER_PASSWORD=" in shown and "api_key" in shown  # what it is called, so it can be found


@pytest.mark.parametrize("line", [
    'password = "your-password-here"',
    "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    'git clone "https://x-access-token:${GH_TOKEN}@github.com/owner/repo.wiki.git"',
    "smb://user:password@nas.example.com/share",
    'key.write_text("-----BEGIN ' + 'PRIVATE KEY-----\\nbroken\\n-----END PRIVATE KEY-----\\n")',
    'secret = base64.b32encode(b"12345678901234567890").decode()',
    'GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"',
    'TOKEN = "lineagebox.device_token"',
    "LINEAGEBOX_ACCOUNTS_GOOGLE_CLIENT_SECRET=",
    "ROUTER_PASSWORD=changeme",
])
def test_what_only_looks_like_a_secret_passes(line):
    assert cc.scan_text("x.py", line, cc.Private()) == []


def test_emails_must_be_made_up_or_public():
    real = "aunt.edna" + "@" + "gmail.com"
    text = f"cora@example.com x@demo.invalid sam@mail.example.org icon@2x.png scott@g-tech.dev {real}"
    found = cc.scan_text("a.md", text, cc.Private())
    assert [f.rule for f in found] == ["email"] and real in found[0].what


def test_a_line_can_be_allowed_but_never_a_private_word():
    pv = cc.Private(words=["Quill*"])
    assert cc.scan_text("t.py", f"made_up = '{GITHUB}'  # commit-check: allow", pv) == []
    found = cc.scan_text("t.py", "Ada Quill's photos  # commit-check: allow", pv)
    assert [(f.rule, f.where) for f in found] == [("private-word", "t.py:1")]


def test_private_words_values_and_paths_come_from_this_machine(tmp_path):
    env, secret = tmp_path / "other.env", "Zx81-Spectrum"
    env.write_text(f"NAS_PASSWORD='{secret}'\nPORT=8000\nPHOTOS=./photos\n# NOTE=not-a-value-at-all\n")
    listing = tmp_path / "private-words"
    listing.write_text("# the family\nQuill*\nrivendale.lan   # the NAS\nab\n\nallow: Copyright 2026 Ada Quill\n"
                       "known: tests/Old_Story.py the Quill farm\n"
                       f"path: notes/\npath: *.plan.md\nenv: {env}\n")
    pv = cc.load_private(listing, env=tmp_path / "missing.env")
    assert pv.words == ["Quill*", "rivendale.lan"]  # "ab" is too short to look for
    assert pv.values == {"Zx81-Spectrum": "other.env NAS_PASSWORD"}  # not a port, nor what .env.example says
    text = ("The QUILLS came over.\nAquilla stayed home.\nCopyright 2026 Ada Quill\nhttp://rivendale.lan:8000\n"
            "pass: Zx81-Spectrum\n")
    found = cc.scan_text("story.md", text, pv)
    assert [(f.rule, f.where) for f in found] == [("private-word", "story.md:1"), ("private-word", "story.md:4"),
                                                  ("env-value", "story.md:5")]
    assert "Zx81" not in " ".join(f.what for f in found)
    # published before, so left as it is in that file; anywhere else, or anything new beside it, is still refused
    assert cc.scan_text("tests/old_story.py", "At THE QUILL FARM.\n", pv) == []
    assert [f.where for f in cc.scan_text("tests/old_story.py", "the Quill farm, the Quills\n", pv)] == [
        "tests/old_story.py:1"]
    assert [f.where for f in cc.scan_text("tests/new_story.py", "the Quill farm\n", pv)] == ["tests/new_story.py:1"]
    assert cc.private_path("notes/2026.md", pv) and cc.private_path("docs/trip.plan.md", pv)
    assert not cc.private_path("docs/notes.md", pv)
    assert [f.rule for f in cc.check([("docs/quill-family.md", b"hello")], pv)] == ["private-word"]


def test_a_picture_that_says_where_it_was_taken_is_refused():
    Image = pytest.importorskip("PIL.Image")
    plain, placed = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (8, 8)).save(plain, "JPEG")
    exif = Image.Exif()
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 26.0, 46.0), 3: "W", 4: (79.0, 58.0, 56.0)})
    Image.new("RGB", (8, 8)).save(placed, "JPEG", exif=exif)
    files = [("docs/screenshots/plain.jpg", plain.getvalue()), ("docs/screenshots/placed.jpg", placed.getvalue())]
    assert [(f.rule, f.where) for f in cc.check(files, cc.Private())] == [("gps", "docs/screenshots/placed.jpg")]


def test_a_file_over_the_limit_is_refused():
    found = cc.check([("lineagebox/data/big.tsv", cc.MAX_BYTES + 1)], cc.Private())
    assert [f.rule for f in found] == ["large-file"]


def test_a_commit_message_is_checked_without_its_comments():
    text = f"Fix the reader\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n# {GITHUB}\n"
    assert cc.scan_text("commit message", cc.message_text(text), cc.Private()) == []
    found = cc.scan_text("commit message", cc.message_text(f"Use {GITHUB}\n"), cc.Private())
    assert rules(found) == ["github-token"]


def _git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_hooks_stop_a_commit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / ".githooks", repo / ".githooks", ignore=shutil.ignore_patterns("__pycache__"))
    for hook in ("pre-commit", "commit-msg"):
        (repo / ".githooks" / hook).chmod(0o755)  # git skips a hook it can't run, and the commit goes through
    assert _git(repo, "init", "-q").returncode == 0
    for key, value in (("user.name", "Ann Miller"), ("user.email", "ann@example.com"),
                       ("core.hooksPath", ".githooks"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    monkeypatch.setenv("COMMIT_CHECK_PYTHON", Path(sys.executable).as_posix())
    monkeypatch.delenv("COMMIT_CHECK_SKIP", raising=False)
    (repo / "notes.txt").write_text("Photos of the Millers.\n")
    _git(repo, "add", "notes.txt")
    ok = _git(repo, "commit", "-q", "-m", "Notes")
    assert ok.returncode == 0, ok.stderr
    (repo / ".env").write_text(f"ROUTER_PASSWORD={ENV_SECRET}\n")
    _git(repo, "add", ".env")
    refused = _git(repo, "commit", "-q", "-m", "Settings")
    assert refused.returncode != 0 and "[env-file]" in refused.stderr and ENV_SECRET not in refused.stderr
    _git(repo, "rm", "-q", "--cached", ".env")
    (repo / "notes.txt").write_text("Photos of the Bakers.\n")
    _git(repo, "add", "notes.txt")
    told = _git(repo, "commit", "-q", "-m", f"Use {GITHUB}")
    assert told.returncode != 0 and "[github-token]" in told.stderr and GITHUB not in told.stderr
    assert _git(repo, "commit", "-q", "-m", "More notes").returncode == 0
    assert _git(repo, "log", "--format=%s").stdout.split() == ["More", "notes", "Notes"]


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_the_hooks_are_committed_as_programs():
    """On Linux and macOS git skips a hook without its executable bit, silently: add them with git add --chmod=+x."""
    modes = cc.git("ls-files", "-s", ".githooks/pre-commit", ".githooks/commit-msg").splitlines()
    assert all(line.startswith("100755") for line in modes), modes


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_the_tracked_files_pass():
    """Every tracked file as it is now. On a machine with a private list and an .env, against those too."""
    found = cc.check(cc.tracked(), cc.load_private())
    assert not found, "\n".join(f"{f.where}: {f.what} [{f.rule}]" for f in found)
