#!/usr/bin/env python3
"""
Extract Disaron inventaire modalities for public documents.

Reads Nuxeo Disaron documents from either:

- a JSON array
- a concatenated dump of objects (optionally comma-separated), as in
  ``2026-05-18_Disarons.json``

and writes a CSV with one row per modality:

- ``disaron:nom`` (from inventaire / mapping CSV, else ``disaron:nom_old``)
- ``disaron:nom_old`` (from JSON ``disaron:nom``)
- ``metadonnee_nom``
- ``libelle_modalite``
- ``metadonnee_modalite``

Only modalities from documents with a non-empty ``disaron:inventaire_disaron``
and ``DISAR-PUBLIC`` in ``disaron:communaute_restit`` are kept. Records use
CRLF line endings.

``disaron:nom`` is resolved from the inventaire CSV via ``disaron:nom_old``,
then overlaid with the curated mapping CSV (mapping wins on conflict). If no
match is found, ``disaron:nom`` is filled with ``disaron:nom_old``.

Defaults:
- ``--input-file``: ``2026-05-18_Disarons.json``
- ``--inventaire-file``: ``2026-06-02_Inventaire-Nuxeo-v18.csv``
- ``--mapping-file``: ``2026-10-28_data_disaron_old_new_mapping.csv``
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

from metadata_editor.clean_text_fields import load_inventaire_nom_map
from metadata_editor.get_raw_text_fields import load_docs

DISAR_PUBLIC = "DISAR-PUBLIC"

FIELDNAMES = [
    "disaron:nom",
    "disaron:nom_old",
    "metadonnee_nom",
    "libelle_modalite",
    "metadonnee_modalite",
]


def _is_public(communaute_restit: object) -> bool:
    if not isinstance(communaute_restit, list):
        return False
    return DISAR_PUBLIC in communaute_restit


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def load_mapping_nom_map(mapping_file: Path) -> dict[str, str]:
    """Map stripped nom_old -> stripped nom from the curated mapping CSV."""
    with mapping_file.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Mapping CSV has no headers: {mapping_file}")
        required = {"disaron:nom", "disaron:nom_old"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Mapping CSV missing column(s): {', '.join(sorted(missing))}"
            )
        rows = list(reader)

    mapping: dict[str, str] = {}
    for row in rows:
        old = (row.get("disaron:nom_old") or "").strip()
        nom = (row.get("disaron:nom") or "").strip()
        if not old or not nom:
            continue
        existing = mapping.get(old)
        if existing is not None and existing != nom:
            raise ValueError(
                f"Mapping: ambiguous disaron:nom for disaron:nom_old={old!r}: "
                f"{existing!r}, {nom!r}"
            )
        mapping[old] = nom
    return mapping


def build_nom_map(inventaire_file: Path, mapping_file: Path) -> dict[str, str]:
    """Inventaire map overlaid by curated mapping (mapping wins)."""
    nom_map = load_inventaire_nom_map(inventaire_file)
    nom_map.update(load_mapping_nom_map(mapping_file))
    return nom_map


def extract_rows(
    docs: Iterable[object],
    nom_map: dict[str, str],
) -> list[dict[str, str]]:
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

        nom_old = nom.strip()
        resolved_nom = nom_map.get(nom_old, nom_old)
        for item in inventaire:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "disaron:nom": resolved_nom,
                    "disaron:nom_old": nom_old,
                    "metadonnee_nom": _cell(item.get("metadonnee_nom")),
                    "libelle_modalite": _cell(item.get("libelle_modalite")),
                    "metadonnee_modalite": _cell(item.get("metadonnee_modalite")),
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
        "--inventaire-file",
        default="2026-06-02_Inventaire-Nuxeo-v18.csv",
        help="Inventaire CSV used to resolve disaron:nom.",
    )
    parser.add_argument(
        "--mapping-file",
        default="2026-10-28_data_disaron_old_new_mapping.csv",
        help="Curated old/new nom mapping CSV (overrides inventaire).",
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

    inventaire_file = Path(args.inventaire_file).resolve()
    if not inventaire_file.exists():
        raise FileNotFoundError(f"Inventaire file not found: {inventaire_file}")

    mapping_file = Path(args.mapping_file).resolve()
    if not mapping_file.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_file}")

    output_file = Path(args.output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    nom_map = build_nom_map(inventaire_file, mapping_file)
    rows = extract_rows(load_docs(input_file), nom_map)

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
