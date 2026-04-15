#!/usr/bin/env python
"""
Check a CSV for missing metadata fields and log rows with gaps.

For each row in the input CSV, checks whether a fixed set of required fields
are present and non-empty.  Rows with at least one missing field are written
to a timestamped output file.

Usage:
    python missing_data_filler/list_missing_data.py --input-csv 2026-04-15-nuxeo-fixed.csv
    python missing_data_filler/list_missing_data.py --input-csv 2026-04-15-nuxeo-fixed.csv \
        --output-dir output
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

REQUIRED_FIELDS = [
    "disaron:nom",
    "dc:title",
    "disaron:Complement_titre",
    "disaron:chapeau",
    "disaron:Auteur",
    "disaron:Date_premiere_publication",
    "disaron:Numerotation",
    "disaron:theme",
    "disaron:annees_reference",
    "disaron:niveau_geographique",
    "disaron:collection",
    "disaron:sous_collection",
    "disaron:categorie",
    "disaron:donnees",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        required=True,
        help="CSV file to check.",
    )
    parser.add_argument(
        "--output-dir",
        default="missing_data_filler/output",
        help="Directory for the failures file (default: missing_data_filler/output/).",
    )
    args = parser.parse_args()

    input_path = Path(args.input_csv)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = output_dir / f"{timestamp}_missing_fields.csv"

    total = 0
    flagged = 0

    with (
        input_path.open("r", encoding="utf-8", newline="") as in_f,
        output_path.open("w", encoding="utf-8", newline="") as out_f,
    ):
        reader = csv.DictReader(in_f)
        flag_fields = [f for f in REQUIRED_FIELDS if f != "disaron:nom"]
        writer = csv.DictWriter(
            out_f, fieldnames=["disaron:nom", "missing_fields"] + flag_fields
        )
        writer.writeheader()

        for row in reader:
            total += 1
            missing = [
                field
                for field in REQUIRED_FIELDS
                if not (row.get(field) or "").strip()
            ]
            if missing:
                flagged += 1
                missing_set = set(missing)
                out_row: dict = {
                    "disaron:nom": (row.get("disaron:nom") or "").strip(),
                    "missing_fields": "|".join(missing),
                }
                for field in flag_fields:
                    out_row[field] = 1 if field in missing_set else 0
                writer.writerow(out_row)

    print(f"Checked {total} row(s): {flagged} with missing fields.")
    print(f"Output written to {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
