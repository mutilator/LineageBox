"""Checks what is about to be committed for secrets, private words and files that must stay on this machine.

Git runs it before each commit once the repository's hooks are switched on (docs/development.md#commit-checks):

    git config core.hooksPath .githooks

    python .githooks/check_commit.py staged          what `git commit` is about to record (the pre-commit hook)
    python .githooks/check_commit.py message FILE    a commit message (the commit-msg hook)
    python .githooks/check_commit.py all             every tracked file as it is now (tests/test_commit_check.py)

It refuses:
- files that never belong in the repository: .env files, keys and certificates, databases (a catalog holds the family's
  names and faces), logs, archives, model weights, scratch/, the assistant's notes (CLAUDE.md, .claude/), the catalog
  and photo folders' contents, anything git is told to ignore (so only `git add -f` could have staged it), and photos,
  videos or sound outside the folders whose pictures ship with LineageBox;
- files over 2 MB, and pictures that carry GPS coordinates;
- secrets in what files say: private keys, the usual services' access tokens (GitHub, AWS, Google, OpenAI, Anthropic,
  Slack, Stripe, Hugging Face, npm), and passwords written into URLs or assignments;
- email addresses other than made-up ones (example.com, .invalid, .test...) and the project's public ones;
- the values in this machine's .env, and the words in its private list (.git/info/private-words, never committed:
  the family's names, its hosts and addresses). The list's format is in docs/development.md#commit-checks.

What it finds is printed with the file and line, never the secret itself. A line that has to say something it would
refuse (a made-up key in a test) can carry `commit-check: allow`; that never lets a private word or a .env value
through. COMMIT_CHECK_SKIP=large-file,email skips those rules for one commit; `git commit --no-verify` skips them all.
Standard library only (Pillow, when the Python running it has it, reads pictures' GPS), so it runs before any install.
"""
import bisect
import fnmatch
import io
import os
import re
import string
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 2 * 1024 * 1024  # the biggest file in the repository is GeoNames' towns, under 1 MB
ALLOW = "commit-check: allow"
_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
_ALNUM = frozenset(string.ascii_lowercase + string.digits)


@dataclass
class Finding:
    rule: str  # what COMMIT_CHECK_SKIP names
    where: str  # path, or path:line
    what: str  # never the secret itself


# --- Files that never belong, whatever they hold -------------------------------------------------------------------

# Folders whose pictures ship with LineageBox: the docs' screenshots and the pages' icons. A photo, video or sound
# anywhere else is most likely one of the family's, copied in by mistake.
PICTURE_FOLDERS = ("docs/", "lineagebox/web/static/", "lineagebox/setup/static/", ".github/")
MEDIA = {"jpg", "jpeg", "jpe", "png", "gif", "webp", "heic", "heif", "avif", "tif", "tiff", "bmp", "ico",
         "nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "dng", "orf", "rw2", "raf", "pef", "srw", "x3f",
         "mp4", "m4v", "mov", "avi", "mkv", "3gp", "mts", "m2ts", "wmv", "mpg", "mpeg", "webm", "vob",
         "wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "wma", "amr"}
ENV_EXAMPLES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
LOCAL_FOLDERS = {"scratch", ".claude", ".vscode", ".idea", ".venv", "venv"}
KEY_NAMES = {".netrc", "_netrc", ".pgpass", ".htpasswd", "known_hosts", "authorized_keys", "credentials.json",
             "service-account.json", ".git-credentials", ".npmrc", ".pypirc"}
KEY_EXTS = {"pem", "key", "p12", "pfx", "jks", "keystore", "ppk", "kdbx", "crt", "cer", "der", "ovpn"}
CATALOG_EXTS = {"db", "sqlite", "sqlite3", "db-wal", "db-shm", "db-journal", "bak"}
ARCHIVE_EXTS = {"zip", "7z", "rar", "tar", "tgz", "txz", "tbz2"}
MODEL_EXTS = {"onnx", "gguf", "safetensors", "pt", "pth", "ckpt", "tflite", "pkl"}


def never(path: str) -> tuple[str, str] | None:
    """(rule, reason) when a file must never be committed, whatever it holds; None when it may be."""
    p = path.lower()
    parts = p.split("/")
    name = parts[-1]
    ext = name.rpartition(".")[2] if "." in name.lstrip(".") else ""
    if name == ".env" or name.endswith(".env") or (name.startswith(".env.") and name not in ENV_EXAMPLES):
        return "env-file", "an .env file holds passwords and this machine's settings"
    if len(parts) > 1 and parts[0] in LOCAL_FOLDERS:
        return "local-only", f"{parts[0]}/ stays on this machine"
    if name in ("claude.md", "claude.local.md"):
        return "local-only", "the assistant's notes stay on this machine"
    if len(parts) > 1 and parts[0] in ("data", "photos") and name != ".gitignore":
        return "local-only", f"{parts[0]}/ is for a catalog or photos of your own"
    if p in ("config.toml", "compose.override.yaml"):
        return "local-only", "this machine's own settings"
    if name in KEY_NAMES or ext in KEY_EXTS or name.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")) \
            or (name.startswith("client_secret") and ext == "json"):
        return "key-file", "keys, certificates and password files stay private"
    if ext in CATALOG_EXTS:
        return "catalog", "a database or backup: a catalog holds the family's names and faces"
    if ext == "log":
        return "log", "logs name hosts, addresses and people"
    if ext in ARCHIVE_EXTS or name.endswith((".tar.gz", ".tar.xz", ".tar.bz2")):
        return "archive", "an archive hides what it holds from these checks"
    if ext in MODEL_EXTS:
        return "model", "model weights are downloaded, not committed"
    if ext in MEDIA and not p.startswith(PICTURE_FOLDERS):
        return "picture-elsewhere", "photos, videos and sound belong only in docs/ or the pages' static/ folders"
    return None


# --- What files say ------------------------------------------------------------------------------------------------

PLACEHOLDERS = ("example", "your", "change", "xxxx", "dummy", "fake", "placeholder", "redacted", "secret", "password",
                "<", "{", "%(", "://", "...", "…")


def _real_value(v: str) -> bool:
    """Whether an assigned value looks like a real secret: letters and digits, and no placeholder or reference."""
    low = v.lower()
    return bool(re.search(r"[a-z]", low) and re.search(r"\d", low)) and not any(s in low for s in PLACEHOLDERS) \
        and not low.startswith(("/", "./", "$"))


def _real_password(v: str) -> bool:
    low = v.lower()
    return low not in ("pass", "passwd", "pwd", "pw", "token") and not any(s in low for s in PLACEHOLDERS)


_SECRET_NAME = r"(?:password|passwd|pwd|secret|token|api_?key|apikey|access_?key|private_?key|client_?secret)"
# (rule, what it looks like, pattern, test of the value in group v). Each is matched over the whole text.
SECRETS = [
    ("private-key", "a private key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY[A-Z ]*-----\s*(?:[\w-]+:[^\n]*\n\s*)*[A-Za-z0-9+/=]{40,}"), None),
    ("aws-key", "an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("github-token", "a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
     None),
    ("google-key", "a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), None),
    ("google-secret", "a Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}"), None),
    ("api-key", "an OpenAI or Anthropic API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"), None),
    ("slack-token", "a Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), None),
    ("stripe-key", "a Stripe key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}"), None),
    ("huggingface-token", "a Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), None),
    ("npm-token", "an npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), None),
    ("password-in-url", "a password written into a URL",
     re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@\"'<>]+:(?P<v>[^\s/@\"'<>]{3,})@[\w.-]+"), _real_password),
    ("assigned-secret", "a password or key written into the code",
     re.compile(rf"(?i){_SECRET_NAME}[\w.-]*[\"']?\s*[:=]\s*[\"'](?P<v>[^\"'\s]{{12,}})[\"']"), _real_value),
    ("assigned-secret", "a password or key written into the settings",  # KEY=value, the value alone on its line
     re.compile(rf"(?im)^[ \t]*(?:export[ \t]+)?[\w.-]*{_SECRET_NAME}[\w.-]*[ \t]*[=:][ \t]*"
                rf"(?P<v>[A-Za-z0-9+/=_.~!@%^&*-]{{8,}})[ \t]*(?:#.*)?$"), _real_value),
]

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# Made-up addresses (RFC 2606 and 6761's names) and the project's public ones, which may be in the repository.
MADE_UP_DOMAINS = ("example.com", "example.org", "example.net")
MADE_UP_ENDINGS = {"example", "invalid", "test", "localhost", "local"}
PUBLIC_EMAILS = {
    "scott@g-tech.dev",  # the project's contact (SECURITY.md, the GPL's written offer)
    "abuse@lineagebox.com",  # the free addresses' abuse contact (names/server.py, ourplace.here, the terms)
    "recognition-oss-pack@insightface.ai",  # InsightFace's licensing contact (THIRD_PARTY_NOTICES.md)
    "noreply@anthropic.com", "noreply@github.com", "git@github.com",  # commit trailers and clone addresses
}
FILE_ENDINGS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "py", "html", "htm", "md", "json", "txt"}


def _public_email(address: str) -> bool:
    a = address.lower()
    domain = a.rpartition("@")[2]
    ending = domain.rpartition(".")[2]
    return a in PUBLIC_EMAILS or ending in MADE_UP_ENDINGS or ending in FILE_ENDINGS \
        or any(domain == d or domain.endswith("." + d) for d in MADE_UP_DOMAINS) \
        or domain.endswith("users.noreply.github.com")


@dataclass
class Private:
    """What this machine keeps out of the repository: its private list and the values in its .env files."""
    words: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)  # phrases that may stand ("Copyright © 2026 A. Miller")
    # (path, phrase): published before the list had its word, and left as it is, since changing it now would show
    # that it was real. It may stay in that file, and only there.
    known: list[tuple[str, str]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    # value: where it is from (".env ROUTER_PASSWORD"); never in a repr, so a failing test can't print one
    values: dict[str, str] = field(default_factory=dict, repr=False)
    _patterns: list | None = field(default=None, repr=False, compare=False)

    def patterns(self) -> list[tuple[re.Pattern, str, str, bool, bool]]:
        """(pattern, rule, label, whole word at its start, at its end) for each word and value. In a word, * is any
        letters or digits (Miller* is Miller and Millers). One plain pattern each, over the text in lower case, which
        re searches many times faster than one pattern of them all that ignores case."""
        if self._patterns is None:
            self._patterns = []
            for w, rule, label in [*((w, "private-word", "") for w in self.words),
                                   *((v, "env-value", f"a value from {where}") for v, where in self.values.items())]:
                low = w.translate(_LOWER)
                body = re.escape(low).replace(r"\*", "[a-z0-9]*")
                self._patterns.append((re.compile(body), rule, label, low[0] in _ALNUM, low[-1] in _ALNUM))
        return self._patterns


def _lines(text: str) -> list[int]:
    return [0] + [m.end() for m in re.finditer("\n", text)]


def _line(starts: list[int], text: str, pos: int) -> tuple[int, str]:
    n = bisect.bisect_right(starts, pos)
    end = text.find("\n", starts[n - 1])
    return n, text[starts[n - 1]:end if end >= 0 else len(text)]


def scan_text(where: str, text: str, private: Private) -> list[Finding]:
    """What a file (or a commit message) says that must not be committed."""
    found = []
    starts = _lines(text)
    for rule, looks, pattern, real in SECRETS:
        for m in pattern.finditer(text):
            at = m.start("v") if real else m.start()
            n, line = _line(starts, text, at)
            if ALLOW in line or (real and not real(m.group("v"))):
                continue
            # the name it is given, or the token's first letters (its kind: ghp_, AKIA, sk-a...), never the secret
            shown = text[m.start():at].strip()[-40:] if real else m.group()[:4] + "..."
            found.append(Finding(rule, f"{where}:{n}", f"looks like {looks} ({shown})"))
    for m in EMAIL.finditer(text):
        n, line = _line(starts, text, m.start())
        if ALLOW not in line and not _public_email(m.group()):
            found.append(Finding("email", f"{where}:{n}", f"a real email address ({m.group()}): use example.com"))
    found += scan_private(where, text, private, starts)
    return found


def scan_private(where: str, text: str, private: Private, starts: list[int] | None = None) -> list[Finding]:
    """The private list's words and the .env files' values, as whole words in any case (of A to Z); `commit-check:
    allow` doesn't let them through, only the list's own allow: phrases and, in their own file, its known: ones."""
    low = text.translate(_LOWER)  # the same length as text, so a place in one is the same place in the other
    for phrase in [*private.allowed, *(phrase for path, phrase in private.known if path == where.lower())]:
        phrase = phrase.translate(_LOWER)
        low = low.replace(phrase, " " * len(phrase))
    hits = []
    for pattern, rule, label, word_start, word_end in private.patterns():
        for m in pattern.finditer(low):
            s, e = m.span()
            inside = (word_start and s and low[s - 1] in _ALNUM) or (word_end and e < len(low) and low[e] in _ALNUM)
            if not inside:  # part of a longer word
                hits.append((s, -e, rule, label or f'"{text[s:e]}", from your private list'))
    starts = starts or _lines(text)
    found, reach = [], -1
    for s, end, rule, label in sorted(hits):
        if s >= reach:  # not inside a longer find (a .env value that holds a listed address)
            reach = -end
            found.append(Finding(rule, f"{where}:{_line(starts, text, s)[0]}", label))
    return found


def has_gps(data: bytes) -> bool:
    """Whether a picture says where it was taken. Without Pillow (a plain Python), pictures aren't read."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(io.BytesIO(data)) as im:
            return bool(im.getexif().get_ifd(0x8825))
    except Exception:
        return False


# --- This machine's private list and .env ----------------------------------------------------------------------------

PUBLIC_EXAMPLES = (".env.example", "config.example.toml", "docker/config.toml")
COMMON_VALUES = {"true", "false", "yes", "no", "localhost", "0.0.0.0", "127.0.0.1", "lineagebox", "changeme"}


def _env_values(path: Path) -> dict[str, str]:
    values = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        key, eq, value = line.strip().removeprefix("export ").partition("=")
        if eq and key and not key.startswith("#"):
            values[key.strip()] = value.split(" #")[0].strip().strip("'\"")
    return values


def private_list() -> Path:
    """.git/info/private-words: inside .git, so it can't be committed, and shared by every worktree."""
    out = git("rev-parse", "--git-path", "info/private-words").strip()
    return (ROOT / out) if not os.path.isabs(out) else Path(out)


def load_private(listing: Path | None = None, env: Path = ROOT / ".env") -> Private:
    """This machine's private list, and the values of .env and of each env file the list names. A value as short as
    a port number, or one the repository's own examples have, isn't private."""
    pv = Private()
    env_files = [env]
    try:
        listing = listing or private_list()
        lines = listing.read_text(encoding="utf-8").splitlines() if listing.is_file() else []
    except (OSError, subprocess.CalledProcessError):
        lines = []
    for raw in lines:
        line = raw.split(" #")[0].strip()  # a comment of its own, or after what the line says
        kind, colon, rest = line.partition(":")
        if not line or line.startswith("#"):
            continue
        if colon and kind in ("allow", "known", "path", "env"):
            rest = rest.strip()
            if kind == "allow":
                pv.allowed.append(rest)
            elif kind == "known":
                path, _, phrase = rest.partition(" ")
                if phrase.strip():
                    pv.known.append((path.lower().lstrip("/"), phrase.strip()))
            elif kind == "path":
                pv.paths.append(rest.lower().lstrip("/"))
            else:
                p = Path(os.path.expanduser(rest))
                env_files.append(p if p.is_absolute() else ROOT / p)
        elif len(line) >= 3:
            pv.words.append(line)
    public = " ".join((ROOT / f).read_text(encoding="utf-8", errors="replace")
                      for f in PUBLIC_EXAMPLES if (ROOT / f).is_file())
    for f in env_files:
        for key, value in _env_values(f).items():
            if len(value) >= 6 and value.lower() not in COMMON_VALUES and value not in public:
                pv.values.setdefault(value, f"{f.name} {key}")
    return pv


def private_path(path: str, private: Private) -> bool:
    p = path.lower()
    return any(fnmatch.fnmatchcase(p, pat) or p.startswith(pat.rstrip("/") + "/") for pat in private.paths)


# --- Git -----------------------------------------------------------------------------------------------------------

def git(*args: str, data: bytes | None = None, ok: tuple[int, ...] = (0,)) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True)
    if r.returncode not in ok:
        raise subprocess.CalledProcessError(r.returncode, ["git", *args], r.stdout, r.stderr)
    return r.stdout.decode("utf-8", "surrogateescape")


def staged() -> list[tuple[str, bytes | int]]:
    """(path, what the commit will hold) of each file the next commit adds or changes."""
    fields = git("diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", "--diff-filter=d").split("\0")
    entries = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        _, mode, _, blob, _ = meta.lstrip(":").split(" ", 4)
        if mode != "160000":  # a submodule is a commit, not a file
            entries.append((path, blob))
    contents = blobs(sorted({b for _, b in entries}))
    return [(path, contents[blob]) for path, blob in entries]


def tracked() -> list[tuple[str, bytes | int]]:
    """(path, what it holds now) of every tracked file still in the working tree: what the next commits start from."""
    entries = []
    for line in git("ls-files", "-s", "-z").split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        f = ROOT / path
        if meta.startswith("160000") or not f.is_file():
            continue
        size = f.stat().st_size
        entries.append((path, size if size > MAX_BYTES else (b"" if f.is_symlink() else f.read_bytes())))
    return entries


def ignored(paths: list[str]) -> set[str]:
    """The paths git's ignore rules (.gitignore, .git/info/exclude, your own) leave out: only git add -f stages them."""
    if not paths:
        return set()
    out = git("check-ignore", "--no-index", "-z", "--stdin", data="\0".join(paths).encode("utf-8", "surrogateescape"),
              ok=(0, 1))
    return {p for p in out.split("\0") if p}


def blobs(ids: list[str]) -> dict[str, bytes | int]:
    """Each blob's bytes, or just its size when it is over MAX_BYTES."""
    if not ids:
        return {}
    sizes = {}
    for line in git("cat-file", "--batch-check", data="\n".join(ids).encode() + b"\n").splitlines():
        blob, _, size = line.split(" ")
        sizes[blob] = int(size)
    small = [b for b in ids if sizes[b] <= MAX_BYTES]
    out = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, input="\n".join(small).encode() + b"\n",
                         capture_output=True, check=True).stdout if small else b""
    got: dict[str, bytes | int] = {b: s for b, s in sizes.items() if s > MAX_BYTES}
    pos = 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        blob, _, size = out[pos:end].decode().split(" ")
        got[blob] = out[end + 1:end + 1 + int(size)]
        pos = end + 2 + int(size)
    return got


# --- The checks ----------------------------------------------------------------------------------------------------

def check(files: list[tuple[str, bytes | int]], private: Private) -> list[Finding]:
    """What is wrong with these files: (path, contents, or the size of one too big to read)."""
    found = []
    left_out = ignored([p for p, _ in files])
    for path, data in files:
        found += scan_private(path, path, private)  # a file named after someone
        stop = never(path)
        if stop:
            found.append(Finding(stop[0], path, f"never committed: {stop[1]}"))
            continue
        if path in left_out:
            found.append(Finding("ignored", path, "git is told to ignore it (.gitignore or .git/info/exclude)"))
            continue
        if private_path(path, private):
            found.append(Finding("private-path", path, "your private list says it stays on this machine"))
            continue
        if isinstance(data, int):
            found.append(Finding("large-file", path, f"{data / 1048576:.1f} MB, more than {MAX_BYTES // 1048576} MB"))
            continue
        if b"\0" in data[:8000]:
            if path.lower().rpartition(".")[2] in MEDIA and has_gps(data):
                found.append(Finding("gps", path, "the picture says where it was taken (GPS): save it without that"))
            continue
        found += scan_text(path, data.decode("utf-8", "replace"), private)
    return found


def message_text(text: str) -> str:
    """A commit message as git keeps it: without the comment lines, or the diff that `git commit -v` adds."""
    text = text.split("# ------------------------ >8 ------------------------")[0]
    return "\n".join("" if line.startswith("#") else line for line in text.splitlines())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    what = args[0] if args else "staged"
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    private = load_private()
    if what == "message" and len(args) == 2:
        text = Path(args[1]).read_text(encoding="utf-8", errors="replace")
        found = scan_text("commit message", message_text(text), private)
    elif what in ("staged", "all"):
        found = check(staged() if what == "staged" else tracked(), private)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    skip = {s.strip() for s in os.environ.get("COMMIT_CHECK_SKIP", "").split(",") if s.strip()}
    found = [f for f in found if f.rule not in skip]
    if not found:
        return 0
    head = {"all": " in the tracked files"}.get(what, ", so nothing was committed")
    print(f"commit check: {len(found)} problem{'s' * (len(found) != 1)}{head}.", file=sys.stderr)
    for f in found:
        print(f"  {f.where}: {f.what} [{f.rule}]", file=sys.stderr)
    print("Take them out and try again. A line can carry \"commit-check: allow\" when it has to say that (not a "
          "private word or a .env value),\nCOMMIT_CHECK_SKIP=<rule>,... skips rules once, and git commit --no-verify "
          "skips every check.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
