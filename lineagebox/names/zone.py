"""Where the names service writes its records: a DNS host's API (Cloudflare), or memory for tests.

A record set is put whole: put(name, "A", ["203.0.113.7"]) leaves exactly that, and put(name, "A", []) none. Values
are what the record says: an address for A, the text itself for TXT, a (flags, tag, value) tuple for CAA.

The zone's own records (the apex: its CAA, no-mail records and web page) are made by hand, never here: the service
writes only below a name it gave out.
"""
import threading

import httpx

TTL = 60  # seconds: an address change reaches visitors within a minute (Cloudflare's least)


class Problem(Exception):
    """The DNS host refused or couldn't be reached: worth trying again later (the caller says so)."""


class Memory:
    """A zone in a dict, for tests and for trying the service without a DNS host."""

    def __init__(self, domain: str):
        self.domain = domain
        self.sets: dict[tuple[str, str], list] = {}
        self._lock = threading.Lock()

    def get(self, name: str, kind: str) -> list:
        with self._lock:
            return list(self.sets.get((name, kind), []))

    def put(self, name: str, kind: str, values) -> None:
        with self._lock:
            values = list(values)
            if values:
                self.sets[(name, kind)] = values
            else:
                self.sets.pop((name, kind), None)

    def names(self) -> set[str]:
        with self._lock:
            return {n for n, _ in self.sets}


class Cloudflare:
    """A zone on Cloudflare, through an API token that can edit that zone's DNS only (and nothing else in the account).
    Records are DNS only (never through Cloudflare's proxy): the connection goes to the house, whose key is its own."""

    API = "https://api.cloudflare.com/client/v4"

    def __init__(self, domain: str, zone_id: str, token: str, http: httpx.Client | None = None):
        self.domain, self.zone_id = domain, zone_id
        self.http = http or httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token}"})

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, f"{self.API}/zones/{self.zone_id}/dns_records{path}", **kw)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise Problem(f"the DNS host could not be reached ({type(e).__name__})") from None
        if not body.get("success"):
            errors = "; ".join(str(e.get("message", e)) for e in body.get("errors") or []) or f"answer {r.status_code}"
            raise Problem(f"the DNS host refused: {errors}")
        return body

    def _records(self, name: str, kind: str) -> list[dict]:
        return self._call("GET", "", params={"name": name, "type": kind, "per_page": 100})["result"]

    @staticmethod
    def _value(record: dict):
        if record["type"] == "CAA":
            d = record.get("data") or {}
            return int(d.get("flags", 0)), d.get("tag"), d.get("value")
        if record["type"] == "TXT":
            text = record.get("content", "")
            return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text
        return record.get("content")

    @staticmethod
    def _body(name: str, kind: str, value) -> dict:
        body = {"type": kind, "name": name, "ttl": TTL}
        if kind == "CAA":
            flags, tag, text = value
            body["data"] = {"flags": flags, "tag": tag, "value": text}
        elif kind == "TXT":
            body["content"] = f'"{value}"'
        else:
            body["content"] = value
            body["proxied"] = False
        return body

    def get(self, name: str, kind: str) -> list:
        return [self._value(r) for r in self._records(name, kind)]

    def put(self, name: str, kind: str, values) -> None:
        want = list(values)
        for record in self._records(name, kind):
            value = self._value(record)
            if value in want:
                want.remove(value)  # already there: kept
            else:
                self._call("DELETE", f"/{record['id']}")
        for value in want:
            self._call("POST", "", json=self._body(name, kind, value))
