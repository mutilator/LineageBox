// The Map page (map.html): photos and family events on OpenStreetMap, by year. #map carries what the server knows:
// data-tiles and data-attribution (the [map] config) and data-admin (folders and places are the admins' to fix).
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
    gps: { label: "Photos with GPS", color: "#2f62c9" },
    confirmed: { label: "Placed by folder name, confirmed", color: "#1f7a3f" },
    folder: { label: "Placed by folder name, not checked yet", color: "#7a3fb8" },
    events: { label: "Family events", color: "#b42318" },
  };
  const layers = Object.fromEntries(Object.keys(kinds).map((k) => [k, L.layerGroup()]));
  let hidden = new Set();
  try { hidden = new Set(JSON.parse(localStorage.getItem("lineagebox-map-hidden") || "[]")); } catch {}
  for (const [k, l] of Object.entries(layers)) if (!hidden.has(k)) l.addTo(map);
  L.control.layers(null, Object.fromEntries(Object.entries(kinds).map(([k, v]) =>
    [`<i class="swatch ${k}"></i>${v.label}`, layers[k]])), { collapsed: false }).addTo(map);
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
                     el("div", { className: "hint", textContent: `${p.count.toLocaleString()} photo${p.count === 1 ? "" : "s"}` }));
    const thumbs = el("div", { className: "thumbs" });
    for (const ph of p.photos) {
      thumbs.append(el("a", { href: `/image/${ph.id}` }, el("img", { src: `/t/${ph.sha}`, alt: "", loading: "lazy" })));
    }
    popup.append(thumbs);
    if (p.folder && admin) {
      popup.append(el("a", { href: `/folder/${p.folder}`, textContent: "Open the folder" }));
      // the place read from the folder names is where the map shows it: confirmed, or taken back
      const right = p.kind === "confirmed";
      const check = el("p", { className: "confirm" }, right ? "Confirmed as the right place. " : "");
      const button = el("button", { type: "button", className: right ? "quiet" : "primary",
                                    textContent: right ? "Undo" : "Right place" });
      button.onclick = () => confirmPlace(p.name, !right);
      check.append(button);
      popup.append(check);
      const input = el("input", { placeholder: "look it up as…", value: p.name });
      const relook = el("button", { type: "button", textContent: "Look up" });
      const remove = el("button", { type: "button", className: "quiet", textContent: "Not a place" });
      relook.onclick = () => fix(p.name, input.value);
      remove.onclick = () => fix(p.name, "");
      popup.append(el("details", {}, el("summary", { textContent: "Wrong place?" }), input, relook, remove));
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

  async function change(url, body) {
    try {
      await post(url, body);
    } catch (err) {
      alert(err.message || "That didn't work.");
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
      const b = el("button", { type: "button", title: `${y.year}: ${y.dated} dated, ${y.undated} by file time only` },
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
    if (!r.ok) { note.textContent = `The map's photos didn't load (${r.status}): try the page again.`; return; }
    const d = await r.json();
    if (!years.length) { years = d.years; drawYears(); }
    label.textContent = year || "All years";
    if (year) slider.value = year;
    bars.querySelectorAll("button").forEach((b) => b.classList.toggle("on", Number(b.dataset.year) === year));
    Object.values(layers).forEach((l) => l.clearLayers());
    const points = [];
    for (const p of d.places) {
      L.circleMarker([p.lat, p.lon], { radius: 5 + Math.sqrt(p.count) * 1.5, color: kinds[p.kind].color, weight: 1, fillOpacity: 0.45 })
        .bindPopup(() => placePopup(p), { maxWidth: 260 }).addTo(layers[p.kind]);
      points.push([p.lat, p.lon]);
    }
    const byPlace = {};
    for (const e of d.events) (byPlace[e.place] ||= []).push(e);
    for (const [place, list] of Object.entries(byPlace)) {
      L.circleMarker([list[0].lat, list[0].lon], { radius: 8, color: kinds.events.color, weight: 2, fillOpacity: 0.25 })
        .bindPopup(() => eventsPopup(place, list)).addTo(layers.events);
      points.push([list[0].lat, list[0].lon]);
    }
    if (points.length && !fitted) { map.fitBounds(points, { padding: [30, 30], maxZoom: 7 }); fitted = true; }
    const parts = [];
    const unchecked = d.places.filter((p) => p.kind === "folder").length;
    if (unchecked) parts.push(`${unchecked} place${unchecked === 1 ? "" : "s"} from folder names to check${year ? " this year" : ""}`);
    if (d.unplaced) parts.push(`${d.unplaced.toLocaleString()} photo${d.unplaced === 1 ? "" : "s"} ${year ? "this year " : ""}with no place yet`);
    if (d.to_find) parts.push(`${d.to_find} place name${d.to_find === 1 ? "" : "s"} still to find`);
    if (d.not_found) parts.push(`${d.not_found} OpenStreetMap didn't know`);
    note.textContent = parts.join(" · ");
  }

  let wait;
  slider.addEventListener("input", () => { clearTimeout(wait); label.textContent = slider.value; wait = setTimeout(() => show(Number(slider.value)), 250); });
  document.getElementById("all-years").onclick = () => show(null);
  show(null);
});
