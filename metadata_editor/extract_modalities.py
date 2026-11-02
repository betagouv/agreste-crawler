#!/usr/bin/env python3
"""
Extract Disaron inventaire modalities for public documents.

Reads Nuxeo Disaron documents from either:

- a JSON array
- a concatenated dump of objects (optionally comma-separated), as in
  ``2026-05-18_Disarons.json``

and writes a JSON array of objects with:

- ``disaron:nom``
- ``disaron:inventaire_disaron``

Only entries with a non-empty ``disaron:inventaire_disaron`` and
``DISAR-PUBLIC`` in ``disaron:communaute_restit`` are kept.

Defaults:
- ``--input-file``: ``2026-05-18_Disarons.json``
- ``--output-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities.json``

Usage:
    just extract_modalities

    just extract_modalities \\
        --input-file 2026-05-18_Disarons.json \\
        --output-file metadata_editor/data/2026-05-18_Disarons_modalities.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

from metadata_editor.get_raw_text_fields import load_docs

DISAR_PUBLIC = "DISAR-PUBLIC"


def _is_public(communaute_restit: object) -> bool:
    if not isinstance(communaute_restit, list):
        return False
    return DISAR_PUBLIC in communaute_restit


def extract_entries(docs: Iterable[object]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        props = doc.get("properties") or {}
        if not isinstance(props, dict):
            continue

        nom = props.get("disaron:nom")
        if not isinstance(nom, str) or not nom.strip():
            continue

        inventaire = props.get("disaron:inventaire_disaron")
        if not isinstance(inventaire, list) or not inventaire:
            continue

        if not _is_public(props.get("disaron:communaute_restit")):
            continue

        entries.append(
            {
                "disaron:nom": nom.strip(),
                "disaron:inventaire_disaron": inventaire,
            }
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        default="2026-05-18_Disarons.json",
        help="Raw Disarons JSON dump.",
    )
    parser.add_argument(
        "--output-file",
        default="metadata_editor/data/2026-05-18_Disarons_modalities.json",
        help="Output JSON path.",
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    output_file = Path(args.output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    entries = extract_entries(load_docs(input_file))

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {output_file} ({len(entries)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
