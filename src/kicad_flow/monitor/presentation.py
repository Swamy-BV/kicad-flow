"""Browser-safe activity presentation and opaque project document identities."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

_PATH = re.compile(
    r"(?<![\w:])(?:[A-Za-z]:[\\/]|\\\\|/|\.\.?[\\/])[^\n\r\"'<>|,;]*"
    r"|(?<![\w:/])(?:[\w.-]+[\\/]+)+[\w. -]+"
)
_URL = re.compile(r"https?://[^\s\"'<>]+")


def private_text(text: str) -> str:
    """Hide local path-like strings, retaining public HTTP source links."""
    urls: list[str] = []

    def keep(match: re.Match[str]) -> str:
        urls.append(match.group())
        return f"URLPLACEHOLDER{len(urls)-1}END"

    masked = _PATH.sub("[local path]", _URL.sub(keep, text))
    for i, url in enumerate(urls):
        masked = masked.replace(f"URLPLACEHOLDER{i}END", url)
    return masked


def public_record(value: Any) -> Any:
    """Recursively redact browser-bound text, including nested errors and JSON."""
    if isinstance(value, dict):
        return {private_text(str(k)): public_record(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public_record(v) for v in value]
    return private_text(value) if isinstance(value, str) else value


def document_id(path: Path) -> str:
    """Expose an opaque selector; browser requests never name local paths."""
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]


def project_documents(active: Path | None) -> dict[str, Path]:
    """List existing sibling design files of the current project, without guessing."""
    if active is None or not active.parent.is_dir():
        return {}
    found = [p for p in active.parent.iterdir()
             if p.suffix in (".kicad_sch", ".kicad_pcb") and p.is_file()
             and not p.name.startswith(".")]
    return {document_id(p): p for p in sorted(found)[:256]}
