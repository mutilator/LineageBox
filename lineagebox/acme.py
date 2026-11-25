"""A certificate from Let's Encrypt, by its DNS check (ACME, RFC 8555), for a free address (address.py).

This machine makes both keys and keeps them: the account's, which proves to Let's Encrypt that the same server asks
again, and the certificate's, which never leaves the data folder. Let's Encrypt asks for a TXT record proving that
whoever asks controls the name; the free-address service publishes it (address.py hands `publish` and `unpublish` in),
and the name's CAA record lets only this account get a certificate for it. Nothing else of the protocol is needed:
one name, ES256 keys (P-256), no email.
"""
import base64
import hashlib
import json
import time

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

from lineagebox import __version__, errors

STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
WAIT = 90           # seconds Let's Encrypt is given to check, then to sign
BUSY_TRIES = 5      # "Service busy; retry later" (503) and the like: asked again this many times in all
BUSY_WAIT = 30      # seconds waited at most between those tries (Retry-After, when it says)
SETTLE = 10         # seconds after the TXT record is published before Let's Encrypt is asked to look
UA = f"LineageBox/{__version__} (free address)"


class Problem(errors.Problem):
    """Let's Encrypt refused, or couldn't be reached; the message says why."""


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def new_key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def lifetime(cert_pem: str) -> tuple[float, float]:
    """When a certificate starts and ends, in seconds since 1970 (its first, in a chain)."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return cert.not_valid_before_utc.timestamp(), cert.not_valid_after_utc.timestamp()


def busy(r: httpx.Response) -> bool:
    """Let's Encrypt answered that it can't now, but may soon: worth asking again after a wait."""
    return r.status_code in (500, 502, 503, 504)


def wait(r: httpx.Response, attempt: int) -> None:
    try:
        seconds = float(r.headers.get("retry-after", ""))
    except ValueError:
        seconds = 5.0 * (attempt + 1)
    time.sleep(min(BUSY_WAIT, max(1.0, seconds)))


class Client:
    def __init__(self, directory: str, account_key_pem: str, kid: str = "", http: httpx.Client | None = None,
                 settle: float = SETTLE):
        self.key = serialization.load_pem_private_key(account_key_pem.encode(), None)
        self.kid, self.settle = kid, settle
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": UA})
        self.nonce = ""
        for attempt in range(BUSY_TRIES):
            try:
                r = self.http.get(directory)
                if busy(r) and attempt < BUSY_TRIES - 1:
                    wait(r, attempt)
                    continue
                r.raise_for_status()
                self.directory = r.json()
                break
            except (httpx.HTTPError, ValueError) as e:
                raise Problem(f"Let's Encrypt could not be reached ({type(e).__name__}). Try again in a few "
                              "minutes.") from None

    # ---- signing ---------------------------------------------------------------------------------------------------

    def jwk(self) -> dict:
        n = self.key.public_key().public_numbers()
        return {"crv": "P-256", "kty": "EC", "x": b64(n.x.to_bytes(32, "big")), "y": b64(n.y.to_bytes(32, "big"))}

    def thumbprint(self) -> str:
        return b64(hashlib.sha256(json.dumps(self.jwk(), sort_keys=True, separators=(",", ":")).encode()).digest())

    def dns_value(self, token: str) -> str:
        """The TXT record's text for a challenge's token: the key authorization's SHA-256 (RFC 8555 8.4)."""
        return b64(hashlib.sha256(f"{token}.{self.thumbprint()}".encode()).digest())

    def _sign(self, url: str, payload) -> dict:
        protected = {"alg": "ES256", "nonce": self.nonce, "url": url}
        protected.update({"kid": self.kid} if self.kid else {"jwk": self.jwk()})
        p = b64(json.dumps(protected).encode())
        body = "" if payload is None else b64(json.dumps(payload).encode())
        r, s = decode_dss_signature(self.key.sign(f"{p}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
        return {"protected": p, "payload": body, "signature": b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}

    def post(self, url: str, payload=None, accept: str = "") -> httpx.Response:
        """A signed POST (payload None: POST-as-GET). A stale nonce is tried again at once, as RFC 8555 6.5 says, and
        Let's Encrypt being busy (a 5xx: "Service busy; retry later") after a wait: BUSY_TRIES times in all."""
        for attempt in range(BUSY_TRIES):
            if not self.nonce:
                try:
                    r = self.http.head(self.directory["newNonce"])
                except httpx.HTTPError as e:
                    raise Problem(f"Let's Encrypt could not be reached ({type(e).__name__}).") from None
                if busy(r) and attempt < BUSY_TRIES - 1:
                    wait(r, attempt)
                    continue
                self.nonce = r.headers.get("replay-nonce", "")
            try:
                r = self.http.post(url, json=self._sign(url, payload),
                                   headers={"Content-Type": "application/jose+json", **({"Accept": accept} if accept
                                                                                        else {})})
            except httpx.HTTPError as e:
                raise Problem(f"Let's Encrypt could not be reached ({type(e).__name__}).") from None
            self.nonce = r.headers.get("replay-nonce", "")
            if r.status_code < 400:
                return r
            try:
                problem = r.json()
            except ValueError:
                problem = {}
            if problem.get("type", "").endswith(":badNonce") and attempt < BUSY_TRIES - 1:
                continue
            if (busy(r) or problem.get("type", "").endswith(":serverInternal")) and attempt < BUSY_TRIES - 1:
                wait(r, attempt)
                continue
            detail = problem.get("detail") or f"answer {r.status_code}"
            raise Problem(f"Let's Encrypt refused: {detail}")
        raise Problem("Let's Encrypt kept refusing this server's requests.")

    # ---- the steps -------------------------------------------------------------------------------------------------

    def account(self) -> str:
        """This key's account (made the first time), by its address: what the name's CAA record names."""
        if not self.kid:
            r = self.post(self.directory["newAccount"], {"termsOfServiceAgreed": True})
            self.kid = r.headers.get("location", "")
            if not self.kid:
                raise Problem("Let's Encrypt made no account.")
        return self.kid

    def _poll(self, url: str, done=("valid",), what: str = "the check") -> dict:
        until = time.monotonic() + WAIT
        while True:
            r = self.post(url)
            body = r.json()
            if body.get("status") in done:
                return body
            if body.get("status") == "invalid":
                reasons = [c.get("error", {}).get("detail", "") for c in body.get("challenges", []) if c.get("error")]
                raise Problem(f"Let's Encrypt turned down {what}: " + ("; ".join(r for r in reasons if r)
                                                                         or body.get("error", {}).get("detail", "")
                                                                         or "no reason given"))
            if time.monotonic() > until:
                raise Problem(f"Let's Encrypt took too long with {what}. Try again in a few minutes.")
            time.sleep(min(10.0, max(1.0, float(r.headers.get("retry-after", "2") or 2))))

    def certificate(self, host: str, publish, unpublish) -> tuple[str, str]:
        """A certificate for host: (the chain as PEM, its new key as PEM). publish(value) puts the TXT record up,
        unpublish() takes it down again, whatever happens."""
        self.account()
        r = self.post(self.directory["newOrder"], {"identifiers": [{"type": "dns", "value": host}]})
        order, order_url = r.json(), r.headers.get("location", "")
        for authz_url in order.get("authorizations", []):
            authz = self.post(authz_url).json()
            if authz.get("status") == "valid":
                continue  # checked lately: Let's Encrypt keeps it a while
            challenge = next((c for c in authz.get("challenges", []) if c.get("type") == "dns-01"), None)
            if challenge is None:
                raise Problem("Let's Encrypt offered no DNS check for this name.")
            publish(self.dns_value(challenge["token"]))
            try:
                time.sleep(self.settle)  # for every one of the DNS host's servers to have it
                self.post(challenge["url"], {})
                self._poll(authz_url, what=f"the DNS check of {host}")
            finally:
                unpublish()
        key_pem = new_key_pem()
        key = serialization.load_pem_private_key(key_pem.encode(), None)
        csr = (x509.CertificateSigningRequestBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
               .sign(key, hashes.SHA256()))
        self.post(order["finalize"], {"csr": b64(csr.public_bytes(serialization.Encoding.DER))})
        order = self._poll(order_url, what="the certificate")
        chain = self.post(order["certificate"], accept="application/pem-certificate-chain").text
        if "BEGIN CERTIFICATE" not in chain:
            raise Problem("Let's Encrypt's certificate could not be read.")
        return chain, key_pem
