#!/usr/bin/env python
"""
Map old Nuxeo theme codes to new theme names and labels.

Reads an input CSV with 'disaron:nom' and 'disaron:theme' columns,
maps each theme code through themes-old-new.csv, and writes an output CSV
with columns: disaron_nom, disaron:theme, themes, themes_labels.

Unmapped codes are written incrementally to a failures CSV file.

Usage:
    python theme-mapper/map_themes.py \
        --input-csv theme-mapper/2026-03-23_Inventaire-Nuxeo-themes.csv \
        --output-csv theme-mapper/output.csv
"""

import argparse
import csv
from datetime import datetime
from pathlib import Path

DEFAULT_MAPPING_CSV = "theme-mapper/themes-old-new.csv"


def load_theme_mapping(mapping_csv: str) -> dict[str, tuple[str, str]]:
    """
    Build a lookup: old_theme_code -> (theme, theme_label).

    Each row in the mapping file may contain several pipe-separated codes in
    its old_theme column; all of them map to the same new theme.
    Rows with an empty old_theme (section headers) are skipped.
    """
    mapping: dict[str, tuple[str, str]] = {}
    with Path(mapping_csv).open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            old_theme = (row.get("old_theme") or "").strip()
            theme = (row.get("theme") or "").strip()
            theme_label = (row.get("theme_label") or "").strip()
            if not old_theme or not theme:
                continue
            for code in old_theme.split("|"):
                code = code.strip()
                if code:
                    mapping[code] = (theme, theme_label)
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        required=True,
        help="CSV with 'disaron:nom' and 'disaron:theme' columns.",
    )
    parser.add_argument(
        "--mapping-csv",
        default=DEFAULT_MAPPING_CSV,
        help=f"Theme mapping file (default: {DEFAULT_MAPPING_CSV}).",
    )
    parser.add_argument(
        "--output-csv",
        required=True,
        help="Destination CSV file.",
    )
    args = parser.parse_args()

    mapping = load_theme_mapping(args.mapping_csv)

    input_path = Path(args.input_csv)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    failures_dir = Path("theme-mapper/output")
    failures_dir.mkdir(parents=True, exist_ok=True)
    failures_path = failures_dir / f"{timestamp}_theme_mapper_failures.csv"

    written = 0
    failures_count = 0

    with (
        input_path.open("r", encoding="utf-8", newline="") as in_f,
        output_path.open("w", encoding="utf-8", newline="") as out_f,
        failures_path.open("w", encoding="utf-8", newline="") as fail_f,
    ):
        reader = csv.DictReader(in_f)
        writer = csv.DictWriter(
            out_f,
            fieldnames=["disaron_nom", "disaron:theme", "themes", "themes_labels"],
        )
        writer.writeheader()

        failures_writer = csv.DictWriter(
            fail_f,
            fieldnames=["disaron:nom", "old_theme", "error"],
        )
        failures_writer.writeheader()

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            raw_themes = (row.get("disaron:theme") or "").strip()
            old_codes = [c.strip() for c in raw_themes.split("|") if c.strip()]

            themes: list[str] = []
            labels: list[str] = []
            seen: set[str] = set()
            for code in old_codes:
                result = mapping.get(code)
                if result is None:
                    failures_writer.writerow(
                        {
                            "disaron:nom": disaron_nom,
                            "old_theme": code,
                            "error": "unmapped theme code",
                        }
                    )
                    fail_f.flush()
                    failures_count += 1
                    continue
                theme, theme_label = result
                if theme not in seen:
                    seen.add(theme)
                    themes.append(theme)
                    labels.append(theme_label)

            writer.writerow(
                {
                    "disaron_nom": disaron_nom,
                    "disaron:theme": raw_themes,
                    "themes": "|".join(themes),
                    "themes_labels": "|".join(labels),
                }
            )
            written += 1

    print(f"Wrote {written} row(s) to {args.output_csv}.")
    print(f"Wrote {failures_count} failure(s) to {failures_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
