#!/usr/bin/env python
"""
Map old Nuxeo theme codes to new theme names and theme codes.

Reads an input CSV with 'disaron:nom' and 'disaron:theme' columns,
maps each theme code through themes-old-new.csv, and writes an output CSV
with columns: disaron_nom, disaron:theme, themes_labels, themes_codes.

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
    Build a lookup: old_theme_code -> (theme_label, theme_code).

    Each row in the mapping file may contain several pipe-separated codes in
    its old_theme_code column; all of them map to the same new theme.
    Rows with an empty old_theme_code (section headers) are skipped.

    When theme_label or theme_code are empty, falls back to the per-code
    old_theme_label and old_theme_code values respectively.
    """
    mapping: dict[str, tuple[str, str]] = {}
    with Path(mapping_csv).open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            old_theme_code = (row.get("old_theme_code") or "").strip()
            if not old_theme_code:
                continue
            old_theme_label = (row.get("old_theme_label") or "").strip()
            theme_label = (row.get("theme_label") or "").strip()
            theme_code = (row.get("theme_code") or "").strip()

            old_codes = [c.strip() for c in old_theme_code.split("|")]
            old_labels = [l.strip() for l in old_theme_label.split("|")]

            for i, code in enumerate(old_codes):
                if not code:
                    continue
                fallback_label = old_labels[i] if i < len(old_labels) else code
                fallback_code = code
                mapping[code] = (
                    theme_label or fallback_label,
                    theme_code or fallback_code,
                )
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
            fieldnames=["disaron_nom", "disaron:theme", "themes_labels", "themes_codes"],
        )
        writer.writeheader()

        failures_writer = csv.DictWriter(
            fail_f,
            fieldnames=["disaron:nom", "old_theme_code", "error"],
        )
        failures_writer.writeheader()

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            raw_themes = (row.get("disaron:theme") or "").strip()
            old_codes = [c.strip() for c in raw_themes.split("|") if c.strip()]

            labels: list[str] = []
            codes: list[str] = []
            seen: set[str] = set()
            for code in old_codes:
                result = mapping.get(code)
                if result is None:
                    failures_writer.writerow(
                        {
                            "disaron:nom": disaron_nom,
                            "old_theme_code": code,
                            "error": "unmapped theme code",
                        }
                    )
                    fail_f.flush()
                    failures_count += 1
                    continue
                theme_label, theme_code = result
                if theme_label not in seen:
                    seen.add(theme_label)
                    labels.append(theme_label)
                    codes.append(theme_code)

            writer.writerow(
                {
                    "disaron_nom": disaron_nom,
                    "disaron:theme": raw_themes,
                    "themes_labels": "|".join(labels),
                    "themes_codes": "|".join(codes),
                }
            )
            written += 1

    print(f"Wrote {written} row(s) to {args.output_csv}.")
    print(f"Wrote {failures_count} failure(s) to {failures_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
