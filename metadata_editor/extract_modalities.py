#!/usr/bin/env python3
"""
Extract Disaron inventaire modalities for public documents.

Reads Nuxeo Disaron documents from either:

- a JSON array
- a concatenated dump of objects (optionally comma-separated), as in
  ``2026-05-18_Disarons.json``

Keeps only documents with a non-empty ``disaron:inventaire_disaron`` and
``DISAR-PUBLIC`` in ``disaron:communaute_restit``. Inventaire triplets
``(metadonnee_modalite, libelle_modalite, metadonnee_nom)`` are deduplicated
per document (first occurrence wins).

Writes a flat CSV sorted by ``disaron:nom``, ``metadonnee_nom``,
``metadonnee_modalite``, with CRLF line endings.

Defaults:
- ``--input-file``: ``2026-05-18_Disarons.json``
- ``--output-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities.csv``

Usage:
    just extract_modalities

    just extract_modalities \\
        --input-file 2026-05-18_Disarons.json \\
        --output-file metadata_editor/data/2026-05-18_Disarons_modalities.csv
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable
from pathlib import Path

from metadata_editor.get_raw_text_fields import load_docs

DISAR_PUBLIC = "DISAR-PUBLIC"

FIELDNAMES = [
    "disaron:nom",
    "metadonnee_nom",
    "metadonnee_modalite",
    "libelle_modalite",
]


def _is_public(communaute_restit: object) -> bool:
    if not isinstance(communaute_restit, list):
        return False
    return DISAR_PUBLIC in communaute_restit


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def extract_rows(docs: Iterable[object]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
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

        seen: set[tuple[str, str, str]] = set()
        for item in inventaire:
            if not isinstance(item, dict):
                continue
            modalite = _cell(item.get("metadonnee_modalite"))
            libelle = _cell(item.get("libelle_modalite"))
            metadonnee_nom = _cell(item.get("metadonnee_nom"))
            key = (modalite, libelle, metadonnee_nom)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "disaron:nom": nom.strip(),
                    "metadonnee_nom": metadonnee_nom,
                    "metadonnee_modalite": modalite,
                    "libelle_modalite": libelle,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        default="2026-05-18_Disarons.json",
        help="Raw Disarons JSON dump.",
    )
    parser.add_argument(
        "--output-file",
        default="metadata_editor/data/2026-05-18_Disarons_modalities.csv",
        help="Output CSV path.",
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    output_file = Path(args.output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    rows = extract_rows(load_docs(input_file))
    rows.sort(
        key=lambda r: (
            r["disaron:nom"],
            r["metadonnee_nom"],
            r["metadonnee_modalite"],
        )
    )

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
