#!/usr/bin/env python3
"""
Extract Disaron HTML and notes fields from a raw Disarons JSON dump to CSV.

Reads Nuxeo Disaron documents from either:

- a JSON array (e.g. ``2026-10-08_Disarons_tableaux_publies.json``)
- a concatenated dump of objects (optionally comma-separated), as in
  ``2026-05-18_Disarons.json``

and writes a CSV with:

- ``disaron:nom_old`` (from JSON ``disaron:nom``)
- ``has_html_principal``, ``has_html_secondaire``, ``has_notes``
- ``html_principal``, ``html_secondaire``, ``notes``

Only rows with at least one non-empty of ``html_principal``,
``html_secondaire``, or ``notes`` are written. Records use CRLF line endings.

Defaults:
- ``--input-file``: ``2026-10-08_Disarons_tableaux_publies.json``
- ``--output-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_raw_text_fields.csv``

Usage:
    just get_raw_text_fields

    just get_raw_text_fields \\
        --input-file 2026-05-18_Disarons.json \\
        --output-file metadata_editor/output/raw_text_fields.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from metadata_editor.extract_disaron_links import _iter_disaron_docs


FIELDNAMES = [
    "disaron:nom_old",
    "has_html_principal",
    "has_html_secondaire",
    "has_notes",
    "html_principal",
    "html_secondaire",
    "notes",
]


def _nonblank_str(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return value


def _notes_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _bool_cell(flag: bool) -> str:
    return "true" if flag else "false"


def load_docs(input_file: Path) -> Iterable[object]:
    """Load docs from a JSON array or a concatenated object dump."""
    try:
        with input_file.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return _iter_disaron_docs(input_file)

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError(
        f"Expected a JSON list or object of documents, got {type(data).__name__}"
    )


def extract_rows(docs: Iterable[object]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        props = doc.get("properties") or {}
        if not isinstance(props, dict):
            continue

        nom = (props.get("disaron:nom") or "").strip()
        if not nom:
            continue

        html_principal = _nonblank_str(props.get("disaron:html_principal"))
        html_secondaire = _nonblank_str(props.get("disaron:html_secondaire"))
        notes = _notes_list(props.get("disaron:notes"))

        has_hp = bool(html_principal)
        has_hs = bool(html_secondaire)
        has_notes = bool(notes)
        if not (has_hp or has_hs or has_notes):
            continue

        rows.append(
            {
                "disaron:nom_old": nom,
                "has_html_principal": _bool_cell(has_hp),
                "has_html_secondaire": _bool_cell(has_hs),
                "has_notes": _bool_cell(has_notes),
                "html_principal": html_principal if has_hp else "",
                "html_secondaire": html_secondaire if has_hs else "",
                "notes": json.dumps(notes, ensure_ascii=False) if has_notes else "",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        default="2026-10-08_Disarons_tableaux_publies.json",
        help="Raw Disarons JSON dump.",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help=(
            "Output CSV path. Default: "
            "metadata_editor/output/<timestamp>_raw_text_fields.csv"
        ),
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    if args.output_file:
        output_file = Path(args.output_file).resolve()
    else:
        prefix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_file = (
            Path("metadata_editor/output").resolve()
            / f"{prefix}_raw_text_fields.csv"
        )
    output_file.parent.mkdir(parents=True, exist_ok=True)

    rows = extract_rows(load_docs(input_file))

    with output_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=FIELDNAMES,
            lineterminator="\r\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_file} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
