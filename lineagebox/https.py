"""HTTPS for the web UI without a reverse proxy: what is in a certificate, whether it can be served, and keeping one
that was uploaded.

The certificate and its key are PEM files, named in the config (tls_cert, tls_key) or on the Settings page, which can
also upload them into the data folder's certs/. The web server (cli serve) loads them when it starts, and starts again
when the Settings page changes them or a renewed certificate lands in their place (serving.py). A certificate that
cannot be loaded leaves the site on plain HTTP, with the reason on the Settings and Status pages, so a bad file never
locks anyone out.
"""
import os
import ssl
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lineagebox import errors
from lineagebox.config import Config

CERTS = "certs"          # in the data folder: uploaded certificates
MAX_PEM = 256 * 1024     # an uploaded certificate chain or key is a few KB


class Problem(errors.Problem):
    """A certificate or key that cannot be served; the message says why, for the person who chose it."""


@dataclass(frozen=True)
class Cert:
    names: tuple[str, ...]       # the names (and addresses) it is for; *.example.com for every name under it
    issuer: str
    expires: datetime | None
    chain: int                   # certificates in the file: the site's own, then the ones that vouch for it
    self_signed: bool

    @property
    def days_left(self) -> int | None:
        return None if self.expires is None else (self.expires - datetime.now()).days

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires < datetime.now()

    @property
    def needs_chain(self) -> bool:
        """Only the site's own certificate, from an authority: some browsers need the ones in between too."""
        return self.chain == 1 and not self.self_signed


def check(cert: str | os.PathLike, key: str | os.PathLike) -> Cert:
    """What a certificate is, if it and the key can be served together. Raises Problem otherwise."""
    if not cert or not key:
        raise Problem("Give both the certificate and its private key.")
    texts = {}
    for path, what in ((cert, "certificate"), (key, "key")):
        try:
            texts[what] = Path(path).read_text(encoding="ascii", errors="replace")
        except FileNotFoundError:
            raise Problem(f"There is no {what} file at {path}.") from None
        except OSError as e:
            raise Problem(f"The {what} file {path} cannot be read: {e.strerror or e}.") from None
    if "-----BEGIN CERTIFICATE-----" not in texts["certificate"]:
        raise Problem(f"{cert} is not a PEM certificate: it should start with -----BEGIN CERTIFICATE-----. "
                      "A .pfx or .p12 file has to be converted first.")
    if "PRIVATE KEY-----" not in texts["key"]:
        raise Problem(f"{key} is not a PEM private key: it should start with -----BEGIN PRIVATE KEY----- "
                      "(or RSA PRIVATE KEY, EC PRIVATE KEY).")
    if "ENCRYPTED" in texts["key"]:
        raise Problem("The key is protected by a password. Save a copy without one: "
                      "openssl pkey -in key.pem -out key-open.pem")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key, password=_no_password)
    except ssl.SSLError as e:
        if "KEY_VALUES_MISMATCH" in str(e):
            raise Problem("The key does not belong to this certificate.") from None
        if e.reason in (None, "PEM lib"):  # OpenSSL's words for a file that isn't valid PEM inside
            raise Problem("The certificate or key file is damaged: it could not be read as PEM.") from None
        raise Problem(f"The certificate and key could not be loaded: {e.reason}.") from None
    return read(cert, texts["certificate"].count("-----BEGIN CERTIFICATE-----"))


def read(path: str | os.PathLike, chain: int = 1) -> Cert:
    """The names, issuer and end date of the first certificate in a PEM file."""
    try:
        # the ssl module's own reader (used by its tests): the cryptography package isn't needed for this alone
        info = ssl._ssl._test_decode_cert(str(path))
    except (AttributeError, ssl.SSLError):
        return Cert((), "", None, chain, False)
    subject, issuer = _fields(info.get("subject", ())), _fields(info.get("issuer", ()))
    names = tuple(v.lower() for kind, v in info.get("subjectAltName", ()) if kind in ("DNS", "IP Address"))
    if not names and subject.get("commonName"):
        names = (subject["commonName"].lower(),)
    expires = info.get("notAfter")
    return Cert(names, issuer.get("organizationName") or issuer.get("commonName", ""),
                datetime.fromtimestamp(ssl.cert_time_to_seconds(expires)) if expires else None, chain,
                subject == issuer)


def about(cfg: Config) -> Cert | None:
    """The certificate set now, for the Settings page: None when HTTPS is off or its file cannot be read."""
    if not cfg.tls_cert or not Path(cfg.tls_cert).is_file():
        return None
    try:
        return read(cfg.tls_cert, Path(cfg.tls_cert).read_text(encoding="ascii", errors="replace")
                    .count("-----BEGIN CERTIFICATE-----"))
    except OSError:
        return None


def covers(names, host: str) -> bool:
    """Whether a certificate for these names is good for a request's Host (port and all). A wildcard covers one
    level: *.example.com is good for photos.example.com, not for example.com or a.b.example.com."""
    host = host.strip().lower()
    if host.startswith("["):
        host = host[1:host.find("]")]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    host = host.rstrip(".")
    for name in names:
        if name == host:
            return True
        if name.startswith("*.") and host.endswith(name[1:]) and "." not in host[:-len(name) + 1]:
            return True
    return False


def store(cfg: Config, cert_text: str, key_text: str) -> tuple[str, str]:
    """Put an uploaded certificate (with any in between) and key into the data folder's certs/, once they are known to
    work together, replacing the last ones uploaded. Returns their paths."""
    if not cert_text.strip() or not key_text.strip():
        raise Problem("Choose both files: the certificate and its private key.")
    if len(cert_text) > MAX_PEM or len(key_text) > MAX_PEM:
        raise Problem("Those files are too big to be a certificate and a key.")
    folder = cfg.data_dir / CERTS
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / "lineagebox.crt", folder / "lineagebox.key"
    new_cert, new_key = folder / ".lineagebox.crt.new", folder / ".lineagebox.key.new"
    try:
        new_cert.write_text(cert_text, encoding="ascii", errors="replace")
        fd = os.open(new_key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the key: for this user only
        with os.fdopen(fd, "w", encoding="ascii", errors="replace") as f:
            f.write(key_text)
        check(new_cert, new_key)
        os.replace(new_cert, cert)
        os.replace(new_key, key)
    finally:
        new_cert.unlink(missing_ok=True)
        new_key.unlink(missing_ok=True)
    return str(cert), str(key)


def _fields(name) -> dict[str, str]:
    return {k: v for rdn in name for k, v in rdn}


def _no_password():
    raise Problem("The key is protected by a password. Save a copy without one: "
                  "openssl pkey -in key.pem -out key-open.pem")
