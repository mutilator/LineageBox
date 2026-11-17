// A journey (journey.html, journeys.py): an album's stops, in the album's own order, on the map, with the way from
// each to the next drawn the way the family travelled it. Everything it needs is on the page already (#journey, the
// same stops the list below the map shows), so nothing is asked for again. Follow the journey runs a light along the
// line, stopping at each place and lighting it up in the list; credit() for the map's attribution is in app.js.
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("map"), data = document.getElementById("journey");
  if (!box || !data || !window.L) return;
  let journey;
  try {
    journey = JSON.parse(data.textContent);
  } catch {
    return;
  }
  const stops = journey.stops || [];
  if (stops.length < 2) return;

  const map = L.map("map", { worldCopyJump: true });
  L.tileLayer(box.dataset.tiles, {
    maxZoom: 18, referrerPolicy: "strict-origin-when-cross-origin", attribution: credit(box.dataset.attribution),
  }).addTo(map);
  const at = stops.map((s) => L.latLng(s.lat, s.lon));

  // The way travelled, as travel maps draw it: each leg a dashed arc bowing out to the next place, drawn once when
  // the page opens, a place at a time. When it is done the dots start running along it (CSS, .journey-flow), and
  // an arrow sits in the middle of each leg pointing the way it went. Anyone who asked their computer for less
  // movement gets the finished drawing at once.
  const arcs = at.slice(1).map((b, i) => arcThrough(map, at[i], b));
  const path = arcs.reduce((all, points, i) => all.concat(i ? points.slice(1) : points), []);
  const stopAt = arcs.reduce((marks, points) => marks.concat(marks[marks.length - 1] + points.length - 1), [0]);
  const line = L.polyline([], { className: "journey-line", interactive: false }).addTo(map);
  const flow = L.polyline([], { className: "journey-flow", interactive: false }).addTo(map);

  function arrowOn(points) {
    const at = Math.floor(points.length / 2);
    return L.marker(points[at], {
      interactive: false, keyboard: false,
      icon: L.divIcon({ className: "journey-arrow", iconSize: [18, 18], iconAnchor: [9, 9],
                        html: `<span style="display:block;rotate:${arcTurn(points, at)}deg">➤</span>` }),
    });
  }

  function bubble(s) {
    const el = (tag, props = {}, ...kids) => {
      const made = Object.assign(document.createElement(tag), props);
      made.append(...kids);
      return made;
    };
    const when = s.from ? (s.to && s.to !== s.from ? `${s.from} – ${s.to}` : s.from) : "";
    const said = [tn("{n} photo", "{n} photos", s.count), when].filter(Boolean).join(" · ");
    const out = el("div", { className: "map-popup" }, el("b", { textContent: `${s.n}. ${s.name}` }),
                   el("div", { className: "hint", textContent: said }));
    if (s.words) out.append(el("div", { className: "said", textContent: s.words }));
    const thumbs = el("div", { className: "thumbs" });
    for (const p of s.photos) {
      thumbs.append(el("a", { href: `/image/${p.id}` },
                       el("img", { src: `/t/${p.sha}`, loading: "lazy",
                                   alt: t("A photo at {place}", { place: s.name }) })));
    }
    out.append(thumbs);
    return out;
  }

  const pins = stops.map((s) => L.marker([s.lat, s.lon], {
    title: `${s.n}. ${s.name}`, alt: `${s.n}. ${s.name}`, riseOnHover: true,
    icon: L.divIcon({ className: "journey-pin", iconSize: [30, 30], iconAnchor: [15, 15], html: `<b>${s.n}</b>` }),
  }).bindPopup(() => bubble(s)));
  map.fitBounds(L.latLngBounds(path), { padding: [34, 34] });

  const DRAWING = 1000, AT_A_STOP = 260;  // ms: drawing one leg, and the wait at the place it reaches

  function drawn() {  // everything in place, the dots running
    line.setLatLngs(path);
    flow.setLatLngs(path);
    pins.forEach((pin) => pin.addTo(map));
    arcs.forEach((points) => arrowOn(points).addTo(map));
  }

  function drawIt(leg) {
    const points = arcs[leg];
    const from = performance.now();
    const frame = (now) => {
      const part = Math.min(1, (now - from) / DRAWING);
      line.setLatLngs(path.slice(0, stopAt[leg] + Math.max(2, Math.round(points.length * part))));
      if (part < 1) { requestAnimationFrame(frame); return; }
      arrowOn(points).addTo(map);
      pins[leg + 1].addTo(map);
      if (leg + 1 < arcs.length) setTimeout(() => drawIt(leg + 1), AT_A_STOP);
      else flow.setLatLngs(path);  // only once the whole way is there do the dots run along it
    };
    requestAnimationFrame(frame);
  }

  if (stillPlease()) {
    drawn();
  } else {
    pins[0].addTo(map);
    setTimeout(() => drawIt(0), 300);  // the first place stands on its own for a moment, then the way out of it
  }

  // the list under the map: a stop lights up while the journey is at it, and its name shows it on the map
  const rows = [...document.querySelectorAll(".journey-stop")];
  const lit = (n) => {
    rows.forEach((r) => r.classList.toggle("on", Number(r.dataset.stop) === n));
    pins.forEach((p, i) => p.getElement()?.classList.toggle("on", i + 1 === n));
  };
  for (const button of document.querySelectorAll(".journey-go")) {
    button.addEventListener("click", () => {
      const i = Number(button.dataset.stop) - 1;
      map.setView(at[i], Math.max(map.getZoom(), 7));
      pins[i].openPopup();
      lit(i + 1);
    });
  }

  // ---- following it ------------------------------------------------------------------------------------------
  // A light runs from stop to stop in the album's order, waits at each one, and starts again at the end. A leg
  // takes as long as it is far, within reason, so a long drive feels long and a walk across town doesn't crawl.
  const play = document.getElementById("journey-play");
  if (!play) return;
  // along the arcs, not straight across: each leg is a stretch of `path`, as far as it is long on the ground
  const legs = arcs.map((points, i) => ({ from: stopAt[i], to: stopAt[i + 1],
                                          far: Math.max(1, at[i].distanceTo(at[i + 1])) }));
  const whole = legs.reduce((sum, leg) => sum + leg.far, 0);
  const light = L.divIcon({ className: "journey-dot", iconSize: [16, 16], iconAnchor: [8, 8] });
  const dot = L.marker(at[0], { interactive: false, keyboard: false, icon: light });
  const WAIT = 800, LAST_WAIT = 1600, SLOWEST = 2600, QUICKEST = 700, WHOLE = 9000;
  let going = false, frame = null, waiting = null;

  function halt() {
    going = false;
    cancelAnimationFrame(frame);
    clearTimeout(waiting);
    dot.remove();
    lit(0);
    play.textContent = t("▶ Follow the journey");
    play.setAttribute("aria-pressed", "false");
  }

  function go() {
    going = true;
    drawn();  // whatever the drawing was up to, the whole way is there before the light sets off
    dot.setLatLng(path[0]).addTo(map);
    play.textContent = t("⏸ Stop following");
    play.setAttribute("aria-pressed", "true");
    const arrive = (n) => {
      lit(n);
      map.panTo(at[n - 1]);
      if (n === at.length) {
        waiting = setTimeout(() => { if (going) { dot.setLatLng(at[0]); arrive(1); } }, LAST_WAIT);
      } else {
        waiting = setTimeout(() => { if (going) walk(n - 1); }, WAIT);
      }
    };
    const walk = (i) => {
      const leg = legs[i], long = Math.min(SLOWEST, Math.max(QUICKEST, (leg.far / whole) * WHOLE));
      const from = performance.now();
      const step = (now) => {
        if (!going) return;
        const part = Math.min(1, (now - from) / long);
        const along = leg.from + (leg.to - leg.from) * part;      // how far along the arc's own points it is
        const k = Math.min(path.length - 1, Math.floor(along)), next = Math.min(path.length - 1, k + 1);
        const over = along - k;
        dot.setLatLng(L.latLng(path[k][0] + (path[next][0] - path[k][0]) * over,
                               path[k][1] + (path[next][1] - path[k][1]) * over));
        if (part < 1) frame = requestAnimationFrame(step);
        else arrive(i + 2);
      };
      frame = requestAnimationFrame(step);
    };
    arrive(1);
  }

  play.setAttribute("aria-pressed", "false");
  play.addEventListener("click", () => (going ? halt() : go()));
});
