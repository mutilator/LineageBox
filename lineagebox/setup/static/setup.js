// LineageBox's setup wizard (lineagebox/setup/server.py serves it): the questions step by step; the compose file, made
// by the server from the answers (POST api/compose) and shown as it is made; then Download, Copy, or Save on this
// machine. Passwords stay in this page: the server's file has a stand-in for each (@@password:N@@), which fill()
// replaces, escaped as compose.py's quote_inner does, only as the file is downloaded or copied. Save on the machine
// itself is the one time they are sent, to that machine.
"use strict";

const STEPS = [
  { id: "machine", title: "Where it will run", short: "Machine" },
  { id: "owner", title: "Who owns the files", short: "Owner" },
  { id: "photos", title: "Your photos", short: "Photos" },
  { id: "ai", title: "AI helpers", short: "AI helpers" },
  { id: "more", title: "Last details", short: "Details" },
  { id: "done", title: "Your compose file", short: "Finish" },
];
const DONE = STEPS.length - 1;
const LANGUAGES = [["auto", "Tell from the speech (each video on its own)"], ["en", "English"], ["de", "German"],
  ["fr", "French"], ["es", "Spanish"], ["it", "Italian"], ["pt", "Portuguese"], ["nl", "Dutch"], ["pl", "Polish"],
  ["sv", "Swedish"], ["da", "Danish"], ["no", "Norwegian"], ["fi", "Finnish"], ["cs", "Czech"], ["hu", "Hungarian"],
  ["ro", "Romanian"], ["el", "Greek"], ["tr", "Turkish"], ["ru", "Russian"], ["uk", "Ukrainian"], ["he", "Hebrew"],
  ["ar", "Arabic"], ["hi", "Hindi"], ["ja", "Japanese"], ["ko", "Korean"], ["zh", "Chinese"], ["tl", "Tagalog"],
  ["vi", "Vietnamese"]];
const STORE = "lineagebox-setup";

const IC = {
  check: '<svg class="ic ic-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 13l4 4L19 7"/></svg>',
  nas: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6"/></svg>',
  computer: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
  other: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/></svg>',
  user: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/></svg>',
  folder: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  disk: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6"/><path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/></svg>',
  spark: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z"/></svg>',
  mic: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>',
  clock: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  net: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="4" width="8" height="6" rx="1.5"/><rect x="14" y="14" width="8" height="6" rx="1.5"/><path d="M10 7h4a2 2 0 0 1 2 2v5"/></svg>',
  refresh: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 0-15.5-6.3L3 8m0-5v5h5M3 12a9 9 0 0 0 15.5 6.3L21 16m0 5v-5h-5"/></svg>',
  warn: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3L2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
  good: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.3 2.3 4.7-5"/></svg>',
  info: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
  download: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M4 20h16"/></svg>',
  copy: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  save: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v6h8V3M7 21v-7h10v7"/></svg>',
  file: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  plus: '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  left: '<svg class="ic ic-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>',
  right: '<svg class="ic ic-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>',
};

let INFO = null;     // api/info: the mode, nas.toml's machines, what was found on this machine
let KEY = "";        // the key the setup printed (not on the hosted one)
let R = null;        // the server's last answer: compose, problems, steps, network_paths, catalog_gb
let step = 0, seen = 0;
let machineTab = "all";
const touched = new Set();   // answers people changed themselves, which a new machine's suggestions leave alone
const secrets = {};          // passwords by source id: never sent but to Save on this machine
let nextId = 1;

function blankAnswers() {
  return {
    system: "", model: "", host_name: "", install_dir: "", data: "", photos_count: "",
    puid: 1000, pgid: 1000, owner_manual: false, root: false, sources: [],
    ollama: { where: "none", gpu: "cpu", url: "" },
    whisper: { where: "none", model: "ggml-large-v3-turbo-q5_0.bin", url: "", language: "auto" },
    about: "", tz: "", port: 8000, bind: "", host_network: false, host_address: "",
    updater: true, socket: "", version: "", network_paths: {},
  };
}
const A = blankAnswers();

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
const isNarrow = () => window.matchMedia("(max-width: 1080px)").matches;

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
const warnings = () => problems().filter((p) => p.level === "warning");

const stepIndex = (sid) => STEPS.findIndex((s) => s.id === sid);
function stepOf(field) {
  if (/^(system|model|host_name)$/.test(field)) return stepIndex("machine");
  if (/^(puid|pgid|root)$/.test(field)) return stepIndex("owner");
  if (/^(sources|photos_count)/.test(field)) return stepIndex("photos");
  if (/^(ollama|whisper|about)/.test(field)) return stepIndex("ai");
  return stepIndex("more");   // and the folders under its More settings: install_dir, data
}

let timer = null, asked = 0, dirty = false;
function schedule(now) {
  dirty = true;
  clearTimeout(timer);
  timer = setTimeout(refresh, now ? 0 : 300);
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

function activeServices() {
  const svcs = ["web", "worker", "copies"];
  if (A.updater) svcs.push("updater");
  if (A.ollama.where === "docker") svcs.push("ollama");
  if (A.whisper.where === "docker") svcs.push("whisper");
  return svcs;
}

function problemHtml(p) {
  return `<p class="problem ${p.level}" data-from-server>${h(p.text)}</p>`;
}

function updatePreviewChrome() {
  const errs = errors().length;
  const warns = warnings().length;
  const status = $("#preview-status");
  if (status) {
    if (errs) {
      status.textContent = `${errs} to fix`;
      status.className = "status-pill is-err";
    } else if (warns) {
      status.textContent = `${warns} note${warns === 1 ? "" : "s"}`;
      status.className = "status-pill is-warn";
    } else {
      status.textContent = "Ready";
      status.className = "status-pill is-ok";
    }
  }
  const badge = $("#preview-badge");
  if (badge) {
    badge.hidden = !errs;
    badge.textContent = String(errs);
  }
  const copyBtn = $("#preview-copy");
  if (copyBtn) copyBtn.disabled = errs > 0;

  const bar = $("#stack-bar");
  if (bar) {
    const svcs = activeServices().map((s) => `<span class="stack-chip svc">${h(s)}</span>`).join("");
    const meta = [];
    meta.push(A.root ? "root" : `${A.puid}:${A.pgid}`);
    if (A.port) meta.push(`:${A.port}`);
    if (R && R.catalog_gb) meta.push(`~${R.catalog_gb} GB`);
    bar.innerHTML = svcs + meta.map((m) => `<span class="stack-chip meta">${h(m)}</span>`).join("");
  }
}

function showResult() {
  if (!R) return;
  $("#preview-text").innerHTML = highlight(masked(R.compose));
  updatePreviewChrome();
  document.querySelectorAll("#step [data-from-server]").forEach((e) => e.remove());
  for (const p of problems()) {
    const el = document.querySelector(`#step [data-field="${CSS.escape(p.field)}"]`);
    if (!el) continue;
    el.insertAdjacentHTML("beforeend", problemHtml(p));
    const folded = el.closest("details");   // the folders under More settings: a problem there unfolds them
    if (folded) folded.open = true;
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

// ---- drawing helpers
function stepHead(title, intro) {
  return `<div class="step-head"><p class="eyebrow">Step ${step + 1} of ${STEPS.length}</p>`
    + `<h1>${title}</h1>` + (intro ? `<p class="intro">${intro}</p>` : "") + `</div>`;
}
function field(key, label, inputHtml, hint, forId) {
  return `<div class="field" data-field="${h(key)}"><label for="${h(forId || id(key))}">${label}</label>${inputHtml}`
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
    + `${IC.folder}Browse…</button></div>`;
}
function radio(name, path, value, label, desc, extra = "") {
  const checked = String(get(path)) === String(value) ? "checked" : "";
  return `<label class="radio"><input type="radio" name="${h(name)}" data-a="${h(path)}" value="${h(value)}" ${checked}>`
    + `<span class="radio-body"><b>${label}</b>${extra}${desc ? `<span class="muted">${desc}</span>` : ""}</span></label>`;
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
    + `<button type="button" data-act="copy-code">Copy</button></div>`;
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
    html += `<div class="note warn">${IC.warn}<div class="note-body"><p><b>That key is from an earlier start.</b> The wizard makes a new one each time `
      + `it starts: open the address in its log again. Until then it makes your file as the online one does: you `
      + `type the paths, then download or copy it.</p></div></div>`;
  } else if (INFO.locked) {
    html += `<div class="note warn">${IC.warn}<div class="note-body"><p><b>Opened without its key.</b> The wizard can still make your file: you type `
      + `the paths, then download or copy it. To let it look at this machine's folders and save the file there, open `
      + `the address with its key. It is in the wizard's log: <code>docker compose logs web</code> in its folder, or `
      + `the container's log in your Docker app.</p></div></div>`;
  }
  if (INFO.by_hand) {
    html += `<div class="note warn">${IC.warn}<div class="note-body"><p><b>Was LineageBox set up here by hand?</b> This folder has a .env file. The `
      + `compose file for a setup by hand is now compose.manual.yaml: put <code>COMPOSE_FILE=compose.manual.yaml</code> `
      + `in .env (first, where it lists other files) and run <code>docker compose up -d</code> again, and LineageBox `
      + `starts as before. Or carry on here: the wizard's file needs no .env (take COMPOSE_FILE out of it if it is `
      + `there).</p></div></div>`;
  }
  return html;
}

function stepSummary(i) {
  if (i > seen && i !== step) return "";
  const at = STEPS[i].id;
  if (at === "machine") return (sys() || {}).name || "Not picked";
  if (at === "owner") return A.root ? "Root" : `${A.puid}:${A.pgid}`;
  if (at === "photos") {
    const n = A.sources.length;
    if (!n) return "None";
    if (n === 1) return { smb: "1 share", new: "1 new folder" }[A.sources[0].choice] || "1 folder";
    return `${n} sources`;
  }
  if (at === "ai") {
    const on = [];
    if (A.ollama.where !== "none") on.push("Ollama");
    if (A.whisper.where !== "none") on.push("Whisper");
    return on.length ? on.join(" + ") : "Off";
  }
  if (at === "more") return A.port ? `Port ${A.port}` : "";
  if (at === "done") return errors().length ? `${errors().length} to fix` : "Ready";
  return "";
}

function drawNav() {
  const bad = new Set(errors().map((p) => stepOf(p.field)));
  $("#steps").innerHTML = STEPS.map((s, i) => {
    const isCurrent = i === step;
    const isBad = bad.has(i) && i !== DONE && i <= seen;
    const isDone = i < step && !isBad;
    const sub = stepSummary(i);
    return `<li class="${isDone ? "is-done" : ""}"><button type="button" data-act="go" data-step="${i}" `
      + `${isCurrent ? 'aria-current="step"' : ""} ${i > seen ? "disabled" : ""}>`
      + `<span class="node">${isDone ? IC.check : i + 1}${isBad ? '<span class="bad" title="Something to put right"></span>' : ""}</span>`
      + `<span class="label">${h(s.short)}</span>`
      + `<span class="sub-val">${h(sub)}</span>`
      + `</button></li>`;
  }).join("");
}

function draw(moved) {
  const was = document.activeElement;
  const again = was && was.dataset && was.dataset.a ? `[data-a="${CSS.escape(was.dataset.a)}"]`
    + (was.type === "radio" ? `[value="${CSS.escape(was.value)}"]` : "") : null;
  document.body.classList.toggle("at-done", step === DONE);
  if (step === DONE) setDrawer(false);
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
  const back = step > 0
    ? `<button type="button" class="pill" data-act="back">${IC.left}Back</button>`
    : `<span></span>`;
  const nextLabel = step === DONE - 1 ? "Review compose file" : `Next: ${STEPS[step + 1] ? STEPS[step + 1].short : ""}`;
  const next = step < DONE
    ? `<button type="button" class="primary" data-act="next">${h(nextLabel)}${IC.right}</button>`
    : `<span></span>`;
  return `<div class="step-footer">${back}<span class="step-counter">Step ${step + 1} of ${STEPS.length}</span>${next}</div>`;
}

const DRAW = {};

DRAW.machine = () => {
  const d = INFO.detected;
  let html = stepHead("Where will LineageBox run?",
    "Pick the NAS or computer that will run it. The wizard then suggests the folders, users and steps that suit it, and you can change any of them.");
  html += machineNotes();
  if (d && d.system) {
    html += `<div class="note good">${IC.good}<div class="note-body"><p><b>Detected:</b> ${h((sys(d.system) || {}).name || d.system)}`
      + `${d.model ? ` (${h(d.model)})` : ""}${d.host_name ? `, named ${h(d.host_name)}` : ""}.</p></div></div>`;
  } else if (canStartHere()) {
    html += `<details class="good-to-know"><summary>Or let the setup look at your NAS for you</summary><p>Run it on `
      + `the NAS itself, and it finds your folders, who owns them and your users, and saves the file in place. Over `
      + `SSH on the NAS (pick your machine below first, for its folder):</p>${codeBlock(setupCommand())}<p class="hint">`
      + `${h(LOOK_HINT)}</p></details>`;
  }

  const s = sys();
  if (s) {
    const kindIcon = IC[s.kind || "nas"] || IC.nas;
    html += `<div class="card selected-panel"><div class="card-head"><div class="card-title-row">`
      + `<span class="card-icon">${kindIcon}</span><div><h3>${h(s.name)}</h3>`
      + (s.summary ? `<p class="hint">${h(s.summary)}</p>` : "")
      + `</div></div><span class="badge accent">Selected</span></div>`;
    if (s.models && s.models.length) {
      const select = `<select id="f-model" data-a="model">${option("", "Not listed, or I don't know", A.model)}`
        + s.models.map((m) => option(m.name, m.name + (m.cpu ? ` (${m.cpu})` : ""), A.model)).join("") + `</select>`;
      html += field("model", `Which model? <span class="muted">(optional)</span>`, select, modelHint(s));
    }
    html += field("host_name", `Its name on your network <span class="muted">(optional)</span>`, input("host_name",
      { placeholder: "mynas", cls: "short" }), "As the family's PCs know it: for LineageBox's address, and for "
      + "where they find the photos.");
    html += goodToKnow(s) + `</div>`;
  }

  const groups = [
    ["nas", "NAS", "A NAS", IC.nas],
    ["computer", "Computer", "A computer", IC.computer],
    ["other", "Other", "Something else", IC.other],
  ];
  const counts = { all: INFO.systems.length };
  for (const [k] of groups) counts[k] = INFO.systems.filter((x) => (x.kind || "nas") === k).length;

  html += `<div class="tabs" role="tablist" aria-label="Filter machine types">`
    + `<button type="button" class="tab" data-act="tab" data-tab="all" aria-pressed="${machineTab === "all"}">All <span class="tab-count">${counts.all}</span></button>`
    + groups.map(([k, short]) => `<button type="button" class="tab" data-act="tab" data-tab="${k}" aria-pressed="${machineTab === k}">${short} <span class="tab-count">${counts[k]}</span></button>`).join("")
    + `</div>`;

  for (const [kind, , label, icon] of groups) {
    if (machineTab !== "all" && machineTab !== kind) continue;
    const list = INFO.systems.filter((x) => (x.kind || "nas") === kind);
    html += `<div class="kind-head">${icon}<span>${label}</span></div><div class="choices">`
      + list.map((x) => `<button type="button" class="choice" data-act="system" data-system="${h(x.id)}" `
        + `aria-pressed="${A.system === x.id}"><span class="choice-top"><b>${h(x.name)}</b>`
        + `<span class="choice-check">${IC.check}</span></span>`
        + `<span class="choice-desc">${h(x.summary || "")}</span></button>`).join("")
      + `</div>`;
  }
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
  let html = stepHead("Who owns the files?",
    "LineageBox doesn't run as root. It runs as one user of this machine, who must be able to read the photos and write the catalog folder: usually whoever owns the photos. What LineageBox writes belongs to them.");

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
    add({ uid: u.uid, gid: u.gid, label: `user ${u.uid}, group ${u.gid}`, who: u.who, guess: u.guess,
      documented: u.documented });
  }
  for (const u of d.users || []) {
    const group = `${u.group || u.gid}${u.group ? ` (${u.gid})` : ""}`;
    add({ uid: u.uid, gid: u.gid, label: `${u.name}: user ${u.uid}, group ${group}`,
      who: u.groups && u.groups.length ? "also in " + u.groups.map((g) => g.name).join(", ") : "a user of this machine" });
  }

  // one choice: a suggested user, or ids typed by hand (only then are the boxes shown)
  const current = `${A.puid}:${A.pgid}`;
  const manual = A.owner_manual || !options.some((o) => `${o.uid}:${o.gid}` === current);
  html += `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${IC.user}</span><h3>Run LineageBox as</h3></div></div>`
    + `<fieldset data-field="puid"><legend class="subtle-legend">Pick one</legend><div class="option-list">`;
  for (const o of options) {
    const k = `${o.uid}:${o.gid}`;
    const tag = o.documented ? '<span class="tag">Vendor documented</span>'
      : o.guess ? '<span class="tag">Usual default: check yours</span>' : "";
    html += `<label class="radio"><input type="radio" name="owner" data-user="${k}" `
      + `${k === current && !manual && !A.root ? "checked" : ""}${A.root ? " disabled" : ""}><span class="radio-body">`
      + `<b>${h(o.label)}</b>${tag}<span class="muted">${h(o.who)}</span></span></label>`;
  }
  html += `<label class="radio"><input type="radio" name="owner" data-user="manual" `
    + `${manual && !A.root ? "checked" : ""}${A.root ? " disabled" : ""}><span class="radio-body"><b>Manually defined</b>`
    + `<span class="muted">Type the user and group ids yourself.</span></span></label></div>`;
  if (manual && !A.root) {
    html += `<div class="inset"><div class="row row-tight">`
      + `${field("puid", "User id (PUID)", input("puid", { type: "number", cls: "short" }))}`
      + `${field("pgid", "Group id (PGID)", input("pgid", { type: "number", cls: "short" }))}`
      + `</div></div>`;
  }
  html += `</fieldset>`;

  html += check("root", `Run as root, because the photos belong to several people `
    + `<span class="tag warn">Not recommended</span>`, "LineageBox's containers then run as root, which is less safe "
    + "than running as one user: pick this only when no one user can read and write all the photos. Root keeps only "
    + "the powers that writing other users' files needs, and what LineageBox writes beside a photo is given to that "
    + "photo's owner.");
  html += `</div>`;

  const how = [...(s.ids_how || [])];
  if (s.ssh_how) how.push("SSH: " + s.ssh_how);
  if (how.length) {
    html += `<details class="good-to-know" open><summary>How to find yours</summary>${steps(how, {}, false)}</details>`;
  }
  if (INFO.can_browse) {
    html += `<div class="note">${IC.info}<div class="note-body"><p>On the next step, <b>Browse…</b> shows who owns each folder so you can pick its owner in one click.</p></div></div>`;
  }
  return html;
};
function ownerLabel(o) {
  const who = `${o.user || "user " + o.uid}${o.user ? ` (${o.uid})` : ""}`;
  return `${who}, group ${o.group || o.gid}${o.group ? ` (${o.gid})` : ""}`;
}

DRAW.photos = () => {
  let html = stepHead("Where are your photos?",
    "LineageBox reads photos and videos where they are, with as many folders inside as you like, and never changes them. It only moves the ones you remove to a Removed folder beside them, saves uploads, and puts 720p copies of videos browsers can't play next to them.");
  A.sources.forEach((src, i) => { html += sourceCard(src, i); });
  html += `<button type="button" class="add-card-btn" data-act="add-source">${IC.plus}Another place with photos</button>`;
  html += `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${IC.disk}</span><h3>Room for the catalog</h3></div>`
    + `<span class="badge soft">Optional</span></div>`
    + field("photos_count", "About how many photos and videos are there?", input("photos_count",
      { type: "number", placeholder: "20000" }), "")
    + `<div class="estimate-box">${IC.info}<span data-out="catalog"></span></div>`
    + `</div>`;
  return html;
};

function sourceCard(src, i) {
  const s = sys() || {};
  const p = `sources.${i}`;
  const many = A.sources.length > 1;
  const kindBadges = { folder: "Local folder", smb: "Network share", new: "New folder" };
  let html = `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${src.choice === "smb" ? IC.net : IC.folder}</span>`
    + `<h3>${many ? `Photos ${i + 1}` : "Photo library"}</h3>`
    + `<span class="badge soft">${kindBadges[src.choice] || "Folder"}</span></div>`
    + (many ? `<button type="button" class="quiet small" data-act="remove-source" data-index="${i}">Remove</button>` : "")
    + `</div><fieldset data-field="${p}.kind"><legend class="subtle-legend">Where are they?</legend>`
    + `<div class="option-grid option-grid-3">`
    + radio(`choice-${src.id}`, `${p}.choice`, "new", "New photos folder",
      "Make one now, then copy or upload photos into it.")
    + radio(`choice-${src.id}`, `${p}.choice`, "folder", "Folder on this machine",
      s.photos_example ? `Such as ${h(s.photos_example)}` : "A local folder path")
    + radio(`choice-${src.id}`, `${p}.choice`, "smb", "Network share (SMB)",
      "A Windows share or another NAS's shared folder.")
    + `</div></fieldset>`;

  html += `<div class="inset">`;
  if (src.choice === "smb") html += smbFields(src, i);
  else if (src.choice === "new") html += newFields(src, i);
  else {
    html += field(`${p}.path`, "The folder", withBrowse(`${p}.path`, { placeholder: s.photos_example || "/srv/photos" }),
      "The whole path, as this machine sees it. Pick the folder that holds them all.");
  }
  html += `</div>`;

  html += `<div class="sub">${field(`${p}.name`, "Its name in LineageBox", input(`${p}.name`, { cls: "short" }),
    "Its top folder in LineageBox, which starts each of its photos' paths: pick it once.")}`;
  html += field(`${p}.network_path`, `Where the family's PCs find it <span class="muted">(optional)</span>`,
    `<input id="${id(p + ".network_path")}" data-np="${h(src.name)}" value="${h(A.network_paths[src.name] || "")}" `
    + `autocomplete="off" spellcheck="false">`, "For each photo's Copy network path. Left empty, the wizard's guess in "
    + "grey is used" + (A.host_name || src.choice === "smb" ? "." : " (it needs this machine's name, on the first "
      + "step)."));
  html += check(`${p}.writable`, "Let LineageBox write here", "For the Removed folder, uploads and 720p copies. "
    + "Untick to keep it read-only.") + `</div>`;
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
    html += `<fieldset data-field="${p}.new_where"><legend class="subtle-legend">Where should the new folder live?</legend>`
      + `<div class="option-list">`
      + radio(`new-${src.id}`, `${p}.new_where`, "nas", "Make a shared folder on the NAS",
        "The NAS backs it up, keeps snapshots, shares it with the family's PCs and lets it grow. LineageBox gets it "
        + "as a folder like any other.", ' <span class="badge">Recommended</span>')
      + radio(`new-${src.id}`, `${p}.new_where`, "here", "A folder next to LineageBox",
        "Simpler, but only LineageBox sees it.") + `</div></fieldset>`;
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

/** LineageBox's own folders, under Last details' More settings: the machine's suggestions suit most people */
function folderFields() {
  const locked = lockedInstall();
  const hint = locked ? "The setup was started here, so Save puts compose.yaml here. To use another folder, download "
    + "the file instead, or start the setup there."
    : INFO.can_save ? "Save puts compose.yaml in the folder the setup was started in (where its own compose.yaml is, "
      + "or where you ran docker run): give that folder here."
      : "compose.yaml goes here, and the catalog folder too unless you pick another.";
  return `<p class="subtle-legend">LineageBox's folders</p>`
    + field("install_dir", "The folder for LineageBox", withBrowse("install_dir", { readonly: locked,
      placeholder: (sys() || {}).install_dir || "/opt/lineagebox" }), hint)
    + field("data", "The catalog folder", withBrowse("data", { placeholder: "/opt/lineagebox/data" }),
      "Its database (names on faces, the family tree, everything the family types in), thumbnails and previews. "
      + "Keep it on this machine's own disk: databases get damaged on network shares. Made at first, if it isn't "
      + "there, by Save on this machine or the steps at the end.");
}

DRAW.ai = () => {
  const models = Object.entries(INFO.whisper_models).map(([k, v]) => option(k, `${k}: ${v}`, A.whisper.model)).join("");
  let html = stepHead("The AI helpers",
    "Both are optional and can be added later on the Settings page. Faces are found and recognized on this machine's own processor, with nothing to set up.");

  html += `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${IC.spark}</span><div><h3>Photo descriptions (Ollama)</h3></div></div>`
    + `<span class="badge soft">Optional</span></div>`
    + `<p class="card-sub">A vision model describes each photo, so that "dog on the beach" finds it, and reads places and dates in folder names. A graphics card makes it fast.</p>`
    + `<fieldset data-field="ollama.where"><legend class="subtle-legend">Where does Ollama run?</legend>`
    + `<div class="option-grid option-grid-2">`
    + radio("ollama", "ollama.where", "none", "Not now", "Everything else works without it.")
    + radio("ollama", "ollama.where", "remote", "On another computer", "A PC with a graphics card is much faster than a NAS.")
    + radio("ollama", "ollama.where", "host", "Already on this machine", "Outside Docker: LineageBox reaches it through Docker's own address.")
    + radio("ollama", "ollama.where", "docker", "Here, in Docker", "Next to LineageBox. Without a graphics card it works, slowly.")
    + `</div></fieldset>`;
  if (A.ollama.where === "remote") {
    html += `<div class="inset">${field("ollama.url", "Its address", withCheck("ollama", "ollama.url", "192.168.1.10"),
      "Such as 192.168.1.10 or http://192.168.1.10:11434. Ollama there must answer other machines: OLLAMA_HOST=0.0.0.0.")}</div>`;
  }
  if (A.ollama.where === "docker") {
    html += `<div class="inset"><fieldset data-field="ollama.gpu"><legend class="subtle-legend">Graphics card</legend>`
      + `<div class="option-grid option-grid-3">`
      + radio("gpu", "ollama.gpu", "cpu", "Processor alone", "Pick a small model on Settings (gemma3:4b).")
      + radio("gpu", "ollama.gpu", "nvidia", "NVIDIA", "Needs its driver and NVIDIA Container Toolkit.")
      + radio("gpu", "ollama.gpu", "amd", "AMD (ROCm)", "x86-64 only.")
      + `</div></fieldset></div>`;
  }
  html += `</div>`;

  html += `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${IC.mic}</span><div><h3>Video transcripts (Whisper)</h3></div></div>`
    + `<span class="badge soft">Optional</span></div>`
    + `<p class="card-sub">What is said in videos and recordings becomes searchable, and shows beside them.</p>`
    + `<fieldset data-field="whisper.where"><legend class="subtle-legend">Where does Whisper run?</legend>`
    + `<div class="option-grid option-grid-3">`
    + radio("whisper", "whisper.where", "none", "Not now", "Skip video transcripts for now.")
    + radio("whisper", "whisper.where", "remote", "On another computer", "LineageBox's speech-to-text service on a PC with an NVIDIA card.")
    + radio("whisper", "whisper.where", "docker", "Here, in Docker", "On this machine's processor: slower on a small NAS, but it gets there.")
    + `</div></fieldset>`;
  if (A.whisper.where !== "none") {
    html += `<div class="inset">`;
    if (A.whisper.where === "remote") {
      html += field("whisper.url", "Its address", withCheck("whisper", "whisper.url", "192.168.1.10"),
        "Such as 192.168.1.10 or http://192.168.1.10:9300.");
    }
    if (A.whisper.where === "docker") {
      const select = `<select id="f-whisper-model" data-a="whisper.model">${models}</select>`;
      html += field("whisper.model", "Model", select);
    }
    html += field("whisper.language", "The language spoken", `<select id="f-whisper-language" data-a="whisper.language">`
      + LANGUAGES.map(([k, v]) => option(k, v, A.whisper.language)).join("") + `</select>`)
      + `</div>`;
  }
  html += `</div>`;

  html += `<div class="card">`
    + field("about", `Whose photos are these? <span class="muted">(optional)</span>`,
      input("about", { placeholder: "a family in Oregon, USA" }), "Helps the AI read the places in folder names.")
    + `</div>`;
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
  let html = stepHead("Last details",
    "Time zone, web port, updates, and settings most people can leave as they are.");

  html += `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${IC.clock}</span><h3>Time &amp; web address</h3></div></div>`
    + field("tz", "Time zone", input("tz", { list: "zones" }) + `<datalist id="zones">${zones}</datalist>`,
      "For the dates shown and the describing hours.")
    + `<div class="row">${field("port", "Port", input("port", { type: "number", min: 1 }),
      `The web page's port: http://${h(A.host_name || "this-machine")}:port`)}</div></div>`;

  html += `<div class="card"><div class="card-head"><div class="card-title-row">`
    + `<span class="card-icon">${IC.refresh}</span><h3>Updates</h3></div></div>`
    + check("updater", "Let admins install new versions from the Status page", "Adds a small updater service: the "
      + "one part with Docker's socket. It has no network and only acts when an admin presses Update now.")
    + `</div>`;

  const unusual = A.host_network || A.version || A.bind || touched.has("install_dir") || touched.has("data")
    || !A.install_dir;
  html += `<details class="good-to-know"${unusual ? " open" : ""}><summary>More settings`
    + `</summary><div class="inside">`
    + folderFields()
    + `<p class="subtle-legend">Docker</p>`
    + `<div class="row">`
    + field("version", `Stay on one version <span class="muted">(optional)</span>`, input("version",
      { placeholder: "the newest", cls: "short" }), "Such as 1.10.3. Left empty, Update now installs each new release.")
    + field("bind", `Answer on one address only <span class="muted">(optional)</span>`, input("bind",
      { placeholder: "every address", cls: "short" }), "One of this machine's IPv4 addresses, such as 192.168.1.5.")
    + `</div>`
    + check("host_network", "Use this machine's own network (host networking)", "For routers, and machines whose "
      + "Docker can't reach the DNS server.");
  if (A.host_network) {
    html += `<div class="inset">` + field("host_address", "The address to answer on", input("host_address",
      { placeholder: "192.168.1.1", cls: "short" }), "This machine's address on the home network.") + `</div>`;
  }
  if (A.updater) {
    html += `<div class="inset">` + field("socket", "Docker's socket", input("socket",
      { placeholder: "/var/run/docker.sock" }), "For rootless Docker: /run/user/&lt;your id&gt;/docker.sock") + `</div>`;
  }
  return html + `</div></details>`;
};

function summaryChips() {
  const chips = [];
  const s = sys();
  if (s) chips.push(["Machine", s.name + (A.model ? ` (${A.model})` : "")]);
  chips.push(["User", A.root ? "root" : `${A.puid}:${A.pgid}`]);
  chips.push(["Sources", `${A.sources.length} photo source${A.sources.length === 1 ? "" : "s"}`]);
  if (A.data) chips.push(["Catalog", A.data]);
  const ai = [];
  if (A.ollama.where !== "none") ai.push(`Ollama (${A.ollama.where})`);
  if (A.whisper.where !== "none") ai.push(`Whisper (${A.whisper.where})`);
  chips.push(["AI", ai.length ? ai.join(", ") : "Off"]);
  if (A.port) chips.push(["Port", String(A.port)]);
  return chips.map(([k, v]) => `<span class="summary-chip"><span>${h(k)}:</span> ${h(v)}</span>`).join("");
}

DRAW.done = () => {
  const hasSecret = A.sources.some((s) => s.choice === "smb" && secrets[s.id]);
  let html = `<div class="done-hero"><div class="done-hero-top"><div>`
    + `<p class="eyebrow">Step ${STEPS.length} of ${STEPS.length}</p>`
    + `<h1>Your compose file</h1>`
    + `<p class="intro">One self-contained file that runs LineageBox as you configured. Paste it into `
    + `${h((sys() || {}).docker_app || "your Docker app")}, or save it as <code>compose.yaml</code> in the folder for LineageBox.</p>`
    + `</div><div class="actions">`
    + `<button type="button" class="primary" data-act="download">${IC.download}Download compose.yaml</button>`
    + `<button type="button" class="pill" data-act="copy">${IC.copy}Copy it</button>`
    + (INFO.can_save ? `<button type="button" class="pill" data-act="save">${IC.save}Save on this machine</button>` : "")
    + `</div></div>`
    + `<div class="done-summary">${summaryChips()}</div></div>`;

  html += `<div id="done-problems" aria-live="polite"></div>`
    + `<div id="save-result" aria-live="polite"></div>`;

  html += `<div class="done-grid">`
    + `<div><div class="done-col-title"><h2>What to do next</h2></div>`
    + `<ol class="next-steps" id="next-steps"></ol></div>`
    + `<div><div class="done-file-card"><div class="preview-head"><div class="preview-title">`
    + `${IC.file}<h2>compose.yaml</h2><span class="status-pill" id="done-status">Ready</span></div>`
    + `<div class="preview-tools">`
    + `<button type="button" class="code-btn" data-act="copy">Copy</button>`
    + `<button type="button" class="code-btn" data-act="download">Download</button>`
    + `</div></div>`
    + (hasSecret ? `<div class="done-file-note">Passwords show as •••••• here; the downloaded and copied file has them.</div>` : "")
    + `<div class="preview-code"><pre id="done-file"></pre></div></div></div>`
    + `</div>`;

  if (INFO.locked) {
    html += `<div class="card local-tip-card"><h3>Let it look for you</h3>${machineNotes()}</div>`;
  } else if (canStartHere()) {
    html += `<div class="card local-tip-card"><div class="card-head"><div class="card-title-row">`
      + `<span class="card-icon">${IC.nas}</span><h3>Want the wizard to inspect your machine directly?</h3></div></div>`
      + `<p class="card-sub">Run the setup on the machine itself to have it find your folders, who owns them and your users, and save the file in place. Over SSH there:</p>`
      + `${codeBlock(setupCommand())}<p class="hint">${h(LOOK_HINT)}</p></div>`;
  }
  return html;
};

function drawDone() {
  const box = $("#done-problems");
  if (!box || !R) return;
  const all = problems();
  box.innerHTML = all.length ? `<ul class="problems">` + all.map((p) => `<li class="problem ${p.level}"><span>${h(p.text)}</span> `
    + `<button type="button" class="link" data-act="go" data-step="${stepOf(p.field)}">Fix</button></li>`).join("")
    + `</ul>` : `<div class="note good">${IC.good}<div class="note-body"><p><b>Everything is in place.</b> Your compose file is valid and ready to download, copy, or save.</p></div></div>`;
  const blocked = errors().length > 0;
  document.querySelectorAll('[data-act="download"], [data-act="copy"], [data-act="save"]').forEach((b) => {
    b.disabled = blocked;
  });
  const st = $("#done-status");
  if (st) {
    const errs = errors().length;
    const warns = warnings().length;
    if (errs) { st.textContent = `${errs} to fix`; st.className = "status-pill is-err"; }
    else if (warns) { st.textContent = `${warns} note${warns === 1 ? "" : "s"}`; st.className = "status-pill is-warn"; }
    else { st.textContent = "Ready"; st.className = "status-pill is-ok"; }
  }
  $("#next-steps").innerHTML = (R.steps || []).map((s) => `<li><h3>${h(s.title)}</h3>`
    + s.lines.map((l) => `<p>${h(l)}</p>`).join("") + (s.code ? codeBlock(s.code) : "") + `</li>`).join("");
  $("#done-file").innerHTML = highlight(masked(R.compose));
}

// ---- what answers change besides themselves
function newSource(choice = "new") {   // the first: a new folder (on a NAS, a shared folder of its own)
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

function initDefaults() {
  const d = INFO.detected || {};
  Object.assign(A, blankAnswers());
  touched.clear();
  for (const k of Object.keys(secrets)) delete secrets[k];
  nextId = 1;
  step = 0;
  seen = 0;
  machineTab = "all";
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
  if (lockedInstall()) A.install_dir = d.install_dir;
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

// ---- theme & preview drawer
function initTheme() {
  try {
    const saved = localStorage.getItem(STORE + "-theme");
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch (e) { /* fine */ }
}
function toggleTheme() {
  const cur = document.documentElement.dataset.theme;
  const darkNow = cur ? cur === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const next = darkNow ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(STORE + "-theme", next); } catch (e) { /* fine */ }
}
function setDrawer(open) {
  document.body.classList.toggle("drawer-open", open);
  $("#drawer-backdrop").hidden = !open;
  syncPreviewButton();
}
function preview(show) {
  document.body.classList.toggle("no-preview", !show);
  try { localStorage.setItem(STORE + "-preview", show ? "1" : "0"); } catch (e) { /* fine */ }
  syncPreviewButton();
}
function syncPreviewButton() {
  const btn = $("#toggle-preview");
  if (!btn) return;
  const open = isNarrow()
    ? document.body.classList.contains("drawer-open")
    : !document.body.classList.contains("no-preview");
  btn.setAttribute("aria-expanded", String(open));
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
    crumbs += `<span class="crumb-sep">/</span><button type="button" class="quiet small" data-act="browse-to" data-path="${h(to)}">${h(part)}</button>`;
  });
  const s = sys() || {};
  const jumps = [s.share_parent, A.install_dir].filter((x, i, all) => x && x.startsWith("/") && all.indexOf(x) === i);
  let html = `<div class="crumbs">${crumbs}</div>`;
  if (jumps.length) {
    const links = jumps.map((j) => `<button type="button" class="link" data-act="browse-to" data-path="${h(j)}">${h(j)}</button>`);
    html += `<p class="hint">Quick jump: ${links.join(" · ")}</p>`;
  }
  if (data.error) html += `<p class="problem error">${h(data.error)}</p>`;
  if (data.exists) {
    html += `<div class="browser-meta">This folder belongs to <b>${h(ownerLabel(data))}</b>`
      + (data.free_gb !== undefined ? `, with <b>${data.free_gb} GB</b> free of ${data.total_gb} GB` : "") + `.`
      + (data.network ? ` <span class="warn">It is on a network share (${h(data.network)}).</span>` : "")
      + (data.writable === false && !A.root ? ` <span class="warn">User ${A.puid}:${A.pgid} can't write it.</span>` : "")
      + `</div>`;
  }
  html += `<ul class="folders">` + (data.folders || []).map((f) => `<li><button type="button" data-act="browse-to" `
    + `data-path="${h(f.path)}"><span class="folder-name">${IC.folder}<span>${h(f.name)}</span></span>`
    + `<span class="owner">${h(f.user || f.uid)}:${h(f.group || f.gid)}`
    + `${f.network ? ` · <span class="warn">share</span>` : ""}${f.writable === false && !A.root ? ` · <span class="warn">`
    + `can't write</span>` : ""}</span></button></li>`).join("")
    + ((data.folders || []).length ? "" : `<li class="empty">No folders in here.</li>`) + `</ul>`;
  html += `<div class="actions"><button type="button" class="primary" data-act="use-folder">Use ${h(data.path)}</button>`;
  if (data.exists && data.uid !== undefined && !(data.uid === A.puid && data.gid === A.pgid) && data.uid !== 0) {
    html += `<button type="button" class="pill" data-act="use-owner">Run LineageBox as its owner</button>`;
  }
  html += `</div>`;
  $("#browser-body").innerHTML = html;
}

// ---- actions
const ACT = {
  next: () => go(step + 1),
  back: () => go(step - 1),
  go: (el) => go(+el.dataset.step),
  tab: (el) => {
    machineTab = el.dataset.tab || "all";
    draw();
  },
  system: (el) => {
    applySystem(el.dataset.system);
    draw();
    schedule();
  },
  "add-source": () => {
    const src = newSource("folder");   // another place: most often photos that are already somewhere
    A.sources.push(src);
    sourceDefaults(src, A.sources.length - 1);
    draw();
    schedule();
    const boxes = document.querySelectorAll("#step .card");
    if (boxes.length) boxes[boxes.length - 1].scrollIntoView({ block: "nearest", behavior: "smooth" });
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
    const outEl = $(`[data-check="${kind}"]`);
    outEl.textContent = "Asking…";
    try {
      const { data } = await api("api/check", { kind, url: A[kind].url });
      outEl.innerHTML = `<span class="${data.ok ? "ok" : "warn"}">${h(data.text)}</span>`;
    } catch (e) {
      outEl.textContent = e.message;
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
  "hide-preview": () => (isNarrow() ? setDrawer(false) : preview(false)),
  "toggle-preview": () => {
    if (isNarrow()) setDrawer(!document.body.classList.contains("drawer-open"));
    else preview(document.body.classList.contains("no-preview"));
  },
  "close-drawer": () => setDrawer(false),
  theme: () => toggleTheme(),
  reset: () => {
    if (!window.confirm("Start over from the beginning? Your answers on this page will be cleared.")) return;
    try { sessionStorage.removeItem(STORE); } catch (e) { /* fine */ }
    initDefaults();
    draw(true);
    schedule(true);
    notice("Started over.");
  },
};

function go(to) {
  if (to < 0 || to > DONE) return;
  step = to;
  seen = Math.max(seen, step);
  draw(true);
  remember();
  if (step === DONE) schedule(true);
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
    box.innerHTML = `<div class="note good">${IC.good}<div class="note-body"><p><b>Saved ${h(data.saved)}.</b>`
      + (data.kept ? ` The one that was there is kept as ${h(data.kept)}.` : "") + `</p>`
      + (made.length ? `<p>Made, for LineageBox's user: ${made.map(h).join(", ")}.</p>` : "")
      + (data.todo.length ? `<p>Still to make, as the steps below say: ${data.todo.map(h).join(", ")}.</p>` : "")
      + ((data.not_given || []).length ? `<p>This disk wouldn't give ${data.not_given.map(h).join(", ")} to user `
        + `${h(A.puid)}:${h(A.pgid)}. That is fine on Docker Desktop; elsewhere, give it to them as the steps below `
        + `say.</p>` : "")
      + (INFO.first ? `<p>Now start it in the wizard's place, as the steps below say.</p>`
        : `<p>You can stop the setup now: Ctrl+C where it runs.</p>`) + `</div></div>`;
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
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("drawer-open")) {
    setDrawer(false);
  }
});
window.addEventListener("resize", () => {
  if (!isNarrow() && document.body.classList.contains("drawer-open")) setDrawer(false);
  else syncPreviewButton();
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
    A.owner_manual = el.dataset.user === "manual";   // the ids stay as they were, to change in the boxes it shows
    if (!A.owner_manual) [A.puid, A.pgid] = el.dataset.user.split(":").map(Number);
    touched.add("puid");
    touched.add("pgid");
    draw();
    schedule();
    return;
  }
  if (!el.dataset.a || (el.type === "radio" && !el.checked)) return;
  let v = el.type === "checkbox" ? el.checked : el.value;
  if (el.dataset.type === "int") v = el.value === "" ? "" : Number(el.value);
  const renamed = el.dataset.a.match(/^sources\.(\d+)\.name$/);
  if (renamed) {   // a network path typed for a source is kept by its name: it follows the new one
    const old = get(el.dataset.a);
    if (old && old !== v && A.network_paths[old] !== undefined) {
      A.network_paths[v] = A.network_paths[old];
      delete A.network_paths[old];
    }
    const box = document.getElementById(id(`sources.${renamed[1]}.network_path`));
    if (box) box.dataset.np = v;
  }
  set(el.dataset.a, v);
  touched.add(el.dataset.a);
  const redraw = changed(el.dataset.a, v) || el.type === "radio" || el.type === "checkbox"
    || el.dataset.a === "model" || el.dataset.a === "whisper.where";
  if (redraw) draw();
  schedule();
});

async function start() {
  initTheme();
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
    $("#step").innerHTML = `<h1>The setup can't start</h1><div class="note warn">${IC.warn}<div class="note-body"><p>${h(e.message)}</p></div></div>`;
    return;
  }
  const d = INFO.detected || {};
  const whereText = hosted() ? "Nothing you type here is kept"
    : d.host_name ? `On ${d.host_name}` : "On this machine";
  $("#where").innerHTML = `<span class="where-dot${hosted() ? " hosted" : ""}"></span><span>${h(whereText)}</span>`;
  if (!recall()) {
    initDefaults();
  } else if (lockedInstall()) {
    A.install_dir = d.install_dir;
  }
  let show = true;
  try { show = localStorage.getItem(STORE + "-preview") !== "0"; } catch (e) { /* fine */ }
  preview(show);
  draw();
  schedule(true);
}
start();
