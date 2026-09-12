"""Bounded, scope-specific deltas for board region observations."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any


class RegionHistory:
    """Remember a bounded set of immutable region observations across clients."""

    def __init__(self) -> None:
        """Limit cached observations to 64 scopes and eight megabytes."""
        self._entries: OrderedDict[str, tuple[str, dict[str, Any], int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def observe(self, scope: str, data: dict[str, Any], since: str,
                max_objects: int, max_bytes: int) -> dict[str, Any]:
        """Return complete collections, or changed items and removed IDs."""
        groups = ("footprints", "pads", "tracks", "vias", "zones", "graphics")
        current: dict[str, Any] = {}
        for group in groups:
            items = data[group]
            current[group] = {}
            occurrences: dict[str, int] = {}
            for item in items:
                fallback = item.get("ref", "")
                if group == "pads":
                    base = f"{fallback}/pad/{item['number']}"
                    ordinal = occurrences.get(base, 0)
                    occurrences[base] = ordinal + 1
                    fallback = f"{base}/{ordinal}"
                identity = item.get("uuid") or (
                    fallback if group in {"footprints", "pads"} else "")
                if not identity:
                    raise ValueError(f"{group} object has no stable ID")
                if identity in current[group]:
                    raise ValueError(f"duplicate {group} ID: {identity}")
                current[group][identity] = {**item, "id": identity}
        count = sum(len(items) for items in current.values())
        if count > max_objects:
            raise ValueError(f"region has {count} objects; max_objects={max_objects}. "
                             "Use a smaller rectangle or fewer layers.")
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
        revision = hashlib.sha256((scope + encoded).encode()).hexdigest()
        size = len(json.dumps(current, ensure_ascii=False).encode())
        with self._lock:
            cached = self._entries.get(since)
            previous = cached[1] if cached and cached[0] == scope else None
            out = {key: value for key, value in data.items() if key not in groups}
            out.update(ok=True, revision=revision,
                       base_revision=since if previous else None,
                       mode="delta" if previous else "full",
                       reset=bool(since and previous is None), object_count=count)
            out["removed"] = {}
            for group in groups:
                old = previous[group] if previous else {}
                out[group] = [item for key, item in current[group].items()
                              if old.get(key) != item]
                out["removed"][group] = sorted(old.keys() - current[group].keys())
            if len(json.dumps(out, ensure_ascii=False).encode()) > max_bytes:
                raise ValueError(f"region exceeds max_bytes={max_bytes}. "
                                 "Use a smaller rectangle or omit zone fills.")
            removed = self._entries.pop(revision, None)
            if removed:
                self._bytes -= removed[2]
            if size <= 8 * 1024 * 1024:
                self._entries[revision] = scope, current, size
                self._bytes += size
            while len(self._entries) > 64 or self._bytes > 8 * 1024 * 1024:
                self._bytes -= self._entries.popitem(last=False)[1][2]
        return out


board_history = RegionHistory()
