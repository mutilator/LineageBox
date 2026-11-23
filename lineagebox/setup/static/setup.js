// LineageBox's setup wizard (lineagebox/setup/server.py serves it): the questions step by step; the compose file, made
// by the server from the answers (POST api/compose) and shown as it is made; then Download, Copy, or Save on this
// machine. Passwords stay in this page: the server's file has a stand-in for each (@@password:N@@), which fill()
// replaces, escaped as compose.py's quote_inner does, only as the file is downloaded or copied. Save on the machine
// itself is the one time they are sent, to that machine.
"use strict";

const STEPS = [
  { id: "machine", title: "Where it will run" },
  { id: "owner", title: "Who owns the files" },
  { id: "photos", title: "Your photos" },
  { id: "catalog", title: "The catalog" },
  { id: "ai", title: "AI helpers" },
  { id: "more", title: "Last details" },
  { id: "done", title: "Your compose file" },
];
const DONE = STEPS.length - 1;
const LANGUAGES = [["auto", "Tell from the speech (each video on its own)"], ["en", "English"], ["de", "German"],
  ["fr", "French"], ["es", "Spanish"], ["it", "Italian"], ["pt", "Portuguese"], ["nl", "Dutch"], ["pl", "Polish"],
  ["sv", "Swedish"], ["da", "Danish"], ["no", "Norwegian"], ["fi", "Finnish"], ["cs", "Czech"], ["hu", "Hungarian"],
  ["ro", "Romanian"], ["el", "Greek"], ["tr", "Turkish"], ["ru", "Russian"], ["uk", "Ukrainian"], ["he", "Hebrew"],
  ["ar", "Arabic"], ["hi", "Hindi"], ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["tl", "Tagalog"],
  ["vi", "Vietnamese"]];
const STORE = "lineagebox-setup";

let INFO = null;     // api/info: the mode, nas.toml's machines, what was found on this machine
let KEY = "";        // the key the setup printed (not on the hosted one)
let R = null;        // the server's last answer: compose, problems, steps, network_paths, catalog_gb
let step = 0, seen = 0;
const touched = new Set();   // answers people changed themselves, which a new machine's suggestions leave alone
const secrets = {};          // passwords by source id: never sent but to Save on this machine
let nextId = 1;
const A = {                  // the answers, as compose.py's read() takes them
  system: "", model: "", host_name: "", install_dir: "", data: "", photos_count: "",
  puid: 1000, pgid: 1000, root: false, sources: [],
  ollama: { where: "none", gpu: "cpu", url: "" },
  whisper: { where: "none", model: "ggml-large-v3-turbo-q5_0.bin", url: "", language: "auto" },
  about: "", tz: "", port: 8000, bind: "", host_network: false, host_address: "",
  updater: true, socket: "", version: "", network_paths: {},
};

// ---- small things
const $ = (sel, root = document) => root.querySelector(sel);
const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
  "'": "&#39;" })[c]);
const id = (path) => "f-" + path.replace(/\./g, "-");
const sys = (sid = A.system) => (INFO.systems || []).find((s) => s.id === sid) || null;
const hosted = () => INFO.mode === "hosted";
/** whether to say how to start the wizard on the machine: it doesn't look at one here, and no key would let it */
const canStartHere = () => !INFO.can_browse && !INFO.locked;
const lockedInstall = () => INFO.can_save && INFO.detected && INFO.detected.install_dir;
const fill = (t, v) => t.replace(/\{(\w+)\}/g, (m, k) => (k in v ? v[k] : m));
const base = (p) => String(p || "").replace(/\/+$/, "").split("/").pop();

function get(path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), A);
}
function set(path, value) {
  const keys = path.split(".");
  let o = A;
  for (const k of keys.slice(0, -1)) o = o[k];
  o[keys[keys.length - 1]] = value;
}

/** text inside a YAML double-quoted string that compose takes as it is: compose.py's quote_inner, word for word */
function quoteInner(text) {
  let out = "";
  for (const c of String(text)) {
    const n = c.codePointAt(0);
    if (c === "\\") out += "\\\\";
    else if (c === '"') out += '\\"';
    else if (c === "$") out += "$$";
    else if (n < 0x20 || (n >= 0x7f && n <= 0x9f) || n === 0x2028 || n === 0x2029 || n === 0xfeff) {
      out += "\\u" + n.toString(16).padStart(4, "0");
    } else out += c;
  }
  return out;
}
const STAND_IN = /@@password:(\d+)@@/g;
const masked = (text) => text.replace(STAND_IN, "••••••");

/** the compose file as HTML, colored as YAML: keys, strings, anchors, comments, numbers and the rest. Made for the
 * files compose.py writes (one key a line, flow mappings and lists on one line), escaped piece by piece. */
function highlight(text) {
  return text.split("\n").map(highlightLine).join("\n");
}
const TOKENS = [
  [/^\s+#.*/, null],                                       // a comment after something: its spaces stay plain
  [/^"(?:[^"\\]|\\.)*"?/, "s"], [/^'(?:[^']|'')*'?/, "s"],
  [/^[&*][\w.-]+/, "a"],
  [/^(?:<<|[A-Za-z_][\w.-]*)(?=:(?:\s|$))/, "k"],          // a key inside {...}
  [/^[[\]{},]|^:(?=\s|$)/, "p"],
  [/^(?:true|false|null)(?![\w.-])/, "l"],
  [/^-?\d+(?:\.\d+)?(?![\w.:/-])/, "n"],
  [/^[^\s"'&*[\]{},:#]+|^\s+|^./, ""],
];
function highlightLine(line) {
  const span = (cls, s) => (cls ? `<span class="y-${cls}">${h(s)}</span>` : h(s));
  const lead = /^(\s*)(- )?/.exec(line);
  let out = lead[1] + (lead[2] ? span("p", "-") + " " : "");
  let i = lead[0].length;
  if (line[i] === "#") return out + span("c", line.slice(i));
  const key = /^(<<|"(?:[^"\\]|\\.)*"|[^\s:#"'{}[\],&*][^:#]*?)(:)(?=\s|$)/.exec(line.slice(i));
  if (key) {
    out += span("k", key[1]) + span("p", ":");
    i += key[0].length;
  }
  while (i < line.length) {
    const rest = line.slice(i);
    for (const [re, cls] of TOKENS) {
      const m = re.exec(rest);
      if (!m) continue;
      if (cls === null) {
        const gap = /^\s+/.exec(rest)[0];
        return out + gap + span("c", rest.slice(gap.length));
      }
      out += span(cls, m[0]);
      i += m[0].length;
      break;
    }
  }
  return out;
}
const filled = (text) => text.replace(STAND_IN, (m, i) => quoteInner(secrets[(A.sources[+i] || {}).id] || ""));

function notice(text, bad) {
  const n = $("#notice");
  n.textContent = text;
  n.className = bad ? "error" : "";
  n.hidden = false;
  clearTimeout(notice.t);
  notice.t = setTimeout(() => { n.hidden = true; }, bad ? 7000 : 3500);
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Setup-Key": KEY },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok && r.status !== 400 && r.status !== 409) {
    throw new Error((data && typeof data.detail === "string" && data.detail) || `The setup answered ${r.status}.`);
  }
  return { status: r.status, data };
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) { /* plain http: the clipboard API needs https */ }
  const t = document.createElement("textarea");
  t.value = text;
  t.setAttribute("readonly", "");
  t.className = "offscreen";
  document.body.append(t);
  t.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  t.remove();
  return ok;
}

// ---- the answers sent, and what the server says of them
function payload() {
  const a = JSON.parse(JSON.stringify(A));
  a.sources = A.sources.map((s) => ({
    kind: s.choice === "smb" ? "smb" : "folder", new: s.choice === "new", name: s.name, path: s.path,
    writable: s.writable, size_gb: s.size_gb, server: s.server, share: s.share, username: s.username,
    version: s.version, has_password: !!secrets[s.id],
  }));
  return a;
}
function secretsByIndex() {
  const out = {};
  A.sources.forEach((s, i) => { if (s.choice === "smb" && secrets[s.id]) out[i] = secrets[s.id]; });
  return out;
}
/** what only this page can see: the passwords */
function ownProblems() {
  const out = [];
  A.sources.forEach((s, i) => {
    if (s.choice === "smb" && (secrets[s.id] || "").includes(",")) {
      out.push({ level: "error", field: `sources.${i}.password`, text: "Docker can't pass a password with a comma to "
        + "the share. Change the share's password, or mount the share on this machine instead (docs/install.md)." });
    }
  });
  return out;
}
const problems = () => [...((R && R.problems) || []), ...ownProblems()];
const errors = () => problems().filter((p) => p.level === "error");

function stepOf(field) {
  if (/^(system|model)$/.test(field)) return 0;
  if (/^(puid|pgid|root)$/.test(field)) return 1;
  if (/^sources/.test(field)) return 2;
  if (/^(data|install_dir|photos_count)$/.test(field)) return 3;
  if (/^(ollama|whisper|about)/.test(field)) return 4;
  return 5;
}

let timer = null, asked = 0, dirty = false;
function schedule(now) {
  dirty = true;
  clearTimeout(timer);
  timer = setTimeout(refresh, now ? 0 : 350);
  remember();
}
async function refresh() {
  const n = ++asked;
  dirty = false;
  try {
    const { data } = await api("api/compose", { answers: payload() });
    if (n !== asked) return;
    R = data;
    showResult();
  } catch (e) {
    notice(e.message, true);
  }
}
/** the file as of the last answers, before it is downloaded, copied or saved */
async function settle() {
  if (dirty) {
    clearTimeout(timer);
    await refresh();
  }
}

function problemHtml(p) {
  return `<p class="problem ${p.level}" data-from-server>${h(p.text)}</p>`;
}
function showResult() {
  if (!R) return;
  $("#preview-text").innerHTML = highlight(masked(R.compose));
  document.querySelectorAll("#step [data-from-server]").forEach((e) => e.remove());
  for (const p of problems()) {
    const el = document.querySelector(`#step [data-field="${CSS.escape(p.field)}"]`);
    if (el) el.insertAdjacentHTML("beforeend", problemHtml(p));
  }
  document.querySelectorAll("#step [data-out]").forEach((el) => { el.textContent = out(el.dataset.out); });
  document.querySelectorAll("#step [data-np]").forEach((el) => {
    el.placeholder = (R.network_paths || {})[el.dataset.np] || "\\\\name\\share";
  });
  drawNav();
  if (step === DONE) drawDone();
}
function out(what) {
  if (what === "catalog") {
    const gb = R && R.catalog_gb;
    return gb ? `The catalog will take about ${gb} GB, and more as photos are added.`
      : `About ${INFO.mb_per_photo} MB a photo: 10 GB for 20,000.`;
  }
  return "";
}

// ---- drawing
function field(key, label, input, hint, forId) {
  return `<div class="field" data-field="${h(key)}"><label for="${h(forId || id(key))}">${label}</label>${input}`
    + (hint ? `<p class="hint">${hint}</p>` : "") + `</div>`;
}
function input(path, opts = {}) {
  const v = get(path);
  const attrs = [`id="${h(opts.id || id(path))}"`, `data-a="${h(path)}"`, `value="${h(v ?? "")}"`,
    `type="${opts.type || "text"}"`, 'autocomplete="off"', 'spellcheck="false"'];
  if (opts.type === "number") attrs.push('data-type="int"', 'inputmode="numeric"', `min="${opts.min ?? 0}"`);
  if (opts.placeholder) attrs.push(`placeholder="${h(opts.placeholder)}"`);
  if (opts.cls) attrs.push(`class="${opts.cls}"`);
  if (opts.list) attrs.push(`list="${opts.list}"`);
  if (opts.readonly) attrs.push("readonly");
  if (opts.disabled) attrs.push("disabled");
  return `<input ${attrs.join(" ")}>`;
}
function withBrowse(path, opts = {}) {
  const box = input(path, opts);
  if (!INFO.can_browse || opts.readonly) return box;
  return `<div class="with-button">${box}<button type="button" data-act="browse" data-target="${h(path)}">`
    + "Browse…</button></div>";
}
function radio(name, path, value, label, desc, extra = "") {
  const checked = String(get(path)) === String(value) ? "checked" : "";
  return `<label class="radio"><input type="radio" name="${h(name)}" data-a="${h(path)}" value="${h(value)}" ${checked}>`
    + `<span><b>${label}</b>${extra}${desc ? `<span class="muted">${desc}</span>` : ""}</span></label>`;
}
function check(path, label, desc, opts = {}) {
  return `<label class="check" data-field="${h(path)}"><input type="checkbox" data-a="${h(path)}" `
    + `${get(path) ? "checked" : ""}`
    + `${opts.disabled ? " disabled" : ""}><span><b>${label}</b>${desc ? `<span class="hint block">${desc}</span>` : ""}`
    + `</span></label>`;
}
function option(value, label, current) {
  return `<option value="${h(value)}" ${String(value) === String(current) ? "selected" : ""}>${h(label)}</option>`;
}
function steps(lines, values, ordered = true) {
  if (!lines || !lines.length) return "";
  const tag = ordered ? "ol" : "ul";
  return `<${tag}>${lines.map((t) => `<li>${h(fill(t, values))}</li>`).join("")}</${tag}>`;
}
function codeBlock(text) {
  return `<div class="code"><pre>${h(text)}</pre>`
    + `<button type="button" class="small" data-act="copy-code">Copy</button></div>`;
}
/** starting the wizard on the machine itself: LineageBox's compose.yaml, which starts the image's wizard */
function setupCommand() {
  const dir = A.install_dir || (sys() || {}).install_dir || "/opt/lineagebox";
  const sudo = (sys() || {}).sudo ? "sudo " : "";
  return `mkdir -p "${dir}" && cd "${dir}"\n`
    + `curl -fsSLO https://raw.githubusercontent.com/${INFO.repository}/main/compose.yaml\n`
    + `${sudo}docker compose up -d && ${sudo}docker compose logs -f web`;
}
const LOOK_HINT = "Then open the address it prints (Ctrl+C stops showing the log, not the wizard). It reads the "
  + "machine's files without changing them, writes only in that folder, and saves the file that takes its place there.";
/** where the page stands with the machine it runs on, when it runs on one */
function machineNotes() {
  let html = "";
  if (INFO.wrong_key) {
    html += `<div class="note warn"><p><b>That key is from an earlier start.</b> The wizard makes a new one each time `
      + `it starts: open the address in its log again. Until then it makes your file as the online one does: you `
      + `type the paths, then download or copy it.</p></div>`;
  } else if (INFO.locked) {
    html += `<div class="note warn"><p><b>Opened without its key.</b> The wizard can still make your file: you type `
      + `the paths, then download or copy it. To let it look at this machine's folders and save the file there, open `
      + `the address with its key. It is in the wizard's log: <code>docker compose logs web</code> in its folder, or `
      + `the container's log in your Docker app.</p></div>`;
  }
  if (INFO.by_hand) {
    html += `<div class="note warn"><p><b>Was LineageBox set up here by hand?</b> This folder has a .env file. The `
      + `compose file for a setup by hand is now compose.manual.yaml: put <code>COMPOSE_FILE=compose.manual.yaml</code> `
      + `in .env (first, where it lists other files) and run <code>docker compose up -d</code> again, and LineageBox `
      + `starts as before. Or carry on here: the wizard's file needs no .env (take COMPOSE_FILE out of it if it is `
      + `there).</p></div>`;
  }
  return html;
}

function drawNav() {
  const bad = new Set(errors().map((p) => stepOf(p.field)));
  $("#steps").innerHTML = STEPS.map((s, i) => `<button type="button" data-act="go" data-step="${i}" `
    + `${i === step ? 'aria-current="step"' : ""} ${i > seen ? "disabled" : ""}><span class="n">${i + 1}</span>`
    + `<span class="label">${h(s.title)}</span>${bad.has(i) && i !== DONE && i <= seen
      ? '<span class="bad" title="Something to put right"></span>' : ""}</button>`).join("");
}

function draw(moved) {
  const was = document.activeElement;
  const again = was && was.dataset && was.dataset.a ? `[data-a="${CSS.escape(was.dataset.a)}"]`
    + (was.type === "radio" ? `[value="${CSS.escape(was.value)}"]` : "") : null;
  document.body.classList.toggle("at-done", step === DONE);   // the file is in the page there: not beside it too
  $("#step").innerHTML = DRAW[STEPS[step].id]() + navButtons();
  showResult();
  drawNav();
  if (moved) {
    const h1 = $("#step h1");
    if (h1) { h1.tabIndex = -1; h1.focus(); }
    window.scrollTo(0, 0);
  } else if (again) {
    const el = $(`#step ${again}`);
    if (el) el.focus();
  }
}
function navButtons() {
  const back = step > 0 ? `<button type="button" data-act="back">Back</button>` : "<span></span>";
  const next = step < DONE
    ? `<button type="button" class="primary" data-act="next">Next: ${h(STEPS[step + 1].title)}</button>` : "";
  return `<div class="nav">${back}${next}</div>`;
}

const DRAW = {};

DRAW.machine = () => {
  const d = INFO.detected;
  let html = `<h1>Where will LineageBox run?</h1><p class="intro">Pick the NAS or computer that will run it. The `
    + `wizard then suggests the folders, users and steps that suit it, and you can change any of them.</p>`;
  html += machineNotes();
  if (d && d.system) {
    html += `<p class="note good">This looks like ${h((sys(d.system) || {}).name || d.system)}`
      + `${d.model ? ` (${h(d.model)})` : ""}${d.host_name ? `, named ${h(d.host_name)}` : ""}.</p>`;
  } else if (canStartHere()) {
    html += `<details class="good-to-know"><summary>Or let the setup look at your NAS for you</summary><p>Run it on `
      + `the NAS itself, and it finds your folders, who owns them and your users, and saves the file in place. Over `
      + `SSH on the NAS (pick your machine below first, for its folder):</p>${codeBlock(setupCommand())}<p class="hint">`
      + `${h(LOOK_HINT)}</p></details>`;
  }
  for (const [kind, label] of [["nas", "A NAS"], ["computer", "A computer"], ["other", "Something else"]]) {
    const list = INFO.systems.filter((s) => (s.kind || "nas") === kind);
    html += `<p class="kind">${label}</p><div class="choices">` + list.map((s) => `<button type="button" class="choice" `
      + `data-act="system" data-system="${h(s.id)}" aria-pressed="${A.system === s.id}"><b>${h(s.name)}</b>`
      + `<span>${h(s.summary || "")}</span></button>`).join("") + `</div>`;
  }
  const s = sys();
  if (s && s.models && s.models.length) {
    const select = `<select id="f-model" data-a="model">${option("", "Not listed, or I don't know", A.model)}`
      + s.models.map((m) => option(m.name, m.name + (m.cpu ? ` (${m.cpu})` : ""), A.model)).join("") + `</select>`;
    html += field("model", `Which model? <span class="muted">(optional)</span>`, select, modelHint(s));
  }
  if (s) html += goodToKnow(s);
  return html;
};
function modelHint(s) {
  const m = (s.models || []).find((x) => x.name === A.model);
  if (!m) return "It lets the wizard check that the model can run LineageBox.";
  const arch = { amd64: "64-bit Intel or AMD processor", arm64: "64-bit ARM processor" }[m.arch] || "32-bit processor";
  const memory = m.memory_gb ? `, ${m.memory_gb} GB of memory as it ships` : "";
  return h(`${arch}${memory}.${m.note ? " " + m.note + "." : ""}`);
}
function goodToKnow(s) {
  const notes = [...(s.notes || [])];
  if (s.ssh_how) notes.push("SSH: " + s.ssh_how);
  if (!notes.length) return "";
  return `<details class="good-to-know"><summary>Good to know about ${h(s.name)}</summary>`
    + `${steps(notes, {}, false)}</details>`;
}

DRAW.owner = () => {
  const s = sys() || {};
  const d = INFO.detected || {};
  let html = `<h1>Who owns the files?</h1><p class="intro">LineageBox doesn't run as root. It runs as one user of `
    + `this machine, who must be able to read the photos and write the catalog folder: usually whoever owns the photos. `
    + `What LineageBox writes belongs to them.</p>`;
  const options = [];
  const seenIds = new Set();
  const add = (o) => {
    const k = `${o.uid}:${o.gid}`;
    if (!seenIds.has(k)) { seenIds.add(k); options.push(o); }
  };
  if (d.install_owner) {
    add({ uid: d.install_owner.uid, gid: d.install_owner.gid, label: ownerLabel(d.install_owner),
      who: "the owner of the folder this setup was started in" });
  }
  for (const u of s.users || []) {
    add({ uid: u.uid, gid: u.gid, label: `user ${u.uid}, group ${u.gid}`, who: u.who, guess: u.guess });
  }
  for (const u of d.users || []) {
    const group = `${u.group || u.gid}${u.group ? ` (${u.gid})` : ""}`;
    add({ uid: u.uid, gid: u.gid, label: `${u.name}: user ${u.uid}, group ${group}`,
      who: u.groups && u.groups.length ? "also in " + u.groups.map((g) => g.name).join(", ") : "a user of this machine" });
  }
  const current = `${A.puid}:${A.pgid}`;
  html += `<fieldset data-field="puid"><legend>Run LineageBox as</legend>`;
  for (const o of options) {
    const k = `${o.uid}:${o.gid}`;
    const guess = o.guess ? '<span class="tag">a guess: check yours</span>' : "";
    html += `<label class="radio"><input type="radio" name="owner" data-user="${k}" `
      + `${k === current && !A.root ? "checked" : ""}${A.root ? " disabled" : ""}><span><b>${h(o.label)}</b>${guess}`
      + `<span class="muted">${h(o.who)}</span></span></label>`;
  }
  html += `<div class="row">${field("puid", "User id", input("puid", { type: "number", cls: "short", disabled: A.root }))}`
    + `${field("pgid", "Group id", input("pgid", { type: "number", cls: "short", disabled: A.root }))}</div></fieldset>`;
  html += check("root", "The photos belong to several people", "So that no one user can write them all. LineageBox "
    + "then runs as root, with only the powers writing other users' files needs, and gives what it writes beside a "
    + "photo to that photo's owner.");
  const how = [...(s.ids_how || [])];
  if (s.ssh_how) how.push("SSH: " + s.ssh_how);
  if (how.length) {
    html += `<details class="good-to-know" open><summary>How to find yours</summary>${steps(how, {}, false)}</details>`;
  }
  if (INFO.can_browse) html += `<p class="hint">On the next step, Browse shows who owns each folder.</p>`;
  return html;
};
function ownerLabel(o) {
  const who = `${o.user || "user " + o.uid}${o.user ? ` (${o.uid})` : ""}`;
  return `${who}, group ${o.group || o.gid}${o.group ? ` (${o.gid})` : ""}`;
}

DRAW.photos = () => {
  let html = `<h1>Where are your photos?</h1><p class="intro">LineageBox reads photos and videos where they are, `
    + `with as many folders inside as you like, and never changes them. It only moves the ones you remove to a `
    + `Removed folder beside them, saves uploads, and puts 720p copies of videos browsers can't play next to them.</p>`;
  A.sources.forEach((src, i) => { html += sourceCard(src, i); });
  html += `<button type="button" data-act="add-source">+ Another place with photos</button>`;
  return html;
};
function sourceCard(src, i) {
  const s = sys() || {};
  const p = `sources.${i}`;
  const many = A.sources.length > 1;
  let html = `<div class="card"><div class="card-head"><h3>${many ? `Photos ${i + 1}` : "The photos"}</h3>`
    + (many ? `<button type="button" class="quiet small" data-act="remove-source" data-index="${i}">Remove</button>` : "")
    + `</div><fieldset data-field="${p}.kind"><legend class="muted small">Where are they?</legend>`
    + radio(`choice-${src.id}`, `${p}.choice`, "folder", "In a folder of this machine",
      s.photos_example ? `Such as ${h(s.photos_example)}` : "")
    + radio(`choice-${src.id}`, `${p}.choice`, "smb", "On another computer or NAS, shared over the network",
      "A Windows share or another NAS's shared folder (SMB): Docker connects to it itself.")
    + radio(`choice-${src.id}`, `${p}.choice`, "new", "I don't have a photos folder yet",
      "Make one now, then copy or upload the photos into it.")
    + `</fieldset>`;
  if (src.choice === "smb") html += smbFields(src, i);
  else if (src.choice === "new") html += newFields(src, i);
  else {
    html += field(`${p}.path`, "The folder", withBrowse(`${p}.path`, { placeholder: s.photos_example || "/srv/photos" }),
      "The whole path, as this machine sees it. Pick the folder that holds them all.");
  }
  html += `<div class="row">${field(`${p}.name`, "Its name in LineageBox", input(`${p}.name`, { cls: "short" }),
    "Its top folder in LineageBox, which starts each of its photos' paths: pick it once.")}</div>`;
  html += check(`${p}.writable`, "Let LineageBox write here", "For the Removed folder, uploads and 720p copies. "
    + "Untick to keep it read-only.");
  return html + `</div>`;
}
function smbFields(src, i) {
  const p = `sources.${i}`;
  const said = { "3.0": " (most)", "1.0": " (very old NASes)" };
  const versions = INFO.smb_versions.map((v) => option(v, v + (said[v] || ""), src.version)).join("");
  return `<div class="row">${field(`${p}.server`, "Server", input(`${p}.server`, { placeholder: "192.168.1.20" }),
    "Its name or address. A whole \\\\server\\share works here too.")}`
    + field(`${p}.share`, "Share", input(`${p}.share`, { placeholder: "photos" }), "A folder inside it works: photos/family")
    + `</div>`
    + `<div class="row">${field(`${p}.username`, "User name", input(`${p}.username`), "Empty for a share open to guests.")}`
    + field(`${p}.password`, "Password", `<input id="${id(p + ".password")}" type="password" autocomplete="new-password" `
      + `data-secret="${src.id}" value="${h(secrets[src.id] || "")}">`, hosted() ? "It stays in this browser: it is "
      + "put into the file here, never sent." : "") + `</div>`
    + field(`${p}.version`, "SMB version", `<select id="${id(p + ".version")}" data-a="${p}.version">${versions}</select>`)
    + `<p class="hint">The password is written into compose.yaml, so keep the file private. If the share is down when `
    + `LineageBox starts, Docker doesn't start it.</p>`;
}
function newFields(src, i) {
  const s = sys() || {};
  const p = `sources.${i}`;
  const nas = s.kind === "nas" && s.share_how && s.share_how.length;
  let html = "";
  if (nas) {
    html += `<fieldset class="sub" data-field="${p}.new_where">`
      + radio(`new-${src.id}`, `${p}.new_where`, "nas", "Make a shared folder on the NAS",
        "The NAS backs it up, keeps snapshots, shares it with the family's PCs and lets it grow. LineageBox gets it "
        + "as a folder like any other.", ' <span class="badge">Recommended</span>')
      + radio(`new-${src.id}`, `${p}.new_where`, "here", "A folder next to LineageBox",
        "Simpler, but only LineageBox sees it.") + `</fieldset>`;
  }
  if (nas && src.new_where === "nas") {
    if (s.quota) {
      html += field(`${p}.size_gb`, "How much room should it have?", `<div class="with-button">`
        + input(`${p}.size_gb`, { type: "number", min: 1, cls: "short" }) + `<span class="unit">GB</span></div>`,
        "A phone photo takes a few MB, a minute of video around 100 MB. The limit can be raised later.");
    }
    const values = { size: src.size_gb || "", name: base(src.path) || "photos", user: `${A.puid}:${A.pgid}` };
    const how = (s.share_how || []).filter((t) => src.size_gb || !t.includes("{size}"));
    html += `<div class="recommended how"><b>On the NAS, before you go on:</b>`
      + `${steps([...how, ...(s.permissions_how || [])], values)}</div>`;
    html += field(`${p}.path`, "Its path, once it's made", withBrowse(`${p}.path`), "Copy it from the NAS if it differs.");
  } else {
    html += field(`${p}.path`, "The new folder", withBrowse(`${p}.path`),
      INFO.can_save ? "Save on this machine makes it, when it is inside the folder for LineageBox."
        : "The steps at the end say how to make it.");
  }
  return html;
}

DRAW.catalog = () => {
  const locked = lockedInstall();
  const hint = locked ? "The setup was started here, so Save puts compose.yaml here. To use another folder, download "
    + "the file instead, or start the setup there."
    : INFO.can_save ? "Save puts compose.yaml in the folder the setup was started in (where its own compose.yaml is, "
      + "or where you ran docker run): give that folder here."
      : "compose.yaml goes here, and the catalog folder too unless you pick another.";
  return `<h1>Where should LineageBox keep its catalog?</h1><p class="intro">The catalog is LineageBox's own folder: `
    + `its database (names on faces, the family tree, everything the family types in), thumbnails and previews. It `
    + `must be on this machine's own disk: databases get damaged on network shares.</p>`
    + field("install_dir", "The folder for LineageBox", withBrowse("install_dir", { readonly: locked,
      placeholder: (sys() || {}).install_dir || "/opt/lineagebox" }), hint)
    + field("data", "The catalog folder", withBrowse("data", { placeholder: "/opt/lineagebox/data" }),
      "Made at first, if it isn't there, by Save on this machine or the steps at the end.")
    + field("photos_count", "About how many photos and videos are there?", input("photos_count",
      { type: "number", placeholder: "20000" }), `<span data-out="catalog"></span>`);
};

DRAW.ai = () => {
  const models = Object.entries(INFO.whisper_models).map(([k, v]) => option(k, `${k}: ${v}`, A.whisper.model)).join("");
  let html = `<h1>The AI helpers</h1><p class="intro">Both are optional and can be added later on the Settings page. `
    + `Faces are found and recognized on this machine's own processor, with nothing to set up.</p>`;
  html += `<div class="card"><h3>Photo descriptions (Ollama)</h3><p class="hint">A vision model describes each photo, `
    + `so that "dog on the beach" finds it, and reads places and dates in folder names. A graphics card makes it fast.</p>`
    + `<fieldset data-field="ollama.where"><legend class="muted small">Where does Ollama run?</legend>`
    + radio("ollama", "ollama.where", "none", "Not now", "Everything else works without it.")
    + radio("ollama", "ollama.where", "remote", "On another computer", "A PC with a graphics card is much faster than a NAS.");
  if (A.ollama.where === "remote") {
    html += `<div class="sub">${field("ollama.url", "Its address", withCheck("ollama", "ollama.url", "192.168.1.10"),
      "Such as 192.168.1.10 or http://192.168.1.10:11434. Ollama there must answer other machines: OLLAMA_HOST=0.0.0.0.")}</div>`;
  }
  html += radio("ollama", "ollama.where", "host", "Already on this machine", "Outside Docker: LineageBox reaches it "
    + "through Docker's own address for this machine.")
    + radio("ollama", "ollama.where", "docker", "Here, in Docker", "Next to LineageBox. Without a graphics card it works, slowly.");
  if (A.ollama.where === "docker") {
    html += `<fieldset class="sub" data-field="ollama.gpu"><legend class="muted small">Graphics card</legend>`
      + radio("gpu", "ollama.gpu", "cpu", "None: the processor alone", "Pick a small model on the Settings page (gemma3:4b).")
      + radio("gpu", "ollama.gpu", "nvidia", "NVIDIA", "Needs its driver and the NVIDIA Container Toolkit on this machine.")
      + radio("gpu", "ollama.gpu", "amd", "AMD (ROCm)", "x86-64 only.") + `</fieldset>`;
  }
  html += `</fieldset></div>`;
  html += `<div class="card"><h3>Video transcripts (Whisper)</h3><p class="hint">What is said in videos and recordings `
    + `becomes searchable, and shows beside them.</p><fieldset data-field="whisper.where"><legend class="muted small">`
    + `Where does Whisper run?</legend>`
    + radio("whisper", "whisper.where", "none", "Not now", "")
    + radio("whisper", "whisper.where", "remote", "On another computer", "LineageBox's speech-to-text service on a PC "
      + "with an NVIDIA card (docs/whisper.md).");
  if (A.whisper.where === "remote") {
    html += `<div class="sub">${field("whisper.url", "Its address", withCheck("whisper", "whisper.url", "192.168.1.10"),
      "Such as 192.168.1.10 or http://192.168.1.10:9300.")}</div>`;
  }
  html += radio("whisper", "whisper.where", "docker", "Here, in Docker", "On this machine's processor: slower than "
    + "the video on a small NAS, but it gets there.");
  if (A.whisper.where === "docker") {
    const select = `<select id="f-whisper-model" data-a="whisper.model">${models}</select>`;
    html += `<div class="sub">${field("whisper.model", "Model", select)}</div>`;
  }
  html += `</fieldset>`;
  if (A.whisper.where !== "none") {
    html += field("whisper.language", "The language spoken", `<select id="f-whisper-language" data-a="whisper.language">`
      + LANGUAGES.map(([k, v]) => option(k, v, A.whisper.language)).join("") + `</select>`);
  }
  html += `</div>`;
  html += field("about", `Whose photos are these? <span class="muted">(optional)</span>`,
    input("about", { placeholder: "a family in Oregon, USA" }), "Helps the AI read the places in folder names.");
  return html;
};
function withCheck(kind, path, placeholder) {
  const box = input(path, { placeholder });
  if (!INFO.can_browse) return box;
  return `<div class="with-button">${box}<button type="button" data-act="check" data-kind="${kind}">Check</button></div>`
    + `<p class="hint" data-check="${kind}" aria-live="polite"></p>`;
}

DRAW.more = () => {
  const zones = (Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : [])
    .map((z) => `<option value="${h(z)}">`).join("");
  let html = `<h1>Last details</h1>`
    + field("tz", "Time zone", input("tz", { list: "zones" }) + `<datalist id="zones">${zones}</datalist>`,
      "For the dates shown and the describing hours.")
    + `<div class="row">${field("port", "Port", input("port", { type: "number", min: 1 }),
      "The web page's port: http://this-machine:port")}`
    + field("host_name", `This machine's name <span class="muted">(optional)</span>`, input("host_name",
      { placeholder: "mynas" }), "As the family's PCs know it: for the address, and for Copy network path.") + `</div>`;
  const named = A.sources.filter((s) => s.name);
  if (named.length) {
    html += `<div class="field"><span class="label">Where the family's PCs find each photo folder</span>`
      + named.map((s) => `<div class="with-button np-row"><span class="np-name">`
        + `${h(s.name)}</span><input data-np="${h(s.name)}" value="${h(A.network_paths[s.name] || "")}" `
        + `aria-label="${h(s.name)}'s network path" autocomplete="off" spellcheck="false"></div>`).join("")
      + `<p class="hint">For each photo's Copy network path. Left empty, the wizard's guess (in grey) is used.</p></div>`;
  }
  html += check("updater", "Let admins install new versions from the Status page", "Adds a small updater service: the "
    + "one part with Docker's socket. It has no network and only acts when an admin presses Update now.");
  html += `<details class="good-to-know"${A.host_network || A.version || A.bind ? " open" : ""}><summary>More settings`
    + `</summary><div class="inside">`
    + field("version", `Stay on one version <span class="muted">(optional)</span>`, input("version",
      { placeholder: "the newest", cls: "short" }), "Such as 1.10.3. Left empty, Update now installs each new release.")
    + field("bind", `Answer on one address only <span class="muted">(optional)</span>`, input("bind",
      { placeholder: "every address", cls: "short" }), "One of this machine's IPv4 addresses, such as 192.168.1.5.")
    + check("host_network", "Use this machine's own network (host networking)", "For routers, and machines whose "
      + "Docker can't reach the DNS server.");
  if (A.host_network) {
    html += field("host_address", "The address to answer on", input("host_address", { placeholder: "192.168.1.1",
      cls: "short" }), "This machine's address on the home network.");
  }
  if (A.updater) {
    html += field("socket", "Docker's socket", input("socket", { placeholder: "/var/run/docker.sock" }),
      "For rootless Docker: /run/user/<your id>/docker.sock");
  }
  return html + `</div></details>`;
};

DRAW.done = () => {
  let html = `<h1>Your compose file</h1><p class="intro">One file that runs LineageBox as you said. Paste it into `
    + `${h((sys() || {}).docker_app || "your Docker app")}, or save it as compose.yaml in the folder for LineageBox.</p>`
    + `<div id="done-problems" aria-live="polite"></div><div class="actions">`
    + `<button type="button" class="primary" data-act="download">Download compose.yaml</button>`
    + `<button type="button" data-act="copy">Copy it</button>`
    + (INFO.can_save ? `<button type="button" data-act="save">Save it on this machine</button>` : "")
    + `</div><div id="save-result" aria-live="polite"></div>`
    + `<h2>What to do next</h2><ol class="next-steps" id="next-steps"></ol>`
    + `<h2>The file</h2>`
    + (A.sources.some((s) => s.choice === "smb" && secrets[s.id])
      ? `<p class="hint">Passwords show as •••••• here; the downloaded and copied file has them.</p>` : "")
    + `<div class="file"><pre id="done-file"></pre></div>`;
  if (INFO.locked) {
    html += `<h2>Let it look for you</h2>${machineNotes()}`;
  } else if (canStartHere()) {
    html += `<h2>Let it look for you</h2><p>Run the setup on the machine itself to have it find your folders, who `
      + `owns them and your users, and save the file in place. Over SSH there:</p>${codeBlock(setupCommand())}`
      + `<p class="hint">${h(LOOK_HINT)}</p>`;
  }
  return html;
};
function drawDone() {
  const box = $("#done-problems");
  if (!box || !R) return;
  const all = problems();
  box.innerHTML = all.length ? `<ul class="problems">` + all.map((p) => `<li class="problem ${p.level}">${h(p.text)} `
    + `<button type="button" class="link" data-act="go" data-step="${stepOf(p.field)}">Put it right</button></li>`).join("")
    + `</ul>` : `<p class="note good">Everything is in place.</p>`;
  const blocked = errors().length > 0;
  document.querySelectorAll('[data-act="download"], [data-act="copy"], [data-act="save"]').forEach((b) => {
    b.disabled = blocked;
  });
  $("#next-steps").innerHTML = (R.steps || []).map((s) => `<li><h3>${h(s.title)}</h3>`
    + s.lines.map((l) => `<p>${h(l)}</p>`).join("") + (s.code ? codeBlock(s.code) : "") + `</li>`).join("");
  $("#done-file").innerHTML = highlight(masked(R.compose));
}

// ---- what answers change besides themselves
function newSource(choice = "folder") {
  const s = sys() || {};
  return { id: nextId++, choice, name: "", path: "", writable: true,
    new_where: s.kind === "nas" && s.share_how ? "nas" : "here",
    size_gb: 500, server: "", share: "", username: "", version: "3.0", _t: {} };
}
function defaultName(src, i) {
  const stem = src.choice === "smb" ? "nas" : i === 0 ? "local" : "photos";
  let name = stem, n = 2;
  const taken = (x) => A.sources.some((o, j) => j !== i && (o.name || "").toLowerCase() === x.toLowerCase());
  while (taken(name)) name = `${stem}-${n++}`;
  return name;
}
function newPath(src) {
  const s = sys() || {};
  if (src.new_where === "nas" && s.kind === "nas" && s.share_parent) return `${s.share_parent}/photos`;
  const dir = A.install_dir || (s.install_dir && !s.install_dir.includes("<") ? s.install_dir : "");
  return dir ? `${dir}/photos` : "./photos";
}
/** a source's name and path follow its choices until they are typed or picked */
function sourceDefaults(src, i) {
  if (!src._t.name) src.name = defaultName(src, i);
  if (!src._t.path) src.path = src.choice === "new" ? newPath(src) : "";
}
function applySystem(sid) {
  const s = sys(sid);
  if (!s) return;
  A.system = sid;
  if (!(s.models || []).some((m) => m.name === A.model)) A.model = "";
  const dir = s.install_dir && !s.install_dir.includes("<") ? s.install_dir : "";
  if (!touched.has("install_dir") && !lockedInstall()) A.install_dir = dir;
  if (!touched.has("data")) A.data = A.install_dir ? `${A.install_dir}/data` : "";
  const d = INFO.detected || {};
  const u = d.install_owner || (s.users || [])[0];
  if (!touched.has("puid") && !touched.has("pgid") && u) { A.puid = u.uid; A.pgid = u.gid; }
  if (!touched.has("socket")) A.socket = d.socket || s.docker_socket || "/var/run/docker.sock";
  if (!touched.has("host_network")) A.host_network = !!s.host_network;
  A.sources.forEach((src, i) => {
    if (!src._t.new_where) src.new_where = s.kind === "nas" && s.share_how ? "nas" : "here";
    sourceDefaults(src, i);
  });
}
function changed(path, value) {
  if (path === "install_dir" && !touched.has("data")) {
    A.data = value ? `${String(value).replace(/\/+$/, "")}/data` : "";
    const box = document.getElementById(id("data"));
    if (box) box.value = A.data;
  }
  const m = path.match(/^sources\.(\d+)\.(\w+)$/);
  if (m) {
    const src = A.sources[+m[1]];
    src._t[m[2]] = true;
    if (m[2] === "choice" || m[2] === "new_where") {
      sourceDefaults(src, +m[1]);
      return true;
    }
  }
  return false;
}

// ---- remembering the answers through a reload (never the passwords)
function remember() {
  try {
    sessionStorage.setItem(STORE, JSON.stringify({ A, step, seen, touched: [...touched] }));
  } catch (e) { /* private window: fine */ }
}
function recall() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) || "null");
    if (!saved || !saved.A) return false;
    Object.assign(A, saved.A);
    step = Math.min(saved.step || 0, DONE);
    seen = Math.min(saved.seen || 0, DONE);
    (saved.touched || []).forEach((t) => touched.add(t));
    nextId = Math.max(0, ...A.sources.map((s) => s.id || 0)) + 1;
    A.sources.forEach((s) => { s._t = s._t || {}; });
    return true;
  } catch (e) {
    return false;
  }
}

// ---- the folder browser (on the machine itself)
let browsing = null;
async function browse(target) {
  browsing = { target };
  const s = sys() || {};
  const start = get(target) || (target.startsWith("sources") ? s.share_parent : A.install_dir) || s.share_parent || "/";
  await browseTo(start.startsWith("/") ? start : "/");
  $("#browser").showModal();
}
async function browseTo(path) {
  let data;
  for (let i = 0; i < 30; i++) {
    ({ data } = await api(`api/browse?path=${encodeURIComponent(path)}&uid=${A.puid}&gid=${A.pgid}`));
    if (!data.error || path === "/") break;
    path = path.replace(/\/[^/]*$/, "") || "/";   // not there (yet): its folder
  }
  browsing.path = data.path;
  browsing.here = data;
  const parts = data.path.split("/").filter(Boolean);
  let crumbs = `<button type="button" class="quiet small" data-act="browse-to" data-path="/">/</button>`;
  parts.forEach((part, i) => {
    const to = "/" + parts.slice(0, i + 1).join("/");
    crumbs += `<button type="button" class="quiet small" data-act="browse-to" data-path="${h(to)}">${h(part)}</button>`;
  });
  const s = sys() || {};
  const jumps = [s.share_parent, A.install_dir].filter((x, i, all) => x && x.startsWith("/") && all.indexOf(x) === i);
  let html = `<div class="crumbs">${crumbs}</div>`;
  if (jumps.length) {
    const links = jumps.map((j) => `<button type="button" class="link" data-act="browse-to" data-path="${h(j)}">${h(j)}</button>`);
    html += `<p class="hint">Go to: ${links.join(", ")}</p>`;
  }
  if (data.error) html += `<p class="problem error">${h(data.error)}</p>`;
  if (data.exists) {
    html += `<p class="small">This folder belongs to <b>${h(ownerLabel(data))}</b>`
      + (data.free_gb !== undefined ? `, with ${data.free_gb} GB free of ${data.total_gb} GB` : "") + `.`
      + (data.network ? ` <span class="warn">It is on a network share (${h(data.network)}).</span>` : "")
      + (data.writable === false && !A.root ? ` <span class="warn">User ${A.puid}:${A.pgid} can't write it.</span>` : "")
      + `</p>`;
  }
  html += `<ul class="folders">` + (data.folders || []).map((f) => `<li><button type="button" data-act="browse-to" `
    + `data-path="${h(f.path)}"><span>${h(f.name)}</span><span class="owner">${h(f.user || f.uid)}:${h(f.group || f.gid)}`
    + `${f.network ? ` · <span class="warn">share</span>` : ""}${f.writable === false && !A.root ? ` · <span class="warn">`
    + `can't write</span>` : ""}</span></button></li>`).join("")
    + ((data.folders || []).length ? "" : `<li class="empty">No folders in here.</li>`) + `</ul>`;
  html += `<div class="actions"><button type="button" class="primary" data-act="use-folder">Use ${h(data.path)}</button>`;
  if (data.exists && data.uid !== undefined && !(data.uid === A.puid && data.gid === A.pgid) && data.uid !== 0) {
    html += `<button type="button" data-act="use-owner">Run LineageBox as its owner</button>`;
  }
  html += `</div>`;
  $("#browser-body").innerHTML = html;
}

// ---- actions
const ACT = {
  next: () => go(step + 1),
  back: () => go(step - 1),
  go: (el) => go(+el.dataset.step),
  system: (el) => {
    applySystem(el.dataset.system);
    draw();
    schedule();
  },
  "add-source": () => {
    const src = newSource();
    A.sources.push(src);
    sourceDefaults(src, A.sources.length - 1);
    draw();
    schedule();
    const boxes = document.querySelectorAll("#step .card");
    if (boxes.length) boxes[boxes.length - 1].scrollIntoView({ block: "nearest" });
  },
  "remove-source": (el) => {
    const src = A.sources.splice(+el.dataset.index, 1)[0];
    if (src) delete secrets[src.id];
    draw();
    schedule();
  },
  browse: (el) => browse(el.dataset.target).catch((e) => notice(e.message, true)),
  "browse-to": (el) => browseTo(el.dataset.path).catch((e) => notice(e.message, true)),
  "close-browser": () => $("#browser").close(),
  "use-folder": () => {
    const t = browsing.target;
    set(t, browsing.path);
    touched.add(t);
    changed(t, browsing.path);
    $("#browser").close();
    draw();
    schedule();
  },
  "use-owner": () => {
    const here = browsing.here;
    A.puid = here.uid;
    A.pgid = here.gid;
    A.root = false;
    touched.add("puid");
    touched.add("pgid");
    notice(`LineageBox will run as ${ownerLabel(here)}.`);
    browseTo(browsing.path);
    draw();
    schedule();
  },
  check: async (el) => {
    const kind = el.dataset.kind;
    const out = $(`[data-check="${kind}"]`);
    out.textContent = "Asking…";
    try {
      const { data } = await api("api/check", { kind, url: A[kind].url });
      out.innerHTML = `<span class="${data.ok ? "ok" : "warn"}">${h(data.text)}</span>`;
    } catch (e) {
      out.textContent = e.message;
    }
  },
  download: async () => {
    await settle();
    if (errors().length) return notice("Put the problems above right first.", true);
    const blob = new Blob([filled(R.compose)], { type: "application/yaml" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "compose.yaml";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  },
  copy: async () => {
    await settle();
    if (errors().length) return notice("Put the problems above right first.", true);
    notice(await copyText(filled(R.compose)) ? "Copied: paste it into your Docker app." : "This browser wouldn't copy it: "
      + "download it instead.", false);
  },
  "copy-code": async (el) => {
    notice(await copyText(el.parentElement.querySelector("pre").textContent) ? "Copied." : "This browser wouldn't copy it.");
  },
  save: () => saveHere(false),
  "hide-preview": () => preview(false),
  "show-preview": () => preview(true),
};
function go(to) {
  if (to < 0 || to > DONE) return;
  step = to;
  seen = Math.max(seen, step);
  draw(true);
  remember();
  if (step === DONE) schedule(true);
}
function preview(show) {
  document.body.classList.toggle("no-preview", !show);
  $("#show-preview").hidden = show;
  try { localStorage.setItem(STORE + "-preview", show ? "1" : "0"); } catch (e) { /* fine */ }
}
async function saveHere(replace) {
  await settle();
  if (errors().length) return notice("Put the problems above right first.", true);
  const box = $("#save-result");
  try {
    const { status, data } = await api("api/save", { answers: payload(), secrets: secretsByIndex(), replace });
    if (status === 409) {
      if (window.confirm("There is a compose.yaml in that folder already. Replace it? The old one is kept beside it.")) {
        return saveHere(true);
      }
      return;
    }
    if (status === 400) {
      R = { ...R, ...data };
      showResult();
      return notice("Put the problems above right first.", true);
    }
    const made = data.made.filter((m) => m.new).map((m) => m.path);
    box.innerHTML = `<div class="note good"><p><b>Saved ${h(data.saved)}.</b>`
      + (data.kept ? ` The one that was there is kept as ${h(data.kept)}.` : "") + `</p>`
      + (made.length ? `<p>Made, for LineageBox's user: ${made.map(h).join(", ")}.</p>` : "")
      + (data.todo.length ? `<p>Still to make, as the steps below say: ${data.todo.map(h).join(", ")}.</p>` : "")
      + ((data.not_given || []).length ? `<p>This disk wouldn't give ${data.not_given.map(h).join(", ")} to user `
        + `${h(A.puid)}:${h(A.pgid)}. That is fine on Docker Desktop; elsewhere, give it to them as the steps below `
        + `say.</p>` : "")
      + (INFO.first ? `<p>Now start it in the wizard's place, as the steps below say.</p>`
        : `<p>You can stop the setup now: Ctrl+C where it runs.</p>`) + `</div>`;
  } catch (e) {
    notice(e.message, true);
  }
}

// ---- events
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (el && ACT[el.dataset.act] && !el.disabled) {
    e.preventDefault();
    ACT[el.dataset.act](el);
  }
});
document.addEventListener("input", (e) => {
  const el = e.target;
  if (el.dataset.secret) {
    secrets[el.dataset.secret] = el.value;
    schedule();
    return;
  }
  if (el.dataset.np !== undefined) {
    A.network_paths[el.dataset.np] = el.value;
    schedule();
    return;
  }
  if (el.dataset.user && el.checked) {
    [A.puid, A.pgid] = el.dataset.user.split(":").map(Number);
    touched.add("puid");
    touched.add("pgid");
    draw();
    schedule();
    return;
  }
  if (!el.dataset.a || (el.type === "radio" && !el.checked)) return;
  let v = el.type === "checkbox" ? el.checked : el.value;
  if (el.dataset.type === "int") v = el.value === "" ? "" : Number(el.value);
  set(el.dataset.a, v);
  touched.add(el.dataset.a);
  const redraw = changed(el.dataset.a, v) || el.type === "radio" || el.type === "checkbox"
    || el.dataset.a === "model" || el.dataset.a === "whisper.where";
  if (redraw) draw();
  schedule();
});

async function start() {
  const q = new URLSearchParams(location.search);
  try {
    KEY = q.get("key") || sessionStorage.getItem(STORE + "-key") || "";
    if (q.get("key")) {
      sessionStorage.setItem(STORE + "-key", KEY);
      history.replaceState(null, "", location.pathname);   // out of the address bar, and out of bookmarks
    }
  } catch (e) { /* storage refused: the key stays in the address */ }
  try {
    INFO = (await api("api/info")).data;
    if (INFO.wrong_key) {
      try { sessionStorage.removeItem(STORE + "-key"); } catch (e) { /* storage refused */ }
    }
  } catch (e) {
    $("#step").innerHTML = `<h1>The setup can't start</h1><p class="note">${h(e.message)}</p>`;
    return;
  }
  const d = INFO.detected || {};
  $("#where").textContent = hosted() ? "Nothing you type here is kept"
    : d.host_name ? `On ${d.host_name}` : "On this machine";
  if (!recall()) {
    A.tz = d.tz || (Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    if (d.host_name) A.host_name = d.host_name;
    if (d.install_dir) A.install_dir = d.install_dir;
    if (d.socket) A.socket = d.socket;
    A.sources.push(newSource());
    if (d.system) {
      applySystem(d.system);
      if ((sys().models || []).some((m) => m.name === d.model)) A.model = d.model;
    } else if (d.install_owner) {
      A.puid = d.install_owner.uid;
      A.pgid = d.install_owner.gid;
    }
    sourceDefaults(A.sources[0], 0);
    if (!A.data && A.install_dir) A.data = `${A.install_dir}/data`;
  }
  if (lockedInstall()) A.install_dir = d.install_dir;
  let show = true;
  try { show = localStorage.getItem(STORE + "-preview") !== "0"; } catch (e) { /* fine */ }
  preview(show);
  draw();
  schedule(true);
}
start();
