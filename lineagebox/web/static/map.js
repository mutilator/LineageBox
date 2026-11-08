// The Map page (map.html): photos and family events on OpenStreetMap, by year. #map carries what the server knows:
// data-tiles and data-attribution (the [map] config) and data-admin (folders and places are the admins' to fix).
// With ?place=image:<id> (or folder:<id>, for an admin) it also puts that photo or folder on the map by hand: see
// placing() below.
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("map");
  if (!box || !window.L) return;
  const admin = "admin" in box.dataset;
  const map = L.map("map", { worldCopyJump: true }).setView([35, -40], 2);
  // OpenStreetMap refuses tiles asked for without a Referer (osm.wiki/Blocked), and the site's pages send none to
  // other sites (Referrer-Policy: same-origin, app.py): the tiles are told this site's address, never the page's
  L.tileLayer(box.dataset.tiles, {
    maxZoom: 18, referrerPolicy: "strict-origin-when-cross-origin", attribution: box.dataset.attribution,
  }).addTo(map);
  // what the map shows, each in its own layer that the legend ticks on and off (remembered in this browser)
  const kinds = {
    placed: { label: t("Placed by hand"), color: "#b35c00" },
    gps: { label: t("Photos with GPS"), color: "#2f62c9" },
    confirmed: { label: t("Placed by folder name, confirmed"), color: "#1f7a3f" },
    folder: { label: t("Placed by folder name, not checked yet"), color: "#7a3fb8" },
    events: { label: t("Family events"), color: "#b42318" },
  };
  const layers = Object.fromEntries(Object.keys(kinds).map((k) => [k, L.layerGroup()]));
  let hidden = new Set();
  try { hidden = new Set(JSON.parse(localStorage.getItem("lineagebox-map-hidden") || "[]")); } catch {}
  for (const [k, l] of Object.entries(layers)) if (!hidden.has(k)) l.addTo(map);
  L.control.layers(null, Object.fromEntries(Object.entries(kinds).map(([k, v]) =>
    [`<i class="swatch ${k}"></i>${attrText(v.label)}`, layers[k]])), { collapsed: false }).addTo(map);
  map.on("overlayadd overlayremove", (e) => {
    const k = Object.keys(layers).find((key) => layers[key] === e.layer);
    if (e.type === "overlayadd") hidden.delete(k); else hidden.add(k);
    try { localStorage.setItem("lineagebox-map-hidden", JSON.stringify([...hidden])); } catch {}
  });
  const slider = document.getElementById("year"), label = document.getElementById("year-label");
  const note = document.getElementById("map-note"), bars = document.getElementById("years");
  let years = [], current = null, fitted = false;

  const el = (tag, props = {}, ...children) => {
    const e = Object.assign(document.createElement(tag), props);
    e.append(...children);
    return e;
  };

  function placePopup(p) {
    const popup = el("div", { className: "map-popup" }, el("b", { textContent: p.name }),
                     el("div", { className: "hint", textContent: tn("{n} photo", "{n} photos", p.count) }));
    const thumbs = el("div", { className: "thumbs" });
    for (const ph of p.photos) {
      thumbs.append(el("a", { href: `/image/${ph.id}` },
                       el("img", { src: `/t/${ph.sha}`, alt: t("A photo at {place}", { place: p.name }), loading: "lazy" })));
    }
    popup.append(thumbs);
    if (p.folder && admin && p.kind === "placed") {  // a folder placed by hand
      popup.append(el("a", { href: `/folder/${p.folder}`, textContent: t("Open the folder") }), " · ",
                   el("a", { href: `/map?place=folder:${p.folder}`, textContent: t("Move it") }));
    } else if (p.folder && admin) {
      popup.append(el("a", { href: `/folder/${p.folder}`, textContent: t("Open the folder") }));
      // the place read from the folder names is where the map shows it: confirmed, or taken back
      const right = p.kind === "confirmed";
      const check = el("p", { className: "confirm" }, right ? t("Confirmed as the right place.") + " " : "");
      const button = el("button", { type: "button", className: right ? "quiet" : "primary",
                                    textContent: right ? t("Undo") : t("Right place") });
      button.onclick = () => confirmPlace(p.name, !right);
      check.append(button);
      popup.append(check);
      const input = el("input", { placeholder: t("look it up as…"), value: p.name });
      const relook = el("button", { type: "button", textContent: t("Look up") });
      const remove = el("button", { type: "button", className: "quiet", textContent: t("Not a place") });
      relook.onclick = () => fix(p.name, input.value);
      remove.onclick = () => fix(p.name, "");
      popup.append(el("details", {}, el("summary", { textContent: t("Wrong place?") }), input, relook, remove));
    }
    return popup;
  }

  function eventsPopup(place, list) {
    const popup = el("div", { className: "map-popup" }, el("b", { textContent: place }));
    const ul = el("ul");
    for (const e of list) {
      ul.append(el("li", {}, el("a", { href: `/person/${e.person}#events`, textContent: e.who }),
                   ` ${e.label}${e.date ? " " + e.date : ""}`));
    }
    popup.append(ul);
    return popup;
  }

  // The circles can be reached with Tab, as buttons, and opened with Enter or Space as a click opens them; the popup
  // then has the focus, and closing it (Esc, its ×) gives the focus back to the circle.
  function reachable(marker, label) {
    let byKey = false;
    const ready = () => {
      const path = marker.getElement?.();
      if (!path || path.dataset.reachable) return;
      path.dataset.reachable = "";
      path.setAttribute("tabindex", "0");
      path.setAttribute("role", "button");
      path.setAttribute("aria-label", label);
      path.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        byKey = true;
        marker.openPopup();
        marker.getPopup()?.getElement()?.querySelector("a, button, input, summary")?.focus();
      });
    };
    marker.on("add", ready);
    ready();
    marker.on("popupclose", () => {
      if (byKey) marker.getElement?.()?.focus();
      byKey = false;
    });
    return marker;
  }

  async function change(url, body) {
    try {
      await post(url, body);
    } catch (err) {
      notice(err.message || t("That didn't work."), { error: true });
      return;
    }
    map.closePopup();
    show(current);
  }
  const fix = (name, query) => change("/api/places/fix", { name, query });
  const confirmPlace = (name, right) => change("/api/places/confirm", { name, right });

  function drawYears() {
    const peak = Math.max(1, ...years.map((y) => y.dated + y.undated));
    bars.replaceChildren(...years.map((y) => {
      const b = el("button", { type: "button", title: t("{year}: {dated} dated, {undated} by file time only", y) },
                   el("i", { className: "dated" }), el("i", { className: "undated" }));
      b.children[0].style.height = `${(100 * y.dated) / peak}%`;
      b.children[1].style.height = `${(100 * y.undated) / peak}%`;
      b.dataset.year = y.year;
      b.onclick = () => show(y.year);
      return b;
    }));
    if (years.length) {
      slider.min = years[0].year;
      slider.max = years[years.length - 1].year;
      slider.value = slider.max;
    }
  }

  async function show(year) {
    current = year;
    const r = await fetch(year ? `/api/map?year=${year}` : "/api/map");
    if (!r.ok) { note.textContent = t("The map's photos didn't load ({status}): try the page again.", { status: r.status }); return; }
    const d = await r.json();
    if (!years.length) { years = d.years; drawYears(); }
    label.textContent = year || t("All years");
    if (year) slider.value = year;
    bars.querySelectorAll("button").forEach((b) => b.classList.toggle("on", Number(b.dataset.year) === year));
    Object.values(layers).forEach((l) => l.clearLayers());
    const points = [];
    for (const p of d.places) {
      reachable(L.circleMarker([p.lat, p.lon], { radius: 5 + Math.sqrt(p.count) * 1.5, color: kinds[p.kind].color, weight: 1, fillOpacity: 0.45 })
        .bindPopup(() => placePopup(p), { maxWidth: 260 }).addTo(layers[p.kind]),
                tn("{place}: {n} photo", "{place}: {n} photos", p.count, { place: p.name }));
      points.push([p.lat, p.lon]);
    }
    const byPlace = {};
    for (const e of d.events) (byPlace[e.place] ||= []).push(e);
    for (const [place, list] of Object.entries(byPlace)) {
      reachable(L.circleMarker([list[0].lat, list[0].lon], { radius: 8, color: kinds.events.color, weight: 2, fillOpacity: 0.25 })
        .bindPopup(() => eventsPopup(place, list)).addTo(layers.events),
                tn("{place}: {n} family event", "{place}: {n} family events", list.length, { place }));
      points.push([list[0].lat, list[0].lon]);
    }
    if (points.length && !fitted) { map.fitBounds(points, { padding: [30, 30], maxZoom: 7 }); fitted = true; }
    if (pin) pin.addTo(map);  // above the circles
    const parts = [];
    const unchecked = d.places.filter((p) => p.kind === "folder").length;
    if (unchecked) {
      parts.push(year ? tn("{n} place from folder names to check this year",
                           "{n} places from folder names to check this year", unchecked)
                      : tn("{n} place from folder names to check", "{n} places from folder names to check", unchecked));
    }
    if (d.unplaced) {
      parts.push(year ? tn("{n} photo this year with no place yet", "{n} photos this year with no place yet", d.unplaced)
                      : tn("{n} photo with no place yet", "{n} photos with no place yet", d.unplaced));
    }
    if (d.to_find) parts.push(tn("{n} place name still to find", "{n} place names still to find", d.to_find));
    if (d.not_found) parts.push(t("{n} OpenStreetMap didn't know", { n: d.not_found }));
    note.textContent = parts.join(" · ");
  }

  // ---- putting a photo or a folder on the map by hand -------------------------------------------------------------
  // A pin is put down by a click on the map, a town found by name (from the list that comes with LineageBox), or the
  // middle of the map (for the keyboard: arrow keys move the map, + and − zoom), then dragged to the spot and saved.
  // A photo's place is tracked like any change, so the notice offers Undo.
  let pin = null;
  async function placing(kind, id) {
    const panel = document.getElementById("placing");
    const r = await fetch(`/api/places/target?kind=${kind}&id=${id}`);
    if (!r.ok) { notice(t("That can't be put on the map from here."), { error: true }); return; }
    const target = await r.json();
    const where = document.getElementById("placing-where"), save = document.getElementById("placing-save");
    const clear = document.getElementById("placing-clear"), town = document.getElementById("placing-town");
    const list = document.getElementById("placing-towns"), thumb = document.getElementById("placing-thumb");
    document.getElementById("placing-what").textContent = target.title;
    document.getElementById("placing-back").href = target.back;
    document.getElementById("placing-back").textContent = kind === "image" ? t("Back to the photo") : t("Back to the folder");
    if (target.sha) { thumb.src = `/t/${target.sha}`; thumb.hidden = false; }
    panel.hidden = false;
    clear.hidden = !target.by_hand;
    if (target.lat != null) {
      const place = target.label || t("here");
      where.textContent = target.by_hand ? t("Now {place}, placed by hand: drag the pin to move it.", { place })
        : t("Now {place}: drag the pin to move it.", { place });
    }
    const icon = L.divIcon({ className: "map-pin", iconSize: [28, 28], iconAnchor: [14, 28] });
    const put = (latlng, go) => {
      if (!pin) {
        pin = L.marker(latlng, { draggable: true, icon, title: t("Drag to where it was taken"), autoPan: true }).addTo(map);
        pin.on("dragend", () => { save.disabled = false; });
      } else pin.setLatLng(latlng);
      save.disabled = false;
      if (go) map.setView(latlng, Math.max(map.getZoom(), 12));
    };
    if (target.lat != null) { put([target.lat, target.lon], true); save.disabled = true; fitted = true; }
    map.on("click", (e) => put(e.latlng, false));
    document.getElementById("placing-centre").onclick = () => put(map.getCenter(), false);
    const send = async (body) => {
      let d;
      try {
        d = await post(kind === "image" ? `/api/images/${id}/place` : `/api/folders/${id}/place`, body);
      } catch (err) {
        notice(err.message || t("That didn't work."), { error: true });
        return null;
      }
      notice(d.message, { undo: d.undo });
      show(current);
      return d;
    };
    save.onclick = async () => {
      const ll = pin.getLatLng();
      if (await send({ lat: ll.lat, lon: ll.lng })) {
        save.disabled = true;
        clear.hidden = false;
        where.textContent = t("Saved. Drag the pin and save again to move it.");
      }
    };
    clear.onclick = async () => {
      if (await send({ clear: true })) {
        if (pin) { pin.remove(); pin = null; }
        clear.hidden = true;
        save.disabled = true;
        where.textContent = t("Taken off the map. Click the map to put it somewhere else.");
      }
    };
    let asking;
    town.addEventListener("input", () => {
      clearTimeout(asking);
      asking = setTimeout(async () => {
        const q = town.value.trim();
        if (q.length < 2) { list.replaceChildren(); return; }
        const answer = await fetch(`/api/towns?q=${encodeURIComponent(q)}`);
        if (!answer.ok) return;
        const found = (await answer.json()).towns;
        list.replaceChildren(...(found.length ? found.map((f) => {
          const b = el("button", { type: "button", className: "quiet", textContent: f.name });
          b.onclick = () => { put([f.lat, f.lon], true); list.replaceChildren(); };
          return el("li", {}, b);
        }) : [el("li", { className: "hint", textContent: t("No town of that name in the list: zoom in and click instead.") })]));
      }, 200);
    });
  }
  const target = (new URLSearchParams(location.search).get("place") || "").match(/^(image|folder):(\d+)$/);
  if (target) placing(target[1], Number(target[2]));  // with no place yet, the map still fits the family's photos

  let wait;
  slider.addEventListener("input", () => { clearTimeout(wait); label.textContent = slider.value; wait = setTimeout(() => show(Number(slider.value)), 250); });
  document.getElementById("all-years").onclick = () => show(null);
  show(null);
});
