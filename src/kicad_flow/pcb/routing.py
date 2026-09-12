"""Explicit terminals for inspecting an authored route, without choosing one."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteTerminal:
    """One caller-selected pad and copper layer."""

    ref: str
    pad: str
    layer: str


@dataclass(frozen=True)
class RoutePath:
    """A named net and the two terminals whose path should be inspected."""

    net: str
    start: RouteTerminal
    end: RouteTerminal
