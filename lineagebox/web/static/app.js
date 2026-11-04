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
  const label = el.dataset.busy && el.textContent;
  controls.forEach((c) => (c.disabled = true));
  if (label) el.textContent = el.dataset.busy;
  try {
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
    if (label) el.textContent = label;
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

// Status page: the progress part reloads itself while the tab is visible.
document.querySelectorAll("[data-live]").forEach((el) => {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const r = await fetch(el.dataset.live);
      if (r.ok) el.innerHTML = await r.text();
    } catch {}
  }, 10000);
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

// Parts that take a while to work out (the Tours page's suggestions) load once the rest of the page is shown.
document.querySelectorAll("[data-later]").forEach(async (el) => {
  try {
    const r = await fetch(el.dataset.later);
    if (r.ok) el.innerHTML = await r.text();
  } catch {}
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
