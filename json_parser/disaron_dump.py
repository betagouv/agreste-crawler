"""Tolerant JSON loading for Nuxeo Disaron dump objects."""

from __future__ import annotations

import json
from typing import Any


def escape_control_chars_in_json_strings(raw: str) -> str:
    """
    Escape raw U+0000–U+001F characters that appear inside JSON strings.

    Some Disaron dumps embed HTML with literal newlines/control chars that
    make ``json.loads`` fail even though brace structure is otherwise valid.
    """
    out: list[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            code = ord(ch)
            if code < 0x20:
                if ch == "\n":
                    out.append("\\n")
                elif ch == "\r":
                    out.append("\\r")
                elif ch == "\t":
                    out.append("\\t")
                else:
                    out.append(f"\\u{code:04x}")
                continue
            out.append(ch)
            continue

        out.append(ch)
        if ch == '"':
            in_string = True
    return "".join(out)


def loads_disaron_object(raw: str) -> dict[str, Any]:
    """
    Parse one Disaron dump object.

    Tries ``json.loads`` first, then retries after escaping control characters
    inside JSON strings. Raises ``json.JSONDecodeError`` if both fail or the
    result is not a dict.
    """
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        doc = json.loads(escape_control_chars_in_json_strings(raw))
    if not isinstance(doc, dict):
        raise json.JSONDecodeError(
            f"Expected a JSON object, got {type(doc).__name__}",
            raw,
            0,
        )
    return doc
