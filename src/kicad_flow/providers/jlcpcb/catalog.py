"""Read the locally downloaded JLCPCB/LCSC SQLite catalogue."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..api import PartsProvider
from ..types import PriceBreak, ProviderPart, ProviderStatus

_DEFAULT_DATABASE = Path(__file__).with_name("data") / "jlcpcb-components.sqlite3"


def database_path() -> Path:
    """Resolve the local database path, with an override for tests/deployments."""
    configured = os.environ.get("KICAD_FLOW_JLCPCB_DATABASE")
    return Path(configured).expanduser() if configured else _DEFAULT_DATABASE


def _prices(raw: object) -> tuple[PriceBreak, ...]:
    """Normalize the catalogue's JSON price breaks, tolerating stale rows."""
    if not isinstance(raw, str) or not raw:
        return ()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = None
    out: list[PriceBreak] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            quantity = item.get("qFrom", item.get("quantity", item.get("qty")))
            price = item.get("price", item.get("unitPrice"))
            if not isinstance(quantity, (str, int, float)) or not isinstance(
                price, (str, int, float)
            ):
                continue
            try:
                out.append(PriceBreak(int(quantity), float(price)))
            except (TypeError, ValueError):
                continue
    if out:
        return tuple(out)
    # Current jlcparts snapshots use ``1-99:0.0737,100-:0.0587``.
    for tier in raw.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:-\d*)?\s*:\s*([0-9.]+)\s*", tier)
        if match is not None:
            out.append(PriceBreak(int(match.group(1)), float(match.group(2))))
    return tuple(out)


def _text(row: sqlite3.Row, key: str) -> str:
    """Read a nullable SQLite value as text."""
    try:
        value = row[key]
    except IndexError:
        value = ""
    return "" if value is None else str(value)


def _integer(row: sqlite3.Row, key: str) -> int:
    """Read a nullable SQLite value as an integer."""
    try:
        value = row[key]
    except IndexError:
        value = 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _assembly_type(row: sqlite3.Row) -> str:
    """Normalize JLC's base/expand and CDFER's preferred flags."""
    if _integer(row, "basic") > 0 or _text(row, "library_type") == "base":
        return "basic"
    if _integer(row, "preferred") > 0:
        return "preferred"
    return "extended"


def _fts_query(query: str) -> str:
    """Turn plain caller text into a safe AND-of-prefixes FTS5 expression."""
    words = re.findall(r"[A-Za-z0-9]+", query)
    if len(words) == 1 and re.fullmatch(r"[Cc]\d+", words[0]):
        words[0] = words[0][1:]
    if not words:
        raise ValueError("query must contain at least one letter or digit")
    return " AND ".join(f'"{word}"*' for word in words)


class JlcpcbPartsProvider(PartsProvider):
    """Search the yaqwsx/CDFER JLCPCB catalogue with read-only SQLite."""

    @property
    def name(self) -> str:
        """Return the registry name."""
        return "jlcpcb"

    def _connect(self) -> sqlite3.Connection:
        path = database_path().resolve()
        if not path.is_file():
            raise FileNotFoundError(
                f"JLCPCB database is not installed at {path}. See "
                "kicad_flow/providers/jlcpcb/README.md"
            )
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def status(self) -> ProviderStatus:
        """Inspect the local SQLite file and its required table."""
        path = database_path().resolve()
        if not path.is_file():
            return ProviderStatus("jlcpcb", False, str(path))
        stat = path.stat()
        modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()
        try:
            with self._connect() as connection:
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM jlc_components"
                    ).fetchone()[0]
                )
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            return ProviderStatus(
                "jlcpcb", False, str(path), stat.st_size, modified,
                error=f"{type(exc).__name__}: {exc}",
            )
        return ProviderStatus(
            "jlcpcb", True, str(path), stat.st_size, modified, count
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        package: str = "",
        manufacturer: str = "",
        assembly_type: str = "",
        min_stock: int = 0,
    ) -> list[ProviderPart]:
        """Search identifiers and descriptions, then apply exact filters."""
        needle = query.strip()
        if not needle:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if min_stock < 0:
            raise ValueError("min_stock must be non-negative")
        wanted_type = assembly_type.strip().lower()
        if wanted_type not in ("", "basic", "preferred", "extended"):
            raise ValueError(
                "assembly_type must be basic, preferred, extended, or empty"
            )

        with self._connect() as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(jlc_components)")
            }
            has_fts = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'jlc_components_fts'"
            ).fetchone() is not None
            if has_fts:
                # CDFER's published file has sometimes carried the external-
                # content FTS tables without rebuilding their index. Selecting
                # rows still reports the content-table count in that state,
                # while every MATCH returns zero; the shadow index is the fact.
                has_fts = connection.execute(
                    "SELECT 1 FROM jlc_components_fts_idx LIMIT 1"
                ).fetchone() is not None
            lcsc_match = re.fullmatch(r"[Cc](\d+)", needle)
            if lcsc_match is not None:
                source = "jlc_components AS component"
                clauses = ["component.lcsc = ?", "component.stock >= ?"]
                values: list[object] = [int(lcsc_match.group(1)), min_stock]
            elif has_fts:
                source = (
                    "jlc_components AS component JOIN jlc_components_fts "
                    "ON jlc_components_fts.rowid = component.rowid"
                )
                clauses = ["jlc_components_fts MATCH ?", "component.stock >= ?"]
                values = [_fts_query(needle), min_stock]
            else:
                source = "jlc_components AS component"
                clauses = ["component.stock >= ?"]
                values = [min_stock]
                words = re.findall(r"[A-Za-z0-9]+", needle)
                if not words:
                    raise ValueError(
                        "query must contain at least one letter or digit"
                    )
                for word in words:
                    clauses.append(
                        "(instr(lower(CAST(component.lcsc AS TEXT)), lower(?)) > 0 "
                        "OR instr(lower(component.mfr), lower(?)) > 0 "
                        "OR instr(lower(component.description), lower(?)) > 0)"
                    )
                    values.extend((word, word, word))
            if package:
                clauses.append(
                    "instr(lower(component.package), lower(?)) > 0"
                )
                values.append(package.strip())
            if manufacturer:
                clauses.append(
                    "instr(lower(component.manufacturer), lower(?)) > 0"
                )
                values.append(manufacturer.strip())
            if wanted_type == "basic":
                clauses.append(
                    "(component.library_type = 'base' OR component.basic > 0)"
                    if "basic" in columns
                    else "component.library_type = 'base'"
                )
            elif wanted_type == "preferred":
                if "preferred" not in columns:
                    raise ValueError(
                        "this database snapshot has no preferred-parts flag"
                    )
                clauses.append("component.preferred > 0")
            elif wanted_type == "extended":
                parts = ["component.library_type != 'base'"]
                if "preferred" in columns:
                    parts.append("component.preferred = 0")
                clauses.append(" AND ".join(parts))
            order = (
                "CASE WHEN component.library_type = 'base' THEN 0 ELSE 1 END, "
                "component.stock DESC, component.lcsc"
            )
            sql = (
                f"SELECT component.* FROM {source} WHERE "
                + " AND ".join(f"({clause})" for clause in clauses)
                + f" ORDER BY {order} LIMIT ?"
            )
            values.append(limit)
            rows = connection.execute(sql, values).fetchall()

        return [
            ProviderPart(
                provider="jlcpcb",
                provider_part_number=f"C{_integer(row, 'lcsc')}",
                manufacturer_part_number=_text(row, "mfr"),
                manufacturer=_text(row, "manufacturer"),
                description=_text(row, "description"),
                package=_text(row, "package"),
                category=_text(row, "category"),
                subcategory=_text(row, "subcategory"),
                assembly_type=_assembly_type(row),
                stock=_integer(row, "stock"),
                datasheet_url=_text(row, "datasheet"),
                product_url=(
                    f"https://jlcpcb.com/partdetail/C{_integer(row, 'lcsc')}"
                ),
                prices=_prices(_text(row, "price")),
            )
            for row in rows
        ]


__all__ = ["JlcpcbPartsProvider", "database_path"]
