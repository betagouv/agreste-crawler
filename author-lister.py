#!/usr/bin/env python3
"""
Build an author index from a DISARON CSV export.

Reads an input CSV file and extracts authors from the "disaron:Auteur" column.
Writes an output CSV with columns:
  - nom_auteur
  - prenom_auteur
  - Structure
  - disaron:noms

"disaron:noms" is a JSON array of distinct disaron:nom values for each author.

Usage examples:
  uv run python author-lister.py --input-csv infos-rapides.csv --output-csv authors.csv
  uv run python author-lister.py --input-csv page-creator/data/infos-rapides.csv --output-csv page-creator/data/authors.csv
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


AUTHOR_BLOCK_RE = re.compile(r"\{([^{}]*)\}")


def _parse_auteurs_cell(cell: str) -> list[tuple[str, str, str]]:
    """
    Parse one disaron:Auteur cell.

    Expected patterns include values like:
      [{nom_auteur=Dupont, prenom_auteur=Jean, Structure=SSP}]
      [{...},{...}]
    """
    if not cell:
        return []

    results: list[tuple[str, str, str]] = []
    for block in AUTHOR_BLOCK_RE.findall(cell):
        fields: dict[str, str] = {}
        for part in block.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()

        nom = fields.get("nom_auteur", "")
        prenom = fields.get("prenom_auteur", "")
        structure = fields.get("Structure", "")

        if nom or prenom or structure:
            results.append((nom, prenom, structure))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        required=True,
        help="Input CSV path containing disaron:nom and disaron:Auteur columns.",
    )
    parser.add_argument(
        "--output-csv",
        required=True,
        help="Output CSV path to write grouped author rows.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_csv)
    output_path = Path(args.output_csv)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    authors_to_disaron_noms: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    with input_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no headers.")
        if "disaron:nom" not in reader.fieldnames:
            raise ValueError("Input CSV must contain 'disaron:nom' column.")
        if "disaron:Auteur" not in reader.fieldnames:
            raise ValueError("Input CSV must contain 'disaron:Auteur' column.")

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            auteurs_cell = (row.get("disaron:Auteur") or "").strip()
            if not disaron_nom or not auteurs_cell:
                continue

            for author_key in _parse_auteurs_cell(auteurs_cell):
                authors_to_disaron_noms[author_key].add(disaron_nom)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        fieldnames = ["nom_auteur", "prenom_auteur", "Structure", "disaron:noms"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for (nom, prenom, structure), disaron_noms in sorted(
            authors_to_disaron_noms.items(),
            key=lambda item: (item[0][0].lower(), item[0][1].lower(), item[0][2].lower()),
        ):
            writer.writerow(
                {
                    "nom_auteur": nom,
                    "prenom_auteur": prenom,
                    "Structure": structure,
                    "disaron:noms": json.dumps(sorted(disaron_noms), ensure_ascii=False),
                }
            )

    print(f"Wrote {len(authors_to_disaron_noms)} author rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
