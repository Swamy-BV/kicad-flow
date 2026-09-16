"""Caller-selected track angle checks shared by preview and write tools."""

from __future__ import annotations

import math

from ...pcb.types import Finding, Track


def _angle_step(value: float | None) -> float | None:
    """Accept an explicit increment that tiles an undirected half-turn."""
    if value is None:
        return None
    if not math.isfinite(value) or value <= 0 or value > 90:
        raise ValueError("track_angle_step must be greater than 0 and at most 90")
    if not math.isclose(180.0 / value, round(180.0 / value), abs_tol=1e-9):
        raise ValueError("track_angle_step must divide 180 degrees evenly")
    return value


def _off_angle(track: Track, step: float) -> float | None:
    """Return the measured undirected angle when it misses the caller's grid."""
    dx = track.end.x - track.start.x
    dy = track.end.y - track.start.y
    angle = math.degrees(math.atan2(dy, dx)) % 180.0
    nearest = round(angle / step) * step
    # A saved KiCad board rounds copper coordinates to micrometres. Allow a
    # tenth of a degree so a short authored 45-degree run survives readback.
    return angle if abs(angle - nearest) > 0.1 else None


def angle_findings(
    tracks: list[Track] | tuple[Track, ...],
    step: float,
    *,
    candidates: bool = False,
) -> list[Finding]:
    """Report only geometry that violates an explicitly selected angle rule."""
    findings = []
    for index, track in enumerate(tracks):
        angle = _off_angle(track, step)
        if angle is None:
            continue
        findings.append(
            Finding(
                severity="error",
                kind="track_angle",
                message=(
                    f"Track angle {angle:.3f} degrees is not a multiple of "
                    f"{step:g} degrees"
                ),
                layer=track.layer,
                at=track.start,
                uuid=track.uuid,
                input_kind="tracks" if candidates else "",
                input_index=index if candidates else None,
            )
        )
    return findings
