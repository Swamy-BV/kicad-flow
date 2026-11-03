"""Native rule writes verified against KiCad's persisted rule document."""

from __future__ import annotations

import hashlib
from typing import Any

from kicad_flow.pcb.types import BoardRule, Constraint

from . import project
from .ipc import Document


def _snapshot(document: Document) -> tuple[dict[str, str], str | None]:
    """Read scopes and an exact revision without writing the rule document."""
    path = document.path.with_suffix(".kicad_dru")
    before = path.read_bytes() if path.is_file() else None
    rules = project.rules(document.path)
    after = path.read_bytes() if path.is_file() else None
    if before != after:
        raise RuntimeError("custom rules changed while reading; inspect and retry")
    scopes = {r.name: r.layer for r in rules}
    if len(scopes) != len(rules):
        raise ValueError("duplicate custom rule names cannot be updated by name")
    return scopes, hashlib.sha256(before).hexdigest() if before is not None else None


def _rules(values: list[dict[str, Any]], scopes: dict[str, str]) -> list[BoardRule]:
    """Retain native numeric facts and the scope the native writer saved."""
    if {v["name"] for v in values} != scopes.keys():
        raise RuntimeError(
            "native custom rule readback differs from its saved document"
        )
    return [
        BoardRule(
            v["name"],
            v["condition"],
            tuple(
                Constraint(c["kind"], c.get("min"), c.get("opt"), c.get("max"))
                for c in v["constraints"]
            ),
            scopes[v["name"]],
        )
        for v in values
    ]


def read(document: Document) -> list[BoardRule]:
    """Complete native readback with layer conditions absent from its reply."""
    with document.lock:
        document.assert_current()
        scopes, revision = _snapshot(document)
        result = document.rpc.call("settings", operation="rules", values=None)
        if _snapshot(document)[1] != revision:
            raise RuntimeError("custom rules changed during native readback; retry")
        return _rules(result["values"], scopes)


def write(document: Document, rules: tuple[BoardRule, ...]) -> list[BoardRule]:
    """Upsert through IPC, preserve untouched scopes and verify native output."""
    if not rules:
        return []
    with document.lock:
        document.assert_current()
        scopes, revision = _snapshot(document)
        if project.has_external_rule_comments(document.path):
            raise ValueError(
                "Native IPC cannot preserve comments outside rule blocks; "
                "no custom rules were changed"
            )
        expected = {**scopes, **{r.name: r.layer for r in rules}}
        result = document.rpc.call(
            "settings",
            operation="rules",
            values=[r.as_dict() for r in rules],
            scopes=scopes,
            revision=revision,
        )
        try:
            saved, _ = _snapshot(document)
            if saved != expected:
                raise RuntimeError("KiCad did not preserve the requested rule layers")
            found = _rules(result["values"], saved)
            document.rpc.call("settings", operation="rules_accept")
        except Exception as exc:
            document.rpc.call("settings", operation="rules_rollback")
            if _snapshot(document)[0] != scopes:
                raise RuntimeError(
                    "native custom rule rollback did not restore layers"
                ) from exc
            raise
        by_name = {r.name: r for r in found}
        return [by_name[r.name] for r in rules]
