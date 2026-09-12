"""PCB MCP tools for validation."""

from __future__ import annotations

from typing import Any

from ...pcb.types import (
    Point,
    Track,
    Via,
)
from .. import _meta
from .._app import mcp
from .copper import (
    _provider_via_refusal,
    _zone_value,
)
from .models import (
    NewTrack,
    NewVia,
    NewZone,
)
from .session import (
    _ERRORS,
    _board,
    _fail,
)


@mcp.tool(tags=_meta.PCB_INSPECT, annotations=_meta.READ)
def check_board(
    path: str,
    tracks: list[NewTrack] | None = None,
    vias: list[NewVia] | None = None,
    zones: list[NewZone] | None = None,
) -> dict[str, Any]:
    """Every design-rule violation, named by part and pad.

    Runs DRC and maps each violation from a position back to the pad that
    sits there, so a finding reads ``R1.2 clearance to U1.7`` rather than
    ``something at (25.46, 10.45)``. It also reports factual routing defects
    KiCad DRC misses: dangling endpoints, zero-length and duplicate tracks.
    Copper findings carry UUIDs for exact repair. Corner angle remains caller
    policy and is not treated as an electrical violation.

    Optional tracks, vias and zones are checked on an isolated copy and never
    applied. Candidate findings identify the exact input list and index when
    KiCad names either candidate object in the violation.

    ``ok`` means DRC ran; ``clean`` is true only when the checked state returned
    no findings, and ``kind_counts`` summarizes them without deciding what
    should change.
    """
    try:
        board = _board(path)
        proposed = tracks is not None or vias is not None or zones is not None
        if proposed:
            refusal = _provider_via_refusal(path, vias or [])
            if refusal is not None:
                return {**refusal, "proposed": True}
        current = board.check()
        if proposed:
            track_items = tuple(
                Track(
                    Point(item.x1, item.y1),
                    Point(item.x2, item.y2),
                    item.layer,
                    item.width,
                    item.net,
                )
                for item in tracks or []
            )
            via_items = tuple(
                Via(
                    at=Point(item.x, item.y),
                    diameter=item.diameter,
                    drill=item.drill,
                    net=item.net,
                    layers=item.layers,
                    kind=item.kind,
                )
                for item in vias or []
            )
            zone_items = tuple(_zone_value(board, item) for item in zones or [])
            for index, track_item in enumerate(track_items):
                if track_item.layer not in board.layers:
                    raise ValueError(
                        f"tracks[{index}].layer {track_item.layer!r} is not one of "
                        f"{list(board.layers)}"
                    )
            found = board.check_proposed(track_items, via_items, zone_items)
        else:
            found = current
        from .._fabrication import profile_findings, read_profile

        profile = read_profile(path)
        if profile is not None:
            found.extend(profile_findings(board, profile))
            if proposed:
                current.extend(profile_findings(board, profile))
    except _ERRORS as exc:
        return _fail(exc)
    errors = sum(1 for finding in found if finding.severity == "error")
    warnings = sum(1 for finding in found if finding.severity == "warning")
    kind_counts: dict[str, int] = {}
    for finding in found:
        kind_counts[finding.kind] = kind_counts.get(finding.kind, 0) + 1
    reply: dict[str, Any] = {
        "ok": True,
        "clean": not found,
        "errors": errors,
        "warnings": warnings,
        "kind_counts": dict(sorted(kind_counts.items())),
        "findings": [finding.as_dict() for finding in found],
    }
    if proposed:

        def fingerprint(item: Any) -> tuple[tuple[str, str], ...]:
            return tuple(
                sorted((name, str(value)) for name, value in item.as_dict().items())
            )

        remaining: dict[tuple[tuple[str, str], ...], int] = {}
        for finding in current:
            key = fingerprint(finding)
            remaining[key] = remaining.get(key, 0) + 1
        new_findings = []
        for finding in found:
            key = fingerprint(finding)
            if remaining.get(key, 0):
                remaining[key] -= 1
            else:
                new_findings.append(finding)

        candidate_remaining: dict[tuple[tuple[str, str], ...], int] = {}
        for finding in found:
            key = fingerprint(finding)
            candidate_remaining[key] = candidate_remaining.get(key, 0) + 1
        resolved_findings = []
        for finding in current:
            key = fingerprint(finding)
            if candidate_remaining.get(key, 0):
                candidate_remaining[key] -= 1
            else:
                resolved_findings.append(finding)
        reply.update(
            {
                "proposed": True,
                "current_error_count": sum(
                    finding.severity == "error" for finding in current
                ),
                "current_finding_count": len(current),
                "candidate_error_count": errors,
                "candidate_finding_count": len(found),
                "new_error_count": sum(
                    finding.severity == "error" for finding in new_findings
                ),
                "new_findings": [finding.as_dict() for finding in new_findings],
                "resolved_findings": [
                    finding.as_dict() for finding in resolved_findings
                ],
            }
        )
    return reply
