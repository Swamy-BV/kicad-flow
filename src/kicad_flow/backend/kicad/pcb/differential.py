"""Parallel-span observations for two explicitly selected centerline paths."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

from kicad_flow.pcb.routing import RoutePath

from ._state import BoardState
from .routing_path import resolve


def _spans(
    path: dict[str, Any], other: dict[str, Any], gap_min: float, gap_max: float
) -> dict[str, Any]:
    totals = {"within_gap": 0.0, "outside_gap": 0.0, "unpaired": 0.0, "ambiguous": 0.0}
    spans: list[dict[str, Any]] = []
    for segment in path["segments"]:
        ax, ay = segment["start"]
        bx, by = segment["end"]
        length = segment["length"]
        ux, uy = (bx - ax) / length, (by - ay) / length
        candidates = []
        boundaries = {0.0, length}
        for partner in other["segments"]:
            if partner["layer"] != segment["layer"]:
                continue
            cx, cy = partner["start"]
            dx, dy = partner["end"]
            if abs(ux * (dy - cy) - uy * (dx - cx)) > 1e-6 * partner["length"]:
                continue
            p, q = (cx - ax) * ux + (cy - ay) * uy, (dx - ax) * ux + (dy - ay) * uy
            low, high = max(0.0, min(p, q)), min(length, max(p, q))
            if high - low <= 1e-6:
                continue
            gap = abs((cx - ax) * uy - (cy - ay) * ux)
            gap -= (segment["width"] + partner["width"]) / 2
            candidates.append((low, high, gap, partner["uuid"]))
            boundaries.update((low, high))
        ordered = sorted(boundaries)
        for low, high in pairwise(ordered):
            if high - low <= 1e-6:
                continue
            mid = (low + high) / 2
            active = [(gap, uid) for p, q, gap, uid in candidates if p < mid < q]
            state = "ambiguous" if len(active) > 1 else "unpaired"
            if len(active) == 1:
                state = (
                    "within_gap"
                    if gap_min - 1e-6 <= active[0][0] <= gap_max + 1e-6
                    else "outside_gap"
                )
            totals[state] += high - low
            # Good spans need only totals; faults retain exact objects/locations.
            if state != "within_gap":
                spans.append(
                    {
                        "kind": state,
                        "uuid": segment["uuid"],
                        "layer": segment["layer"],
                        "start": [round(ax + ux * low, 6), round(ay + uy * low, 6)],
                        "end": [round(ax + ux * high, 6), round(ay + uy * high, 6)],
                        "length": round(high - low, 6),
                        "gap": round(active[0][0], 6) if len(active) == 1 else None,
                        "partner_ids": sorted({uid for _, uid in active}),
                    }
                )
                if len(spans) > 1000:
                    raise ValueError(
                        "pair has more than 1000 fault spans; inspect a simpler path"
                    )
    return {
        "net": path["net"],
        "lengths": {k: round(v, 6) for k, v in totals.items()},
        "spans": spans,
    }


def inspect_pair(
    board: BoardState,
    first: RoutePath,
    second: RoutePath,
    *,
    gap_min: float,
    gap_max: float,
    max_uncoupled: float | None = None,
    max_skew: float | None = None,
) -> dict[str, object]:
    """Measure supported paths and flag ambiguous parallel-span associations.

    Every parallel overlapping segment on the other path is a candidate. If
    several qualify, no nearest partner is chosen. These are geometric checks,
    not electromagnetic coupling or an impedance/delay calculation.
    """
    if not first.net or first.net == second.net:
        raise ValueError("a differential pair requires two distinct named nets")
    values = [gap_min, gap_max, max_uncoupled, max_skew]
    if any(v is not None and (not math.isfinite(v) or v < 0) for v in values):
        raise ValueError("pair limits must be finite and non-negative")
    if gap_min > gap_max:
        raise ValueError("gap_min must not exceed gap_max")
    a, b = resolve(board, first), resolve(board, second)
    result: dict[str, Any] = {
        "first": a,
        "second": b,
        "units": "mm",
        "path_skew": None,
        "within_requested_limits": None,
        "spacing": [],
        "violations": [],
        "limits": {
            "gap_min": gap_min,
            "gap_max": gap_max,
            "max_uncoupled": max_uncoupled,
            "max_skew": max_skew,
        },
        "scope": ("straight centerline geometry; pad contacts, via spacing, "
                  "planes and delay unverified"),
        "electrical_verified": False,
    }
    if any(p["status"] != "resolved_centerline" for p in (a, b)):
        return result
    if a["path_length"] is not None and b["path_length"] is not None:
        result["path_skew"] = round(abs(a["path_length"] - b["path_length"]), 6)
    spacing = [_spans(a, b, gap_min, gap_max), _spans(b, a, gap_min, gap_max)]
    result["spacing"] = spacing
    violations: list[dict[str, Any]] = result["violations"]
    for side in spacing:
        lengths = side["lengths"]
        if lengths["outside_gap"] > 1e-6:
            violations.append({"kind": "gap_out_of_range", "net": side["net"]})
        uncoupled = lengths["outside_gap"] + lengths["unpaired"]
        if max_uncoupled is not None and uncoupled > max_uncoupled + 1e-6:
            violations.append(
                {
                    "kind": "uncoupled_limit",
                    "net": side["net"],
                    "measured_minimum": round(uncoupled, 6),
                }
            )
    skew = result["path_skew"]
    if max_skew is not None and skew is not None and skew > max_skew + 1e-6:
        violations.append({"kind": "path_skew_limit", "measured": skew})
    incomplete = any(side["lengths"]["ambiguous"] > 1e-6 for side in spacing)
    incomplete |= bool(a["transitions"] or b["transitions"])
    incomplete |= max_skew is not None and skew is None
    result["within_requested_limits"] = (
        False if violations else (None if incomplete else True)
    )
    return result
