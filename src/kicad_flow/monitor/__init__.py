"""A live view of what the MCP server is doing, and of what it has drawn.

Runs as a SEPARATE process from the (stdio) MCP server and needs no coupling to
it: it tails the activity JSONL the server writes (see
:mod:`kicad_flow.server.activity`) for a live feed of tool calls, and follows the
newest schematic/board path in that feed to re-render a viewable image whenever
the file changes. One self-contained page streams both over Server-Sent Events --
no build step, no extra web dependency (stdlib ``http.server``).

    python -m kicad_flow.monitor            # http://localhost:8472
    python -m kicad_flow.monitor --port 9000 --open

Two things only: the VIEW and the LOG. Pan/zoom with auto-fit, 2D and selectable
board cameras (rendered by ``kicad-cli pcb render``), the activity feed beside
it with a filter and a Clear button, and a placeholder image when there is
nothing to show yet (never a broken image). KiCad's own editor cannot show our
writes live; this is the live view instead.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import secrets
import socket
import tempfile
import threading
import time
import webbrowser
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kicad_flow.backend.kicad import render
from kicad_flow.backend.kicad.preview import schematic_snapshot
from kicad_flow.server.activity import activity_log_path
from kicad_flow.server.scene import SceneHistory

from .presentation import document_id, project_documents, public_record

DEFAULT_PORT = 8472
_RENDER_DIR = Path(tempfile.gettempdir()) / "kicad-flow-monitor"

# 1x1 transparent PNG -- last-resort placeholder if PyMuPDF is unavailable.
_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
_PLACEHOLDER_CACHE: dict[str, bytes] = {}


def _placeholder_png(message: str = "waiting for a design…") -> bytes:
    """A valid PNG carrying *message* -- shown instead of a broken <img>."""
    if message in _PLACEHOLDER_CACHE:
        return _PLACEHOLDER_CACHE[message]
    data = _TRANSPARENT_PNG
    try:
        import pymupdf as fitz  # PyMuPDF (already a dependency for PNG rendering)

        doc = fitz.open()
        page = doc.new_page(width=900, height=520)
        page.draw_rect(
            page.rect, color=(0.82, 0.82, 0.80), fill=(0.96, 0.955, 0.94), width=1
        )
        page.insert_textbox(
            fitz.Rect(40, 232, 860, 300),
            message,
            fontsize=22,
            align=1,
            color=(0.45, 0.45, 0.45),
        )
        data = page.get_pixmap(dpi=96).tobytes("png")
    except Exception:
        # The transparent PNG is the fallback when optional PDF rendering fails.
        pass
    _PLACEHOLDER_CACHE[message] = data
    return data


@contextlib.contextmanager
def _board_snapshot(board: Path) -> Iterator[Path]:
    """Keep KiCad CLI handles off the live board during Windows atomic saves.

    A hidden sibling keeps relative 3D model paths rooted in the project folder.
    Project settings and manufacturing colors travel with the snapshot.
    """
    snapshot = board.with_name(f".preview-{secrets.token_hex(16)}.kicad_pcb")
    copied: list[Path] = []
    try:
        for suffix in (".kicad_pcb", ".kicad_pro", ".kicad-flow.json"):
            source = board.with_suffix(suffix)
            if source == board or source.is_file():
                target = snapshot.with_suffix(suffix)
                copied.append(target)
                target.write_bytes(source.read_bytes())
        yield snapshot
    finally:
        for target in copied:
            with contextlib.suppress(OSError):
                target.unlink()


def _render_board_2d(board: Path, dpi: int = 300) -> Path | None:
    """Render a board's 2D layers cropped to the board (no title-block frame).

    ``pcb export pdf`` always plots on the drawing sheet, so a small board is a
    speck on an A4 page. ``pcb export svg`` with ``--page-size-mode 2`` +
    ``--exclude-drawing-sheet`` crops the SVG canvas to the board area; we
    rasterize that with PyMuPDF.
    """
    _RENDER_DIR.mkdir(parents=True, exist_ok=True)
    out = _RENDER_DIR / (board.stem + "-2d.png")
    try:
        with _board_snapshot(board) as snapshot:
            render.render_board_layout(snapshot, out, side="top", dpi=dpi,
                                       copper=True, silkscreen=True,
                                       courtyard=True)
    except Exception:
        return None
    return out if out.is_file() and out.stat().st_size > 0 else None


def _render(src: Path, dpi: int = 150) -> Path | None:
    """Render *src* (.kicad_sch or .kicad_pcb) to a 2D PNG; None on failure."""
    _RENDER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if src.suffix == ".kicad_sch":
            with schematic_snapshot(src) as snapshot:
                pngs = render.export_png(snapshot, output_dir=_RENDER_DIR, dpi=dpi)
            return pngs[0] if pngs else None
        if src.suffix == ".kicad_pcb":
            return _render_board_2d(src)
    except Exception:
        return None
    return None


_3D_VIEWS: dict[str, dict[str, object]] = {
    "top-angle": {"side": "top", "rotate": "-30,0,25", "perspective": True},
    "bottom-angle": {"side": "top", "rotate": "150,0,205", "perspective": True},
    "top": {"side": "top"},
    "bottom": {"side": "bottom"},
    "front": {"side": "front"},
    "right": {"side": "right"},
}
_3d_cache: dict[str, tuple[int, bytes]] = {}
_3d_render_locks: dict[str, threading.Lock] = {}
_3d_lock = threading.Lock()


def _render_3d(board: Path, view: str) -> bytes | None:
    """Render one named 3D camera to an isolated file; return complete PNG bytes."""
    _RENDER_DIR.mkdir(parents=True, exist_ok=True)
    profile = _3D_VIEWS[view]
    # The request's view and the board name select what to render, but neither
    # belongs in a filesystem path. A random name also isolates parallel jobs.
    out = _RENDER_DIR / f".render-{secrets.token_hex(16)}.png"
    try:
        with _board_snapshot(board) as snapshot:
            render.render_board(
                snapshot, out, width=1600, height=1100,
                side=str(profile["side"]), rotate=str(profile.get("rotate", "")),
                perspective=bool(profile.get("perspective", False)),
                quality="basic", background="opaque", zoom=0.65,
            )
        return out.read_bytes() if out.stat().st_size > 0 else None
    except Exception:
        return None
    finally:
        with contextlib.suppress(OSError):
            out.unlink()


def _cached_3d(board: Path, view: str) -> bytes | None:
    """Return one complete frame, rendering each board/view revision once."""
    if view not in _3D_VIEWS:
        return None
    key = f"{board.resolve()}\0{view}"
    try:
        revision = board.stat().st_mtime_ns
    except OSError:
        return None
    with _3d_lock:
        hit = _3d_cache.get(key)
        if hit and hit[0] == revision:
            return hit[1]
        render_lock = _3d_render_locks.setdefault(key, threading.Lock())
    # Browsers can request the same refresh twice (render + active SSE events).
    # Only one KiCad process draws it; followers reuse that complete frame.
    with render_lock:
        with _3d_lock:
            hit = _3d_cache.get(key)
            if hit and hit[0] == revision:
                return hit[1]
        png = _render_3d(board, view)
        if png is not None:
            with _3d_lock:
                _3d_cache[key] = (revision, png)
        return png




_document_cache: dict[str, tuple[str, bytes]] = {}
_document_lock = threading.Lock()


def _document_frame(source: Path, side: str) -> bytes | None:
    """Cache isolated 2D frames by full document identity and sibling revisions."""
    if side not in ("top", "bottom"):
        return None
    key = f"{source.resolve()}:{side}"
    # A root schematic also changes when a child sheet is saved.
    try:
        revision = repr([(k, p.stat().st_mtime_ns)
                         for k, p in project_documents(source).items()])
    except OSError:
        return None
    with _document_lock:
        cached = _document_cache.get(key)
        if cached and cached[0] == revision:
            return cached[1]
        try:
            with tempfile.TemporaryDirectory() as name:
                root = Path(name)
                if source.suffix == ".kicad_pcb":
                    output = root / "board.png"
                    with _board_snapshot(source) as snapshot:
                        render.render_board_layout(snapshot, output, side=side, dpi=200,
                                                   copper=True, silkscreen=True,
                                                   courtyard=False)
                else:
                    with schematic_snapshot(source) as snapshot:
                        output = render.export_png(
                            snapshot, output_dir=root, dpi=150
                        )[0]
                data = output.read_bytes()
            if len(_document_cache) >= 32:
                _document_cache.clear()
            _document_cache[key] = (revision, data)
            return data
        except (OSError, RuntimeError, ValueError, IndexError):
            return None


class _State:
    """Shared state: the active design, its render, and the log read cursor."""

    _CAP = 500      # keep the most recent activity records
    _TAIL = 512_000  # bytes of history to open with

    def __init__(self, log: Path) -> None:
        self.log = log
        # Start near the END of the log, not at 0. It is append-only, so
        # starting at 0 meant parsing every line ever written before the first
        # frame appeared -- measured 1.9 s against a 61.7 MB / 134k-line log,
        # to keep the last 500 records and discard the rest. A window of the
        # last _TAIL bytes holds comfortably more than _CAP records at the
        # ~460 bytes a record runs to, so the feed still opens with history.
        # Seeking mid-file can land inside a record; that line fails to parse
        # and `poll` skips it.
        size = log.stat().st_size if log.is_file() else 0
        self.offset = max(0, size - self._TAIL)

        self.active: Path | None = None  # design being rendered
        self.src_mtime = 0.0
        self.png: Path | None = None
        self.png_ver = 0  # bumps every re-render (cache-buster for the <img>)
        self.events: list[dict[str, object]] = []  # recent tool calls
        self.clear_version = 0
        self.seq = 0  # total events ever seen (a stable cursor for SSE readers)
        self.lock = threading.Lock()
        self.scene_lock = threading.Lock()
        self.scene_history = SceneHistory()
        self.documents: dict[str, Path] = {}
        self.document_revision = ""

    def poll(self) -> None:
        """Ingest new activity lines and re-render if the active design changed.

        Run by a single background thread so the render + feed stay fresh
        regardless of how many browsers are connected.
        """
        with self.lock:
            if self.log.is_file():
                with self.log.open("r", encoding="utf-8") as f:
                    f.seek(self.offset)
                    lines = f.readlines()
                    self.offset = f.tell()
                for line in lines:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.events.append(rec)
                    self.events[:] = self.events[-self._CAP :]
                    self.seq += 1
                    p = str(rec.get("path", ""))
                    if p and Path(p).suffix in (".kicad_sch", ".kicad_pcb"):
                        self.active = Path(p)
            active, revision = self.active, self.clear_version
        documents = project_documents(active)
        signature = repr([(key, p.stat().st_mtime_ns) for key, p in documents.items()])
        with self.lock:
            if revision != self.clear_version:
                return
            self.documents = documents
            if signature != self.document_revision:
                self.document_revision = signature
                self.png_ver += 1
        if active and active.is_file():
            m = active.stat().st_mtime
            if m != self.src_mtime:
                png = _render(active)
                with self.lock:
                    if revision != self.clear_version:
                        return
                    self.src_mtime = m
                    if png is not None:
                        self.png = png
                        self.png_ver += 1

    def clear(self) -> None:
        """Reset the visible activity feed and rendered preview, retaining logs.

        The preview goes too, because the feed is what *chooses* it: the active
        design is whatever the log last named. Clearing only the feed left the
        image of a design with no events behind it, and nothing could dismiss it
        -- a finished project's sheet stayed on screen until some other design
        was written.

        ``seq`` remains monotonic. Advancing the read cursor preserves the
        append-only history; the reset event clears every connected browser.
        In-flight renders cannot publish across a clear generation.
        """
        with self.lock:
            # Keep the append-only activity/replay history. Clear the view by
            # advancing its cursor, without racing a tool's log append.
            self.offset = self.log.stat().st_size if self.log.is_file() else 0
            self.clear_version += 1
            self.events.clear()
            self.documents.clear()
            self.document_revision = ""
            self.active = None
            self.src_mtime = 0.0
            self.png = None
            self.png_ver += 1

    def run(self) -> None:
        """Poll forever (the monitor's background worker)."""
        while True:
            with contextlib.suppress(Exception):  # a bad line must not kill it
                self.poll()
            time.sleep(0.6)


_STATIC = files("kicad_flow.monitor") / "static"
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


def _static_bytes(name: str) -> bytes:
    """Read a file from the monitor's static package data (index/css/js)."""
    return (_STATIC / name).read_bytes()


class _Handler(BaseHTTPRequestHandler):
    state: _State  # set on the server instance

    def log_message(self, *_a: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._serve_static("index.html")
        elif path in ("/style.css", "/app.js", "/scene.js", "/preview.js"):
            self._serve_static(path.lstrip("/"))
        elif path == "/render.png":
            self._send_png()
        elif path == "/documents":
            with self.state.lock:
                documents = dict(self.state.documents)
                active = self.state.active
            self._send(200, "application/json", json.dumps({
                "active": document_id(active) if active else "",
                "documents": [{"id": key, "name": p.name,
                               "kind": "board" if p.suffix == ".kicad_pcb"
                               else "schematic"} for key, p in documents.items()],
            }).encode())
        elif path == "/scene.json":
            self._send_scene()
        elif path == "/events":
            self._stream()
        else:
            self._send(404, "text/plain", b"not found")


    def _serve_static(self, name: str) -> None:
        try:
            body = _static_bytes(name)
        except (FileNotFoundError, OSError):
            self._send(404, "text/plain", b"not found")
            return
        ext = "." + name.rsplit(".", 1)[-1]
        self._send(200, _STATIC_TYPES.get(ext, "application/octet-stream"), body)

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/clear":
            self.state.clear()
            self._send(200, "application/json", b'{"ok":true}')
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            self.wfile.write(body)

    def _send_png(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        mode = qs.get("mode", ["2d"])[0].lower()
        with self.state.lock:
            selected = self.state.documents.get(qs.get("doc", [""])[0])
        if "doc" in qs:
            if selected is None:
                self._send(404, "text/plain", b"Unknown project document")
                return
            if mode == "3d" and selected.suffix == ".kicad_pcb":
                data = _cached_3d(selected, qs.get("view", ["top-angle"])[0])
            else:
                data = _document_frame(selected, qs.get("side", ["top"])[0])
            if data is None:
                self._send(503, "text/plain", b"Preview unavailable")
            else:
                self._send(200, "image/png", data)
            return
        if mode == "3d":
            view = qs.get("view", ["top-angle"])[0].lower()
            with self.state.lock:
                active = self.state.active
            if (
                active is not None
                and active.suffix == ".kicad_pcb"
                and active.is_file()
            ):
                frame = _cached_3d(active, view)
                data = (
                    frame if frame else _placeholder_png(
                        "Unknown 3D view or render failed -- is KiCad 10 installed?"
                    )
                )
            else:
                data = _placeholder_png("3D view needs a board (.kicad_pcb)")
            self._send(200, "image/png", data)
            return
        with self.state.lock:
            png = self.state.png
        data = png.read_bytes() if png and png.is_file() else _placeholder_png()
        self._send(200, "image/png", data)

    def _send_scene(self) -> None:
        """Serve the same geometry contract used by MCP, from the saved file."""
        from kicad_flow.backend import load

        qs = parse_qs(urlparse(self.path).query)
        with self.state.lock:
            active = (self.state.documents.get(qs["doc"][0]) if "doc" in qs
                      else self.state.active)
        if active is None or active.suffix != ".kicad_sch":
            self._send(200, "application/json", json.dumps({
                "ok": False, "error": "Geometry view needs an active schematic."
            }).encode())
            return
        try:
            with self.state.scene_lock:
                scene = load(active).scene(max_objects=10000)
                result = self.state.scene_history.observe(
                    str(active.resolve()), scene, qs.get("since", [""])[0])
        except (OSError, ValueError, LookupError) as exc:
            result = {"ok": False, "error": str(exc)}
        # Preserve schematic labels/IDs containing slashes; only metadata is private.
        for key in ("path", "error"):
            if key in result:
                result[key] = public_record(result[key])
        self._send(200, "application/json", json.dumps(result).encode())

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        st = self.state
        # Replay everything still buffered so a page opened after a run shows
        # that run, not a blank feed -- the browser groups and filters it. The
        # buffer is already capped (_CAP), so this is bounded.
        with st.lock:
            cursor = max(0, st.seq - len(st.events))
        last_ver, last_active = -1, ""
        last_clear = st.clear_version
        try:
            while True:
                with st.lock:
                    seq, events = st.seq, list(st.events)
                    ver = st.png_ver
                    clear_version = st.clear_version
                    active = str(st.active) if st.active else ""
                if clear_version != last_clear:
                    last_clear = clear_version
                    self._emit("reset", str(clear_version))
                if cursor < seq:
                    backlog = len(events)
                    for rec in events[max(0, backlog - (seq - cursor)) :]:
                        self._emit("activity", json.dumps(public_record(rec)))
                    cursor = seq
                if ver != last_ver:
                    last_ver = ver
                    self._emit("render", str(ver))
                if active != last_active:
                    last_active = active
                    # An empty name is emitted too (a clear): the browser needs
                    # to be told the design went away, or it keeps the old title
                    # and image. Only the very first tick is skipped, where
                    # there genuinely is nothing to say yet.
                    kind = (
                        ("board" if active.endswith(".kicad_pcb") else "schematic")
                        if active
                        else ""
                    )
                    name = Path(active).name if active else ""
                    self._emit("active", json.dumps({"name": name, "kind": kind}))
                self._emit_comment()  # keep-alive ping
                time.sleep(0.8)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _emit(self, event: str, data: str) -> None:
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    def _emit_comment(self) -> None:
        self.wfile.write(b": ping\n\n")
        self.wfile.flush()


def _port_in_use(port: int) -> bool:
    """True if something is already listening on 127.0.0.1:*port*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _make_httpd(port: int, log_path: Path | None) -> ThreadingHTTPServer:
    state = _State(log_path or activity_log_path())
    threading.Thread(target=state.run, daemon=True).start()  # background poller
    handler = type("H", (_Handler,), {"state": state})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


class _StartupState:
    """Keep startup idempotent when multiple callers enter concurrently."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.lock = threading.Lock()


_startup = _StartupState()


def ensure_running(
    port: int = DEFAULT_PORT,
    log_path: Path | None = None,
    *,
    open_browser: bool = False,
) -> str:
    """Start the monitor in a background thread if it isn't already up.

    Idempotent and non-blocking: if this process already started it, or another
    process holds the port, it reuses that one. Returns the monitor URL. Meant
    to be called from ``server.main`` and from examples so the live view comes up
    automatically for any KiCad interaction.
    """
    url = f"http://localhost:{port}"
    with _startup.lock:
        if _startup.url is not None:
            return _startup.url
        if not _port_in_use(port):
            try:
                httpd = _make_httpd(port, log_path)
            except OSError:
                pass  # lost the race to bind; another process is serving it
            else:
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _startup.url = url
    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    return url


def serve(
    port: int = DEFAULT_PORT, log_path: Path | None = None, open_browser: bool = False
) -> None:
    """Run the monitor server (blocking) until interrupted."""
    httpd = _make_httpd(port, log_path)
    url = f"http://localhost:{port}"
    print(f"kicad-flow monitor: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def main() -> None:
    """CLI entry point for ``python -m kicad_flow.monitor``."""
    ap = argparse.ArgumentParser(description="kicad-flow live monitor")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--activity",
        type=Path,
        default=None,
        help="activity JSONL to tail (default: the server's)",
    )
    ap.add_argument("--open", action="store_true", help="open a browser")
    args = ap.parse_args()
    serve(port=args.port, log_path=args.activity, open_browser=args.open)
