"""Settings, from these places, each overriding the one before:
1. the defaults below;
2. config.toml in the project root, or the file named by LINEAGEBOX_CONFIG (in Docker, the image's docker/config.toml);
3. config.toml in the data folder, if there is one (handy on a NAS: no need to touch the container);
4. environment variables named after the settings: LINEAGEBOX_<SECTION>_<KEY>, e.g. LINEAGEBOX_OLLAMA_URL for [ollama]
   url, LINEAGEBOX_MEDIA_ROOT for media_root. Lists are comma-separated; network_paths are folder=path,folder=path.

Everything people change on the web UI's Settings page (watched folders, schedule, Ollama) is kept in the catalog
instead (see settings.py); all of the above only gives those their starting values.
"""
import dataclasses
import ipaddress
import os
import re
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"
PRIVATE_NETWORKS = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10")
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)  # Monday is 0
# environment variables read elsewhere
NOT_SETTINGS = {"LINEAGEBOX_CONFIG", "LINEAGEBOX_FFMPEG", "LINEAGEBOX_HOST", "LINEAGEBOX_PORT"}


# Whole-number settings and the values that work, here and on the Settings page (settings.py)
RANGES = {"ingest_threads": (1, 16), "worker_interval_minutes": (5, 1440), "llm_workers": (1, 8),
          "session_days": (1, 365), "upload_max_gb": (1, 100), "video_threads": (1, 8), "copy_threads": (1, 32),
          "hover_preview_ms": (0, 5000), "backup_keep_daily": (1, 90), "backup_keep_weekly": (0, 104),
          "demo_uploads": (0, 1000), "demo_upload_max_mb": (1, 4096)}


class ConfigError(ValueError):
    """A setting that is missing or cannot be used."""


@dataclass(frozen=True)
class Config:
    share_root: Path                                  # the media root (media_root in config.toml): the folder every
                                                      # photo source is in; catalog paths start here
    data_dir: Path
    folders: tuple[str, ...] = ()                     # watched folders, relative to share_root, '/' separated
    # JPEG, PNG, TIFF, HEIC (iPhones) and cameras' raw files: every kind media.py reads (tests/test_media.py checks)
    image_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".tif", ".tiff", ".heic", ".heif",
                                         ".hif", ".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2",
                                         ".dng", ".raf", ".orf", ".rw2", ".rwl", ".pef", ".srw", ".3fr", ".iiq",
                                         ".mos", ".mef", ".mrw", ".erf", ".kdc", ".dcr")
    video_extensions: tuple[str, ...] = (".mts", ".m2ts", ".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".avi", ".wmv",
                                         ".mpg", ".mpeg", ".mkv")
    # Sound alone: digitised cassettes, voice memos, recorders' files (media 'audio'; a "video" file with no picture,
    # like a phone's .3gp voice note, is one too). A song tagged as one from an album is left out (ingest.py).
    audio_extensions: tuple[str, ...] = (".mp3", ".m4a", ".aac", ".wav", ".wma", ".ogg", ".oga", ".opus", ".flac",
                                         ".amr", ".aif", ".aiff")
    ignore_dirs: tuple[str, ...] = ("Thumbs", "Previews", ".thumbnails")
    ingest_threads: int = 8
    video_threads: int = 2                            # videos read at a time (of ingest_threads): each is a long read
    thumb_size: int = 400
    preview_size: int = 2048
    describe_enabled: bool = True                     # AI descriptions and folder names (Ollama)
    transcribe_enabled: bool = True                   # transcripts of videos (Whisper)
    faces_enabled: bool = True                        # finding and recognizing faces
    places_lookup: bool = False                       # find place names on the map with OpenStreetMap (sends them)
    sidecars_enabled: bool = False                    # XMP sidecars of names and captions beside the photos (sidecars.py)
    import_enabled: bool = True                       # names, tags and dates from other apps' metadata (imports.py)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "ministral-3"
    llm_workers: int = 3
    llm_image_size: int = 1024
    ollama_keep_alive: str = "30m"
    ollama_embed_model: str = ""                      # search by meaning (embeddings.py), e.g. "nomic-embed-text";
                                                      # "" finds words only
    models_dir: Path = Path.home() / ".insightface"   # holds models/buffalo_l
    share_path: str = ""                              # share_root as people's PCs see it; default share_root
    # Folders of the media root that people's PCs find somewhere else, most often one per source:
    # (folder, path), e.g. ("nas", "\\\\nas\\photos"), for "Copy network path" and pasting a folder to watch.
    network_paths: tuple[tuple[str, str], ...] = ()
    allowed_networks: tuple[str, ...] = PRIVATE_NETWORKS
    allowed_hosts: tuple[str, ...] = ()               # host names to answer to besides addresses and LAN-only names
    trusted_proxies: tuple[str, ...] = ()             # reverse proxies whose X-Forwarded-For/-Proto are believed
    tls_cert: str = ""                                # HTTPS: the certificate and key files (PEM); "" serves HTTP
    tls_key: str = ""
    hover_preview_ms: int = 500                       # pointing at a photo this long shows it larger (app.js); 0: never
    language: str = ""                                # pages' language for browsers asking none installed (i18n.py)
    # Family accounts (accounts.py, access.py): off, the web UI answers the home network only, with no sign-in
    accounts_enabled: bool = False
    google_client_id: str = ""                        # Sign in with Google: the OAuth client's ID and secret
    google_client_secret: str = ""
    public_url: str = ""                              # https://photos.example.com: where Google sends people back to
    session_days: int = 90                            # signed in for this long since they last came
    uploads_folder: str = "Family uploads"            # members' uploads: <watched folder>/<this>/<person id>
    uploads_into: str = ""                            # the watched folder that holds it; "" for the first one
    upload_max_gb: int = 4                            # the most one uploaded file may be
    # Updates (updates.py): once a day the newest release's version number is asked of GitHub, for admins to see
    update_check: bool = True
    update_repository: str = "mutilator/LineageBox"   # owner/name on GitHub; a fork's Docker image names its own
    # Backups (backup.py): a checked copy of the catalog once a day, kept for weeks
    backup_enabled: bool = True
    backup_folder: str = ""                           # "" for the data folder's backups/nightly
    backup_keep_daily: int = 7                        # the newest copy of each of the last 7 days...
    backup_keep_weekly: int = 4                       # ...and of each of 4 weeks before those
    worker_interval_minutes: int = 60
    describe_schedule: str = "always"                 # always | hours | paused
    describe_from: str = "22:00"                      # with "hours": local time, may run past midnight
    describe_until: str = "07:00"
    describe_days: tuple[int, ...] = EVERY_DAY        # days a describing window starts on
    blurry_below: float = 30.0                        # sharpness cut-off for the Blank and blurry list
    turn_suggest: str = "sure"                        # Turned photos: sure | more | off (see orientation.py)
    whisper_url: str = ""                             # the speech-to-text service (whisper_service/); "" = none
    whisper_language: str = "auto"                    # what is spoken: a code (en, de) or auto, Whisper's own guess
    copy_threads: int = 4                             # ffmpeg threads per 720p copy
    southern_hemisphere: bool = False                 # seasons six months on: summer is December to February
    day_first: bool = False                           # 5/6/1990 is 5 June, not May 6 (dates typed, folder names)
    library_about: str = ""                           # whose photos these are, e.g. "a family in Oregon, USA": helps
                                                      # the AI read place names in folder names
    # OpenStreetMap, for the world map (places.py, map.html). OpenStreetMap may ask an app to move elsewhere at any
    # time, so both can be pointed at another server (a Nominatim of your own, or a tile provider) without an update.
    places_url: str = "https://nominatim.openstreetmap.org/search"
    places_contact: str = ""                          # an email Nominatim may write to about this install's lookups
    map_tiles: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_attribution: str = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    # A public demo (demo.py, docs/demo.md): anyone signs in with one click, as demo_person; reset every day
    demo_enabled: bool = False
    demo_person: str = ""                             # who visitors are in the family tree: a name, or a person's id
    demo_uploads: int = 5                             # uploads from each address until the next reset (0: none)
    demo_upload_max_mb: int = 10                      # the most one of them may be
    demo_resets: str = "every day"                    # when, for the note on every page: "every day at 4:00 UTC"
    demo_preview: str = ""                            # the photo link previews show: its path, e.g. "local/a.jpg"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lineagebox.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    def thumb_path(self, sha256: str) -> Path:
        return self.thumbs_dir / sha256[:2] / f"{sha256}.webp"

    def sharp_thumb_path(self, sha256: str) -> Path:
        """A tile twice the thumbnail's size, for sharp screens: made from the preview the first time one asks for it
        (web/tiles.py), never for the whole library at once."""
        return self.data_dir / "thumbs2x" / sha256[:2] / f"{sha256}.webp"

    def preview_path(self, sha256: str) -> Path:
        return self.previews_dir / sha256[:2] / f"{sha256}.jpg"

    def source_path(self, rel: str) -> Path:
        """Where a catalogued file is: its catalog path is relative to the media root."""
        return self.share_root / rel

    def frame_path(self, sha256: str, idx: int) -> Path:
        """A video's sampled frames; its preview is a copy of the clearest one."""
        return self.data_dir / "frames" / sha256[:2] / f"{sha256}_{idx:02d}.jpg"

    def picture_path(self, sha256: str, frame: int | None) -> Path:
        """What a face was found on: the photo's preview, or one of a video's frames."""
        return self.preview_path(sha256) if frame is None else self.frame_path(sha256, frame)

    def face_crop_path(self, face_id: int, x1: float, y1: float) -> Path:
        """A face's crop, named by its box as well as its id, so a face found again never shows a stale crop."""
        return self.data_dir / "faces" / f"{face_id % 100:02d}" / f"{face_id}_{x1:.4f}_{y1:.4f}.jpg"

    def aligned_face_path(self, face_id: int, x1: float, y1: float) -> Path:
        """A face lined up on its eyes (face_years.aligned), beside its crops: face_crops() finds it with them."""
        return self.face_crop_path(face_id, x1, y1).with_name(f"{face_id}_eyes_{x1:.4f}_{y1:.4f}.jpg")

    def face_crops(self, face_id: int) -> list[Path]:
        """Every crop made of a face, whatever its box was then (and its lined-up pictures)."""
        return list(self.face_crop_path(face_id, 0, 0).parent.glob(f"{face_id}_*.jpg"))

    def network_roots(self) -> list[tuple[str, str]]:
        """(folder of the media root, where people's PCs find it), deepest folder first; '' is the media root."""
        return sorted([*self.network_paths, ("", self.share_path or str(self.share_root))], key=lambda r: -len(r[0]))


# (section, key) in config.toml -> Config field; section '' is the top level. The environment variable for each is
# LINEAGEBOX_<SECTION>_<KEY> (LINEAGEBOX_<KEY> at the top level).
KEYS = {
    ("", "media_root"): "share_root", ("", "share_root"): "share_root", ("", "data_dir"): "data_dir",
    ("scan", "folders"): "folders", ("scan", "image_extensions"): "image_extensions",
    ("scan", "video_extensions"): "video_extensions", ("scan", "audio_extensions"): "audio_extensions",
    ("scan", "ignore_dirs"): "ignore_dirs",
    ("ingest", "threads"): "ingest_threads", ("ingest", "video_threads"): "video_threads",
    ("ingest", "thumb_size"): "thumb_size", ("ingest", "preview_size"): "preview_size",
    ("ollama", "enabled"): "describe_enabled", ("whisper", "enabled"): "transcribe_enabled",
    ("faces", "enabled"): "faces_enabled", ("places", "lookup"): "places_lookup",
    ("places", "url"): "places_url", ("places", "contact"): "places_contact",
    ("map", "tiles"): "map_tiles", ("map", "attribution"): "map_attribution",
    ("xmp", "sidecars"): "sidecars_enabled", ("xmp", "import"): "import_enabled",
    ("ollama", "url"): "ollama_url", ("ollama", "model"): "ollama_model", ("ollama", "workers"): "llm_workers",
    ("ollama", "image_size"): "llm_image_size", ("ollama", "keep_alive"): "ollama_keep_alive",
    ("ollama", "embed_model"): "ollama_embed_model",
    ("faces", "models_dir"): "models_dir",
    ("web", "share_path"): "share_path", ("web", "network_paths"): "network_paths",
    ("web", "allowed_networks"): "allowed_networks", ("web", "allowed_hosts"): "allowed_hosts",
    ("web", "trusted_proxies"): "trusted_proxies", ("web", "tls_cert"): "tls_cert", ("web", "tls_key"): "tls_key",
    ("web", "hover_preview_ms"): "hover_preview_ms", ("web", "language"): "language",
    ("worker", "interval_minutes"): "worker_interval_minutes",
    ("cleanup", "blurry_below"): "blurry_below", ("cleanup", "turn_suggest"): "turn_suggest",
    ("whisper", "url"): "whisper_url", ("whisper", "language"): "whisper_language",
    ("copies", "threads"): "copy_threads",
    ("library", "about"): "library_about",
    ("accounts", "enabled"): "accounts_enabled", ("accounts", "google_client_id"): "google_client_id",
    ("accounts", "google_client_secret"): "google_client_secret", ("accounts", "public_url"): "public_url",
    ("accounts", "session_days"): "session_days", ("accounts", "uploads_folder"): "uploads_folder",
    ("accounts", "uploads_into"): "uploads_into", ("accounts", "upload_max_gb"): "upload_max_gb",
    ("updates", "check"): "update_check", ("updates", "repository"): "update_repository",
    ("backups", "enabled"): "backup_enabled", ("backups", "folder"): "backup_folder",
    ("backups", "keep_daily"): "backup_keep_daily", ("backups", "keep_weekly"): "backup_keep_weekly",
    ("demo", "enabled"): "demo_enabled", ("demo", "person"): "demo_person", ("demo", "uploads"): "demo_uploads",
    ("demo", "upload_max_mb"): "demo_upload_max_mb", ("demo", "resets"): "demo_resets",
    ("demo", "preview"): "demo_preview",
}
_SECTIONS = {s for s, _ in KEYS if s}
_TYPES = {f.name: f.type for f in dataclasses.fields(Config)}


def load(path: str | os.PathLike | None = None) -> Config:
    named = path or os.environ.get("LINEAGEBOX_CONFIG")
    values = _read(Path(named)) if named else _read(DEFAULT_PATH) if DEFAULT_PATH.is_file() else {}
    env = _from_environment()
    data_dir = env.get("data_dir", values.get("data_dir"))
    if data_dir is not None and (extra := Path(data_dir) / "config.toml").is_file():
        if not named or extra.resolve() != Path(named).resolve():
            local = _read(extra)
            local.pop("data_dir", None)  # it is found through data_dir: it cannot move it
            values.update(local)
    values.update(env)

    for needed, env_name in (("share_root", "LINEAGEBOX_MEDIA_ROOT"), ("data_dir", "LINEAGEBOX_DATA_DIR")):
        if needed not in values:
            name = "media_root" if needed == "share_root" else needed
            raise ConfigError(f"{name} is not set: give it in config.toml or as {env_name}")
    proxies = []
    for proxy in values.get("trusted_proxies", ()):
        try:
            net = ipaddress.ip_network(proxy, strict=False)
        except ValueError:
            raise ConfigError(f"trusted_proxies: {proxy!r} is not an address or network (e.g. 172.18.0.0/16)") from None
        # uvicorn reads "172.18.0.1/16" (host bits set) as a name that never matches: give it the network
        proxies.append(str(net.network_address) if net.num_addresses == 1 else str(net))
    if "trusted_proxies" in values:
        values["trusted_proxies"] = tuple(proxies)
    for field, (low, high) in RANGES.items():  # a value the Settings page wouldn't take would stop a stage each pass
        if field in values and not low <= values[field] <= high:
            raise ConfigError(f"{field} is {values[field]}: it can be {low} to {high}")
    for field, example in (("ollama_url", "http://192.168.1.10:11434"), ("whisper_url", "http://192.168.1.10:9300")):
        if values.get(field) and (problem := url_problem(values[field])):
            raise ConfigError(f"{field}: {values[field]!r} {problem} (like {example})")
    if "whisper_language" in values and not language_ok(values["whisper_language"]):
        raise ConfigError(f"whisper_language is {values['whisper_language']!r}: it can be auto, or a language's "
                          "two- or three-letter code like en or de")
    if bool(values.get("tls_cert")) != bool(values.get("tls_key")):
        raise ConfigError("HTTPS needs both tls_cert and tls_key")
    return Config(**values)


def language_ok(value: str) -> bool:
    """What the Whisper service takes as the language spoken (whisper_service/app.py): auto, or a code."""
    return bool(re.fullmatch(r"[a-z]{2,3}|auto", value))


def url_problem(url: str) -> str | None:
    """What is wrong with a service's address (Ollama, Whisper), in words; None when it can be used."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port  # a port that isn't a number from 1 to 65535 is refused here
    except ValueError:
        return "has a port that isn't a number from 1 to 65535"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "isn't an address starting with http:// and a machine's name or address"
    if parts.path.strip("/") or parts.query or parts.fragment or port == 0:
        return "should be only the machine and port, with nothing after them"
    return None


def _read(path: Path) -> dict:
    """A config file's settings as Config fields."""
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"{path} can't be read: {e.strerror or e}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} isn't written right: {e}. Each setting is a line like  key = \"value\"") from None
    out = {}
    for name, value in raw.items():
        if name in _SECTIONS and isinstance(value, dict):
            for key, v in value.items():
                _put(out, name, key, v, f"[{name}] {key} in {path}")
        else:
            _put(out, "", name, value, f"{name} in {path}")
    return out


def _from_environment() -> dict:
    out = {}
    for name, value in os.environ.items():
        if not name.startswith("LINEAGEBOX_") or name in NOT_SETTINGS or not value.strip():
            continue  # an empty variable is unset: compose passes ${X:-} through as ""
        key = name[len("LINEAGEBOX_"):].lower()
        section = next((s for s in _SECTIONS if key.startswith(s + "_")), "")
        _put(out, section, key[len(section) + 1:] if section else key, value.strip(), name)
    return out


def _put(out: dict, section: str, key: str, value, where: str) -> None:
    field = KEYS.get((section, key))
    if field is None:
        print(f"LineageBox: ignoring unknown setting {where}", file=sys.stderr)
        return
    try:
        out[field] = _convert(field, value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{where}: {e}") from None


def _convert(field: str, value):
    kind = _TYPES[field]
    if kind is Path:
        return Path(value)
    if kind is bool:
        if isinstance(value, bool):
            return value
        if (text := str(value).strip().lower()) in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"expected true or false, not {value!r}")
    if kind in (int, float):
        return kind(value)
    if kind is str:
        value = str(value)
        return value.rstrip("/") if field in ("ollama_url", "whisper_url") else value
    if kind == tuple[str, ...]:
        items = [str(v).strip() for v in (value.split(",") if isinstance(value, str) else value)]
        items = [v for v in items if v]
        if field in ("image_extensions", "video_extensions", "audio_extensions", "allowed_hosts"):
            items = [v.lower() for v in items]
        return tuple(items)
    if kind == tuple[tuple[str, str], ...]:  # a table, a list of pairs, or folder=path,folder=path
        if isinstance(value, str):
            value = [item.split("=", 1) for item in value.split(",") if item.strip()]
        pairs = value.items() if isinstance(value, dict) else value
        out = []
        for pair in pairs:
            if len(pair) != 2:
                raise ValueError("expected folder=path pairs, e.g. nas=\\\\nas\\photos")
            folder, where = (str(p).strip() for p in pair)
            out.append((folder.replace("\\", "/").strip("/"), where))
        return tuple(out)
    raise TypeError(f"cannot read {field} from a config")
