#!/usr/bin/env python3
"""
Add themes_NEW-codes_revus-PB_alphabetique next to the PB theme codes column.

Reads ``themes_NEW-codes_revus-PB, sans les 4 anc catego fourre-tout`` (unchanged)
and writes a new column with the same codes sorted A→Z.

By default writes a new CSV file; the input file is not modified unless
``--in-place`` is passed.

Usage::

    just sort_theme_codes_column 2026-05-29_Inventaire-Nuxeo-v17.2.csv

    just sort_theme_codes_column inventaire.csv --output inventaire_sorted.csv

    just sort_theme_codes_column inventaire.csv --in-place
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

SOURCE_COLUMN = (
    "themes_NEW-codes_revus-PB, sans les 4 anc catego fourre-tout"
)
TARGET_COLUMN = "themes_NEW-codes_revus-PB_alphabetique"


def sort_pipe_codes(value: str) -> str:
    if not value or not value.strip():
        return ""
    parts = [part.strip() for part in value.split("|") if part.strip()]
    return "|".join(sorted(parts))


def _ensure_target_column_after_source(fieldnames: list[str]) -> list[str]:
    if TARGET_COLUMN in fieldnames:
        fieldnames = [name for name in fieldnames if name != TARGET_COLUMN]
    insert_at = fieldnames.index(SOURCE_COLUMN) + 1
    fieldnames.insert(insert_at, TARGET_COLUMN)
    return fieldnames


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        default="2026-05-29_Inventaire-Nuxeo-v17.2.csv",
        help="Inventaire CSV path (default: 2026-05-29_Inventaire-Nuxeo-v17.2.csv).",
    )
    parser.add_argument(
        "--output",
        default="",
        help=(
            "Output CSV path. Default: <input>_with_sorted_themes.csv "
            "(or .csv suffix preserved)."
        ),
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite the input CSV instead of writing a new file.",
    )
    args = parser.parse_args()

    input_path = Path(args.csv_file).resolve()
    if args.in_place:
        output_path = input_path
    elif args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = input_path.with_name(
            f"{input_path.stem}_with_sorted_themes{input_path.suffix}"
        )

    with input_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"No headers in {input_path}")
        if SOURCE_COLUMN not in reader.fieldnames:
            raise ValueError(f"Missing column {SOURCE_COLUMN!r}")

        fieldnames = _ensure_target_column_after_source(list(reader.fieldnames))
        rows = list(reader)

    changed = 0
    for row in rows:
        source = row.get(SOURCE_COLUMN, "") or ""
        sorted_value = sort_pipe_codes(source)
        if source.strip() and source != sorted_value:
            changed += 1
        row[TARGET_COLUMN] = sorted_value

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Read {len(rows)} row(s) from {input_path}")
    print(f"Wrote {output_path} ({SOURCE_COLUMN!r} unchanged, {TARGET_COLUMN!r} added)")
    print(f"Rows where sort order differs from source: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
