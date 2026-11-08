// Photo page: arrow keys step through the current folder/year/search, Esc goes back (or out of only the photo).
// Anything with data-api (buttons, forms) posts to this app's JSON API, with data-body as the body if given;
// data-remove / data-reload / data-go / data-then say what to do afterwards, data-show where to put its message,
// data-html where to put the HTML it answers with.

document.addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey || e.target.closest("input, textarea, select, video, dialog")) return;
  const follow = (selector) => {
    const link = document.querySelector(selector);
    if (!link) return;
    if (link.closest("[data-viewer]")) goStep(link.href);  // a photo in place of this one (Back goes to the list)
    else location.href = link.href;
  };
  if (e.key === "ArrowLeft") follow('a[rel="prev"]');
  else if (e.key === "ArrowRight") follow('a[rel="next"]');
  else if (e.key === "Escape" && document.body.classList.contains("immersive")) setImmersive(false);
  else if (e.key === "Escape") document.querySelector("[data-back]")?.click();
});

// Video page: frames, faces and transcript lines with data-seek jump the player to that moment.
document.addEventListener("click", (e) => {
  const seek = e.target.closest("[data-seek]");
  const player = document.getElementById("player");
  if (!seek || !player) return;
  player.currentTime = parseFloat(seek.dataset.seek);
  player.play();
});

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "X-Requested-With": "lineagebox", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const text = await r.text();
    let detail = `${r.status} ${text}`;
    try {
      detail = JSON.parse(text).detail ?? detail;
    } catch {}
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg ?? d).join("; "); // FastAPI's own, should one come
    throw new Error(detail);
  }
  return r.status === 204 ? {} : r.json();
}

// A message at the foot of the page, instead of the browser's alert box: what went wrong (it stays until closed), or
// what was just done, with Undo beside a change that can be put back (answered with its change id as "undo"). One at
// a time; a new one takes the place of the last. Kept over a reload (later()), so a change that loads the page again
// still says what it did.
function notice(text, { error = false, undo = null } = {}) {
  let bar = document.getElementById("notice");
  if (!bar) {
    bar = Object.assign(document.createElement("div"), { id: "notice" });
    document.body.append(bar);
  }
  clearTimeout(bar.closing);
  bar.setAttribute("role", error ? "alert" : "status");
  bar.className = error ? "error" : "";
  bar.replaceChildren(Object.assign(document.createElement("span"), { textContent: text }));
  if (undo) {
    const button = Object.assign(document.createElement("button"), { type: "button", textContent: t("Undo") });
    button.dataset.api = `/api/changes/${undo}/undo-own`;
    bar.append(button);
  }
  const close = Object.assign(document.createElement("button"),
    { type: "button", className: "quiet close", textContent: "×", title: t("Close") });
  close.setAttribute("aria-label", t("Close"));
  close.addEventListener("click", () => (bar.hidden = true));
  bar.append(close);
  bar.hidden = false;
  if (!error) bar.closing = setTimeout(() => (bar.hidden = true), undo ? 15000 : 6000);
}
function later(text, options) {
  try { sessionStorage.setItem("lineagebox-notice", JSON.stringify([text, options])); } catch {}
}
try {
  const kept = JSON.parse(sessionStorage.getItem("lineagebox-notice") || "null");
  sessionStorage.removeItem("lineagebox-notice");
  if (kept) notice(...kept);
} catch {}

// The catalog is served over plain http on the LAN, where navigator.clipboard is not available.
function copyText(button, text) {
  const area = Object.assign(document.createElement("textarea"), { value: text });
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) {
    prompt(t("Copy this path:"), text);
    return;
  }
  const label = button.textContent;
  button.textContent = t("Copied");
  setTimeout(() => (button.textContent = label), 1500);
}

// A form's data-confirm can name its choices: "{into}" becomes the text of the option picked in "into".
function confirmText(el) {
  return el.dataset.confirm.replace(/\{(\w+)\}/g, (_, name) => {
    const field = el.elements?.[name];
    return field?.selectedOptions?.[0]?.text ?? field?.value ?? "";
  });
}

async function run(el, url, body) {
  if (el.dataset.confirm && !confirm(confirmText(el))) return;
  const controls = el.matches("form") ? el.querySelectorAll("button, input") : [el];
  // what says it is busy: the button, or a form's submit button with data-busy ("Making the changes…")
  const busy = el.matches("form") ? el.querySelector('button[type="submit"][data-busy]') : el.dataset.busy && el;
  const label = busy?.textContent;
  controls.forEach((c) => (c.disabled = true));
  if (busy) busy.textContent = busy.dataset.busy;
  try {
    await ticksSaved; // the ticks just clicked are kept before what follows them (who is who) loads the page again
    let result = await post(url, body);
    // a change that needs a yes first (a rename that merges two people): asked, then sent again with "again" added
    if (result.confirm) {
      if (!confirm(result.confirm)) return;
      result = await post(url, { ...(body ?? {}), ...(result.again ?? { merge: true }) });
    }
    everyone = null; // a name just given may be a new person: suggest them from now on
    // what is said is shown now, or after the page this leads to has loaded
    const leaves = result.go || (!el.dataset.remove && (el.dataset.go || el.dataset.then || "reload" in el.dataset
                                                        || result.reload));
    const say = leaves ? later : notice;
    if (result.errors?.length) {
      const more = result.more_errors ? t(" …and {n} more", { n: num(result.more_errors) }) : "";
      say(t("{moved} done. Not these: {errors}", { moved: result.moved, errors: result.errors.join("; ") }) + more,
          { error: true });
    }
    if (el.dataset.show && result.message) document.querySelector(el.dataset.show).textContent = result.message;
    else if (!result.errors?.length && (result.message || result.undo)) {
      say(result.message || t("Done."), { undo: result.undo });
    }
    if (el.dataset.followUpdate) followUpdate(el);
    if (el.dataset.html && result.html) document.querySelector(el.dataset.html).innerHTML = result.html;
    if (el.dataset.refocus) try { sessionStorage.setItem("lineagebox-refocus", el.dataset.refocus); } catch {}
    if (result.go) setTimeout(() => (location.href = result.go), (result.wait ?? 0) * 1000);
    else if (el.dataset.remove) {
      const form = el.closest("form.select-form");
      (el.closest(el.dataset.remove) || document.querySelector(el.dataset.remove))?.remove();
      if (form) updateSelection(form);
    }
    else if (el.dataset.go) location.href = el.dataset.go + result.person_id;
    else if (el.dataset.then) location.href = el.dataset.then;
    else if ("reload" in el.dataset || result.reload) {  // result.reload: only when something changed
      keepPhotoPlace();
      location.reload();
    }
    return result;  // it was done (the tick-photos bar then forgets the photos it moved)
  } catch (err) {
    notice(t("That didn't work: {why}", { why: err.message }), { error: true });
  } finally {
    controls.forEach((c) => (c.disabled = false));
    if (busy) busy.textContent = label;
    if (el.matches("form") && el.querySelector("[data-needs]")) followNeeds(el); // those switched off stay off
  }
}

// Status page, Update now: follows the updater until the new version answers (the page then shows it), or says how
// the update ended. While LineageBox restarts nothing answers, which is expected.
function followUpdate(el) {
  const said = document.querySelector(el.dataset.show);
  const started = Date.now();
  const look = async () => {
    try {
      const r = await fetch("/api/update");
      if (!r.ok) throw new Error(r.status);
      const now = await r.json();
      if (now.version !== el.dataset.followUpdate) return location.reload();
      const u = now.updater || {};
      if (u.message) said.textContent = u.message;
      if (!u.requested && u.state !== "updating" && Date.parse(u.at) > started - 5000) return;  // done, or failed
    } catch {
      said.textContent = t("LineageBox is restarting with the new version…");
    }
    if (Date.now() - started < 15 * 60 * 1000) setTimeout(look, 3000);
    else said.textContent = t("The update is taking a long time: `docker compose logs updater` says what it is doing.");
  };
  setTimeout(look, 3000);
}

// Cleanup lists: tick photos (clicking a photo ticks it), then move the ticked ones to the Removed folder. Faces
// suggested as someone (data-faces forms) are ticked the same way.
function updateSelection(form) {
  // a list whose ticks are kept (ticksKept, at the end): also those further down or on its other pages
  const kept = ticksKept(form);
  const n = kept?.size ?? form.querySelectorAll('input[name="image"]:checked, input[name="face"]:checked').length;
  if (kept) sayTicked(form, n);
  form.querySelectorAll('button[type="submit"][data-label]').forEach((submit) => {
    submit.textContent = submit.dataset.label.replace("{n}", num(n));
    submit.disabled = n === 0;
  });
}

// Settings forms: lists from checkboxes named "x[]", other checkboxes as on/off, the checked radio, everything
// else as typed.
function settingsBody(form) {
  const body = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.name.endsWith("[]")) {
      const key = el.name.slice(0, -2);
      body[key] ??= [];
      if (el.checked) body[key].push(el.value);
    } else if (el.type === "radio") {
      if (el.checked) body[el.name] = el.value;
    } else if (el.type === "checkbox") {
      body[el.name] = el.checked; // a switch: on or off
    } else {
      body[el.name] = el.value;
    }
  }
  return body;
}
// Forms with data-files send the text of their chosen files (certificates), with the other fields as typed.
async function filesBody(form) {
  const body = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    body[el.name] = el.type === "file" ? (el.files[0] ? await el.files[0].text() : "") : el.value;
  }
  return body;
}
// Forms with data-photos send their chosen photos as a list of data: URLs, each made a JPEG at most 2,000 pixels
// across first (a phone's photo is many times that to send, and the AI reads at less), with the other fields as typed.
// One the browser can't draw (HEIC, on some) goes as it is, and the server says whether it can read it.
async function smaller(file, most = 2000) {
  try {
    const picture = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, most / Math.max(picture.width, picture.height));
    const canvas = Object.assign(document.createElement("canvas"),
      { width: Math.round(picture.width * scale), height: Math.round(picture.height * scale) });
    canvas.getContext("2d").drawImage(picture, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.9);
  } catch {
    return new Promise((done, failed) => {
      const reader = new FileReader();
      reader.onload = () => done(reader.result);
      reader.onerror = failed;
      reader.readAsDataURL(file);
    });
  }
}
async function photosBody(form) {
  const body = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    body[el.name] = el.type === "file" ? await Promise.all([...el.files].map((f) => smaller(f))) : el.value;
  }
  return body;
}

// Ticked changes that need another (data-needs: its value, or several, space apart) are switched off while that one
// isn't ticked, and so not sent: a new person's life events, when the person isn't to be added.
function followNeeds(form) {
  const boxes = [...form.querySelectorAll('input[type="checkbox"]')];
  for (let round = 0; round < 5; round++) {  // switching one off can switch off those that need it
    const on = new Set(boxes.filter((b) => b.checked && !b.disabled).map((b) => b.value));
    let changed = false;
    for (const box of boxes.filter((b) => b.dataset.needs)) {
      const off = !box.dataset.needs.split(" ").every((key) => on.has(key));
      if (box.disabled !== off) [box.disabled, changed] = [off, true];
    }
    if (!changed) break;
  }
}
document.querySelectorAll("form").forEach((form) => form.querySelector("[data-needs]") && followNeeds(form));
// A form with data-ticks="url" sends every box's tick to it as they change, so a page loaded again (a reading's, when
// who someone is is picked) shows them as they were left. One at a time, in order; run() waits for them.
let ticksSaved = Promise.resolve();
function saveTicks(form) {
  if (!form?.dataset.ticks) return;
  const boxes = form.querySelectorAll('input[type="checkbox"]');
  const ticked = Object.fromEntries([...boxes].map((b) => [b.value, b.checked]));
  ticksSaved = ticksSaved.then(() => post(form.dataset.ticks, { ticked }))
    .catch((err) => notice(t("Those ticks weren't kept: {why}", { why: err.message }), { error: true }));
}
// A button with data-tick="all" or "none" ticks, or unticks, every box of its form.
document.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-tick]");
  if (!button) return;
  button.form.querySelectorAll('input[type="checkbox"]').forEach((box) => (box.checked = button.dataset.tick === "all"));
  followNeeds(button.form);
  saveTicks(button.form);
});
document.addEventListener("change", (e) => {
  const form = e.target.closest("form");
  if (form?.querySelector("[data-needs]")) followNeeds(form);
  if (e.target.matches('input[type="checkbox"]')) saveTicks(form);
  // a field with data-submit sends its form as soon as it is changed (who someone is, on a reading's page)
  if (e.target.matches("[data-submit]")) e.target.form?.requestSubmit();
});
document.querySelectorAll("form.select-form, form[data-faces]").forEach(updateSelection);
document.addEventListener("change", (e) => {
  const form = e.target.closest("form.select-form, form[data-faces]");
  if (form) updateSelection(form);
});

document.addEventListener("submit", (e) => {
  const form = e.target.closest("form[data-api], form.select-form");
  if (!form) return;
  e.preventDefault();
  if (form.matches(".select-form")) {
    const ids = tickedIds(form);  // with those ticked further down the list, or on its other pages
    const body = { image_ids: ids, reason: form.dataset.reason };
    for (const el of form.elements) {
      if (el.name && el.name !== "image" && el.matches("input:not([type=checkbox])")) body[el.name] = el.value.trim();
    }
    addPicked(form, body);
    // the moved or kept photos drop off the page; tagging them (data-keep) leaves them, ticked
    const keep = "keep" in (e.submitter?.dataset ?? {});
    // photos ticked out of sight (Select all on a big list, or on other pages): moving them is asked first
    const here = form.querySelectorAll('input[name="image"]:checked').length;
    if (!keep && ids.length > here && !confirm(t("{action}? {n} photos are ticked, and only {here} of them are on this page.",
        { action: (e.submitter?.textContent || "").trim(), n: num(ids.length), here: num(here) }))) return;
    if (keep) delete form.dataset.reload;
    else form.dataset.reload = "";
    run(form, e.submitter?.dataset.api || form.dataset.selectApi, body).then((done) => {
      if (done && !keep) forgetTicks(form);  // moved away: not ticked when the page comes back
      updateSelection(form);
    });
  } else if ("settings" in form.dataset) {
    run(form, form.dataset.api, addPicked(form, settingsBody(form)));
  } else if ("files" in form.dataset) {
    filesBody(form).then((body) => run(form, form.dataset.api, body));
  } else if ("photos" in form.dataset) {
    photosBody(form).then((body) => run(form, form.dataset.api, body), (err) => notice(t("A photo couldn't be read: {why}", { why: err }), { error: true }));
  } else if ("faces" in form.dataset) {
    // faces suggested as someone: the ticked ones are them and the rest are not; "None of these" says none are
    const boxes = [...form.querySelectorAll('input[name="face"]')];
    const none = "none" in (e.submitter?.dataset ?? {});
    const ids = (ticked) => boxes.filter((b) => !none && b.checked === ticked).map((b) => Number(b.value));
    run(form, form.dataset.api, { yes: ids(true), no: none ? boxes.map((b) => Number(b.value)) : ids(false) });
  } else {
    run(form, form.dataset.api, addPicked(form, Object.fromEntries(new FormData(form))));
  }
});

document.addEventListener("click", (e) => {
  const pick = e.target.closest("button[data-select]");
  if (pick) {
    const form = pick.closest("form");
    form.querySelectorAll('input[name="image"]').forEach((i) => (i.checked = pick.dataset.select === "all"));
    if (ticksKept(form)) tickEverything(form, pick.dataset.select === "all");  // the whole list, loaded or not
    updateSelection(form);
    return;
  }
  const button = e.target.closest("button[data-api]:not([type=submit])");
  if (button) {
    run(button, button.dataset.api, button.dataset.body ? JSON.parse(button.dataset.body) : undefined);
    return;
  }
  const copy = e.target.closest("[data-copy]");
  if (copy) copyText(copy, copy.dataset.copy);
});

// Status page: the progress part reloads itself while the tab is visible, every 10 seconds or data-every. One that
// answers with data-reload-page (a reading the AI has finished) has the whole page loaded again.
document.querySelectorAll("[data-live]").forEach((el) => {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const r = await fetch(el.dataset.live);
      if (r.ok) el.innerHTML = await r.text();
      if (el.querySelector("[data-reload-page]")) location.reload();
    } catch {}
  }, (Number(el.dataset.every) || 10) * 1000);
});

// A link with data-double="url": one click follows it (a moment later, to tell it from a double click), a double
// click goes to the url instead. Family: one click puts someone in the middle, a double click opens their page.
let doubleWait;
document.addEventListener("click", (e) => {
  const link = e.target.closest("a[data-double]");
  if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;  // a new tab or window, as usual
  e.preventDefault();
  clearTimeout(doubleWait);
  if (e.detail >= 2) location.href = link.dataset.double;
  else doubleWait = setTimeout(() => (location.href = link.href), 250);
});

// A button with data-append="url" puts what the url answers in place of its part (.more-part): on Home, a member's
// family's next older photos, with the button for those before them.
document.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-append]");
  if (!button) return;
  const label = button.textContent;
  button.disabled = true;
  if (button.dataset.busy) button.textContent = button.dataset.busy;
  try {
    const r = await fetch(button.dataset.append);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    (button.closest(".more-part") ?? button).outerHTML = await r.text();
  } catch (err) {
    button.disabled = false;
    button.textContent = label;
    notice(t("That didn't work: {why}", { why: err.message }), { error: true });
  }
});

// A details element with data-keep-open (an account, on the Accounts page) opens when the address's # names it, and
// puts its id there while it is open: a change that loads the page again leaves it open.
document.querySelectorAll("details[data-keep-open][id]").forEach((d) => {
  if (location.hash === `#${d.id}`) {
    d.open = true;
    d.scrollIntoView({ block: "start" });
  }
  d.addEventListener("toggle", () => {
    if (d.open) history.replaceState(null, "", `#${d.id}`);
    else if (location.hash === `#${d.id}`) history.replaceState(null, "", location.pathname + location.search);
  });
});

// A part with data-on-open="url" in a details element loads the first time it is wanted: when the details opens, or
// as soon as its heading is pointed at (a Timeline year's events, which show a moment later).
function loadOnOpen(part) {
  if (!part || part.dataset.loading) return;
  part.dataset.loading = "1";
  fetch(part.dataset.onOpen)
    .then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.status))))
    .then((html) => (part.innerHTML = html))
    .catch(() => {
      delete part.dataset.loading;  // asked again next time
      const note = part.querySelector(".loading");
      if (note) note.textContent = t("Couldn't load: point at it again to try again");
    });
}
document.addEventListener("toggle", (e) => {
  if (e.target.open) loadOnOpen(e.target.querySelector?.(":scope > [data-on-open]"));
}, true);
document.querySelectorAll("details > summary").forEach((summary) => {
  const part = summary.parentElement.querySelector(":scope > [data-on-open]");
  if (part) summary.addEventListener("mouseenter", () => loadOnOpen(part), { once: true });
});
// A link with data-swap="selector" loads what it points to into the part around it that the selector names, instead
// of going there: a person's faces to choose a portrait from, one year at a time.
document.addEventListener("click", async (e) => {
  const link = e.target.closest("a[data-swap]");
  const part = link?.closest(link.dataset.swap);
  if (!part || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
  e.preventDefault();
  try {
    const r = await fetch(link.href);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    part.innerHTML = await r.text();
  } catch (err) {
    notice(t("That didn't load: {why}", { why: err.message }), { error: true });
  }
});

// Parts that take a while to work out (the Tours page's suggestions, the Cleanup page's numbers) load once the rest of
// the page is shown, each on its own. While the catalog is busy (503) one is asked for again a few times; one that
// can't be had says so in place of its Loading… (class "loading").
document.querySelectorAll("[data-later]").forEach(async (el) => {
  for (let tries = 1; ; tries++) {
    try {
      const r = await fetch(el.dataset.later);
      if (r.ok) return void (el.innerHTML = await r.text());
      if (r.status !== 503 || tries === 4) break;
    } catch {
      break;
    }
    await new Promise((done) => setTimeout(done, 3000));
  }
  el.querySelectorAll(".loading").forEach((x) => {
    x.textContent = t("Couldn't load");
    x.title = t("Reload the page to try again");
  });
});

// Live filters: typing in an input with data-filter="selector" hides the elements it selects whose data-text (or
// text) lacks any of the typed words. A button with data-fill="#input" data-value="..." types that into it.
// data-limit="30": with nothing typed, only that many show; a button with data-more="#input" shows that many more
// (and an element with data-more-left="#input" says how many are left). Typing finds among all of them.
function runFilter(input) {
  const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
  const limit = words.length || !input.dataset.limit ? Infinity : Number(input.dataset.limit);
  let shown = 0, matched = 0;
  document.querySelectorAll(input.dataset.filter).forEach((el) => {
    const match = words.every((w) => (el.dataset.text || el.textContent).toLowerCase().includes(w));
    matched += match;
    el.hidden = !match || matched > limit;
    shown += !el.hidden;
  });
  const count = input.dataset.count && document.querySelector(input.dataset.count);
  if (count) count.textContent = words.length ? t("{n} shown", { n: shown }) : "";
  const more = document.querySelector(`[data-more="#${input.id}"]`);
  if (more) more.closest("p").hidden = matched <= shown;
  const left = document.querySelector(`[data-more-left="#${input.id}"]`);
  if (left) left.textContent = t("{n} more", { n: num(matched - shown) });
}
document.addEventListener("click", (e) => {
  const more = e.target.closest("[data-more]");
  if (!more) return;
  const input = document.querySelector(more.dataset.more);
  input.dataset.limit = Number(input.dataset.limit) + Number(input.dataset.step || 30);
  runFilter(input);
});
document.querySelectorAll("input[data-filter]").forEach((input) => {
  input.addEventListener("input", () => runFilter(input));
  if (input.value) runFilter(input);
});
document.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-fill]");
  if (!chip) return;
  const input = document.querySelector(chip.dataset.fill);
  input.value = input.value === chip.dataset.value ? "" : chip.dataset.value;
  runFilter(input);
});

// Bars with several buttons: Enter in an input with data-enter="#button" presses that button, not the form's first.
document.addEventListener("keydown", (e) => {
  const input = e.key === "Enter" && e.target.closest("input[data-enter]");
  if (!input) return;
  e.preventDefault();
  document.querySelector(input.dataset.enter)?.click();
});

// A button with data-toggle="#id" shows or hides that element, ready to type in (Fix on a face named on a photo shows
// a box for who it is instead); data-toggle-label is the button's text while it is open. Esc in it hides it again.
function toggle(button, open) {
  const target = document.querySelector(button.dataset.toggle);
  if (!target || target.hidden === !open) return;
  target.hidden = !open;
  button.setAttribute("aria-expanded", open);
  const label = button.dataset.toggleLabel;
  if (label) [button.dataset.toggleLabel, button.textContent] = [button.textContent, label];
  (open ? target.querySelector("input, textarea") : button)?.focus();
}
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-toggle]");
  if (button) toggle(button, document.querySelector(button.dataset.toggle)?.hidden);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const button = [...document.querySelectorAll('[data-toggle][aria-expanded="true"]')]
    .find((b) => document.querySelector(b.dataset.toggle)?.contains(e.target));
  if (button) toggle(button, false);
});

// An input with data-suggest="url" fills its datalist from that url (a JSON list) the first time it is used.
document.addEventListener("focusin", async (e) => {
  const input = e.target.closest("input[data-suggest]");
  if (!input || "suggested" in input.dataset || !input.list) return;
  input.dataset.suggested = "";
  try {
    const r = await fetch(input.dataset.suggest);
    if (r.ok) input.list.replaceChildren(...(await r.json()).map((value) => Object.assign(document.createElement("option"), { value })));
  } catch {}
});

// Boxes names are typed in (data-names) suggest people as they are typed: by any part of their name or other names,
// with their face, years and photos, so that two people of the same name can be told apart. ↓ ↑ pick one; Enter takes
// it and then does what Enter does there (names the faces, adds the person); Tab takes it and moves on; Esc closes.
const fold = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const nameBox = { input: null, box: null, found: [], on: -1 };
let everyone = null; // fetched when a name box is first used, and again after any change
function people() {
  everyone ??= fetch("/api/people/names")
    .then((r) => (r.ok ? r.json() : []))
    .then((list) => list.map((p) => ({ ...p, words: fold(p.name).split(/\s+/), alsoWords: fold(p.also).split(/[\s·]+/).filter(Boolean) })))
    .catch(() => ((everyone = null), []));
  return everyone;
}

function matchingPeople(list, text) {
  const typed = fold(text).split(/\s+/).filter(Boolean);
  if (!typed.length) return [];
  const whole = typed.join(" ");
  const found = [];
  for (const p of list) {
    const inName = typed.every((t) => p.words.some((w) => w.startsWith(t)));
    if (!inName && !typed.every((t) => [...p.words, ...p.alsoWords].some((w) => w.startsWith(t)))) continue;
    found.push({ ...p, rank: fold(p.name).startsWith(whole) ? 0 : inName ? 1 : 2 });
  }
  found.sort((a, b) => a.rank - b.rank || b.photos - a.photos || a.name.localeCompare(b.name));
  return found.slice(0, 8);
}

function closeNames() {
  nameBox.box?.remove();
  nameBox.input?.setAttribute("aria-expanded", "false");
  nameBox.input?.removeAttribute("aria-activedescendant");
  Object.assign(nameBox, { box: null, found: [], on: -1 });
}

async function suggestNames(input) {
  const list = await people();
  if (document.activeElement !== input) return;
  closeNames();
  const text = input.value.trim();
  const found = matchingPeople(list, text);
  if (!found.length && !text) return;
  const box = Object.assign(document.createElement("ul"), { className: "suggest-box", id: "name-suggestions" });
  box.setAttribute("role", "listbox");
  found.forEach((p, i) => {
    const li = Object.assign(document.createElement("li"), { id: `name-suggestion-${i}` });
    li.setAttribute("role", "option");
    li.append(p.face ? Object.assign(document.createElement("img"), { src: `/face/${p.face}`, alt: "" })
                     : Object.assign(document.createElement("span"), { className: "ph" }));
    const about = [p.rank === 2 && t("also {name}", { name: p.also }), p.about,
                   p.photos ? tn("{n} photo", "{n} photos", p.photos) : t("no photos yet")];
    const who = document.createElement("span");
    who.append(Object.assign(document.createElement("b"), { textContent: p.name }),
               Object.assign(document.createElement("small"), { textContent: about.filter(Boolean).join(" · ") }));
    li.append(who);
    li.addEventListener("click", () => takeName(input, p));
    box.append(li);
  });
  if (text && !found.some((p) => fold(p.name) === fold(text))) {
    box.append(Object.assign(document.createElement("li"), {
      className: "new", textContent: `${found.length ? "Or someone new" : "Someone new"}: “${text}”` }));
  }
  box.addEventListener("mousedown", (e) => e.preventDefault()); // clicking a name leaves the typing where it is
  const r = input.getBoundingClientRect();
  Object.assign(box.style, { left: `${r.left + scrollX}px`, top: `${r.bottom + scrollY}px`, minWidth: `${r.width}px` });
  document.body.append(box);
  input.setAttribute("aria-expanded", "true");
  Object.assign(nameBox, { input, box, found, on: -1 });
}

// Two people may have the same name: the one picked is remembered, and sent as "<box's name>_id" with the form while
// the box still holds their name (addPicked). A name typed and not picked is looked up, or is someone new.
function takeName(input, p) {
  input.value = p.name;
  input.dataset.pickedId = p.id;
  input.dataset.pickedName = p.name;
  closeNames();
}

function addPicked(form, body) {
  form.querySelectorAll("input[data-names]").forEach((input) => {
    if (input.dataset.pickedId && input.name && input.value.trim() === input.dataset.pickedName) {
      body[`${input.name}_id`] = Number(input.dataset.pickedId);
    }
  });
  return body;
}

function highlightName(step) {
  const n = nameBox.found.length;
  if (!n) return;
  nameBox.on = nameBox.on + step >= n ? -1 : nameBox.on + step < -1 ? n - 1 : nameBox.on + step;
  [...nameBox.box.querySelectorAll("li[role=option]")].forEach((li, i) => {
    li.classList.toggle("on", i === nameBox.on);
    if (i === nameBox.on) li.scrollIntoView({ block: "nearest" });
  });
  if (nameBox.on >= 0) nameBox.input.setAttribute("aria-activedescendant", `name-suggestion-${nameBox.on}`);
  else nameBox.input.removeAttribute("aria-activedescendant");
}

// in the capture phase, so the box has the keys before Enter sends the form and Esc leaves the box
document.addEventListener("keydown", (e) => {
  if (!nameBox.box || e.target !== nameBox.input) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    highlightName(e.key === "ArrowDown" ? 1 : -1);
  } else if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    closeNames();
  } else if (e.key === "Enter" || e.key === "Tab") {
    if (nameBox.on >= 0) takeName(nameBox.input, nameBox.found[nameBox.on]);
    else closeNames();
  }
}, true);
document.addEventListener("input", (e) => {
  if (e.target.matches?.("input[data-names]")) suggestNames(e.target);
});
document.addEventListener("focusin", (e) => {
  const input = e.target.closest?.("input[data-names]");
  if (!input) return;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", "name-suggestions");
  people();
  if (input.value.trim()) suggestNames(input); // a name suggested from folder names: who it could be
});
document.addEventListener("focusout", (e) => {
  if (e.target === nameBox.input) closeNames();
});
addEventListener("resize", closeNames);

// Forms: parts marked data-show-if="field=a,b" show only while that field is a or b (tour types, adding a child).
function updateShown(form) {
  form.querySelectorAll("[data-show-if]").forEach((el) => {
    const [name, values] = el.dataset.showIf.split("=");
    el.hidden = !values.split(",").includes(form.elements[name]?.value.trim().toLowerCase());
  });
}
document.querySelectorAll("form").forEach(updateShown);
document.addEventListener("input", (e) => e.target.form && updateShown(e.target.form));
document.addEventListener("change", (e) => {
  if (e.target.form) updateShown(e.target.form);
  if (e.target.form?.matches("[data-autosubmit]")) e.target.form.submit();
});

// Tour wizard: how many photos the answers so far give, and the name they make (until a name is typed).
document.querySelectorAll("form[data-preview]").forEach((form) => {
  const out = form.querySelector("[data-preview-out]");
  const name = form.querySelector("[data-auto-name]");
  let timer;
  name?.addEventListener("input", () => (name.dataset.touched = ""));
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const result = await post(form.dataset.preview, settingsBody(form));
        out.textContent = result.message ?? "";
        if (result.name && name && !("touched" in name.dataset)) name.value = result.name;
      } catch {}
    }, 250);
  };
  form.addEventListener("change", refresh);
  refresh();
});

// Tour page: the slideshow, full screen. Space pauses, ← → step, Esc leaves. It is a dialog that holds the focus while
// it plays (a screen reader stays in it), and gives it back to the button that opened it.
document.addEventListener("click", (e) => {
  const play = e.target.closest("[data-play]");
  if (play) playTour(JSON.parse(document.querySelector(play.dataset.play).textContent), Number(play.dataset.seconds) || 5);
});

function playTour(slides, seconds) {
  let i = 0;
  let paused = false;
  let timer;
  const opener = document.activeElement;
  const box = document.createElement("dialog");
  box.className = "player";
  box.setAttribute("aria-label", slides[0]?.title ? t("Slideshow: {title}", { title: slides[0].title }) : t("Slideshow"));
  box.innerHTML = `<div class="slide"></div><div class="caption"><span class="words"></span><span class="when"></span><span class="who"></span></div>
    <div class="controls"><button type="button" data-act="prev" aria-label="${attrText(t("Previous photo"))}" title="${attrText(t("Previous photo (←)"))}">‹</button>
    <button type="button" data-act="pause" aria-label="${attrText(t("Pause"))}" title="${attrText(t("Pause or go on (space)"))}">❚❚</button>
    <button type="button" data-act="next" aria-label="${attrText(t("Next photo"))}" title="${attrText(t("Next photo (→)"))}">›</button><span class="count"></span>
    <button type="button" data-act="close" aria-label="${attrText(t("Close the slideshow"))}" title="${attrText(t("Close the slideshow (Esc)"))}">×</button></div>`;
  document.body.append(box);
  box.showModal();  // the page behind it can't be reached until it closes
  const stage = box.querySelector(".slide");
  const pauseButton = box.querySelector('[data-act="pause"]');
  pauseButton.focus();

  // a photo shows for the tour's seconds; a video plays to its end (or, one that can't be played, for those seconds)
  const schedule = () => {
    clearTimeout(timer);
    if (!paused && !slides[i].video) timer = setTimeout(next, seconds * 1000);
  };
  const show = () => {
    const s = slides[i];
    const media = s.title !== undefined ? titleCard(s)  // the first slide: the tour's or album's name (Tours with stories)
      : s.video
      ? Object.assign(document.createElement("video"), { src: s.src, poster: s.poster, autoplay: !paused, playsInline: true })
      : Object.assign(document.createElement("img"), { src: s.src, alt: s.caption || s.people });
    if (s.video) {
      media.addEventListener("ended", () => !paused && next());
      media.addEventListener("error", () => { if (!paused) timer = setTimeout(next, seconds * 1000); });
    }
    stage.replaceChildren(media);
    box.querySelector(".words").textContent = s.caption || "";
    box.querySelector(".when").textContent = s.when;
    box.querySelector(".who").textContent = s.people;
    box.querySelector(".count").textContent = `${i + 1} / ${slides.length}`;
    if (slides[i + 1] && !slides[i + 1].video) new Image().src = slides[i + 1].src;
    schedule();
  };
  const next = () => {
    if (i < slides.length - 1) {
      i++;
      show();
    } else setPaused(true);
  };
  const prev = () => {
    if (i > 0) {
      i--;
      show();
    }
  };
  const setPaused = (value) => {
    paused = value;
    pauseButton.textContent = paused ? "▶" : "❚❚";
    pauseButton.setAttribute("aria-label", paused ? t("Play") : t("Pause"));
    const video = stage.querySelector("video");
    if (video) paused ? video.pause() : video.play();
    schedule();
  };
  const close = () => {
    clearTimeout(timer);
    document.removeEventListener("keydown", keys, true);
    document.removeEventListener("fullscreenchange", left);
    if (document.fullscreenElement) document.exitFullscreen();
    if (box.open) box.close();
    box.remove();
    if (opener?.isConnected) opener.focus();
  };
  const keys = (e) => {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowRight") next();
    else if (e.key === "ArrowLeft") prev();
    else if (e.key === " ") setPaused(!paused);
    else return;
    e.preventDefault();
    e.stopPropagation();
  };
  const left = () => !document.fullscreenElement && close();  // Esc in full screen only leaves full screen
  document.addEventListener("keydown", keys, true);
  box.addEventListener("cancel", (e) => { e.preventDefault(); close(); });  // Esc, as the browser tells a dialog
  box.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "prev") prev();
    else if (act === "next") next();
    else if (act === "pause") setPaused(!paused);
    else if (act === "close") close();
  });
  box.requestFullscreen?.().then(() => document.addEventListener("fullscreenchange", left), () => {});
  show();
}

// Tags page: the list follows the filter box as you type.
document.querySelectorAll("form[data-filter]").forEach((form) => {
  const target = document.querySelector(form.dataset.filter);
  let timer;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const query = new URLSearchParams(new FormData(form)).toString();
      history.replaceState(null, "", `${form.action}?${query}`);
      try {
        const r = await fetch(`${form.action}?${query}`, { headers: { "X-Requested-With": "lineagebox" } });
        if (r.ok) target.innerHTML = await r.text();
      } catch {}
    }, 200);
  };
  form.addEventListener("input", refresh);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    refresh();
  });
});

// Keyboard: ? opens the help, / the search, g then a letter goes to a page (as listed in the help), Esc leaves the box
// being typed in. Anything with data-key="x" is focused (a box) or pressed (a button, a link) by that key, opening
// the panel it is in; data-key-label says what it does in the help. A form with data-refocus="x" presses x again
// once the page has reloaded after saving it, ready for the next one (life events, tags).
const GO = { h: "/home", f: "/", t: "/timeline", m: "/map", p: "/people", a: "/family", o: "/tours", g: "/tags", c: "/cleanup",
             s: "/status", e: "/settings" };
let goNext = 0;

function keyTarget(key) {
  return [...document.querySelectorAll("main [data-key]")].find((el) => el.dataset.key === key && !el.closest("[hidden]"));
}

function pressKey(key) {
  const el = keyTarget(key);
  if (!el) return false;
  for (let d = el.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
  if (el.matches("input, select, textarea")) {
    el.focus();
    el.scrollIntoView({ block: "center" });
    if (el.select && el.type !== "checkbox" && el.type !== "radio") el.select();
  } else {
    el.click();
  }
  return true;
}

function keyLabel(el) {
  return el.dataset.keyLabel || el.getAttribute("aria-label") || el.placeholder || el.textContent.trim();
}

function showHelp() {
  const dialog = document.getElementById("help");
  if (!dialog) return;
  const list = dialog.querySelector("[data-page-keys]");
  list.querySelectorAll("[data-found]").forEach((el) => el.remove());
  const found = new Map();  // what it does -> its keys
  document.querySelectorAll("main [data-key]").forEach((el) => {
    const keys = found.get(keyLabel(el)) ?? [];
    if (!keys.includes(el.dataset.key)) keys.push(el.dataset.key);
    found.set(keyLabel(el), keys);
  });
  for (let [label, keys] of found) {
    if (keys.length > 2 && keys.every((k) => /^\d$/.test(k))) keys = [`${keys[0]}–${keys.at(-1)}`];
    const dt = document.createElement("dt");
    keys.forEach((k) => dt.append(Object.assign(document.createElement("kbd"), { textContent: k })));
    const dd = Object.assign(document.createElement("dd"), { textContent: label });
    dt.dataset.found = dd.dataset.found = "";
    list.append(dt, dd);
  }
  dialog.querySelector("[data-no-keys]").hidden = list.children.length > 0;
  dialog.showModal();
}

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
  if (e.target.closest("input, textarea, select, [contenteditable]")) {
    if (e.key === "Escape" && !e.target.closest("dialog")) e.target.blur();
    return;
  }
  if (e.target.closest("video, dialog")) return;
  if (e.key.length === 1 && lettersOff()) return;  // keys of one letter or sign, turned off in the help (at the end)
  if (Date.now() < goNext) {
    goNext = 0;
    if (GO[e.key]) {
      e.preventDefault();
      location.href = GO[e.key];
    }
    return;
  }
  if (e.key === "?") showHelp();
  else if (e.key === "/") document.querySelector(".search input")?.focus();
  else if (e.key === "g") goNext = Date.now() + 1500;
  else if (!pressKey(e.key)) return;
  e.preventDefault();
});

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-help]")) showHelp();
});

// The top bar. Its links fold into the ☰ menu when they don't all fit (on phones they always do, by the stylesheet,
// so they never show squashed first); the round button says who is signed in, with Sign out. A menu opens on a
// click and closes on a click anywhere else, or Esc.
const topBar = document.querySelector("header.top");
const topLinks = topBar?.querySelector("nav");
const phone = matchMedia("(max-width: 760px)");

function openPanel(button) {
  topBar?.querySelectorAll("[data-panel]").forEach((b) => {
    const open = b === button && b.getAttribute("aria-expanded") !== "true";
    b.setAttribute("aria-expanded", String(open));
    document.getElementById(b.dataset.panel).classList.toggle("open", open);
  });
}

let measuredAt = 0;
function foldLinks() {
  if (!topLinks || phone.matches || innerWidth === measuredAt) return;  // only when the width changed
  measuredAt = innerWidth;
  if (topLinks.matches(".open")) openPanel(null);
  topBar.classList.remove("folded");
  topBar.classList.toggle("folded", topBar.scrollWidth > topBar.clientWidth + 1);  // the links never shrink
}
foldLinks();
addEventListener("resize", foldLinks);

document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-panel]");
  if (button) openPanel(button);
  else if (!e.target.closest(".profile-menu.open, nav.open")) openPanel(null);
});
document.addEventListener("keydown", (e) => {  // before the photo page's Esc, which goes back
  const open = topBar?.querySelector("[data-panel][aria-expanded=true]");
  if (e.key !== "Escape" || !open) return;
  e.stopImmediatePropagation();
  openPanel(null);
  open.focus();
}, true);

// Each key is in its button's or box's tooltip (not while such keys are turned off).
if (!lettersOff()) {
  document.querySelectorAll("main [data-key]").forEach((el) => {
    el.title = t("{label} (key {key})", { label: el.title || keyLabel(el), key: el.dataset.key });
  });
}

try {
  const again = sessionStorage.getItem("lineagebox-refocus");
  sessionStorage.removeItem("lineagebox-refocus");
  if (again) pressKey(again);
} catch {}

// ---- Back goes where you came from -----------------------------------------------------------------------------
// A photo opened from a list (a grid, Home's days, a person's faces, a place on the map) remembers that list in this
// tab: Back goes back to it, scrolled to where it was, and ← → step through it where the photo's address doesn't say
// what it is in (Home, the map, recently added). Stepping replaces the photo in the browser's history instead of
// adding to it, so the browser's own Back goes to the list too.
const FROM_KEY = "lineagebox-from";
const STEP_KEY = "lineagebox-step";
function goStep(href) {
  try {
    sessionStorage.setItem(STEP_KEY, "1");
    if (document.body.classList.contains("immersive")) sessionStorage.setItem("lineagebox-immersive", "1");
  } catch {}
  location.replace(href);
}
const photoLink = (a) => {
  const url = new URL(a.href, location.href);
  const m = url.origin === location.origin && url.pathname.match(/^\/image\/(\d+)$/);
  return m ? { id: Number(m[1]), href: url.pathname + url.search,
               sha: a.querySelector('img[src^="/t/"]')?.getAttribute("src").slice(3) } : null;
};
document.addEventListener("click", (e) => {
  const link = e.target.closest("a[href]");
  if (!link || link.target === "_blank" || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button > 0) return;
  if (link.closest("[data-viewer]") || !photoLink(link)) return;  // from a photo (stepping, similar photos): kept
  const holder = link.closest("[data-list], .grid, .thumbs, .days, .faces") || link.closest("main") || document.body;
  const seen = new Set();
  const items = [...holder.querySelectorAll("a[href]")].map(photoLink)
    .filter((it) => it && !seen.has(it.id) && seen.add(it.id));
  try {
    sessionStorage.setItem(FROM_KEY, JSON.stringify({ url: location.href, y: scrollY, items: items.slice(0, 3000),
                                                      opened: true }));
  } catch {}
}, true);  // capture: before the map's popups keep the click to themselves
try {  // back on a list without the browser's own Back: where it was scrolled to
  const back = JSON.parse(sessionStorage.getItem("lineagebox-scroll") || "null");
  if (back?.url === location.href) {
    sessionStorage.removeItem("lineagebox-scroll");
    const to = () => scrollTo(0, back.y);
    to();
    addEventListener("load", to, { once: true });
    setTimeout(to, 700);  // parts loaded after the page (Home's days)
  }
} catch {}

document.querySelectorAll("[data-viewer]").forEach((viewer) => {
  let from = null;
  let stepped = false;
  try {
    from = JSON.parse(sessionStorage.getItem(FROM_KEY) || "null");
    stepped = sessionStorage.getItem(STEP_KEY) === "1";
    sessionStorage.removeItem(STEP_KEY);
  } catch {}
  if (!from) return;
  if (from.opened || stepped) {  // this history entry is one step on from the list: the browser's Back goes there
    history.replaceState({ ...(history.state || {}), lbFrom: from.url }, "");
    if (from.opened) {
      from.opened = false;
      try { sessionStorage.setItem(FROM_KEY, JSON.stringify(from)); } catch {}
    }
  }
  if (history.state?.lbFrom === from.url) {
    viewer.querySelectorAll("[data-back]").forEach((a) => {
      a.href = from.url;
      a.addEventListener("click", (e) => {
        e.preventDefault();
        history.back();
      });
    });
  }
  const id = Number(viewer.dataset.image);
  const at = from.items.findIndex((it) => it.id === id);
  if (at < 0 || viewer.querySelector('a[rel="prev"], a[rel="next"]') || from.items.length < 2) return;
  const stage = viewer.querySelector("[data-stage]");
  for (const [rel, item, label, mark] of [["prev", from.items[at - 1], t("Previous photo"), "‹"],
                                          ["next", from.items[at + 1], t("Next photo"), "›"]]) {
    if (!item) continue;
    const step = Object.assign(document.createElement("a"), { className: `step ${rel}`, rel, href: item.href,
                                                              textContent: mark });
    step.setAttribute("aria-label", label);
    stage.append(step);
  }
  const count = viewer.querySelector("[data-count]");
  if (count && !count.textContent.trim()) count.textContent = t("{at} of {all}", { at: num(at + 1), all: num(from.items.length) });
  const ahead = from.items[at + 1]?.sha;
  if (ahead) document.head.append(Object.assign(document.createElement("link"), { rel: "prefetch", href: `/p/${ahead}` }));
});
// A change made on a photo's page can move it in the list it was opened from: a new date moves it in a folder, a year
// or a tag, which go by date. The page comes back from that change with its place as it was (its number, ‹ › to the
// photos that were either side of it, Back to where it was in the list), so going on through a folder carries on from
// there, not from wherever the change put it. Kept only across the reload a change makes (run()), for that one photo.
const PLACE_KEY = "lineagebox-place";
function keepPhotoPlace() {
  const viewer = document.querySelector("[data-viewer]");
  if (!viewer) return;
  const href = (selector) => viewer.querySelector(selector)?.getAttribute("href") ?? null;
  try {
    sessionStorage.setItem(PLACE_KEY, JSON.stringify({
      url: location.href, prev: href('a.step[rel="prev"]'), next: href('a.step[rel="next"]'), back: href("[data-back]"),
      count: viewer.querySelector("[data-count]")?.textContent ?? "" }));
  } catch {}
}
(() => {
  let kept = null;
  try {
    kept = JSON.parse(sessionStorage.getItem(PLACE_KEY) || "null");
    sessionStorage.removeItem(PLACE_KEY);
  } catch {}
  const viewer = document.querySelector("[data-viewer]");
  if (!kept || !viewer || kept.url !== location.href) return;
  const back = viewer.querySelector("[data-back]");
  const moved = { next: viewer.querySelector('a.step[rel="next"]')?.getAttribute("href"), back: back?.getAttribute("href") };
  for (const [rel, mark, label] of [["prev", "‹", t("Previous photo")], ["next", "›", t("Next photo")]]) {
    let link = viewer.querySelector(`a.step[rel="${rel}"]`);
    if (!kept[rel]) {
      link?.remove();
      continue;
    }
    if (!link) {
      link = Object.assign(document.createElement("a"), { className: `step ${rel}`, rel, textContent: mark });
      link.setAttribute("aria-label", label);
      viewer.querySelector("[data-stage]")?.append(link);
    }
    link.setAttribute("href", kept[rel]);
  }
  const count = viewer.querySelector("[data-count]");
  if (count && kept.count) count.textContent = kept.count;
  if (back && kept.back && history.state?.lbFrom === undefined) back.setAttribute("href", kept.back);
  // Remove photo… goes on to the photo that was next, as ›
  document.querySelectorAll("[data-then]").forEach((el) => {
    if (el.dataset.then && [moved.next, moved.back].includes(el.dataset.then)) el.dataset.then = kept.next || kept.back;
  });
})();

document.addEventListener("click", (e) => {  // ‹ › on a photo: in place of this one, not after it
  const link = e.target.closest('[data-viewer] a[rel="prev"], [data-viewer] a[rel="next"]');
  if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button > 0) return;
  e.preventDefault();
  goStep(link.href);
});

// ---- The photo page (data-viewer): only the photo, zoom, swipe, the faces on it, and the details sheet ------------
// On a phone a swipe steps to the next or previous photo, a tap shows only the photo (another tap brings the rest
// back), pinching or a double tap zooms in (a drag then moves about it), and the details are a sheet at the foot that
// a tap or a swipe on its handle slides up. With a mouse: ‹ › and ← →, a double click or Ctrl and the wheel zoom, ⛶
// shows only the photo. The faces found on it are outlined; one clicked or tapped is named in the details.
function setImmersive(on) {
  document.body.classList.toggle("immersive", on);
  if (on && document.fullscreenEnabled && !document.fullscreenElement) {
    document.documentElement.requestFullscreen?.().catch(() => {});
  } else if (!on && document.fullscreenElement) {
    document.exitFullscreen?.().catch(() => {});
  }
  dispatchEvent(new Event("resize"));
}
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && document.querySelector("[data-viewer]")) document.body.classList.remove("immersive");
});

document.querySelectorAll("[data-viewer]").forEach((viewer) => {
  const stage = viewer.querySelector("[data-stage]");
  const zoomBox = viewer.querySelector("[data-zoom]");
  const photo = viewer.querySelector("[data-photo]");
  const boxes = viewer.querySelector("[data-face-boxes]");
  const sheet = viewer.querySelector("[data-sheet]");
  const handle = viewer.querySelector("[data-sheet-toggle]");
  try {
    if (sessionStorage.getItem("lineagebox-immersive")) document.body.classList.add("immersive");
    sessionStorage.removeItem("lineagebox-immersive");
  } catch {}
  viewer.querySelector("[data-full]")?.addEventListener("click", () =>
    setImmersive(!document.body.classList.contains("immersive")));

  // the details sheet (on a phone): its handle's tap or swipe slides it up or down
  const openSheet = (open) => {
    viewer.classList.toggle("sheet-open", open);
    handle?.setAttribute("aria-expanded", open);
    handle?.setAttribute("aria-label", open ? t("Hide the details") : t("Show the details"));
    if (!open && sheet) sheet.scrollTop = 0;
  };
  let grabbed = null;
  handle?.addEventListener("pointerdown", (e) => (grabbed = e.clientY));
  handle?.addEventListener("pointerup", (e) => {
    const dy = grabbed === null ? 0 : e.clientY - grabbed;
    grabbed = null;
    if (Math.abs(dy) > 30) {
      openSheet(dy < 0);
      handle.dataset.swiped = "";
    }
  });
  handle?.addEventListener("click", () => {
    if ("swiped" in handle.dataset) delete handle.dataset.swiped;
    else openSheet(!viewer.classList.contains("sheet-open"));
  });
  sheet?.addEventListener("focusin", (e) => e.target !== handle && openSheet(true));  // a key that types in it

  // the faces on the photo: the boxes lie over the picture as shown (its element is the picture's own shape)
  const fit = () => {
    if (!boxes || !photo) return;
    Object.assign(boxes.style, { left: `${photo.offsetLeft}px`, top: `${photo.offsetTop}px`,
                                 width: `${photo.offsetWidth}px`, height: `${photo.offsetHeight}px` });
  };
  if (photo) {
    if (photo.complete) fit();
    photo.addEventListener("load", fit);
    addEventListener("resize", fit);
  }
  const facesButton = viewer.querySelector("[data-faces-toggle]");
  const showFaces = (on) => {
    boxes?.classList.toggle("off", !on);
    facesButton?.setAttribute("aria-pressed", on);
  };
  let facesOn = true;
  try { facesOn = localStorage.getItem("lineagebox-faces") !== "off"; } catch {}
  showFaces(facesOn);
  facesButton?.addEventListener("click", () => {
    facesOn = !facesOn;
    showFaces(facesOn);
    try { localStorage.setItem("lineagebox-faces", facesOn ? "on" : "off"); } catch {}
  });
  boxes?.addEventListener("click", (e) => {
    const box = e.target.closest("[data-face-box]");
    const row = box && document.getElementById(`f${box.dataset.faceBox}`);
    if (!row) return;
    openSheet(true);
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    row.classList.add("lit");
    setTimeout(() => row.classList.remove("lit"), 2500);
    const input = row.querySelector(":scope > form input[name=name]");
    if (input) input.focus({ preventScroll: true });
    else row.querySelector('[data-toggle][aria-expanded="false"]')?.click();  // named: Fix, to say who it is instead
  });

  // a Live Photo: its clip plays over the still, where the still is, and is gone when it ends
  viewer.querySelector("[data-live]")?.addEventListener("click", (e) => {
    if (!photo || zoomBox.querySelector("video.live")) return;
    const clip = Object.assign(document.createElement("video"),
      { src: e.currentTarget.dataset.live, autoplay: true, playsInline: true, className: "live" });
    Object.assign(clip.style, { left: `${photo.offsetLeft}px`, top: `${photo.offsetTop}px`,
                                width: `${photo.offsetWidth}px`, height: `${photo.offsetHeight}px` });
    const done = () => clip.remove();
    clip.addEventListener("ended", done);
    clip.addEventListener("error", done);
    clip.addEventListener("click", done);
    zoomBox.append(clip);
  });

  if (!photo) return;
  // zoom and move about: the stage's content is scaled from its top left corner, then moved by x, y
  stage.style.touchAction = "none";
  let scale = 1, x = 0, y = 0;
  const apply = (smooth) => {
    zoomBox.style.transition = smooth ? "transform .2s" : "none";
    zoomBox.style.transform = scale === 1 ? "" : `translate(${x}px, ${y}px) scale(${scale})`;
    viewer.classList.toggle("zoomed", scale > 1);
  };
  const clamp = () => {
    x = Math.min(0, Math.max(stage.clientWidth * (1 - scale), x));
    y = Math.min(0, Math.max(stage.clientHeight * (1 - scale), y));
  };
  const zoomAt = (px, py, to) => {  // px, py: a point on the stage, which stays where it is
    const cx = (px - x) / scale, cy = (py - y) / scale;
    scale = to;
    x = px - cx * scale;
    y = py - cy * scale;
    if (scale <= 1.02) [scale, x, y] = [1, 0, 0];
    clamp();
  };
  const pointers = new Map();
  let start = null;   // one finger (or the mouse) down: where, when, and where the photo was
  let pinch = null;   // two fingers: how far apart, and the zoom then
  let lastTap = 0, tapTimer = 0;
  const local = (e) => {
    const r = stage.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const middle = () => {
    const [a, b] = [...pointers.values()];
    return { d: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) || 1,
             at: local({ clientX: (a.clientX + b.clientX) / 2, clientY: (a.clientY + b.clientY) / 2 }) };
  };
  stage.addEventListener("pointerdown", (e) => {
    if (e.target.closest("a, button, video, .note") || (e.pointerType === "mouse" && e.button !== 0)) return;
    stage.setPointerCapture?.(e.pointerId);
    pointers.set(e.pointerId, e);
    if (pointers.size === 1) {
      start = { x: e.clientX, y: e.clientY, ox: x, oy: y, moved: false };
    } else if (pointers.size === 2) {
      const m = middle();
      pinch = { d: m.d, at: m.at, scale, ox: x, oy: y };
      start = null;
    }
  });
  stage.addEventListener("pointermove", (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, e);
    if (pinch && pointers.size === 2) {
      const m = middle();
      const to = Math.min(5, Math.max(1, pinch.scale * m.d / pinch.d));
      // what was under the fingers when they came down stays under them
      const cx = (pinch.at[0] - pinch.ox) / pinch.scale, cy = (pinch.at[1] - pinch.oy) / pinch.scale;
      scale = to;
      x = m.at[0] - cx * scale;
      y = m.at[1] - cy * scale;
      clamp();
      apply(false);
      return;
    }
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (Math.abs(dx) + Math.abs(dy) > 8) start.moved = true;
    if (scale > 1) {
      x = start.ox + dx;
      y = start.oy + dy;
      clamp();
      apply(false);
    } else if (e.pointerType !== "mouse" && start.moved && Math.abs(dx) > Math.abs(dy)) {
      zoomBox.style.transition = "none";
      zoomBox.style.transform = `translateX(${dx}px)`;  // the photo follows the finger
    }
  });
  const end = (e) => {
    if (!pointers.delete(e.pointerId)) return;
    if (pinch) {
      if (pointers.size < 2) {
        pinch = null;
        if (scale <= 1.02) [scale, x, y] = [1, 0, 0];
        apply(true);
      }
      return;
    }
    const s0 = start;
    start = null;
    if (!s0) return;
    const dx = e.clientX - s0.x, dy = e.clientY - s0.y;
    if (e.type === "pointercancel") return apply(true);
    if (scale === 1 && s0.moved) {
      const link = Math.abs(dx) > 60 && Math.abs(dx) > 1.5 * Math.abs(dy) && e.pointerType !== "mouse"
        && viewer.querySelector(`a[rel="${dx < 0 ? "next" : "prev"}"]`);
      if (link) {
        zoomBox.style.transition = "transform .15s";
        zoomBox.style.transform = `translateX(${dx < 0 ? -100 : 100}vw)`;
        return goStep(link.href);
      }
      return apply(true);  // nothing that way: it springs back
    }
    if (s0.moved) return;  // moved about a zoomed photo
    // a tap or click: two close together zoom in there, or out; a finger's tap alone shows only the photo
    const now = performance.now();
    if (now - lastTap < 300) {
      clearTimeout(tapTimer);
      lastTap = 0;
      if (scale > 1) [scale, x, y] = [1, 0, 0];
      else zoomAt(...local(e), 2.5);
      return apply(true);
    }
    lastTap = now;
    if (e.pointerType !== "mouse") {
      tapTimer = setTimeout(() => setImmersive(!document.body.classList.contains("immersive")), 300);
    }
  };
  stage.addEventListener("pointerup", end);
  stage.addEventListener("pointercancel", end);
  stage.addEventListener("wheel", (e) => {  // Ctrl and the wheel, or a trackpad's pinch
    if (!e.ctrlKey) return;
    e.preventDefault();
    zoomAt(...local(e), Math.min(5, Math.max(1, scale * Math.exp(-e.deltaY / 200))));
    apply(false);
  }, { passive: false });
  addEventListener("resize", () => {
    [scale, x, y] = [1, 0, 0];
    apply(false);
  });
});

// Tick-photos bar: Download sends the ticked photos' ids as a plain form, and the browser saves the zip it gets back.
document.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-download]");
  if (!button) return;
  const ids = tickedIds(button.closest("form"));  // with those ticked further down the list or on its other pages
  if (!ids.length) return notice(t("Tick the photos to download first."), { error: true });
  const most = Number(button.dataset.most);  // one zip holds so many (common.ZIP_MOST): never fewer than were ticked
  if (most && ids.length > most) {
    return notice(t("That's {n} photos: one download holds at most {most}. Tick fewer, and download them in parts.",
      { n: num(ids.length), most: num(most) }), { error: true });
  }
  const form = Object.assign(document.createElement("form"), { method: "post", action: "/download" });
  ids.forEach((id) => form.append(Object.assign(document.createElement("input"), { type: "hidden", name: "image", value: id })));
  document.body.append(form);
  form.submit();
  form.remove();
});

// Upload page: files and folders dropped or picked go up 3 at a time, each as the body of its own request (so a big
// video is never held in memory), with a bar for each. XMLHttpRequest, because fetch can't tell how far an upload is.
document.querySelectorAll("[data-upload]").forEach((zone) => {
  const list = zone.querySelector("[data-upload-list]");
  const status = zone.querySelector("[data-upload-status]");
  const folder = zone.querySelector('input[name="folder"]');
  const queue = [];
  let running = 0, left = 0, done = 0, failed = 0;

  const show = () => {
    status.textContent = left
      ? t("{done} uploaded, {left} to go", { done, left }) + (failed ? t(", {n} not taken", { n: failed }) : "") + "…"
      : t("{done} uploaded", { done }) + (failed ? t(", {n} not taken", { n: failed }) : "")
        + t(". They show here once the worker has read them.");
  };
  const send = ({ file, path, item }) => {
    running++;
    const bar = item.querySelector("progress");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload?${new URLSearchParams({ path, folder: folder.value.trim() })}`);
    xhr.setRequestHeader("X-Requested-With", "lineagebox");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.value = e.loaded / e.total; };
    item.classList.remove("failed");
    item.querySelector("button")?.remove();
    xhr.onload = () => {
      let detail = "";
      try { detail = JSON.parse(xhr.responseText).detail ?? ""; } catch {}
      if (xhr.status === 200) { done++; item.classList.add("ok"); bar.value = 1; }
      else fail(item, detail || t("didn't work ({status})", { status: xhr.status }), xhr.status >= 500 || [408, 429].includes(xhr.status));
    };
    xhr.onerror = () => fail(item, t("the connection dropped"), true);
    xhr.onloadend = () => { running--; left--; show(); next(); };
    xhr.send(file);
  };
  // A file that wasn't taken for a reason that may pass (the connection, a busy server) can be sent again.
  const fail = (item, why, again) => {
    failed++;
    item.classList.add("failed");
    item.querySelector("small").textContent = why;
    if (!again) return;
    const retry = Object.assign(document.createElement("button"), { type: "button", className: "quiet",
                                                                    textContent: t("Try again") });
    retry.addEventListener("click", () => {
      failed--;
      left++;
      item.querySelector("small").textContent = "";
      queue.push(item.upload);
      show();
      next();
    });
    item.append(retry);
  };
  const next = () => { while (running < 3 && queue.length) send(queue.shift()); };
  const add = (file, path) => {
    const item = document.createElement("li");
    item.append(Object.assign(document.createElement("span"), { textContent: path }),
                Object.assign(document.createElement("progress"), { max: 1, value: 0, title: t("Sending {path}", { path }) }),
                document.createElement("small"));
    list.prepend(item);
    item.upload = { file, path, item };
    queue.push(item.upload);
    left++;
    show();
    next();
  };
  // a dropped folder: every file in it, with its path, however deep (a folder's entries come a batch at a time)
  const walk = async (entry, path) => {
    if (entry.isFile) return new Promise((ok) => entry.file((f) => { add(f, path + f.name); ok(); }, ok));
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise((ok) => reader.readEntries(ok, () => ok([])));
      if (!batch.length) break;
      for (const child of batch) await walk(child, `${path}${entry.name}/`);
    }
  };
  zone.querySelectorAll("[data-pick]").forEach((input) => input.addEventListener("change", () => {
    [...input.files].forEach((f) => add(f, f.webkitRelativePath || f.name));
    input.value = "";
  }));
  const drop = zone.querySelector("[data-drop]");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    const entries = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
    if (entries.length) entries.forEach((entry) => walk(entry, ""));
    else [...e.dataTransfer.files].forEach((f) => add(f, f.name));
  });
  addEventListener("beforeunload", (e) => { if (left) e.preventDefault(); });  // leaving mid-upload asks first
});

// Family page: a GEDCOM file to bring in goes as the request's body; the matches are then shown for checking.
document.getElementById("gedcom-upload")?.addEventListener("click", async () => {
  const file = document.getElementById("gedcom-file").files[0], note = document.getElementById("gedcom-note");
  if (!file) { note.textContent = t("Pick a file first."); return; }
  note.textContent = t("Reading it and looking for who is who…");
  let r, result = {};
  try {
    r = await fetch(`/api/family/import?name=${encodeURIComponent(file.name)}`, {
      method: "POST", body: file, headers: { "X-Requested-With": "lineagebox", "Content-Type": "application/octet-stream" } });
    try { result = JSON.parse(await r.text()); } catch {}  // a proxy's error page, say
  } catch {
    note.textContent = t("The file didn't get through: check the connection and try again.");
    return;
  }
  if (r.ok && result.go) location.href = result.go;
  else note.textContent = result.detail || t("That didn't work ({status}).", { status: r.status });
});

// Settings: the menu at the side shows one part of the page at a time (each a data-part). Which one the address's #
// says: the part holding that panel or heading (#https, #add, as other pages link to them), else the first part. Its
// link in the menu is marked. Without this script every part shows, one under another, and the menu's links jump to
// them.
const settingsParts = [...document.querySelectorAll("[data-part]")];

function showSettingsPart(e) {  // e: the # changed (a link in the menu); none: the page opened
  if (!settingsParts.length) return;
  let id = "";
  try { id = decodeURIComponent(location.hash.slice(1)); } catch {}
  const target = id ? document.getElementById(id) : null;
  const part = target?.closest("[data-part]") ?? settingsParts[0];
  settingsParts.forEach((p) => (p.hidden = p !== part));
  document.querySelectorAll(".settings-nav a[href^='/settings#']").forEach((link) => {
    const its = document.getElementById(link.hash.slice(1))?.closest("[data-part]");
    if (its === part) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (!target || (e && target === part.firstElementChild)) {
    if (e) scrollTo(0, 0);  // a part picked in the menu shows from the top
  } else {
    target.scrollIntoView();  // as the browser does for a #, now that its part shows (#https, #add)
  }
}
showSettingsPart();
addEventListener("hashchange", showSettingsPart);

// Pointing at a photo or a face for a moment (PEEK_AFTER ms: Settings → Browsing, on <body>) shows the whole photo
// larger beside it: what is around a face tells who it is, without opening the photo. Once one shows, the next photo
// pointed at shows at once; moving off, scrolling, clicking or a key puts it away. It never takes the pointer, so ↗
// and × stay clickable under it. On touch screens, where nothing is pointed at, holding a finger on a face for a moment
// shows its photo until the finger is lifted (the tap it would have been does nothing). Not when the setting is 0;
// covers (folders, memories, suggested tours) are left alone.
const PEEK_AFTER = Number(document.body.dataset.hoverPreview ?? 500);
const PEEK_WARM = 400;  // ms after one is put away in which the next shows without waiting
const PEEK_HOLD = 450;  // ms a finger is held on a face before its photo shows
if (PEEK_AFTER > 0) {
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  const skip = ".folder-card, .memory, .suggestion, .player, .peek, [data-no-preview]";
  const peek = document.createElement("div");
  peek.className = "peek";
  peek.hidden = true;
  peek.setAttribute("aria-hidden", "true");
  peek.innerHTML = '<div class="peek-in"><img alt=""><span class="peek-face"></span></div>';
  document.body.append(peek);
  const inner = peek.firstElementChild;
  const pic = inner.querySelector("img");
  const mark = inner.querySelector(".peek-face");
  const faces = new Map();  // face id -> what /api/faces/{id}/photo said
  let at = null;  // {holder, img} pointed at
  let timer = 0;
  let warm = 0;
  let turn = 0;  // which showing is current: a picture that loads late for an earlier one is dropped

  const pointedAt = (node) => {
    const holder = node?.closest?.(".tile, .face, img");
    const img = holder?.matches("img") ? holder : holder?.querySelector("img");
    if (!img || img.closest(skip) || !/^\/(t|face)\//.test(img.getAttribute("src") || "")) return null;
    return { holder, img };
  };
  const about = async (img) => {
    const src = img.getAttribute("src");
    let m = src.match(/^\/t\/(\w+)/);
    if (m) return { full: `/p/${m[1]}`, low: src, w: img.naturalWidth, h: img.naturalHeight };
    m = src.match(/^\/face\/(\d+)/);
    if (!m) return null;
    if (!faces.has(m[1])) {
      faces.set(m[1], fetch(`/api/faces/${m[1]}/photo`).then((r) => (r.ok ? r.json() : null)).catch(() => null));
    }
    const f = await faces.get(m[1]);
    return f && { full: f.src, low: f.thumb, box: f.box, w: f.width, h: f.height };
  };
  const place = (rect, w, h) => {  // beside what is pointed at, on the side with more room; w, h: as shown
    const edge = 12;
    const gap = 16;
    const right = innerWidth - rect.right - gap - edge;
    const left = rect.left - gap - edge;
    const scale = Math.min(Math.min(innerWidth * 0.55, 1000) / w, (innerHeight * 0.8) / h, Math.max(right, left, 320) / w);
    const pw = Math.round(w * scale);
    const ph = Math.round(h * scale);
    const x = right >= left ? rect.right + gap : rect.left - gap - pw;
    peek.style.left = `${Math.max(edge, Math.min(x, innerWidth - pw - edge))}px`;
    peek.style.top = `${Math.max(edge, Math.min(rect.top + rect.height / 2 - ph / 2, innerHeight - ph - edge))}px`;
    peek.style.width = `${pw}px`;
    peek.style.height = `${ph}px`;
    return [pw, ph];
  };
  const show = async (t) => {
    const mine = ++turn;
    const info = await about(t.img);
    if (!info || mine !== turn) return;
    let { w, h } = info;
    if (!w || !h) {  // its shape isn't known: from the picture itself
      const probe = new Image();
      probe.src = info.low || info.full;
      try {
        await probe.decode();
      } catch {
        return;
      }
      if (mine !== turn) return;
      [w, h] = [probe.naturalWidth, probe.naturalHeight];
    }
    const turned = Number(t.img.className.match(/\bturn-(90|180|270)\b/)?.[1] || 0);  // Turned photos: as turned
    const across = turned === 90 || turned === 270;
    const [pw, ph] = place(t.holder.getBoundingClientRect(), across ? h : w, across ? w : h);
    inner.style.width = `${across ? ph : pw}px`;
    inner.style.height = `${across ? pw : ph}px`;
    inner.style.transform = `translate(-50%, -50%) rotate(${turned}deg)`;
    mark.hidden = !info.box;
    if (info.box) {
      const [x1, y1, x2, y2] = info.box;
      Object.assign(mark.style, { left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`,
                                  height: `${(y2 - y1) * 100}%` });
    }
    if (info.low) pic.src = info.low;  // the small picture at once, the large one once it is in
    else pic.removeAttribute("src");
    peek.hidden = false;
    const big = new Image();
    big.src = info.full;
    big.decode().then(() => mine === turn && (pic.src = info.full)).catch(() => {});
  };
  const hide = (cold) => {
    clearTimeout(timer);
    turn++;
    warm = cold || peek.hidden ? 0 : performance.now() + PEEK_WARM;
    peek.hidden = true;
    at = null;
  };
  if (fine) {
    document.addEventListener("mouseover", (e) => {
      const t = pointedAt(e.target);
      if (!t || t.holder === at?.holder) return;
      clearTimeout(timer);
      at = t;
      if (!peek.hidden || performance.now() < warm) show(t);
      else timer = setTimeout(() => show(t), PEEK_AFTER);
    });
    document.addEventListener("mouseout", (e) => {
      if (!at || at.holder.contains(e.relatedTarget) || pointedAt(e.relatedTarget)) return;  // within, or on to another
      hide(false);
    });
  } else {
    let held = false;  // a face's photo shown by holding a finger on it
    document.addEventListener("touchstart", (e) => {
      const t = pointedAt(e.target);
      clearTimeout(timer);
      held = false;
      if (!t || !t.img.getAttribute("src").startsWith("/face/")) return;
      timer = setTimeout(() => { held = true; show(t); }, PEEK_HOLD);
    }, { passive: true });
    document.addEventListener("touchmove", () => clearTimeout(timer), { passive: true });
    document.addEventListener("touchend", (e) => {
      clearTimeout(timer);
      if (!held) return;
      held = false;
      if (e.cancelable) e.preventDefault();  // only a look: the face isn't opened or ticked
      hide(true);
    });
    document.addEventListener("contextmenu", (e) => { if (pointedAt(e.target)?.img.src.includes("/face/")) e.preventDefault(); });
  }
  for (const type of ["scroll", "mousedown", "keydown", "blur"]) addEventListener(type, () => hide(true), { capture: true, passive: true });
}

// Home's row of memories: ‹ › scroll it by what shows of it.
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-scroll]");
  const row = button && document.querySelector(button.dataset.scroll);
  if (row) row.scrollBy({ left: Number(button.dataset.by || 1) * row.clientWidth * 0.9, behavior: "smooth" });
});

// Folded groups on the Family page and the Timeline's years (details with data-glance): pointing at one's heading for
// a moment shows its cards floating over what follows, so one can be clicked without unfolding it, and moving off puts
// them away. A click on the heading keeps it unfolded, and another folds it again; on touch screens a tap does that. A
// link in the heading (a Timeline year) is followed as usual.
const GLANCE_AFTER = 250;   // ms pointing at a heading before its cards show
const GLANCE_LINGER = 300;  // ms off it before they go: time to cross a gap, or to come back
if (matchMedia("(hover: hover) and (pointer: fine)").matches) {
  const putAway = (group) => {
    group.classList.remove("glancing");
    group.open = false;
  };
  document.querySelectorAll("details[data-glance]").forEach((group) => {
    const heading = group.querySelector(":scope > summary");
    let opening = 0;
    let closing = 0;
    heading.addEventListener("mouseenter", () => {
      if (group.open) return;
      opening = setTimeout(() => {
        document.querySelectorAll("details.glancing").forEach((other) => other !== group && putAway(other));
        group.classList.add("glancing");
        group.open = true;
      }, GLANCE_AFTER);
    });
    heading.addEventListener("mouseleave", () => clearTimeout(opening));
    group.addEventListener("mouseenter", () => clearTimeout(closing));
    group.addEventListener("mouseleave", () => {
      clearTimeout(opening);
      if (group.classList.contains("glancing")) closing = setTimeout(() => putAway(group), GLANCE_LINGER);
    });
    heading.addEventListener("click", (e) => {
      clearTimeout(opening);
      // folded or unfolded for good, or a link: the click does as usual
      if (!group.classList.contains("glancing") || e.target.closest("a[href]")) return;
      e.preventDefault();  // showing for a moment: now it stays
      group.classList.remove("glancing");
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") document.querySelectorAll("details.glancing").forEach(putAway);
  });
}

// The whole line down from one ancestor (family_tree.html): the tree on a sheet in a frame. Dragging moves it (so do
// the arrow keys, the frame focused); the wheel, a pinch and the buttons (+ − 0) zoom. A card still opens its person's
// page with a click, but not at the end of a drag. It starts with all of the tree if that is still readable, else its
// top at a readable size; or on the person asked for (data-person), marked.
const treeFrame = document.querySelector("[data-pan-zoom]");
if (treeFrame) {
  const sheet = treeFrame.firstElementChild;
  const READABLE = 0.6;  // the smallest zoom it starts at
  const MOST = 3;
  const EDGE = 16;       // px around the tree when all of it shows
  const KEEP = 60;       // px of the tree that always stay in the frame
  let scale = 1;
  let x = 0;
  let y = 0;
  treeFrame.classList.add("panning");  // without this script the frame just scrolls
  const across = () => sheet.offsetWidth;  // as laid out, before zooming
  const down = () => sheet.offsetHeight;
  const fitScale = () => Math.min(1, (treeFrame.clientWidth - 2 * EDGE) / across(),
                                  (treeFrame.clientHeight - 2 * EDGE) / down());
  const show = () => {
    x = Math.min(treeFrame.clientWidth - KEEP, Math.max(KEEP - across() * scale, x));
    y = Math.min(treeFrame.clientHeight - KEEP, Math.max(KEEP - down() * scale, y));
    sheet.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  };
  const zoomAt = (factor, px, py) => {  // px, py: the point that stays put, from the frame's corner
    const s = Math.min(MOST, Math.max(Math.min(0.25, fitScale()), scale * factor));
    x = px - ((px - x) * s) / scale;
    y = py - ((py - y) * s) / scale;
    scale = s;
    show();
  };
  const fit = () => {
    scale = fitScale();
    x = (treeFrame.clientWidth - across() * scale) / 2;
    y = Math.max(EDGE, (treeFrame.clientHeight - down() * scale) / 2);
    show();
  };
  const onto = (card) => {  // that card at full size, across the middle and a third of the way down (or the tree
    // in the middle, if it fits across; its top at the top, if they are near it)
    const s = sheet.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    const cx = (c.left + c.width / 2 - s.left) / scale;
    const cy = (c.top + c.height / 2 - s.top) / scale;
    scale = 1;
    x = across() <= treeFrame.clientWidth ? (treeFrame.clientWidth - across()) / 2 : treeFrame.clientWidth / 2 - cx;
    y = Math.min(EDGE, treeFrame.clientHeight / 3 - cy);
    show();
  };
  const wanted = treeFrame.dataset.person && sheet.querySelector(`a.fcard[href="/person/${treeFrame.dataset.person}"]`);
  if (wanted) {
    wanted.classList.add("here");
    onto(wanted);
  } else if (fitScale() >= READABLE) {
    fit();
  } else {
    scale = READABLE;
    x = (treeFrame.clientWidth - across() * scale) / 2;  // the top of the tree, in the middle
    y = EDGE;
    show();
  }
  addEventListener("resize", show);

  const pointers = new Map();  // pointer id -> [x, y], last seen
  let from = null;      // where the first pointer went down
  let dragged = false;  // it moved far enough to be a drag, not a click
  let swallow = false;  // the click that ends a drag opens nothing
  let spread = 0;       // between two fingers, last seen
  const together = () => {
    const [[ax, ay], [bx, by]] = [...pointers.values()];
    const r = treeFrame.getBoundingClientRect();
    return { mid: [(ax + bx) / 2 - r.left, (ay + by) / 2 - r.top], apart: Math.hypot(ax - bx, ay - by) };
  };
  treeFrame.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 1) {
      from = [e.clientX, e.clientY];
      dragged = false;
    } else if (pointers.size === 2) {
      spread = together().apart;
      dragged = true;
    }
  });
  treeFrame.addEventListener("pointermove", (e) => {
    const was = pointers.get(e.pointerId);
    if (!was) return;
    if (!dragged) {
      if (Math.hypot(e.clientX - from[0], e.clientY - from[1]) < 5) return;
      dragged = true;
      treeFrame.setPointerCapture(e.pointerId);
      treeFrame.classList.add("dragging");
    }
    if (pointers.size === 2) {
      const before = together();
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      const now = together();
      x += now.mid[0] - before.mid[0];
      y += now.mid[1] - before.mid[1];
      zoomAt(now.apart / (spread || now.apart), ...now.mid);
      spread = now.apart;
      return;
    }
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    x += e.clientX - was[0];
    y += e.clientY - was[1];
    show();
  });
  const lifted = (e) => {
    if (!pointers.delete(e.pointerId)) return;
    if (pointers.size) return;
    treeFrame.classList.remove("dragging");
    if (dragged) {
      swallow = true;
      setTimeout(() => (swallow = false));
    }
  };
  treeFrame.addEventListener("pointerup", lifted);
  treeFrame.addEventListener("pointercancel", lifted);
  treeFrame.addEventListener("click", (e) => {
    if (!swallow) return;
    e.preventDefault();
    e.stopPropagation();
    swallow = false;
  }, true);
  treeFrame.addEventListener("dragstart", (e) => e.preventDefault());  // a card's picture or link isn't dragged off
  treeFrame.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = treeFrame.getBoundingClientRect();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? treeFrame.clientHeight : 1;
    // a pinch on a touchpad comes as the wheel with Ctrl, in small steps
    zoomAt(Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  treeFrame.addEventListener("keydown", (e) => {
    const by = { ArrowLeft: [80, 0], ArrowRight: [-80, 0], ArrowUp: [0, 80], ArrowDown: [0, -80] }[e.key];
    if (!by || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    e.stopPropagation();
    x += by[0];
    y += by[1];
    show();
  });
  document.addEventListener("click", (e) => {
    const button = e.target.closest("button[data-zoom]");
    if (!button) return;
    if (button.dataset.zoom === "fit") fit();
    else zoomAt(button.dataset.zoom === "in" ? 1.25 : 0.8, treeFrame.clientWidth / 2, treeFrame.clientHeight / 2);
  });

  document.querySelector("[data-tree-picture]")?.addEventListener("click", async (e) => {
    const button = e.currentTarget;
    const note = document.querySelector("[data-tree-note]");
    const said = note.textContent;
    button.disabled = true;
    note.textContent = t("Drawing the picture…");
    try {
      const picture = await treePicture(sheet);
      const link = Object.assign(document.createElement("a"),
                                 { href: URL.createObjectURL(picture), download: button.dataset.file });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 60_000);
      note.textContent = said;
    } catch (err) {
      note.textContent = t("The picture couldn't be made: {why}", { why: err.message });
    } finally {
      button.disabled = false;
    }
  });
}

// The tree as one PNG, drawn from the page as it is laid out: each box's background and border, the lines between
// the cards (the stylesheet draws them with ::before and ::after), the faces and the words. Twice the size it is on
// screen, or less for a tree too large for the browser to hold as one picture. Always in the light colours, on white,
// whichever the page shows: the picture is printed, sent and shown elsewhere.
const PICTURE_MOST_SIDE = 16_384;         // px: browsers refuse a canvas wider or taller
const PICTURE_MOST_PIXELS = 120_000_000;  // and one larger than about this
async function treePicture(sheet) {
  const held = sheet.style.transform;
  const root = document.documentElement;
  const theme = root.dataset.theme;
  sheet.style.transform = "none";  // measured as laid out, unzoomed
  root.classList.add("drawing");  // no colour fades (app.css) while the light colours are read
  root.dataset.theme = "light";
  const origin = sheet.getBoundingClientRect();
  const items = [];
  try {
    measure(sheet, origin, items, 1);
  } finally {
    sheet.style.transform = held;
    if (theme) root.dataset.theme = theme;
    else delete root.dataset.theme;
    void getComputedStyle(sheet).color;  // the page's own colours back before fades are allowed again
    root.classList.remove("drawing");
  }
  const faces = new Map();  // src -> the picture, loaded once (a card off screen may not have loaded its own)
  await Promise.all(items.filter((i) => i.kind === "img").map(async (i) => {
    const src = i.el.currentSrc || i.el.src;
    if (!faces.has(src)) {
      const picture = new Image();
      picture.src = src;
      faces.set(src, picture.decode().then(() => picture, () => null));
    }
    i.picture = await faces.get(src);
  }));
  const width = Math.ceil(origin.width);
  const height = Math.ceil(origin.height);
  const background = "#ffffff";
  let scale = Math.min(2, PICTURE_MOST_SIDE / width, PICTURE_MOST_SIDE / height,
                       Math.sqrt(PICTURE_MOST_PIXELS / (width * height)));
  for (; scale >= 0.05; scale /= 2) {  // a browser that can't hold that much gives no picture: smaller, then
    const canvas = Object.assign(document.createElement("canvas"),
                                 { width: Math.round(width * scale), height: Math.round(height * scale) });
    const ctx = canvas.getContext("2d");
    if (!ctx) continue;
    ctx.scale(scale, scale);
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);
    items.forEach((i) => drawItem(ctx, i));
    const blob = await new Promise((done) => canvas.toBlob(done, "image/png"));
    if (blob) return blob;
  }
  throw new Error(t("the tree is too large for this browser to draw as one picture"));
}

const painted = (color) => color && color !== "transparent" && !/(,\s*0\)|\/\s*0\))$/.test(color);

function corners(cs, box) {  // each corner's radius, in px: "10px", "50%" (a round face), "999px" (a pill)
  return ["TopLeft", "TopRight", "BottomRight", "BottomLeft"].map((k) => {
    const value = cs[`border${k}Radius`].split(" ")[0];
    const n = parseFloat(value) || 0;
    return Math.min(value.endsWith("%") ? (n / 100) * Math.min(box.w, box.h) : n, box.w / 2, box.h / 2);
  });
}

function boxLook(cs, box) {  // what there is to draw of a box: its fill and its borders; null when nothing
  const sides = ["Top", "Right", "Bottom", "Left"].map((s) => {
    const side = { w: parseFloat(cs[`border${s}Width`]) || 0, style: cs[`border${s}Style`],
                   color: cs[`border${s}Color`] };
    return side.w > 0 && side.style !== "none" && side.style !== "hidden" && painted(side.color) ? side : null;
  });
  const fill = painted(cs.backgroundColor) ? cs.backgroundColor : null;
  return fill || sides.some(Boolean) ? { fill, sides, radius: corners(cs, box) } : null;
}

function measure(el, origin, items, alpha) {
  const cs = getComputedStyle(el);
  if (cs.display === "none" || cs.visibility === "hidden") return;
  alpha *= Number(cs.opacity);
  const r = el.getBoundingClientRect();
  const box = { x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height };
  const look = boxLook(cs, box);
  if (look) items.push({ kind: "box", box, look, alpha });
  if (el.tagName === "IMG") items.push({ kind: "img", el, box, radius: corners(cs, box), alpha });
  pseudo(el, "::before", cs, box, items, alpha, -1);
  const mark = items.length;
  for (const child of el.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) wordItems(child, cs, origin, items, alpha);
    else if (child.nodeType === Node.ELEMENT_NODE) measure(child, origin, items, alpha);
  }
  pseudo(el, "::after", cs, box, items, alpha, mark);
}

function pseudo(el, which, owner, ownerBox, items, alpha, mark) {
  const cs = getComputedStyle(el, which);
  if (cs.content === "none" || cs.content === "normal" || cs.display === "none") return;
  alpha *= Number(cs.opacity);
  const px = (v) => parseFloat(v) || 0;
  if (cs.position === "absolute") {  // a line between cards, placed against its li or ul (position: relative)
    // the stylesheet's * { box-sizing } doesn't reach ::before and ::after: their width is inside the border
    const extra = (a, b) => (cs.boxSizing === "border-box" ? 0 : px(cs[`padding${a}`]) + px(cs[`padding${b}`]) +
                                                               px(cs[`border${a}Width`]) + px(cs[`border${b}Width`]));
    const w = px(cs.width) + extra("Left", "Right");
    const h = px(cs.height) + extra("Top", "Bottom");
    const inner = { x: ownerBox.x + px(owner.borderLeftWidth), y: ownerBox.y + px(owner.borderTopWidth),
                    w: ownerBox.w - px(owner.borderLeftWidth) - px(owner.borderRightWidth) };
    const x = cs.left !== "auto" ? inner.x + px(cs.left) : inner.x + inner.w - px(cs.right) - w;
    const box = { x, y: inner.y + px(cs.top), w, h };
    const look = boxLook(cs, box);
    if (look) items.push({ kind: "box", box, look, alpha });
    return;
  }
  // words the stylesheet adds after an element's own (the † after a name): after its last word
  const text = cs.content.replace(/^["']|["']$/g, "");
  const last = mark < 0 ? null : items.slice(mark).findLast((i) => i.kind === "text");
  if (!text.trim() || !last) return;
  items.push({ kind: "text", text, x: last.right, y: last.y, h: last.h, right: last.right, alpha, color: cs.color,
               font: `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}` });
}

function wordItems(node, cs, origin, items, alpha) {
  if (!node.data.trim()) return;
  const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const upper = cs.textTransform === "uppercase";
  const range = document.createRange();
  const add = (text, r) => items.push({ kind: "text", text: upper ? text.toUpperCase() : text, x: r.left - origin.left,
                                        y: r.top - origin.top, h: r.height, right: r.right - origin.left, font,
                                        color: cs.color, alpha });
  for (const m of node.data.matchAll(/\S+/g)) {
    range.setStart(node, m.index);
    range.setEnd(node, m.index + m[0].length);
    const rects = [...range.getClientRects()].filter((r) => r.width > 0);
    if (rects.length === 1) {
      add(m[0], rects[0]);
      continue;
    }
    for (let i = 0; i < m[0].length; i++) {  // a long name broken across lines: letter by letter
      range.setStart(node, m.index + i);
      range.setEnd(node, m.index + i + 1);
      const r = range.getClientRects()[0];
      if (r) add(m[0][i], r);
    }
  }
}

function pen(ctx, side) {
  ctx.strokeStyle = side.color;
  ctx.lineWidth = side.w;
  ctx.setLineDash(side.style === "dashed" ? [side.w * 3, side.w * 2] : side.style === "dotted" ? [side.w, side.w] : []);
}

function drawItem(ctx, item) {
  ctx.globalAlpha = item.alpha;
  if (item.kind === "text") {
    ctx.font = item.font;
    ctx.fillStyle = item.color;
    ctx.textBaseline = "alphabetic";
    const m = ctx.measureText(item.text);
    const [up, below] = [m.fontBoundingBoxAscent, m.fontBoundingBoxDescent];
    ctx.fillText(item.text, item.x, up ? item.y + (item.h - up - below) / 2 + up : item.y + item.h * 0.8);
    return;
  }
  const { x, y, w, h } = item.box;
  if (item.kind === "img") {
    const p = item.picture;
    if (!p?.naturalWidth) return;
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, item.radius);
    ctx.clip();
    const s = Math.max(w / p.naturalWidth, h / p.naturalHeight);  // object-fit: cover
    ctx.drawImage(p, x + (w - p.naturalWidth * s) / 2, y + (h - p.naturalHeight * s) / 2, p.naturalWidth * s,
                  p.naturalHeight * s);
    ctx.restore();
    return;
  }
  const { fill, sides, radius } = item.look;
  if (fill) {
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, radius);
    ctx.fill();
  }
  if (sides.every(Boolean)) {  // a card's frame
    const bw = sides[0].w;
    pen(ctx, sides[0]);
    ctx.beginPath();
    ctx.roundRect(x + bw / 2, y + bw / 2, w - bw, h - bw, radius.map((r) => Math.max(0, r - bw / 2)));
    ctx.stroke();
    return;
  }
  // some sides only (the lines between cards): each run of sides one after another as one line, rounded where the
  // stylesheet rounds the corner between two. Side k goes from corner k to corner k + 1 (top, right, bottom, left).
  const half = (k) => (sides[k]?.w || 0) / 2;
  const at = [[x + half(3), y + half(0)], [x + w - half(1), y + half(0)], [x + w - half(1), y + h - half(2)],
              [x + half(3), y + h - half(2)]];
  for (let first = 0; first < 4; first++) {
    if (!sides[first] || sides[(first + 3) % 4]) continue;  // a run starts where the side before it isn't drawn
    pen(ctx, sides[first]);
    ctx.beginPath();
    ctx.moveTo(...at[first]);
    let k = first;
    while (sides[(k + 1) % 4]) {
      ctx.arcTo(...at[(k + 1) % 4], ...at[(k + 2) % 4], radius[(k + 1) % 4]);
      k = (k + 1) % 4;
    }
    ctx.lineTo(...at[(k + 1) % 4]);
    ctx.stroke();
  }
}

// Passkeys (routes/passkeys.py). A button with data-passkey signs in with one ("signin"), makes the first with an
// admin's link ("invite": the link's token is after its #, which never reaches the server) or adds one to the account
// signed in ("add"). The server says what to ask for, the browser asks the person to unlock their phone or computer,
// and the server checks what it signed. [data-passkey-able] shows where this browser can use passkeys,
// [data-passkey-unable] where it can't; #passkey-said says what went wrong.
const passkeysWork = !!(window.PublicKeyCredential && window.isSecureContext && navigator.credentials);
document.querySelectorAll(passkeysWork ? "[data-passkey-able]" : "[data-passkey-unable]")
  .forEach((el) => (el.hidden = false));

// WebAuthn's JSON has its bytes as base64url
const fromB64url = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")
  .padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
const toB64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

function passkeyOptions(o) {
  const out = { ...o, challenge: fromB64url(o.challenge) };
  if (o.user) out.user = { ...o.user, id: fromB64url(o.user.id) };
  for (const list of ["excludeCredentials", "allowCredentials"]) {
    if (o[list]) out[list] = o[list].map((c) => ({ ...c, id: fromB64url(c.id) }));
  }
  return out;
}

function passkeyAnswer(cred) {
  const r = cred.response;
  const response = { clientDataJSON: toB64url(r.clientDataJSON) };
  if (r.attestationObject) {  // a passkey made
    response.attestationObject = toB64url(r.attestationObject);
    response.transports = r.getTransports?.() ?? [];
  } else {  // a passkey used
    response.authenticatorData = toB64url(r.authenticatorData);
    response.signature = toB64url(r.signature);
    if (r.userHandle) response.userHandle = toB64url(r.userHandle);
  }
  return { id: cred.id, rawId: toB64url(cred.rawId), type: cred.type, response,
           authenticatorAttachment: cred.authenticatorAttachment || undefined,
           clientExtensionResults: cred.getClientExtensionResults?.() ?? {} };
}

function passkeyTrouble(err) {
  if (err.name === "NotAllowedError" || err.name === "AbortError") {
    return t("The passkey wasn't used: it was cancelled, or took too long. Try again.");
  }
  if (err.name === "InvalidStateError") return t("This phone or computer has a passkey for this account already.");
  if (err.name === "SecurityError") return t("Passkeys don't work on this address.");
  if (err.name === "NotSupportedError") return t("This phone or computer can't make the kind of passkey this site needs.");
  return err.message;
}

function passkeySay(words) {
  const said = document.getElementById("passkey-said");
  if (!said) return notice(words, { error: true });
  said.textContent = words;
  said.hidden = !words;
}

const PASSKEY_URLS = {
  signin: ["/auth/passkey/start", "/auth/passkey/finish"],
  invite: ["/auth/invite/start", "/auth/invite/finish"],
  add: ["/api/passkeys/start", "/api/passkeys/finish"],
};

async function usePasskey(button) {
  const kind = button.dataset.passkey;
  const [start, finish] = PASSKEY_URLS[kind];
  button.disabled = true;
  passkeySay("");
  try {
    const body = kind === "signin" ? { next: button.dataset.next || "/" }
      : kind === "invite" ? { token: location.hash.slice(1) } : undefined;
    const { options } = await post(start, body);
    const publicKey = passkeyOptions(options);
    const cred = kind === "signin" ? await navigator.credentials.get({ publicKey })
      : await navigator.credentials.create({ publicKey });
    const result = await post(finish, { credential: passkeyAnswer(cred) });
    if (result.go) location.href = result.go;
    else location.reload();
  } catch (err) {
    passkeySay(err instanceof DOMException ? passkeyTrouble(err) : err.message);
    button.disabled = false;
  }
}

document.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-passkey]");
  if (button) usePasskey(button);
});

// An admin's link (passkey_invite.html): whom it is for, before making the passkey
const invite = document.querySelector("[data-invite]");
if (invite) {
  const token = location.hash.slice(1);
  if (!token) passkeySay(t("This link is missing its end. Open the whole link you were sent, or ask for it again."));
  else {
    post("/auth/invite/check", { token }).then((found) => {
      invite.querySelector("[data-invite-name]").textContent = found.who;
      invite.querySelector("[data-invite-email]").textContent = found.who === found.email ? "" : found.email;
      invite.querySelector("[data-invite-who]").hidden = false;
    }, (err) => passkeySay(err.message));
  }
}

// ---- Search as it is typed (the box at the top, data-search-suggest="url") ----------------------------------------
// The people, family words ("cousins"), places, tags and years that go with the end of what is typed are offered in
// a list under the box (the url: GET /api/search/suggest, from what the viewer can see). Picking a person, a place
// or a year adds it to the search as a filter, in place of the words typed for it; a tag or a family word goes into
// the words. On the search page the box keeps that search's filters (form[data-search-keep]), so new words narrow
// the same search. ↓ ↑ pick one, Enter takes it (or searches for what is typed), Esc closes the list; on a phone,
// tap one.
const searchBox = { input: null, box: null, items: [], on: -1, timer: 0, asked: 0 };
const SEARCH_KINDS = { person: "People", family: "Family", place: "Places", tag: "Tags", year: "Years" };

function searchKept() {
  const form = document.querySelector("form[data-search-keep]");
  return new URLSearchParams(form ? new FormData(form) : undefined);
}

function searchFor(words, add = {}) {
  const params = searchKept();
  params.delete("q");
  if (words.trim()) params.set("q", words.trim());
  for (const [name, value] of Object.entries(add)) {
    if (name === "people") {
      const ids = (params.get("people") || "").split(",").filter(Boolean);
      params.set("people", [...new Set([...ids, value])].join(","));
    } else {
      params.set(name, value);
    }
  }
  const query = params.toString();
  location.href = query ? `/search?${query}` : "/search";
}

function closeSearchSuggest() {
  clearTimeout(searchBox.timer);
  searchBox.box?.remove();
  searchBox.input?.setAttribute("aria-expanded", "false");
  searchBox.input?.removeAttribute("aria-activedescendant");
  Object.assign(searchBox, { box: null, items: [], on: -1 });
}

function takeSuggestion(item) {
  closeSearchSuggest();
  searchFor([item.keep, item.words].filter(Boolean).join(" "), item.add || {});
}

function placeSearchSuggest(input, box) {
  const r = input.getBoundingClientRect();
  if (innerWidth <= 760) {
    Object.assign(box.style, { left: "16px", right: "16px", top: `${r.bottom + 4}px` });
    return;
  }
  Object.assign(box.style, { top: `${r.bottom + 4}px`, minWidth: `${r.width}px` });
  box.style.left = `${Math.max(16, Math.min(r.left, innerWidth - 16 - box.offsetWidth))}px`;
}

async function suggestSearch(input) {
  const asked = ++searchBox.asked;
  const text = input.value;
  if (!text.trim()) return closeSearchSuggest();
  let items = [];
  try {
    const params = new URLSearchParams({ q: text });
    const people = searchKept().get("people");
    if (people) params.set("people", people);
    const r = await fetch(`${input.dataset.searchSuggest}?${params}`, { headers: { "X-Requested-With": "lineagebox" } });
    if (r.ok) items = (await r.json()).items || [];
  } catch {}
  if (asked !== searchBox.asked || document.activeElement !== input) return; // typed on since, or gone from the box
  closeSearchSuggest();
  if (!items.length) return;
  const box = Object.assign(document.createElement("ul"), { className: "suggest-box search-suggest", id: "search-suggestions" });
  box.setAttribute("role", "listbox");
  box.setAttribute("aria-label", t("Suggestions"));
  let kind = null;
  items.forEach((item, i) => {
    if (item.kind !== kind) {
      kind = item.kind;
      const head = Object.assign(document.createElement("li"), { className: "head", textContent: SEARCH_KINDS[kind] || kind });
      head.setAttribute("role", "presentation");
      box.append(head);
    }
    const li = Object.assign(document.createElement("li"), { id: `search-suggestion-${i}` });
    li.setAttribute("role", "option");
    if (item.kind === "person") {
      li.append(item.face ? Object.assign(document.createElement("img"), { src: `/face/${item.face}`, alt: "" })
                          : Object.assign(document.createElement("span"), { className: "ph" }));
    }
    const what = document.createElement("span");
    what.append(Object.assign(document.createElement("b"), { textContent: item.label }),
                Object.assign(document.createElement("small"), { textContent: item.about || "" }));
    li.append(what);
    li.addEventListener("click", () => takeSuggestion(item));
    box.append(li);
  });
  box.addEventListener("mousedown", (e) => e.preventDefault()); // a click or tap leaves the typing where it is
  document.body.append(box);
  placeSearchSuggest(input, box);
  input.setAttribute("aria-expanded", "true");
  Object.assign(searchBox, { input, box, items, on: -1 });
}

function highlightSearch(step) {
  const n = searchBox.items.length;
  if (!n) return;
  const on = searchBox.on + step;
  searchBox.on = on >= n ? -1 : on < -1 ? n - 1 : on;
  searchBox.box.querySelectorAll("li[role=option]").forEach((li, i) => {
    li.classList.toggle("on", i === searchBox.on);
    if (i === searchBox.on) li.scrollIntoView({ block: "nearest" });
  });
  if (searchBox.on >= 0) searchBox.input.setAttribute("aria-activedescendant", `search-suggestion-${searchBox.on}`);
  else searchBox.input.removeAttribute("aria-activedescendant");
}

document.addEventListener("input", (e) => {
  const input = e.target.closest?.("input[data-search-suggest]");
  if (!input) return;
  clearTimeout(searchBox.timer);
  searchBox.timer = setTimeout(() => suggestSearch(input), 120);
});
// in the capture phase, so the list has the keys before Enter sends the form and Esc leaves the box
document.addEventListener("keydown", (e) => {
  if (!searchBox.box || e.target !== searchBox.input) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    highlightSearch(e.key === "ArrowDown" ? 1 : -1);
  } else if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    closeSearchSuggest();
  } else if (e.key === "Enter" && searchBox.on >= 0) {
    e.preventDefault();
    takeSuggestion(searchBox.items[searchBox.on]);
  } else if (e.key === "Tab") {
    closeSearchSuggest();
  }
}, true);
document.addEventListener("focusin", (e) => {
  const input = e.target.closest?.("input[data-search-suggest]");
  if (!input) return;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", "search-suggestions");
  input.setAttribute("aria-expanded", "false");
});
document.addEventListener("focusout", (e) => {
  if (e.target === searchBox.input) closeSearchSuggest();
});
// on the search page, words typed in the box search with the page's filters still on
document.addEventListener("submit", (e) => {
  const input = e.target.querySelector?.("input[data-search-suggest]");
  if (!input || !document.querySelector("form[data-search-keep]")) return;
  e.preventDefault();
  closeSearchSuggest();
  searchFor(input.value);
});
addEventListener("resize", closeSearchSuggest);

// ---- Naming faces one at a time (People → Name faces: name_faces.html, routes/naming.py) --------------------------
// The card in [data-naming] asks "Is this …?" (Yes, No, Skip) about one face, or has a box for who it is, or for who
// a group of faces is. Anything in it with data-answer (a button, the Skip link, a form) is posted to its data-url, with
// data-body or the form's fields, and then the next card comes from the server, without loading the page again:
// after "no", the same face's card again (data-again: who else it may be, or a box for who it is); after "skip", the
// next one but those passed over (data-skip-face, data-skip-group: kept in the address, so a reload keeps the place);
// after anything else ("yes", "done", a name typed), the next one. Y, N and S press Yes, No and Skip (data-key), and →
// skips too. A [data-naming] with data-face="id" holds a card about one face on a page of its own (a face tapped on
// its photo, from GET /people/name/face/{id}?one=1): instead of a next card it gets a "naming:done" event, whose
// detail has the face, the answer ("yes", "named", "skip", "done") and what the server said (person_id, message).
// Check faces (person_faces.html): form[data-yes-faces="url"] sends its ticked faces as {yes: [...]}, then reloads.
const namingSay = (text, options = {}) => (typeof notice === "function" ? notice(text, options)
                                           : options.error && alert(text));
let namingBusy = false;

function namingQuery(root) {
  const q = new URLSearchParams();
  if (root.dataset.face) q.set("one", "1");
  if (root.dataset.only) q.set("only", root.dataset.only);
  if (root.dataset.skip) q.set("skip", root.dataset.skip);
  if (root.dataset.skipGroups) q.set("skip_groups", root.dataset.skipGroups);
  return q.toString();
}

async function namingLoad(root, url) {
  const q = namingQuery(root);
  const r = await fetch(q ? `${url}${url.includes("?") ? "&" : "?"}${q}` : url,
                        { headers: { "X-Requested-With": "lineagebox" } });
  if (!r.ok) {
    let detail = `${r.status}`;
    try { detail = (await r.json()).detail ?? detail; } catch {}
    throw new Error(detail);
  }
  const html = await r.text();
  closeNames();
  root.classList.remove("busy");  // the new card isn't dimmed for a moment
  root.innerHTML = html;
  if (!lettersOff()) {
    root.querySelectorAll("[data-key]").forEach((el) => (el.title = `${el.title || keyLabel(el)} (key ${el.dataset.key})`));
  }
  root.querySelector("[data-naming-focus]")?.focus({ preventScroll: true });  // read out; the keys work from there
  if (root.getBoundingClientRect().top < 0) root.scrollIntoView({ block: "start" });
  if (!root.dataset.face) history.replaceState(null, "", `/people/name${q ? `?${q}` : ""}`);
}

function namingPass(root, key, id) {
  if (!id) return;
  const ids = (root.dataset[key] || "").split(",").filter(Boolean);
  root.dataset[key] = [...ids, id].slice(-2000).join(",");
}

async function namingAnswer(el) {
  const root = el.closest("[data-naming]");
  if (!root || namingBusy) return;
  namingBusy = true;
  root.classList.add("busy");
  const answer = el.dataset.answer;
  try {
    let result = {};
    if (el.dataset.url) {
      const form = el.matches("form") ? el : null;
      const body = form ? addPicked(form, Object.fromEntries(new FormData(form)))
        : el.dataset.body ? JSON.parse(el.dataset.body) : undefined;
      result = await post(el.dataset.url, body);
      everyone = null;  // a name typed may be someone new: the name boxes suggest them from now on
      const typed = form?.elements.name?.value.trim();
      const said = result.message || el.dataset.said || (typed ? t("Named {name}.", { name: typed }) : "");
      if (said || result.undo) namingSay(said || t("Done."), { undo: result.undo });
    }
    if (answer === "skip") {
      namingPass(root, "skip", el.dataset.skipFace);
      namingPass(root, "skipGroups", el.dataset.skipGroup);
    }
    if (answer === "no" && el.dataset.again) await namingLoad(root, el.dataset.again);
    else if (root.dataset.face) {
      root.dispatchEvent(new CustomEvent("naming:done", { bubbles: true,
        detail: { face: Number(root.dataset.face), answer, result } }));
    } else await namingLoad(root, "/people/name/next");
  } catch (err) {
    namingSay(t("That didn't work: {why}", { why: err.message }), { error: true });
  } finally {
    namingBusy = false;
    root.classList.remove("busy");
  }
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-naming] [data-answer]");
  if (!el || el.matches("form")) return;  // a form's answer is its submit
  e.preventDefault();  // Skip is also a link to the next card, for when this script can't run
  namingAnswer(el);
});
document.addEventListener("submit", (e) => {
  const form = e.target.closest("[data-naming] form[data-answer]");
  if (!form) return;
  e.preventDefault();
  namingAnswer(form);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.defaultPrevented) return;
  if (e.target.closest("input, textarea, select, [contenteditable], dialog")) return;
  const skip = document.querySelector('main [data-naming] [data-answer="skip"][data-key="s"]');
  if (!skip) return;
  e.preventDefault();
  skip.click();
});

// Check faces: the Yes button says how many are ticked, as boxes are ticked, faces taken off (× or ✓) or all ticked.
function yesFacesCount(form) {
  const n = form.querySelectorAll('input[name="face"]:checked').length;
  form.querySelectorAll('button[type="submit"][data-label]').forEach((button) => {
    const text = button.dataset.label.replace("{n}", num(n));
    if (button.textContent !== text) button.textContent = text;  // else the observer below would see it change again
    button.disabled = n === 0;
  });
}
document.querySelectorAll("form[data-yes-faces]").forEach((form) => {
  yesFacesCount(form);
  new MutationObserver(() => yesFacesCount(form)).observe(form, { childList: true, subtree: true });
});
document.addEventListener("change", (e) => {
  const form = e.target.closest?.("form[data-yes-faces]");
  if (form) yesFacesCount(form);
});
document.addEventListener("click", (e) => {
  const form = e.target.closest("form[data-yes-faces]");
  if (form && e.target.closest("button[data-tick]")) yesFacesCount(form);
});
document.addEventListener("submit", (e) => {
  const form = e.target.closest("form[data-yes-faces]");
  if (!form) return;
  e.preventDefault();
  const yes = [...form.querySelectorAll('input[name="face"]:checked')].map((box) => Number(box.value));
  run(form, form.dataset.yesFaces, { yes });
});

// ---- An app on the phone, and New for you (N7, N8) -----------------------------------------------------------------
// The service worker (/sw.js, routes/phone.py) lets a phone keep LineageBox on its home screen and share photos to it.
// Browsers run one only over HTTPS (or on the machine itself): over plain http at home nothing is registered, and every
// page works as it always has.
if ("serviceWorker" in navigator && window.isSecureContext) {
  addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
// Upload page: photos and videos shared to LineageBox from the phone's share sheet, which the service worker kept, are
// sent like files picked here (the upload zone's own picker takes them), and then forgotten on the phone.
(async () => {
  const shared = new URLSearchParams(location.search).get("shared");
  if (shared !== null) history.replaceState(null, "", location.pathname);
  const zone = document.querySelector("[data-upload]");
  if (shared === "lost") {
    notice(t("The photos you shared didn't come through: pick them here instead."), { error: true });
  }
  if (!zone || !("caches" in window)) return;
  let files = [];
  try {
    const cache = await caches.open("lineagebox-shared");
    for (const key of await cache.keys()) {
      const kept = await cache.match(key);
      if (kept) {
        const name = decodeURIComponent(kept.headers.get("X-File-Name") || "shared");
        const blob = await kept.blob();
        files.push(new File([blob], name, { type: blob.type }));
      }
      await cache.delete(key);
    }
  } catch {
    files = [];
  }
  const picker = zone.querySelector("input[data-pick]:not([webkitdirectory])");
  if (!files.length || !picker || typeof DataTransfer === "undefined") return;
  // asked first: on a phone more than one person signs in to, what someone shared goes up only if it is meant to
  const who = document.querySelector(".profile-menu .who strong")?.textContent.trim();
  if (!confirm(who ? tn("Upload the {n} photo or video shared from this phone as {who}?",
                      "Upload the {n} photos or videos shared from this phone as {who}?", files.length, { who })
                : tn("Upload the {n} photo or video shared from this phone?",
                     "Upload the {n} photos or videos shared from this phone?", files.length))) return;
  const picked = new DataTransfer();
  files.forEach((f) => picked.items.add(f));
  picker.files = picked.files;
  picker.dispatchEvent(new Event("change"));
  notice(t("{n} shared from your phone: uploading…", { n: files.length }));
})();
// Signing out forgets photos shared to LineageBox that weren't sent yet: the next person on this phone isn't sent them.
document.addEventListener("click", (e) => {
  if (e.target.closest('[data-api="/auth/signout"], [data-api="/auth/signout-everywhere"]') && "caches" in window) {
    caches.delete("lineagebox-shared").catch(() => {});
  }
});

// ---- Recorded stories and long tapes (stories.py, _stories.html, chapters.py) --------------------------------------
// A form with data-story-form="url" records a story with the microphone, or takes a recording picked with its file
// box (a phone opens its own recorder there), lets it be listened to, then sends the sound as the request's body, its
// title, life event and length in the address. Recording here (MediaRecorder) needs a secure address: https, or the
// machine itself; on plain http at home only the file box shows. A person's stories load after the page
// (data-later), so the forms are set up as they arrive.
const STORY_TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"];
const canRecord = !!(window.isSecureContext && window.MediaRecorder && navigator.mediaDevices?.getUserMedia);
let storyRecording = null;  // { recorder, stream, chunks, started, timer, form } while the microphone is on

function storyClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function storySetUp(root) {
  root.querySelectorAll?.("form[data-story-form]:not([data-ready])").forEach((form) => {
    form.dataset.ready = "";
    form.querySelector("[data-recorder]").hidden = !canRecord;
    form.querySelector("[data-recorder-away]").hidden = canRecord;
  });
}
storySetUp(document);
document.querySelectorAll('[data-later*="/stories"]').forEach((part) => {
  new MutationObserver(() => storySetUp(part)).observe(part, { childList: true });
});
function storyReady(form, sound, seconds) {  // sound: the Blob recorded or the File picked; null: none yet
  form.storySound = sound;
  form.storySeconds = seconds;
  const player = form.querySelector("[data-recorded]");
  if (player.dataset.url) URL.revokeObjectURL(player.dataset.url);
  player.dataset.url = sound ? URL.createObjectURL(sound) : "";
  if (sound) player.src = player.dataset.url;
  else player.removeAttribute("src");
  player.hidden = !sound;
  form.querySelector("[data-recorded-actions]").hidden = !sound;
}
async function storyStart(form) {
  if (storyRecording) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    notice(err.name === "NotAllowedError"
      ? t("The microphone wasn't allowed. Allow it for this site in the browser, or pick a recording instead.")
      : t("The microphone can't be used: {why}", { why: err.message }), { error: true });
    return;
  }
  const type = STORY_TYPES.find((t) => MediaRecorder.isTypeSupported?.(t));
  const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  const most = Number(form.dataset.mostSeconds) || 1800;
  const rec = { recorder, stream, chunks: [], started: Date.now(), form };
  const time = form.querySelector("[data-record-time]");
  storyRecording = rec;
  recorder.addEventListener("dataavailable", (e) => { if (e.data.size) rec.chunks.push(e.data); });
  recorder.addEventListener("stop", () => {
    clearInterval(rec.timer);
    stream.getTracks().forEach((t) => t.stop());
    const seconds = (Date.now() - rec.started) / 1000;
    storyReady(form, new Blob(rec.chunks, { type: (recorder.mimeType || type || "audio/webm").split(";")[0] }), seconds);
    form.querySelector("[data-record]").hidden = false;
    form.querySelector("[data-record-stop]").hidden = true;
    time.textContent = t("{time} recorded: listen to it, then keep it.", { time: storyClock(seconds) });
    storyRecording = null;
  });
  storyReady(form, null, null);
  const file = form.querySelector('input[type="file"]');
  if (file) file.value = "";
  recorder.start(1000);
  form.querySelector("[data-record]").hidden = true;
  const stop = form.querySelector("[data-record-stop]");
  stop.hidden = false;
  stop.focus();
  const tick = () => {
    const seconds = (Date.now() - rec.started) / 1000;
    time.textContent = t("Recording… {time} of {most}", { time: storyClock(seconds), most: storyClock(most) });
    if (seconds >= most && recorder.state !== "inactive") recorder.stop();
  };
  tick();
  rec.timer = setInterval(tick, 500);
}
document.addEventListener("click", (e) => {
  const form = e.target.closest("form[data-story-form]");
  if (!form) return;
  if (e.target.closest("[data-record]")) storyStart(form);
  else if (e.target.closest("[data-record-stop]") && storyRecording?.recorder.state !== "inactive") {
    storyRecording?.recorder.stop();
  } else if (e.target.closest("[data-record-again]")) {
    storyReady(form, null, null);
    form.querySelector("[data-record-time]").textContent = "";
    const file = form.querySelector('input[type="file"]');
    if (file) file.value = "";
  }
});
document.addEventListener("change", (e) => {
  const input = e.target.closest?.('form[data-story-form] input[type="file"]');
  if (input) storyReady(input.closest("form"), input.files[0] || null, null);
});
document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-story-form]");
  if (!form) return;
  e.preventDefault();
  if (!form.storySound) {
    notice(t("Record something first, or pick a recording."), { error: true });
    return;
  }
  const url = new URL(form.dataset.storyForm, location.href);
  const title = form.elements.title?.value.trim();
  if (title) url.searchParams.set("title", title);
  if (form.elements.event?.value) url.searchParams.set("event", form.elements.event.value);
  if (form.storySeconds) url.searchParams.set("seconds", form.storySeconds.toFixed(1));
  const button = form.querySelector('button[type="submit"]');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = t("Keeping it…");
  try {
    const r = await fetch(url, { method: "POST", body: form.storySound, headers: {
      "X-Requested-With": "lineagebox", "Content-Type": form.storySound.type || "application/octet-stream" } });
    const text = await r.text();
    let answer = {};
    try { answer = JSON.parse(text); } catch {}
    if (!r.ok) throw new Error(answer.detail || `${r.status} ${text.slice(0, 200)}`);
    later(answer.message || t("Kept."), { undo: answer.undo });
    location.reload();
  } catch (err) {
    notice(t("That didn't work: {why}", { why: err.message }), { error: true });
    button.disabled = false;
    button.textContent = label;
  }
});
addEventListener("beforeunload", (e) => {  // a story being told isn't lost to a click elsewhere
  if (storyRecording) e.preventDefault();
});

// ---- Albums, favourites, tours with stories and the family book (albums.py, favourites.py, slideshow.py, book.py) ----

// A slideshow's title card (tours.title_card): the tour's or album's name, the time its photos span, its words, and
// how many photos it has. playTour shows it in place of a photo.
function titleCard(s) {
  const card = Object.assign(document.createElement("div"), { className: "title-card" });
  card.append(Object.assign(document.createElement("h2"), { textContent: s.title }));
  for (const text of [s.span, s.about, s.count]) {
    if (text) card.append(Object.assign(document.createElement("p"), { textContent: text }));
  }
  return card;
}

// ♡ on a photo's page (data-heart="url"): the viewer's own heart on it, given or taken off at once.
document.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-heart]");
  if (!button) return;
  const on = button.getAttribute("aria-pressed") !== "true";
  button.disabled = true;
  try {
    await post(button.dataset.heart, { on });
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "♥" : "♡";
    button.title = on ? t("One of your favourites: press to take the heart off (h)")
      : t("A favourite of yours? Tours and memories show favourites first (h)");
    notice(on ? t("One of your favourites now.") : t("No longer one of your favourites."));
  } catch (err) {
    notice(t("That didn't work: {why}", { why: err.message }), { error: true });
  } finally {
    button.disabled = false;
  }
});

// An album's photos in order (ol[data-album], album.html): dragged with a mouse, or moved with ↑ ↓ (data-move, for
// touch screens and the keyboard). Each new order is sent at once; the page stays as it is.
async function albumOrder(list) {
  const ids = [...list.querySelectorAll("li[data-image]")].map((li) => Number(li.dataset.image));
  try {
    await post(`/api/albums/${list.dataset.album}/order`, { image_ids: ids });
  } catch (err) {
    notice(t("The new order wasn't kept: {why}", { why: err.message }), { error: true });
  }
}
let albumDragged = null;
document.addEventListener("dragstart", (e) => {
  const li = e.target.closest?.("ol[data-album] li[data-image]");
  if (!li) return;
  albumDragged = li;
  li.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", li.dataset.image);
});
document.addEventListener("dragover", (e) => {
  const list = e.target.closest?.("ol[data-album]");
  if (!albumDragged || !list || albumDragged.parentElement !== list) return;
  e.preventDefault();
  const over = e.target.closest("li[data-image]");
  if (!over || over === albumDragged) return;
  const box = over.getBoundingClientRect();
  const after = e.clientX > box.left + box.width / 2;  // the photos wrap in rows: the side of the photo pointed at
  over.parentElement.insertBefore(albumDragged, after ? over.nextSibling : over);
});
document.addEventListener("drop", (e) => {
  if (albumDragged && e.target.closest?.("ol[data-album]")) e.preventDefault();
});
document.addEventListener("dragend", () => {
  if (!albumDragged) return;
  const list = albumDragged.parentElement;
  albumDragged.classList.remove("dragging");
  albumDragged = null;
  albumOrder(list);
});
document.addEventListener("click", (e) => {
  const button = e.target.closest("ol[data-album] button[data-move]");
  if (!button) return;
  const li = button.closest("li[data-image]");
  const other = button.dataset.move === "-1" ? li.previousElementSibling : li.nextElementSibling;
  if (!other) return;
  li.parentElement.insertBefore(li, button.dataset.move === "-1" ? other : other.nextSibling);
  button.focus();  // moved again with another press, or Enter
  albumOrder(li.parentElement);
});

// The family book (book.html): Print or save as a PDF opens the browser's print window.
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-print]")) window.print();
});

// ---- A shorter menu, first steps, plain words, light or dark, printing (U8, U9, U10, U12; base.html, home.html) ----
// More, in the top bar: it opens and closes as the other menus there do (openPanel, a [data-panel] button), and also
// when the keyboard moves on out of it, so it never stays open over what has the focus next. Folded into ☰ (or on a
// phone) its links are part of that menu, and there is no More button to close.
document.querySelector(".top .more")?.addEventListener("focusout", (e) => {
  const more = e.currentTarget;
  if (e.relatedTarget && !more.contains(e.relatedTarget) && more.querySelector(".more-menu.open")) openPanel(null);
});
addEventListener("resize", () => {  // open when the links folded into ☰: its button is gone, so close it
  const menu = document.getElementById("more-menu");
  if (menu?.classList.contains("open") && getComputedStyle(menu).position === "static") openPanel(null);
});

// Colours, at the foot of More: Automatic (as the computer or phone is set), Light or Dark, kept in this browser.
// theme.js puts the choice on every page before it is drawn; a press changes this page at once, and the menu stays
// open to try the other. Another tab of the site changing it changes this one too.
const THEME_KEY = "lineagebox-theme";
function showTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll("[data-theme-choice]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.themeChoice === (document.documentElement.dataset.theme || "")));
  });
}
showTheme(document.documentElement.dataset.theme);
document.addEventListener("click", (e) => {
  const button = e.target.closest("[data-theme-choice]");
  if (!button) return;
  e.stopPropagation();  // before the top bar's own click, which would close the menu
  const theme = button.dataset.themeChoice;
  try {
    if (theme) localStorage.setItem(THEME_KEY, theme);
    else localStorage.removeItem(THEME_KEY);
  } catch {}  // a browser that keeps nothing: this page only
  showTheme(theme);
}, true);
addEventListener("storage", (e) => {
  if (e.key === THEME_KEY) showTheme(e.newValue);
});

// Printing a person's page or the Family page: the panels folded away print unfolded (the life story, family, life
// events, sources; the family by relationship), and fold again after. Reading into the tree and family tree
// programs are left out of the print by the stylesheet.
let printUnfolded = [];
addEventListener("beforeprint", () => {
  if (!document.body.matches(".person-page, .family-page")) return;
  printUnfolded = [...document.querySelectorAll(
    "main details:is(.panel, .glance, .info-panel):not([open]):not(.read-panel):not(#gedcom)")];
  printUnfolded.forEach((d) => { d.open = true; });
});
addEventListener("afterprint", () => {
  printUnfolded.forEach((d) => { d.open = false; });
  printUnfolded = [];
});

// ---- Big pages in pieces, ticking photos, and keyboard and screen readers (U6, U11) --------------------------------
// Keys of one letter or sign (?, /, g then a letter, and every data-key) can be turned off in the help panel: then a
// screen reader's or speech software's own keys reach it, and nothing happens when one is pressed by mistake. The
// arrow keys, Esc, Tab and Enter still work. Kept in this browser. (The key handlers above ask lettersOff().)
function lettersOff() {
  try { return localStorage.getItem("lineagebox-letter-keys") === "off"; } catch { return false; }
}
document.querySelectorAll("[data-letter-keys]").forEach((box) => {
  box.checked = !lettersOff();
  box.addEventListener("change", () => {
    try { localStorage.setItem("lineagebox-letter-keys", box.checked ? "on" : "off"); } catch {}
  });
});

// Ticks kept. A list with the tick-photos bar (form.select-form with .select-bar.bulk) keeps the photos ticked in it
// in this tab, under the list's address without its page or part: they stay ticked as more of the list loads, on its
// other pages and when the list is opened again, until they are moved away or Clear is pressed. Select all ticks the
// whole list where the page has it (script[data-everything]), loaded or not. Shift and a click (or Shift and Space)
// ticks, or unticks, every photo from the one last ticked.
function ticksKey() {
  const url = new URL(location.href);
  for (const name of ["page", "per", "select", "skip", "upto", "piece"]) url.searchParams.delete(name);
  return `lineagebox-ticks:${url.pathname}?${url.searchParams}`;
}
function ticksKept(form) {  // the ticked photos' ids, or null for a list whose ticks aren't kept
  if (!form?.matches?.("form.select-form") || !form.querySelector(".select-bar.bulk")) return null;
  try {
    const kept = JSON.parse(sessionStorage.getItem(ticksKey()) || "null");
    if (Array.isArray(kept)) return new Set(kept);
  } catch {}
  return new Set([...form.querySelectorAll('input[name="image"]:checked')].map((b) => Number(b.value)));
}
function keepTicks(set) {
  try { sessionStorage.setItem(ticksKey(), JSON.stringify([...set])); } catch {}
}
function forgetTicks() {
  try { sessionStorage.removeItem(ticksKey()); } catch {}
}
function tickedIds(form) {
  const kept = ticksKept(form);
  return kept ? [...kept] : [...form.querySelectorAll('input[name="image"]:checked')].map((b) => Number(b.value));
}
function tickEverything(form, on) {
  if (!on) return forgetTicks();
  let all = null;
  try { all = JSON.parse(form.querySelector("script[data-everything]")?.textContent || "null"); } catch {}
  const here = [...form.querySelectorAll('input[name="image"]')].map((b) => Number(b.value));
  keepTicks(new Set([...(ticksKept(form) ?? []), ...(all ?? here)]));
}
function sayTicked(form, n) {
  const said = form.querySelector("[data-ticked]");
  if (!said) return;
  const here = form.querySelectorAll('input[name="image"]:checked').length;
  said.textContent = !n ? "" : t("{n} ticked", { n: num(n) })
    + (n > here ? t(" ({n} not shown here)", { n: num(n - here) }) : "");
}
function showTicks(form) {
  const kept = ticksKept(form);
  if (!kept) return;
  form.querySelectorAll('input[name="image"]').forEach((b) => (b.checked = kept.has(Number(b.value))));
  updateSelection(form);
}
let shiftTick = false;
let lastTick = null;
document.addEventListener("pointerdown", (e) => {  // not the click a photo's label passes on to its box
  if (e.target.closest?.(".grid.selectable .pick")) shiftTick = e.shiftKey;
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key === " " && e.target.matches?.('.pick input[name="image"]')) shiftTick = e.shiftKey;
}, true);
document.addEventListener("change", (e) => {
  const box = e.target;
  const form = box.matches?.('input[type="checkbox"][name="image"]') && box.closest("form.select-form");
  if (!form) return;
  let changed = [box];
  const boxes = [...form.querySelectorAll('input[name="image"]')];
  if (shiftTick && lastTick && lastTick !== box && boxes.includes(lastTick)) {  // the photos between the two
    const [a, b] = [boxes.indexOf(lastTick), boxes.indexOf(box)].sort((x, y) => x - y);
    changed = boxes.slice(a, b + 1);
    changed.forEach((other) => (other.checked = box.checked));
  }
  shiftTick = false;
  lastTick = box;
  const kept = ticksKept(form);
  if (kept) {
    changed.forEach((b) => (b.checked ? kept.add(Number(b.value)) : kept.delete(Number(b.value))));
    keepTicks(kept);
  }
  updateSelection(form);
});
document.querySelectorAll("form.select-form").forEach((form) => {
  showTicks(form);
  // photos loaded into the list later (its next piece, a person's age group opened) are ticked as they were left
  new MutationObserver((records) => {
    const added = records.some((r) => [...r.addedNodes].some((n) => n.nodeType === 1
      && (n.matches('input[name="image"]') || n.querySelector('input[name="image"]'))));
    if (added) showTicks(form);
  }).observe(form, { childList: true, subtree: true });
});

// Pieces (pieces.py, _pieces.html). The next piece of a long page loads as the page is scrolled near its end (and on
// a press of Show more); Show earlier photos loads the piece before, keeping the photos in view where they are. A
// month the page already has gets the piece's photos of it, without a second heading. The address follows what is
// loaded (?skip= ?upto=), so Back from a photo, or loading the page again, shows the same photos in the same places.
function pieceAddress(href) {
  const url = new URL(href, location.href);
  url.searchParams.set("piece", "1");
  return url;
}
function keepPlace(holder) {
  const url = new URL(location.href);
  const start = Number(holder.dataset.start);
  if (start) url.searchParams.set("skip", start);
  else url.searchParams.delete("skip");
  url.searchParams.set("upto", holder.dataset.end);
  history.replaceState(history.state, "", url);
}
async function loadPiece(link, before) {
  if (link.dataset.loading) return;
  link.dataset.loading = "1";
  const holder = link.closest("[data-pieces]");
  const label = link.innerHTML;
  link.textContent = t("Loading…");
  try {
    const r = await fetch(pieceAddress(before ? link.dataset.earlier : link.href));
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const part = document.createElement("template");
    part.innerHTML = await r.text();
    const pressed = document.activeElement === link;
    const first = part.content.querySelector("[data-piece] .tile")?.id;
    addPiece(holder, part.content.querySelector("[data-piece]"), before);
    if (pressed && first) {  // pressed from the keyboard: it goes on from the first photo loaded
      const tile = document.getElementById(first);
      (tile?.matches("a") ? tile : tile?.querySelector("input"))?.focus();
    }
  } catch (err) {
    delete link.dataset.loading;
    link.innerHTML = label;
    notice(t("More photos didn't load: {why}", { why: err.message }), { error: true });
  }
}
function addPiece(holder, piece, before) {
  const anchor = before ? holder.querySelector(".tile") : null;  // what is in view stays where it is
  const top = anchor?.getBoundingClientRect().top;
  const mark = holder.querySelector(before ? ".pieces-earlier" : ".pieces-more");
  const at = before ? mark?.nextElementSibling ?? null : mark;  // after Show earlier, or before Show more
  for (const node of [...piece.children]) {
    if (node.matches(".pieces-earlier, .pieces-more")) continue;
    const { gridPart, gridHead, once } = node.dataset;  // (not data-part: the Settings page's)
    if ((gridHead && holder.querySelector(`[data-grid-head="${gridHead}"]`))
        || (once && holder.querySelector(`[data-once="${once}"]`))) continue;
    const same = gridPart && holder.querySelector(`.grid[data-grid-part="${gridPart}"]`);
    if (same) before ? same.prepend(...node.children) : same.append(...node.children);
    else holder.insertBefore(node, at);
  }
  const link = piece.querySelector(before ? ".pieces-earlier" : ".pieces-more");
  if (mark && link) mark.replaceWith(link);
  else mark?.remove();
  if (before) holder.dataset.start = piece.dataset.start;
  else holder.dataset.end = piece.dataset.end;
  if (anchor) scrollBy(0, anchor.getBoundingClientRect().top - top);
  keepPlace(holder);
  watchMore(holder);
  markScrubber(holder.closest(".pieced"));
}
const moreSeen = "IntersectionObserver" in window ? new IntersectionObserver((seen) => {
  seen.forEach((s) => s.isIntersecting && loadPiece(s.target, false));
}, { rootMargin: "0px 0px 1200px 0px" }) : null;
function watchMore(holder) {
  const more = holder.querySelector(".pieces-more a[data-more]");
  if (more && moreSeen) moreSeen.observe(more);
}
document.querySelectorAll("[data-pieces]").forEach(watchMore);
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-pieces] a[data-more], [data-pieces] a[data-earlier]");
  if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button > 0) return;
  e.preventDefault();
  loadPiece(link, "earlier" in link.dataset);
});

// The scrubber: a year or month already loaded is scrolled to (and its first photo has the focus, for the keyboard);
// one further away loads the page from there. The one in view is marked as the page scrolls.
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-scrubber] a[data-at]");
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button > 0) return;
  const holder = a.closest(".pieced")?.querySelector("[data-pieces]");
  const at = Number(a.dataset.at);
  const target = document.getElementById(new URL(a.href).hash.slice(1));
  if (!holder || !target || at < Number(holder.dataset.start) || at >= Number(holder.dataset.end)) return;
  e.preventDefault();
  target.scrollIntoView({ block: "start" });
  const first = target.matches("a, input") ? target
    : target.querySelector("a, input") ?? target.nextElementSibling?.querySelector("a.tile, .tile input");
  first?.focus({ preventScroll: true });
});
function markScrubber(pieced) {
  const nav = pieced?.querySelector("[data-scrubber]");
  if (!nav) return;
  const tiles = pieced.querySelectorAll(".pieces .tile[data-ym]");
  const strip = nav.getBoundingClientRect();  // on a phone a strip at the top, which the photos pass under
  const line = strip.height < innerHeight / 3 && strip.top < innerHeight / 3 ? strip.bottom : 60;
  let lo = 0, hi = tiles.length - 1, found = -1;
  while (lo <= hi) {  // the first photo not yet scrolled past
    const mid = (lo + hi) >> 1;
    if (tiles[mid].getBoundingClientRect().bottom > line) [found, hi] = [mid, mid - 1];
    else lo = mid + 1;
  }
  if (found < 0) return;
  const ym = tiles[found].dataset.ym;
  let shown = null;
  nav.querySelectorAll("a[data-ym]").forEach((a) => {
    const on = a.dataset.ym === (a.dataset.ym.length === 4 ? ym.slice(0, 4) : ym);
    if (on) a.setAttribute("aria-current", "true");
    else a.removeAttribute("aria-current");
    if (a.parentElement.matches(".scrub-year")) a.parentElement.classList.toggle("on", on);
    if (on) shown = a;
  });
  if (!shown) return;
  const r = shown.getBoundingClientRect(), n = nav.getBoundingClientRect();
  if (r.top < n.top) nav.scrollTop -= n.top - r.top + 8;
  else if (r.bottom > n.bottom) nav.scrollTop += r.bottom - n.bottom + 8;
  if (r.left < n.left) nav.scrollLeft -= n.left - r.left + 16;
  else if (r.right > n.right) nav.scrollLeft += r.right - n.right + 16;
}
let scrubbing = false;
addEventListener("scroll", () => {
  if (scrubbing || !document.querySelector("[data-scrubber]")) return;
  scrubbing = true;
  requestAnimationFrame(() => {
    scrubbing = false;
    document.querySelectorAll(".pieced").forEach(markScrubber);
  });
}, { passive: true });

// A person's age groups: Show more opens by itself as it is scrolled near, as a long page's pieces do.
const groupMore = "IntersectionObserver" in window ? new IntersectionObserver((seen) => {
  seen.forEach((s) => {
    if (s.isIntersecting && s.target.isConnected) {
      s.target.open = true;
      groupMore.unobserve(s.target);
    }
  });
}, { rootMargin: "0px 0px 800px 0px" }) : null;
document.querySelectorAll(".life-story").forEach((life) => {
  if (!groupMore) return;
  const watch = () => life.querySelectorAll("details.period-photos.more:not([open])")
    .forEach((d) => groupMore.observe(d));
  watch();
  new MutationObserver(watch).observe(life, { childList: true, subtree: true });
});

// ---- Other languages (N13, i18n.py, docs/translating.md) ------------------------------------------------------------
// Every message a script shows goes through t("English text", { name: value }), and one that counts something through
// tn("{n} photo", "{n} photos", n). A page in another language carries its translations (#lineagebox-text, from
// i18n.script_catalog); an English page carries none, and each text is shown as it is written. {name} in a text is
// filled from the values; tn fills {n} with the number as num() writes it. Function declarations, so the parts above
// can use them however early they run.
function scriptText() {
  if (scriptText.kept === undefined) {
    try {
      scriptText.kept = JSON.parse(document.getElementById("lineagebox-text")?.textContent || "{}");
    } catch {
      scriptText.kept = {};
    }
  }
  return scriptText.kept;
}

function fillIn(text, values) {
  return values ? text.replace(/\{(\w+)\}/g, (all, name) => (name in values ? String(values[name]) : all)) : text;
}

function t(text, values) {
  const said = scriptText().text?.[text];
  return fillIn(typeof said === "string" && said ? said : text, values);
}

// the plural form a translation's rule picks (a table for 0-199, as i18n.PLURAL_TABLE), else English's one or many
function tn(one, many, n, values) {
  const forms = scriptText().text?.[one], table = scriptText().plural;
  let said = n === 1 ? one : many;
  if (Array.isArray(forms) && Array.isArray(table)) said = forms[table[n < table.length ? n : 100 + (n % 100)]] || said;
  return fillIn(said, { n: num(n), ...values });
}

// a number as the page's language writes it (in English, as this browser does, as before)
function num(n) {
  return Number(n).toLocaleString(scriptText().text ? document.documentElement.lang : undefined);
}

// text put into an attribute of HTML made here (a translation may hold quotes)
function attrText(text) {
  return String(text).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}
