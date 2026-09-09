// kicad-flow live monitor -- native ES module, no build step, no dependencies.
// Renders the active design (2D or 3D) on a pan/zoom stage and streams the MCP
// tool-call feed over Server-Sent Events.

const $ = (id) => document.getElementById(id);
const view = $("view"), stage = $("stage"), img = $("img"), spin = $("spin"),
  feed = $("feed"), active = $("active"), b2d = $("b2d"), b3d = $("b3d"),
  zlab = $("zoom"), q = $("q"), onlyBad = $("only-bad"), count = $("count");

let scale = 1, tx = 0, ty = 0, mode = "2d", nat = { w: 0, h: 0 },
  fitNext = true, kind = "";

function apply() {
  stage.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`;
  zlab.textContent = Math.round(scale * 100) + "%";
}

function fit() {
  if (!nat.w) return;
  const vw = view.clientWidth, vh = view.clientHeight;
  scale = Math.min(vw / nat.w, vh / nat.h) * 0.97;
  tx = (vw - nat.w * scale) / 2;
  ty = (vh - nat.h * scale) / 2;
  apply();
}

function zoom(f, cx, cy) {
  cx = cx == null ? view.clientWidth / 2 : cx;
  cy = cy == null ? view.clientHeight / 2 : cy;
  const nx = (cx - tx) / scale, ny = (cy - ty) / scale;
  scale = Math.max(0.05, Math.min(40, scale * f));
  tx = cx - nx * scale;
  ty = cy - ny * scale;
  apply();
}

let spinTimer = null;
const showSpin = () => { spinTimer = setTimeout(() => spin.classList.add("on"), 250); };
const hideSpin = () => { clearTimeout(spinTimer); spin.classList.remove("on"); };

// Double-buffer: decode the new render off-screen, then swap it in one step so
// the visible image never blanks (no flash on every re-render). Only the slow 3D
// render shows a spinner.
function reload(refit) {
  if (refit) fitNext = true;
  if (mode === "3d") showSpin();
  const next = new Image();
  next.onload = () => {
    img.src = next.src;
    nat = { w: next.naturalWidth, h: next.naturalHeight };
    if (fitNext) { fit(); fitNext = false; }
    hideSpin();
  };
  next.onerror = hideSpin;
  next.src = `/render.png?mode=${mode}&v=` + Date.now();
}

// --- interaction ---------------------------------------------------------
view.addEventListener("wheel", (e) => {
  e.preventDefault();
  // Zoom proportional to the scroll amount so a gesture feels the same on a
  // mouse wheel and a high-res trackpad; clamp so one event can't jump far.
  const f = Math.min(Math.max(Math.exp(-e.deltaY * 0.0012), 0.85), 1.18);
  zoom(f, e.offsetX, e.offsetY);
}, { passive: false });

let drag = null;
const endDrag = () => { drag = null; view.classList.remove("drag"); };
view.addEventListener("pointerdown", (e) => {
  e.preventDefault();  // stop native image drag from swallowing pointerup
  drag = { x: e.clientX, y: e.clientY, tx, ty };
  view.classList.add("drag");
  view.setPointerCapture(e.pointerId);
});
view.addEventListener("pointermove", (e) => {
  if (!drag) return;
  if (e.buttons === 0) { endDrag(); return; }  // self-heal a missed pointerup
  tx = drag.tx + (e.clientX - drag.x);
  ty = drag.ty + (e.clientY - drag.y);
  apply();
});
view.addEventListener("pointerup", endDrag);
view.addEventListener("pointercancel", endDrag);
view.addEventListener("dblclick", fit);

$("fit").onclick = fit;
$("zin").onclick = () => zoom(1.25);
$("zout").onclick = () => zoom(0.8);

// 2D and 3D are two views of the same design and live together in the main
// toolbar; the side panel stays the agent's activity feed.
function setMode(m) {
  if (m === mode) return;
  mode = m;
  for (const [btn, name] of [[b2d, "2d"], [b3d, "3d"]]) {
    btn.classList.toggle("active", m === name);
  }
  reload(true);
}
b2d.onclick = () => setMode("2d");
b3d.onclick = () => { if (!b3d.disabled) setMode("3d"); };

// --- theme ---------------------------------------------------------------
const theme = $("theme");
const setTheme = (t) => {
  document.documentElement.dataset.theme = t;
  theme.textContent = t === "light" ? "☼" : "☽";
  try { localStorage.setItem("kf-theme", t); } catch { /* private mode */ }
};
let stored = null;
try { stored = localStorage.getItem("kf-theme"); } catch { /* private mode */ }
setTheme(stored || "dark");
theme.onclick = () =>
  setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");

// --- the feed ------------------------------------------------------------
// Records are kept as data and re-rendered, so filtering and run-grouping work
// on the whole history rather than on whatever happens to be in the DOM.
const CAP = 500;
let records = [];
// The RECORD showing its detail, held by identity: an index would drift the
// moment the buffer rolls over its cap and the wrong row would pop open.
let expanded = null;
let showBad = false;

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  // textContent, never innerHTML: tool arguments are arbitrary strings and a
  // value containing "&" or "<" would otherwise mangle the row (or worse).
  if (text != null) n.textContent = text;
  return n;
};

const clock = (t) =>
  new Date(t * 1000).toLocaleTimeString([], { hour12: false });

// Both `result` and `args` arrive as objects from the middleware; a hand-edited
// or older log may carry a bare string, so neither is assumed.
const pairsOf = (v) => (v && typeof v === "object") ? Object.entries(v) : [];
const digestPairs = (r) => pairsOf(r.result);

function haystack(r) {
  return [r.tool, r.error, r.project, JSON.stringify(r.args || {}),
    JSON.stringify(r.result || {}), JSON.stringify(r.argv || {})]
    .join(" ").toLowerCase();
}

function matches(r) {
  if (showBad && r.ok) return false;
  const needle = q.value.trim().toLowerCase();
  return !needle || haystack(r).includes(needle);
}

function summaryCell(r) {
  const cell = el("span", "sum");
  // What the call RETURNED if it returned anything scalar; otherwise what it
  // was called WITH, which is all a failed call has to show.
  const pairs = digestPairs(r);
  const shown = pairs.length ? pairs : pairsOf(r.args);
  if (shown.length) {
    shown.forEach(([k, v], i) => {
      if (i) cell.append(el("span", "k", " · "));
      cell.append(el("span", "k", k + " "), document.createTextNode(String(v)));
    });
  } else {
    cell.textContent = typeof r.args === "string" ? r.args : "";
  }
  return cell;
}

function detailFor(r) {
  const box = el("div", "detail");
  const dl = (title, obj) => {
    const entries = Object.entries(obj || {});
    if (!entries.length) return;
    box.append(el("div", "sec", title));
    const list = el("dl");
    for (const [k, v] of entries) {
      list.append(el("dt", null, k), el("dd", null, String(v)));
    }
    box.append(list);
  };
  dl("arguments", r.argv);
  dl("result", r.result);
  if (r.path || r.project) {
    dl("where", { ...(r.path && { path: r.path }), ...(r.project && { project: r.project }) });
  }
  if (!box.childNodes.length) box.append(el("div", "sec", "no detail recorded"));
  return box;
}

function rowFor(r) {
  const row = el("div", "row" + (r.ok ? "" : " bad"));
  row.append(
    el("span", "at", clock(r.t)),
    // Glyph AND colour AND the error text below: status never rides on hue
    // alone, which is what makes red/green safe for colour-blind readers.
    el("span", "mark " + (r.ok ? "ok" : "bad"), r.ok ? "✓" : "✗"),
    el("span", "tool", r.tool),
  );
  row.append(summaryCell(r));
  row.append(el("span", "ms" + (r.ms > 1000 ? " slow" : ""), Math.round(r.ms) + "ms"));
  if (!r.ok && r.error) row.append(el("span", "err", r.error));
  row.tabIndex = 0;
  row.onclick = () => { expanded = expanded === r ? null : r; render(); };
  row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); } };
  return row;
}

function render() {
  const keep = records.slice(-CAP);
  const shown = [];
  for (let i = keep.length - 1; i >= 0; i--) if (matches(keep[i])) shown.push(i);

  feed.replaceChildren();
  const bad = keep.filter((r) => !r.ok).length;
  count.textContent = shown.length === keep.length
    ? `${keep.length} call${keep.length === 1 ? "" : "s"}${bad ? ` · ${bad} failed` : ""}`
    : `${shown.length} / ${keep.length}`;

  if (!shown.length) {
    feed.append(el("div", "empty",
      keep.length ? "No calls match this filter." : "Waiting for MCP tool calls…"));
    return;
  }

  // Newest first, grouped by run -- one build session per heading.
  let run = null;
  for (const i of shown) {
    const r = keep[i];
    if (r.run !== run) {
      run = r.run;
      const all = keep.filter((x) => x.run === run);
      const fails = all.filter((x) => !x.ok).length;
      const head = el("div", "run-head");
      head.append(
        el("span", null, "run " + (run || "—")),
        el("span", "grow"),
        el("span", null,
          `${all.length} call${all.length === 1 ? "" : "s"}${fails ? ` · ${fails} failed` : ""}`),
      );
      feed.append(head);
    }
    feed.append(rowFor(r));
    if (expanded === r) feed.append(detailFor(r));
  }
}

let queued = false;
function scheduleRender() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => { queued = false; render(); });
}

q.oninput = () => { expanded = null; scheduleRender(); };
onlyBad.onclick = () => {
  showBad = !showBad;
  onlyBad.classList.toggle("active", showBad);
  expanded = null;
  scheduleRender();
};

$("clear").onclick = () =>
  fetch("/clear", { method: "POST" }).then(() => {
    // The server also drops the active design, so the "active" SSE event
    // resets the title and swaps the image back to the placeholder. Clear
    // the feed here rather than waiting a poll for it to come back empty.
    records = [];
    expanded = null;
    active.textContent = "";
    render();
  });


// --- live stream ---------------------------------------------------------
const es = new EventSource("/events");
es.addEventListener("render", () => reload(false));
es.addEventListener("active", (e) => {
  const a = JSON.parse(e.data);
  active.textContent = a.name;
  kind = a.kind;
  b3d.disabled = kind !== "board";
  if (kind !== "board" && mode === "3d") setMode("2d");
  else reload(true);
});
es.addEventListener("activity", (e) => {
  const rec = JSON.parse(e.data);
  records.push(rec);
  if (records.length > CAP) records = records.slice(-CAP);
  scheduleRender();
});

render();
reload(true);
