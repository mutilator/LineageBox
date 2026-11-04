"""Host names, the reverse proxy and HTTPS, set on the Settings page; the web server following them."""
import dataclasses
import ipaddress
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lineagebox import cli, db, https, serving, settings
from lineagebox.web import app as webapp
from tests.conftest import LAN

x509 = pytest.importorskip("cryptography.x509")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

APP = {"X-Requested-With": "lineagebox"}


def make_cert(folder, names=("photos.example.com", "*.example.com"), days=90, name="site", issuer=None,
              password=None):
    """A certificate for these names and its key, as PEM files; signed by `issuer` ((cert, key)) or by itself."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "lineagebox")])
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer[0].subject if issuer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(days=200)).not_valid_after(now + timedelta(days=days)))
    if names:
        alt = [x509.IPAddress(ipaddress.ip_address(n)) if n[0].isdigit() else x509.DNSName(n) for n in names]
        builder = builder.add_extension(x509.SubjectAlternativeName(alt), critical=False)
    cert = builder.sign(issuer[1] if issuer else key, hashes.SHA256())
    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / f"{name}.crt", folder / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    encryption = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption))
    return cert_path, key_path, (cert, key)


def authority(folder):
    """A made-up certificate authority, to sign site certificates with."""
    *_, ca = make_cert(folder, names=(), name="ca", days=3650)
    return ca


def test_what_a_certificate_is_and_what_is_wrong_with_one(tmp_path):
    ca = authority(tmp_path)
    cert, key, _ = make_cert(tmp_path, issuer=ca)
    found = https.check(cert, key)
    assert found.names == ("photos.example.com", "*.example.com") and not found.self_signed
    assert 85 <= found.days_left <= 90 and not found.expired and found.needs_chain  # only the site's own

    cert.write_text(cert.read_text() + (tmp_path / "ca.crt").read_text())  # the full chain
    assert https.check(cert, key).chain == 2 and not https.check(cert, key).needs_chain
    assert https.check(*make_cert(tmp_path, name="self")[:2]).self_signed

    _, other_key, _ = make_cert(tmp_path, name="other")
    _, locked_key, _ = make_cert(tmp_path, name="locked", password=b"secret")
    for bad_cert, bad_key, says in [(cert, other_key, "does not belong"), (cert, locked_key, "password"),
                                    (tmp_path / "nowhere.crt", key, "no certificate file"),
                                    (key, key, "not a PEM certificate"), (cert, cert, "not a PEM private key"),
                                    (cert, "", "both")]:
        with pytest.raises(https.Problem, match=says):
            https.check(bad_cert, bad_key)

    old = make_cert(tmp_path, name="old", days=-1)
    assert https.check(*old[:2]).expired  # still served, with a warning: but not chosen anew
    with pytest.raises(settings.Invalid, match="expired"):
        settings._tls(str(old[0]), str(old[1]))


def test_which_names_a_certificate_is_good_for():
    names = ("photos.example.com", "*.home.example.org", "192.168.7.1")
    assert https.covers(names, "photos.example.com:8000") and https.covers(names, "192.168.7.1:8000")
    assert https.covers(names, "nas.home.example.org") and https.covers(names, "PHOTOS.example.com.")
    assert not https.covers(names, "home.example.org") and not https.covers(names, "a.b.home.example.org")
    assert not https.covers(names, "192.168.7.2") and not https.covers((), "photos.example.com")


def test_host_names_on_the_settings_page(cfg):
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert client.get("/status", headers={"host": "photos.example.com"}).status_code == 400
    r = client.post("/api/settings/address", headers=APP,
                    json={"allowed_hosts": "https://Photos.Example.com:8000/\n*.home.example.org, 192.168.7.1",
                          "trusted_proxies": ""})
    assert r.status_code == 200, r.text
    con = db.connect(cfg)
    assert settings.current(con, cfg).allowed_hosts == ("photos.example.com", "*.home.example.org")  # no address
    assert client.get("/status", headers={"host": "photos.example.com"}).status_code == 200  # straight away
    assert client.get("/status", headers={"host": "nas.home.example.org:8000"}).status_code == 200
    page = client.get("/settings").text
    assert "photos.example.com\n*.home.example.org</textarea>" in page

    # a list without the name this page was opened by would lock it out, so it isn't saved
    r = client.post("/api/settings/address", headers={**APP, "host": "photos.example.com"},
                    json={"allowed_hosts": "*.home.example.org", "trusted_proxies": ""})
    assert r.status_code == 400 and "photos.example.com" in r.json()["detail"]
    assert client.get("/status", headers={"host": "photos.example.com"}).status_code == 200
    for bad in ("*", "-bad.example.com", "bad_name!.com"):
        r = client.post("/api/settings/address", headers=APP, json={"allowed_hosts": bad, "trusted_proxies": ""})
        assert r.status_code == 400, bad
    con.close()


def test_a_reverse_proxy_set_on_the_settings_page(cfg):
    proxy = TestClient(webapp.create_app(cfg), client=("172.18.0.2", 1))
    outside = {"x-forwarded-for": "8.8.8.8"}
    assert proxy.get("/status", headers=outside).status_code == 200  # the proxy is who is checked: it is private
    r = proxy.post("/api/settings/address", headers=APP, json={"allowed_hosts": "", "trusted_proxies": "172.18.0.0/16"})
    assert r.status_code == 200, r.text
    assert proxy.get("/status", headers=outside).status_code == 403  # now the client behind it is
    assert proxy.get("/status", headers={"x-forwarded-for": "192.168.1.20"}).status_code == 200
    r = proxy.post("/api/settings/address", headers=APP, json={"allowed_hosts": "", "trusted_proxies": "proxy.lan"})
    assert r.status_code == 400 and "not an address" in r.json()["detail"]


def test_https_from_files_uploads_and_off(cfg, monkeypatch):
    monkeypatch.setattr(serving.saved, "set", lambda: None)  # no web server here to start again
    ca = authority(cfg.data_dir / "made")
    cert, key, _ = make_cert(cfg.data_dir / "made", names=("photos.example.com",), issuer=ca)
    client = TestClient(webapp.create_app(cfg), client=LAN, base_url="http://192.168.7.1:8000")
    assert "Off: LineageBox is served over plain HTTP" in client.get("/settings").text

    r = client.post("/api/https", headers=APP, json={"tls_cert": str(cert), "tls_key": ""})
    assert r.status_code == 400 and "both" in r.json()["detail"]
    r = client.post("/api/https", headers=APP, json={"tls_cert": str(cert), "tls_key": str(cert)})
    assert r.status_code == 400 and "private key" in r.json()["detail"]
    r = client.post("/api/https", headers=APP, json={"tls_cert": str(cert), "tls_key": str(key)})
    assert r.status_code == 200, r.text
    assert r.json()["go"] == "https://192.168.7.1:8000/settings#https" and r.json()["wait"] > 0
    page = client.get("/settings").text
    assert "On: a certificate for <b>photos.example.com</b>" in page
    assert "It is not for <code>192.168.7.1:8000</code>" in page and "https://photos.example.com:8000" in page
    assert "only this site&#39;s certificate" in page or "only this site's certificate" in page

    # uploaded: kept in the data folder, the key readable by LineageBox alone
    own_cert, own_key, _ = make_cert(cfg.data_dir / "made", names=("192.168.7.1",), name="ip")
    r = client.post("/api/https/upload", headers=APP, json={"cert": own_cert.read_text(), "key": own_key.read_text()})
    assert r.status_code == 200, r.text
    con = db.connect(cfg)
    now = settings.current(con, cfg)
    assert now.tls_cert == str(cfg.data_dir / "certs" / "lineagebox.crt") and https.about(now).names == ("192.168.7.1",)
    assert not list((cfg.data_dir / "certs").glob(".*"))
    r = client.post("/api/https/upload", headers=APP, json={"cert": own_cert.read_text(), "key": key.read_text()})
    assert r.status_code == 400 and "does not belong" in r.json()["detail"]
    assert https.about(settings.current(con, cfg)).names == ("192.168.7.1",)  # the last good ones stay
    assert "self-signed" in client.get("/settings").text

    r = client.post("/api/https/off", headers=APP)
    assert r.status_code == 200 and r.json()["go"] == "http://192.168.7.1:8000/settings#https"
    assert settings.current(con, cfg).tls_cert == ""
    con.close()


def test_switching_through_a_reverse_proxy_is_refused(cfg, monkeypatch):
    monkeypatch.setattr(serving.saved, "set", lambda: None)
    cert, key, _ = make_cert(cfg.data_dir / "made")
    behind = dataclasses.replace(cfg, trusted_proxies=("172.18.0.0/16",))
    proxy = TestClient(webapp.create_app(behind), client=("172.18.0.2", 1))
    r = proxy.post("/api/https", headers={**APP, "x-forwarded-for": "192.168.1.20"},
                   json={"tls_cert": str(cert), "tls_key": str(key)})
    assert r.status_code == 400 and "reverse proxy" in r.json()["detail"]


def test_the_web_server_follows_the_settings(cfg, monkeypatch):
    monkeypatch.setattr(serving, "SETTLE", 0)
    con = db.connect(cfg)
    assert serving.tls(cfg) == (None, None) and not settings.problems(con)

    cert, key, _ = make_cert(cfg.data_dir / "certs")
    settings.save(con, cfg, {"tls_cert": str(cert), "tls_key": str(key)})
    assert serving.tls(cfg) == ((str(cert), str(key)), None)

    # switched on the Settings page: the server is stopped, to start again the new way
    server = SimpleNamespace(should_exit=False)
    changed = serving.watch(cfg, server)
    settings.save(con, cfg, {"tls_cert": "", "tls_key": ""})
    serving.saved.set()
    assert changed.wait(5) and server.should_exit

    # a renewed certificate in place of the one being served
    settings.save(con, cfg, {"tls_cert": str(cert), "tls_key": str(key)})
    server = SimpleNamespace(should_exit=False)
    changed = serving.watch(cfg, server)
    make_cert(cfg.data_dir / "certs", days=365)  # same files, new contents
    serving.saved.set()  # rather than waiting for the next look
    assert changed.wait(5) and server.should_exit

    # one that can't be loaded: plain HTTP, and the reason on the Settings and Status pages
    key.write_text("-----BEGIN PRIVATE KEY-----\nbroken\n-----END PRIVATE KEY-----\n")
    assert serving.tls(cfg)[0] is None
    assert "plain HTTP" in settings.problems(con)["https"]["message"]
    client = TestClient(webapp.create_app(cfg), client=LAN)
    assert "plain HTTP" in client.get("/settings").text and "HTTPS:" in client.get("/status").text
    con.close()


def test_turning_https_off_from_the_command_line(cfg, monkeypatch, capsys):
    con = db.connect(cfg)
    cert, key, _ = make_cert(cfg.data_dir / "certs")
    settings.save(con, cfg, {"tls_cert": str(cert), "tls_key": str(key)})
    config_file = cfg.data_dir / "test-config.toml"
    config_file.write_text(f'media_root = "{cfg.share_root.as_posix()}"\ndata_dir = "{cfg.data_dir.as_posix()}"\n')
    cli.main(["--config", str(config_file), "https"])
    assert "photos.example.com" in capsys.readouterr().out
    cli.main(["--config", str(config_file), "https", "off"])
    assert "HTTPS is off" in capsys.readouterr().out
    assert settings.current(con, cfg).tls_cert == ""
    con.close()
