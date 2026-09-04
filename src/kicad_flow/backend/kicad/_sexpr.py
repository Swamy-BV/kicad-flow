"""A small S-expression parser/serializer for KiCad files.

PRIVATE to the KiCad backend. Nothing outside ``backend/kicad`` should read
or write a KiCad file directly -- see BUGS.md ARCH-1 for the four that still
do. KiCad's ``.kicad_sch`` (and ``.kicad_sym``, ``.kicad_pcb``) files are
S-expressions. To place components or wires we must read an existing file into
a tree, modify it, and write it back in a form KiCad accepts. This module is
that read/modify/write layer.

The tree uses three node kinds:

* :class:`Node` -- a parenthesized list, e.g. ``(at 1 2 90)``.
* :class:`Sym`  -- a bareword/number token (unquoted), e.g. ``at``, ``yes``, ``90``.
* :class:`str`  -- a quoted string, e.g. ``"Device:R"``.

The distinction between :class:`Sym` and :class:`str` matters: KiCad treats
``yes`` (a keyword) and ``"yes"`` (a string) differently, so quotedness is
preserved through a round-trip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class Sym(str):
    """An unquoted S-expression token (keyword or number).

    Subclasses :class:`str` so it can be compared and used as text directly,
    while remaining distinguishable from a quoted string by type.
    """

    __slots__ = ()


@dataclass
class Node:
    """A parenthesized S-expression list such as ``(at 1 2 90)``.

    The first item is conventionally the node's name (a :class:`Sym`).
    """

    items: list[Node | Sym | str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """The head token (node name), or ``""`` for an empty list."""
        if self.items and isinstance(self.items[0], Sym):
            return str(self.items[0])
        return ""

    def get(self, name: str) -> Node | None:
        """Return the first child :class:`Node` whose name is *name*."""
        for it in self.items:
            if isinstance(it, Node) and it.name == name:
                return it
        return None

    def get_all(self, name: str) -> list[Node]:
        """Return every child :class:`Node` whose name is *name*."""
        return [it for it in self.items if isinstance(it, Node) and it.name == name]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_WHITESPACE = " \t\r\n"

# One token: a paren, a quoted string, a bareword run, or -- last, so it only
# wins when the string alternative could not -- a lone `"`. The string
# alternative consumes escape pairs, so an escaped quote does not end the token;
# and since it needs both delimiters, a token of exactly `"` can *only* be an
# unterminated literal. That lets the scan use findall (no match objects, no
# position arithmetic) and still detect the one malformed input there is.
#
# The scan is driven by the regex engine rather than a per-character Python loop
# because it sits under everything: each edit re-reads the whole sheet, and a
# large one is tens of thousands of lines.
_TOKEN_RE = re.compile(r'[()]|"(?:[^"\\]|\\.)*"|[^\s()"]+|"')


def _tokenize(text: str) -> list[str]:
    r"""Split *text* into ``(``, ``)``, quoted-string, and bareword tokens.

    Quoted strings retain their surrounding quotes so the parser can tell them
    apart from barewords; escapes ``\\"`` and ``\\\\`` are kept verbatim here
    and decoded in :func:`_parse_atom`.

    Raises:
        ValueError: On an unterminated string literal.
    """
    tokens: list[str] = _TOKEN_RE.findall(text)
    if '"' in tokens:  # only an unclosed literal can yield a bare quote token
        raise ValueError("unterminated string literal")
    return tokens


def _unescape(s: str) -> str:
    """Decode a quoted token's escapes into the string it represents."""
    if "\\" not in s:
        return s  # the overwhelming majority: nothing to decode, skip the walk
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _parse_atom(tok: str) -> Sym | str:
    """Turn a token into a quoted ``str`` or an unquoted :class:`Sym`."""
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        return _unescape(tok[1:-1])
    return Sym(tok)


def loads(text: str) -> Node:
    """Parse a full S-expression document and return its root :class:`Node`.

    Raises:
        ValueError: On unbalanced parentheses or an empty/invalid document.
    """
    tokens = _tokenize(text)
    if not tokens:
        raise ValueError("empty S-expression document")
    pos = 0

    def parse_list() -> Node:
        nonlocal pos
        assert tokens[pos] == "("
        pos += 1
        node = Node()
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "(":
                node.items.append(parse_list())
            elif tok == ")":
                pos += 1
                return node
            else:
                node.items.append(_parse_atom(tok))
                pos += 1
        raise ValueError("unbalanced parentheses: missing ')'")

    if tokens[pos] != "(":
        raise ValueError("document must begin with '('")
    root = parse_list()
    if pos != len(tokens):
        raise ValueError("trailing tokens after root S-expression")
    return root


# --------------------------------------------------------------------------- #
# Serializing
# --------------------------------------------------------------------------- #


def _escape(s: str) -> str:
    """Escape a Python string for a KiCad quoted token."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _atom_text(atom: Sym | str) -> str:
    """Render a leaf atom: barewords raw, strings quoted+escaped."""
    if isinstance(atom, Sym):
        return str(atom)
    return f'"{_escape(atom)}"'


def dumps(node: Node, *, indent: int = 0) -> str:
    """Serialize a :class:`Node` tree in KiCad's formatting style.

    Leading atoms stay on the head line; child lists each go on their own
    tab-indented line, matching how KiCad itself writes these files.

    Args:
        node: The tree to serialize.
        indent: Starting indentation depth (tabs). Callers normally leave 0.

    Returns:
        The serialized text (no trailing newline).
    """
    pad = "\t" * indent
    head: list[str] = []
    k = 0
    while k < len(node.items) and not isinstance(node.items[k], Node):
        head.append(_atom_text(node.items[k]))  # type: ignore[arg-type]
        k += 1
    list_children = node.items[k:]

    line = pad + "(" + " ".join(head)
    if not list_children:
        return line + ")"

    lines = [line]
    for child in list_children:
        if isinstance(child, Node):
            lines.append(dumps(child, indent=indent + 1))
        else:
            lines.append("\t" * (indent + 1) + _atom_text(child))
    lines.append(pad + ")")
    return "\n".join(lines)
