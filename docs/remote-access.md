# Reaching LineageBox by name, HTTPS, and from outside

Out of the box LineageBox is a home-network service at `http://<address>:8000`, with no sign-in. This page covers giving
it a name, serving it over HTTPS, and reaching it from outside the house.

Everything here can be set on the **Settings** page, under **Address and HTTPS**. The `.env` settings only give the
starting values.

## Who LineageBox answers

Without [family accounts](family-accounts.md), LineageBox only answers:

- clients on private networks (10.x, 172.16–31.x, 192.168.x, loopback and their IPv6 equivalents). `ALLOWED_NETWORKS`
  in `.env` narrows that;
- requests made by address, or by a name only a home network can have (`nas`, `photos.lan`, `nas.local`,
  `*.home.arpa`).

Any other name is refused. This stops DNS rebinding, a trick in which a website points its own name at your server so
that its page can read LineageBox through your browser.

To use a name of your own, such as one for HTTPS with a real certificate, list it under **Address and HTTPS → Host
names**: `photos.example.com`, or `*.example.com` for every name under it. `ALLOWED_HOSTS` in `.env` gives the starting
list. The page won't save a list that leaves out the name you opened it by, so you can't lock yourself out.

## HTTPS with a reverse proxy (recommended)

Caddy, Traefik, nginx, Nginx Proxy Manager and NAS reverse proxies handle certificates and their renewal. With Caddy:

```
photos.example.com {
    reverse_proxy 192.168.1.5:8000
}
```

Every request then comes from the proxy. Set **Reverse proxy** on the Settings page (or `TRUSTED_PROXIES`) to the
proxy's own address (e.g. `192.168.1.5`, or the proxy container's address such as `172.18.0.3`). LineageBox then checks
the client named in the proxy's `X-Forwarded-For` header, and reads `X-Forwarded-Proto` to know the page is on HTTPS.

Give the proxy's own address rather than a whole Docker network where you can. Connections that Docker relays (from
the machine itself, and every connection under Docker Desktop on Windows or macOS) reach LineageBox from the Docker
network's gateway, so a whole network such as `172.18.0.0/16` would also believe whatever those connections claim.
For the same reason, under Docker Desktop every visitor looks like the home network: keep family accounts on there if
the port can be reached from outside.

The proxy must send both headers. Caddy, Traefik and Nginx Proxy Manager do by default. Plain nginx needs:

```nginx
location / {
    proxy_pass http://192.168.1.5:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 0;          # uploads of large videos
    proxy_request_buffering off;
    proxy_buffering off;             # videos play while they download
}
```

**Set the reverse proxy on the Settings page.** Without it, LineageBox checks the proxy's own private address, so it
answers everyone the proxy lets in. That includes the whole internet, if the proxy faces it.

## HTTPS without a proxy

LineageBox can serve HTTPS itself from a certificate and its private key, as PEM files. On **Settings → HTTPS
certificate**, either:

- give their paths as the container sees them. The catalog folder is `/data`, so files put in `data/certs/` on this
  machine are at `/data/certs/fullchain.pem` and `/data/certs/privkey.pem`;
- or upload them from your browser. They are kept in `data/certs/`.

Before using them, the page checks that the key belongs to the certificate and that the certificate hasn't expired. It
also shows which names the certificate is for and when it runs out. The web server then restarts, and the page moves to
the same address with `https://`.

- Use the full chain (`fullchain.pem`), not just the site's own certificate.
- While HTTPS is on, `http://` addresses don't open. For `https://photos.example.com` without a port, set `PORT=443`.
- A renewed certificate written over the same files (by certbot, acme.sh or a NAS) is picked up within a minute. The
  web server restarts by itself.
- If the certificate can't be loaded (it was deleted, or replaced by a broken file), LineageBox serves plain HTTP and
  says why on the Settings and Status pages.
- To turn HTTPS off without the page: `docker compose exec web python -m lineagebox https off`. Without `off`, the
  command shows the certificate in use.

`TLS_CERT` and `TLS_KEY` in `.env` set a certificate from the start.

## From outside your home

Without family accounts LineageBox has no sign-in, so **don't open it to the internet as it is**. Either:

- **use a VPN** (WireGuard, Tailscale and the like). LineageBox then sees a private address, as at home; or
- **turn on [family accounts](family-accounts.md)**. Everyone then signs in, at home too, and each relative sees only
  their own part of the family. Put a reverse proxy with a real certificate in front (above), and forward your router's
  port 443 to the proxy.

With family accounts on, LineageBox adds the protections a public site needs: sign-in rate limits, security headers,
HSTS over HTTPS, and private caching for photos. [Access control](access-control.md) has the details.
