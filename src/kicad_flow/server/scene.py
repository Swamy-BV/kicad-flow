"""Bounded revision history for image-free schematic observations."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, OrderedDict
from typing import Any

from kicad_flow.schematic.types import SceneObject, SceneSnapshot


class SceneHistory:
    """Keep bounded observations; unknown cursors safely receive a full reset."""

    def __init__(self, *, max_entries: int = 64,
                 max_bytes: int = 8 * 1024 * 1024) -> None:
        """Bound storage across all paths, regions and clients."""
        self._entries: OrderedDict[
            str, tuple[str, dict[str, Any], int]
        ] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = threading.Lock()

    def observe(self, path: str, scene: SceneSnapshot,
                since: str = "", *, detail: str = "full",
                max_bytes: int = 2000000) -> dict[str, Any]:
        """Return a full observation or the changes since a matching cursor."""
        if detail not in {"full", "compact", "conflicts"}:
            raise ValueError("detail must be full, compact or conflicts")
        region = scene.region.as_list() if scene.region else None
        scope = json.dumps([path, scene.sheet_id, region, detail])
        revision = hashlib.sha256(
            (scope + scene.revision).encode()).hexdigest()
        implicated = {identity for finding in scene.findings
                      for identity in finding.objects}
        objects = [o for o in scene.objects
                   if detail != "conflicts" or o.id in implicated]

        def describe(obj: SceneObject) -> dict[str, Any]:
            if detail == "full":
                return dict(obj.as_dict())
            value: dict[str, Any] = {
                "id": obj.id, "kind": obj.kind,
                "bounds": obj.bounds.as_list(),
                "at": [obj.at.x, obj.at.y], **dict(obj.properties)}
            if obj.parent_id:
                value["parent_id"] = obj.parent_id
            if obj.points:
                value["points"] = [[p.x, p.y] for p in obj.points]
            return value

        current = {
            "objects": {o.id: describe(o) for o in objects},
            "findings": {f.id: f.as_dict() for f in scene.findings},
        }
        size = len(json.dumps(current).encode())
        with self._lock:
            cached = self._entries.get(since)
            previous = cached[1] if cached and cached[0] == scope else None
        out: dict[str, Any] = {
            "ok": True, "path": path, "sheet_id": scene.sheet_id,
            "revision": revision, "mode": "delta" if previous else "full",
            "base_revision": since if previous else None,
            "reset": bool(since and previous is None),
            "units": "mm", "origin": "top_left", "y_direction": "down",
            "page_bounds": scene.page.as_list(), "region": region,
            "object_count": len(scene.objects),
            "returned_object_count": len(objects),
            "object_kinds": dict(Counter(o.kind for o in scene.objects)),
            "finding_kinds": dict(Counter(f.kind for f in scene.findings)),
            "detail": detail,
            "obstacles_complete": detail != "conflicts" and not scene.unsupported,
            "finding_count": len(scene.findings),
            "unsupported_kinds": list(scene.unsupported),
            "geometry_only": True, "text_bounds": "estimated",
            "connectivity_verified": False,
        }
        if since and previous is None:
            out["reset_reason"] = "unknown_expired_or_different_scope"
        for key, removal_key in (("objects", "removed"),
                                 ("findings", "removed_findings")):
            old = previous[key] if previous else {}
            new = current[key]
            out[key] = [value for identity, value in new.items()
                        if old.get(identity) != value]
            out[removal_key] = sorted(old.keys() - new.keys())
        if len(json.dumps(out, ensure_ascii=False).encode()) > max_bytes:
            raise ValueError(f"scene exceeds max_bytes={max_bytes}; use a smaller "
                             "region or detail=conflicts for repair inspection")
        with self._lock:
            removed = self._entries.pop(revision, None)
            if removed:
                self._bytes -= removed[2]
            if size <= self._max_bytes:
                self._entries[revision] = scope, current, size
                self._bytes += size
            while (len(self._entries) > self._max_entries
                   or self._bytes > self._max_bytes):
                self._bytes -= self._entries.popitem(last=False)[1][2]
        return out


history = SceneHistory()
