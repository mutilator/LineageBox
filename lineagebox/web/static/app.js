// Photo page: arrow keys step through the current folder/year/search, Esc goes back.
// Anything with data-api (buttons, forms) posts to this app's JSON API, with data-body as the body if given;
// data-remove / data-reload / data-go / data-then say what to do afterwards, data-show where to put its message,
// data-html where to put the HTML it answers with.

document.addEventListener("keydown", (e) => {
  if (e.altKey || e.ctrlKey || e.metaKey || e.target.closest("input, textarea, select, video, dialog")) return;
  const follow = (selector) => {
    const link = document.querySelector(selector);
    if (link) location.href = link.href;
  };
  if (e.key === "ArrowLeft") follow('a[rel="prev"]');
  else if (e.key === "ArrowRight") follow('a[rel="next"]');
  else if (e.key === "Escape") follow("[data-back]");
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

// The catalog is served over plain http on the LAN, where navigator.clipboard is not available.
function copyText(button, text) {
  const area = Object.assign(document.createElement("textarea"), { value: text });
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) {
    prompt("Copy this path:", text);
    return;
  }
  const label = button.textContent;
  button.textContent = "Copied";
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
    const result = await post(url, body);
    everyone = null; // a name just given may be a new person: suggest them from now on
    if (result.errors?.length) {
      const more = result.more_errors ? `\n…and ${result.more_errors} more` : "";
      alert(`${result.moved} done. Not these:\n${result.errors.join("\n")}${more}`);
    }
    if (el.dataset.show && result.message) document.querySelector(el.dataset.show).textContent = result.message;
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
    else if ("reload" in el.dataset || result.reload) location.reload(); // result.reload: only when something changed
  } catch (err) {
    alert(`That didn't work: ${err.message}`);
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
      said.textContent = "LineageBox is restarting with the new version…";
    }
    if (Date.now() - started < 15 * 60 * 1000) setTimeout(look, 3000);
    else said.textContent = "The update is taking a long time: `docker compose logs updater` says what it is doing.";
  };
  setTimeout(look, 3000);
}

// Cleanup lists: tick photos (clicking a photo ticks it), then move the ticked ones to the Removed folder. Faces
// suggested as someone (data-faces forms) are ticked the same way.
function updateSelection(form) {
  const n = form.querySelectorAll('input[name="image"]:checked, input[name="face"]:checked').length;
  form.querySelectorAll('button[type="submit"][data-label]').forEach((submit) => {
    submit.textContent = submit.dataset.label.replace("{n}", n.toLocaleString());
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
    .catch((err) => alert(`Those ticks weren't kept: ${err.message}`));
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
    const ids = [...form.querySelectorAll('input[name="image"]:checked')].map((i) => Number(i.value));
    const body = { image_ids: ids, reason: form.dataset.reason };
    for (const el of form.elements) {
      if (el.name && el.name !== "image" && el.matches("input:not([type=checkbox])")) body[el.name] = el.value.trim();
    }
    addPicked(form, body);
    // the moved or kept photos drop off the page; tagging them (data-keep) leaves them, ticked
    if ("keep" in (e.submitter?.dataset ?? {})) delete form.dataset.reload;
    else form.dataset.reload = "";
    run(form, e.submitter?.dataset.api || form.dataset.selectApi, body).then(() => updateSelection(form));
  } else if ("settings" in form.dataset) {
    run(form, form.dataset.api, addPicked(form, settingsBody(form)));
  } else if ("files" in form.dataset) {
    filesBody(form).then((body) => run(form, form.dataset.api, body));
  } else if ("photos" in form.dataset) {
    photosBody(form).then((body) => run(form, form.dataset.api, body), (err) => alert(`A photo couldn't be read: ${err}`));
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
    alert(`That didn't work: ${err.message}`);
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
      if (note) note.textContent = "Couldn't load: point at it again to try again";
    });
}
document.addEventListener("toggle", (e) => {
  if (e.target.open) loadOnOpen(e.target.querySelector?.(":scope > [data-on-open]"));
}, true);
document.querySelectorAll("details > summary").forEach((summary) => {
  const part = summary.parentElement.querySelector(":scope > [data-on-open]");
  if (part) summary.addEventListener("mouseenter", () => loadOnOpen(part), { once: true });
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
    x.textContent = "Couldn't load";
    x.title = "Reload the page to try again";
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
  if (count) count.textContent = words.length ? `${shown} shown` : "";
  const more = document.querySelector(`[data-more="#${input.id}"]`);
  if (more) more.closest("p").hidden = matched <= shown;
  const left = document.querySelector(`[data-more-left="#${input.id}"]`);
  if (left) left.textContent = `${(matched - shown).toLocaleString()} more`;
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
  (open ? target.querySelector("input") : button)?.focus();
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
    const about = [p.rank === 2 && `also ${p.also}`, p.about,
                   p.photos ? `${p.photos.toLocaleString()} photo${p.photos === 1 ? "" : "s"}` : "no photos yet"];
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

// Tour page: the slideshow, full screen. Space pauses, ← → step, Esc leaves.
document.addEventListener("click", (e) => {
  const play = e.target.closest("[data-play]");
  if (play) playTour(JSON.parse(document.querySelector(play.dataset.play).textContent), Number(play.dataset.seconds) || 5);
});

function playTour(slides, seconds) {
  let i = 0;
  let paused = false;
  let timer;
  const box = document.createElement("div");
  box.className = "player";
  box.innerHTML = `<div class="slide"></div><div class="caption"><span class="when"></span><span class="who"></span></div>
    <div class="controls"><button type="button" data-act="prev" aria-label="Previous">‹</button>
    <button type="button" data-act="pause" aria-label="Pause">❚❚</button>
    <button type="button" data-act="next" aria-label="Next">›</button><span class="count"></span>
    <button type="button" data-act="close" aria-label="Close">×</button></div>`;
  document.body.append(box);
  const stage = box.querySelector(".slide");
  const pauseButton = box.querySelector('[data-act="pause"]');

  const schedule = () => {
    clearTimeout(timer);
    if (!paused) timer = setTimeout(next, slides[i].video ? 30000 : seconds * 1000);  // a video also ends itself
  };
  const show = () => {
    const s = slides[i];
    const media = s.video
      ? Object.assign(document.createElement("video"), { src: s.src, poster: s.poster, autoplay: !paused, playsInline: true })
      : Object.assign(document.createElement("img"), { src: s.src, alt: s.people });
    if (s.video) media.addEventListener("ended", () => !paused && next());
    stage.replaceChildren(media);
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
    pauseButton.setAttribute("aria-label", paused ? "Play" : "Pause");
    const video = stage.querySelector("video");
    if (video) paused ? video.pause() : video.play();
    schedule();
  };
  const close = () => {
    clearTimeout(timer);
    document.removeEventListener("keydown", keys, true);
    document.removeEventListener("fullscreenchange", left);
    if (document.fullscreenElement) document.exitFullscreen();
    box.remove();
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

// Each key is in its button's or box's tooltip.
document.querySelectorAll("main [data-key]").forEach((el) => {
  el.title = `${el.title || keyLabel(el)} (key ${el.dataset.key})`;
});

try {
  const again = sessionStorage.getItem("lineagebox-refocus");
  sessionStorage.removeItem("lineagebox-refocus");
  if (again) pressKey(again);
} catch {}

// Tick-photos bar: Download sends the ticked photos' ids as a plain form, and the browser saves the zip it gets back.
document.addEventListener("click", (e) => {
  const button = e.target.closest("button[data-download]");
  if (!button) return;
  const ids = [...button.closest("form").querySelectorAll('input[name="image"]:checked')].map((i) => i.value);
  if (!ids.length) return alert("Tick the photos to download first.");
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
    status.textContent = left ? `${done} uploaded, ${left} to go${failed ? `, ${failed} not taken` : ""}…`
      : `${done} uploaded${failed ? `, ${failed} not taken` : ""}. They show here once the worker has read them.`;
  };
  const send = ({ file, path, item }) => {
    running++;
    const bar = item.querySelector("progress");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload?${new URLSearchParams({ path, folder: folder.value.trim() })}`);
    xhr.setRequestHeader("X-Requested-With", "lineagebox");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.value = e.loaded / e.total; };
    xhr.onload = () => {
      let detail = "";
      try { detail = JSON.parse(xhr.responseText).detail ?? ""; } catch {}
      if (xhr.status === 200) { done++; item.classList.add("ok"); bar.value = 1; }
      else { failed++; item.classList.add("failed"); item.querySelector("small").textContent = detail || `didn't work (${xhr.status})`; }
    };
    xhr.onerror = () => { failed++; item.classList.add("failed"); item.querySelector("small").textContent = "the connection dropped"; };
    xhr.onloadend = () => { running--; left--; show(); next(); };
    xhr.send(file);
  };
  const next = () => { while (running < 3 && queue.length) send(queue.shift()); };
  const add = (file, path) => {
    const item = document.createElement("li");
    item.append(Object.assign(document.createElement("span"), { textContent: path }),
                Object.assign(document.createElement("progress"), { max: 1, value: 0 }),
                document.createElement("small"));
    list.prepend(item);
    queue.push({ file, path, item });
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
  if (!file) { note.textContent = "Pick a file first."; return; }
  note.textContent = "Reading it and looking for who is who…";
  let r, result = {};
  try {
    r = await fetch(`/api/family/import?name=${encodeURIComponent(file.name)}`, {
      method: "POST", body: file, headers: { "X-Requested-With": "lineagebox", "Content-Type": "application/octet-stream" } });
    try { result = JSON.parse(await r.text()); } catch {}  // a proxy's error page, say
  } catch {
    note.textContent = "The file didn't get through: check the connection and try again.";
    return;
  }
  if (r.ok && result.go) location.href = result.go;
  else note.textContent = result.detail || `That didn't work (${r.status}).`;
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
// and × stay clickable under it. Not on touch screens, where nothing is pointed at, nor when the setting is 0; covers
// (folders, memories, suggested tours) are left alone.
const PEEK_AFTER = Number(document.body.dataset.hoverPreview ?? 500);
const PEEK_WARM = 400;  // ms after one is put away in which the next shows without waiting
if (PEEK_AFTER > 0 && matchMedia("(hover: hover) and (pointer: fine)").matches) {
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
    note.textContent = "Drawing the picture…";
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
      note.textContent = `The picture couldn't be made: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

// The tree as one PNG, drawn from the page as it is laid out: each box's background and border, the lines between
// the cards (the stylesheet draws them with ::before and ::after), the faces and the words. Twice the size it is on
// screen, or less for a tree too large for the browser to hold as one picture.
const PICTURE_MOST_SIDE = 16_384;         // px: browsers refuse a canvas wider or taller
const PICTURE_MOST_PIXELS = 120_000_000;  // and one larger than about this
async function treePicture(sheet) {
  const held = sheet.style.transform;
  sheet.style.transform = "none";  // measured as laid out, unzoomed
  const origin = sheet.getBoundingClientRect();
  const items = [];
  try {
    measure(sheet, origin, items, 1);
  } finally {
    sheet.style.transform = held;
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
  const background = getComputedStyle(document.body).backgroundColor;
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
  throw new Error("the tree is too large for this browser to draw as one picture");
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
    return "The passkey wasn't used: it was cancelled, or took too long. Try again.";
  }
  if (err.name === "InvalidStateError") return "This phone or computer has a passkey for this account already.";
  if (err.name === "SecurityError") return "Passkeys don't work on this address.";
  if (err.name === "NotSupportedError") return "This phone or computer can't make the kind of passkey this site needs.";
  return err.message;
}

function passkeySay(words) {
  const said = document.getElementById("passkey-said");
  if (!said) return alert(words);
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
  if (!token) passkeySay("This link is missing its end. Open the whole link you were sent, or ask for it again.");
  else {
    post("/auth/invite/check", { token }).then((found) => {
      invite.querySelector("[data-invite-name]").textContent = found.who;
      invite.querySelector("[data-invite-email]").textContent = found.who === found.email ? "" : found.email;
      invite.querySelector("[data-invite-who]").hidden = false;
    }, (err) => passkeySay(err.message));
  }
}
