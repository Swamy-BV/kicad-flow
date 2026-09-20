// Independent live-follow and manual-snapshot workspaces. No CAD decisions.
import { sceneView } from './scene.js';

export function previewWorkspace() {
  const $ = id => document.getElementById(id);
  const view = $("view"), stage = $("stage"), img = $("img");
  const geometry = sceneView($("scene"), $("scene-info"));
  const remember = (key, value) => { try { localStorage.setItem(key, value); } catch { /* private mode */ } };
  const recalled = (key, value) => { try { return localStorage.getItem(key) || value; } catch { return value; } };
  const savedZoom = recalled("kf-zoom-preset", "1");
  const initialZoom = ["1", "0.5", "0.75", "1.5", "2"].includes(savedZoom) ? savedZoom : "1";
  const make = kind => ({ kind, id: "", name: "", docs: [], mode: "2d", side: "top",
    camera: "top-angle", zoom: initialZoom, scale: 1, tx: 0, ty: 0,
    nat: { w: 0, h: 0 }, url: "", scene: null, captured: "", stale: false, error: "" });
  const states = { live: make(""), schematic: make("schematic"), board: make("board") };
  let workspace = "live", paused = false, epoch = 0, generation = 0;
  let liveBusy = false, liveQueued = false, spinner = null;
  let logs = recalled("kf-log-visible", "true") === "true";
  const state = () => states[workspace];
  const stopSpinner = () => { clearTimeout(spinner); $("spin").classList.remove("on"); };
  const busy = () => { stopSpinner(); spinner = setTimeout(() => $("spin").classList.add("on"), 250); };
  const stamp = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  function controls() {
    const s = state(), live = workspace === "live";
    document.body.dataset.workspace = workspace;
    $("side").hidden = !live || !logs;
    document.body.classList.toggle("activity-hidden", !live || !logs);
    for (const [id, key] of [["tab-live", "live"], ["tab-sch", "schematic"], ["tab-pcb", "board"]]) {
      $(id).classList.toggle("active", workspace === key);
      if (workspace === key) $(id).setAttribute("aria-current", "page");
      else $(id).removeAttribute("aria-current");
    }
    $("view-badge").textContent = live ? (paused ? "LIVE PREVIEW PAUSED" : "LIVE PREVIEW") : "MANUAL PREVIEW";
    $("view-title").textContent = s.name || (live ? "Following current work" :
      workspace === "board" ? "PCB preview" : "Schematic preview");
    $("pause-live").hidden = !live;
    $("pause-live").textContent = paused ? "Resume" : "Pause";
    $("toggle-log").hidden = !live;
    $("toggle-log").textContent = logs ? "Hide logs" : "Show logs";
    $("toggle-log").setAttribute("aria-expanded", String(logs && live));
    $("refresh-preview").hidden = live;
    $("clear").hidden = !live;
    $("document-row").hidden = live;
    const options = s.docs.filter(d => d.kind === s.kind);
    $("document").replaceChildren(...options.map(d => {
      const option = document.createElement("option");
      option.value = d.id; option.textContent = d.name; return option;
    }));
    $("document").value = s.id;
    $("document").disabled = !options.length;
    for (const [id, mode] of [["b2d", "2d"], ["b3d", "3d"], ["bscene", "scene"]]) {
      $(id).classList.toggle("active", s.mode === mode);
      $(id).disabled = !s.id;
    }
    $("b3d").hidden = s.kind !== "board";
    $("bscene").hidden = s.kind !== "schematic";
    $("side2d-ctl").hidden = s.kind !== "board" || s.mode !== "2d";
    $("view3d-ctl").hidden = s.mode !== "3d";
    $("side2d").value = s.side;
    $("view3d").value = s.camera;
    $("zoom-preset").value = s.zoom;
    status();
  }
  function status() {
    const s = state();
    $("capture-status").textContent = s.error || (s.captured
      ? `${workspace === "live" ? (paused ? "Paused at" : "Live · updated") : "Snapshot captured"} ${s.captured}${s.stale && workspace !== "live" ? " · Project changed — refresh when ready" : ""}`
      : s.id ? "Preparing preview…" : "Waiting for a design");
    $("capture-status").classList.toggle("changed", s.stale && workspace !== "live");
  }
  function apply() {
    const s = state();
    stage.style.transform = `translate(${s.tx}px,${s.ty}px) scale(${s.scale})`;
    const fitScale = Math.min(view.clientWidth / s.nat.w, view.clientHeight / s.nat.h) * 0.9;
    $("zoom").textContent = s.nat.w ? Math.round(s.scale / fitScale * 100) + "% fit" : "";
  }
  function fit() {
    const s = state();
    if (!s.nat.w) return;
    s.scale = Math.min(view.clientWidth / s.nat.w, view.clientHeight / s.nat.h) * 0.9 * (Number(s.zoom) || 1);
    s.tx = (view.clientWidth - s.nat.w * s.scale) / 2;
    s.ty = (view.clientHeight - s.nat.h * s.scale) / 2;
    apply();
  }
  function paint() {
    const s = state(), hasFrame = Boolean(s.mode === "scene" ? s.scene : s.url);
    stage.hidden = !hasFrame;
    $("preview-empty").hidden = hasFrame;
    $("empty-title").textContent = s.error ? "Preview unavailable" : s.id ? "Preparing preview" : "No design to preview yet";
    $("empty-description").textContent = s.error ? "Check the capture status below, then refresh or select a document." : s.id ? "Rendering the selected document…" : "Open a schematic or PCB through MCP to start.";
    img.hidden = s.mode === "scene";
    $("scene").toggleAttribute("hidden", s.mode !== "scene");
    $("scene-info").hidden = s.mode !== "scene" || !hasFrame;
    if (s.mode === "scene" && s.scene) { geometry.clear(); s.nat = geometry.update(s.scene); }
    if (s.url && img.src !== s.url) img.src = s.url;
    img.alt = `${s.name} · ${s.mode === "3d" ? s.camera : s.side} preview`;
    controls();
    if (s.zoom !== "custom") fit(); else apply();
  }
  function discard(s) {
    if (s.url) URL.revokeObjectURL(s.url);
    s.url = ""; s.scene = null; s.nat = { w: 0, h: 0 }; s.captured = ""; s.error = "";
  }
  async function catalogue() {
    const response = await fetch("/documents");
    if (!response.ok) throw new Error("Project list unavailable. Check the server connection.");
    return response.json();
  }
  function select(s, doc) {
    if (!doc || s.id !== doc.id) discard(s);
    s.id = doc?.id || ""; s.name = doc?.name || "";
    if (doc) s.kind = doc.kind;
    if ((s.mode === "3d" && s.kind !== "board") || (s.mode === "scene" && s.kind !== "schematic")) s.mode = "2d";
  }
  async function renderFrame() {
    const s = state(), request = ++epoch, atGeneration = generation;
    if (!s.id) { stopSpinner(); paint(); return; }
    s.error = ""; busy();
    try {
      const query = `doc=${encodeURIComponent(s.id)}&mode=${s.mode}&side=${s.side}&view=${s.camera}`;
      const response = await fetch(s.mode === "scene" ? `/scene.json?${query}` : `/render.png?${query}`);
      if (!response.ok) throw new Error("Preview unavailable. Refresh the document list and try again.");
      if (s.mode === "scene") {
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || "Geometry preview unavailable.");
        if (request !== epoch) return;
        s.scene = data;
      } else {
        const blob = await response.blob();
        if (request !== epoch) return;
        const url = URL.createObjectURL(blob), decoded = new Image();
        decoded.src = url;
        try { await decoded.decode(); } catch (error) { URL.revokeObjectURL(url); throw error; }
        if (request !== epoch) { URL.revokeObjectURL(url); return; }
        if (s.url) URL.revokeObjectURL(s.url);
        s.url = url; s.nat = { w: decoded.naturalWidth, h: decoded.naturalHeight };
      }
      s.captured = stamp(); s.stale = generation !== atGeneration;
      paint();
    } catch (error) {
      if (request === epoch) { s.error = error.message; paint(); }
    } finally { if (request === epoch) stopSpinner(); }
  }
  async function refreshManual() {
    const s = state(), request = ++epoch;
    if (workspace === "live") return;
    busy();
    try {
      const data = await catalogue();
      if (request !== epoch) return;
      const docs = data.documents.filter(d => d.kind === s.kind);
      const current = docs.find(d => d.id === s.id);
      if (s.id && !current) {
        s.docs = [...docs, { id: s.id, name: s.name, kind: s.kind }];
        s.error = "Selected document is outside the active project. Choose a current document.";
        controls(); stopSpinner(); return;
      }
      s.docs = docs;
      const stem = states.live.name.replace(/\.kicad_(sch|pcb)$/, "");
      select(s, current || docs.find(d => d.name.replace(/\.kicad_(sch|pcb)$/, "") === stem) || docs[0]);
      paint(); await renderFrame();
    } catch (error) { if (request === epoch) { s.error = error.message; paint(); } }
    finally { if (request === epoch) stopSpinner(); }
  }
  async function updateLive() {
    if (workspace !== "live" || paused) return;
    if (liveBusy) { liveQueued = true; return; }
    liveBusy = true;
    let request = epoch;
    try {
      do {
        liveQueued = false;
        request = ++epoch;
        const data = await catalogue();
        if (request !== epoch || workspace !== "live" || paused) break;
        const s = states.live;
        s.docs = data.documents;
        select(s, data.documents.find(d => d.id === data.active));
        paint(); await renderFrame();
      } while (liveQueued && workspace === "live" && !paused);
    } catch (error) {
      if (request === epoch && workspace === "live") { states.live.error = error.message; paint(); }
    } finally {
      liveBusy = false;
      if (liveQueued && workspace === "live" && !paused) { liveQueued = false; updateLive(); }
    }
  }
  function enter(next) {
    if (next === workspace) return;
    epoch++; stopSpinner(); geometry.clear();
    workspace = next; paint();
    if (next === "live") updateLive();
    else if (!state().captured) refreshManual();
  }
  $("tab-live").onclick = () => enter("live");
  $("tab-sch").onclick = () => enter("schematic");
  $("tab-pcb").onclick = () => enter("board");
  $("refresh-preview").onclick = refreshManual;
  $("document").onchange = () => { select(state(), state().docs.find(d => d.id === $("document").value)); paint(); renderFrame(); };
  $("pause-live").onclick = () => { paused = !paused; epoch++; stopSpinner(); controls(); if (!paused) updateLive(); };
  $("toggle-log").onclick = () => { logs = !logs; remember("kf-log-visible", String(logs)); controls(); };
  const changeView = () => { discard(state()); paint(); renderFrame(); };
  for (const [id, mode] of [["b2d", "2d"], ["b3d", "3d"], ["bscene", "scene"]]) {
    $(id).onclick = () => { if (state().mode !== mode) { state().mode = mode; changeView(); } };
  }
  $("side2d").onchange = () => { state().side = $("side2d").value; changeView(); };
  $("view3d").onchange = () => { state().camera = $("view3d").value; changeView(); };
  function resetFit() { state().zoom = "1"; $("zoom-preset").value = "1"; remember("kf-zoom-preset", "1"); fit(); }
  $("fit").onclick = resetFit;
  $("zoom-preset").onchange = () => { state().zoom = $("zoom-preset").value; remember("kf-zoom-preset", state().zoom); fit(); };
  function zoom(factor, x = view.clientWidth / 2, y = view.clientHeight / 2) {
    const s = state(), nx = (x - s.tx) / s.scale, ny = (y - s.ty) / s.scale;
    s.zoom = "custom"; $("zoom-preset").value = "custom";
    s.scale = Math.max(0.01, Math.min(40, s.scale * factor));
    s.tx = x - nx * s.scale; s.ty = y - ny * s.scale; apply();
  }
  $("zin").onclick = () => zoom(1.25);
  $("zout").onclick = () => zoom(0.8);
  view.addEventListener("wheel", e => {
    e.preventDefault(); const rect = view.getBoundingClientRect();
    zoom(Math.min(Math.max(Math.exp(-e.deltaY * 0.0012), 0.85), 1.18), e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });
  let drag = null;
  const endDrag = () => { drag = null; view.classList.remove("drag"); };
  view.addEventListener("pointerdown", e => { e.preventDefault(); drag = { x: e.clientX, y: e.clientY, tx: state().tx, ty: state().ty, captured: false }; view.classList.add("drag"); });
  view.addEventListener("pointermove", e => {
    if (!drag) return;
    if (!e.buttons) { endDrag(); return; }
    if (!drag.captured) {
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 3) return;
      view.setPointerCapture(e.pointerId); drag.captured = true;
    }
    state().tx = drag.tx + e.clientX - drag.x; state().ty = drag.ty + e.clientY - drag.y;
    state().zoom = "custom"; $("zoom-preset").value = "custom"; apply();
  });
  view.addEventListener("pointerup", endDrag);
  view.addEventListener("pointercancel", endDrag);
  view.addEventListener("dblclick", resetFit);
  new ResizeObserver(() => { if (state().zoom !== "custom") fit(); }).observe(view);
  controls(); paint(); updateLive();
  return { clearLive() {
    select(states.live, null);
    states.live.docs = [];
    if (workspace === "live") { epoch++; stopSpinner(); paint(); }
  }, changed() {
    generation++;
    for (const key of ["schematic", "board"]) if (states[key].captured) states[key].stale = true;
    status(); updateLive();
  } };
}
