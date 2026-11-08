// LineageBox's service worker (routes/phone.py serves it at /sw.js, with this version's number in place of
// __VERSION__): what lets a phone keep the site on its home screen, start it fast, and share photos to it.
//
// It keeps only the site's own files: the stylesheet and script of this version, the icons and an offline page. Pages,
// photos and everything the site answers are always asked for as before, never kept here, so that what a signed-out
// phone may see is decided by the server alone; photos are kept only as the browser keeps them anyway (their HTTP
// caching). A new version's worker drops the old one's files.
//
// Photos and videos shared to LineageBox from the phone's share sheet (the manifest's share_target) are posted to
// /upload/shared. They are kept on the phone, in their own cache, and Upload is opened, whose script (app.js) sends
// them as if picked there, and then forgets them; signing out forgets them too.
const VERSION = "__VERSION__";
const SHELL = `lineagebox-shell-${VERSION}`;
const SHARED = "lineagebox-shared";
const OFFLINE = "/static/offline.html";
const KEEP = [`/static/app.css?v=${VERSION}`, `/static/app.js?v=${VERSION}`, OFFLINE, "/static/icons/icon.svg",
              "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(KEEP)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((n) => n.startsWith("lineagebox-shell-") && n !== SHELL)
                                      .map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (event.request.method === "POST" && url.pathname === "/upload/shared") {
    event.respondWith(shared(event.request));
    return;
  }
  if (event.request.method !== "GET") return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(ownFile(event.request, url));
  } else if (event.request.mode === "navigate") {
    // a page: from the server, always; the offline page only when the server can't be reached at all
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));
  }
  // anything else (photos, videos, the parts pages load, the API): the browser's own way, untouched
});

// The site's own files: those of this version from the phone, at once; the rest from the server.
async function ownFile(request, url) {
  const kept = await caches.match(request);
  if (kept) return kept;
  const response = await fetch(request);
  if (response.ok && url.searchParams.get("v") === VERSION) {
    const cache = await caches.open(SHELL);
    cache.put(request, response.clone());
  }
  return response;
}

// Photos and videos from the share sheet: kept, each under a name of its own, until Upload sends them.
async function shared(request) {
  try {
    const form = await request.formData();
    const cache = await caches.open(SHARED);
    const now = Date.now();
    let n = 0;
    for (const file of form.getAll("files")) {
      if (typeof file === "string") continue;
      await cache.put(`/upload/shared/${now}-${n}`, new Response(file, { headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-File-Name": encodeURIComponent(file.name || `shared-${n + 1}`),
      } }));
      n++;
    }
    return Response.redirect(`/upload?shared=${n}`, 303);
  } catch {
    return Response.redirect("/upload?shared=lost", 303);
  }
}
