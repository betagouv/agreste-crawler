#!/usr/bin/env python3
"""
disaron_prefixer.py

For each file in my-downloader/downloads/, look up the corresponding
disaron_nom from files_to_download_20260317_123722.csv and rename the file
to <disaron_nom>_<original_filename> (if it hasn't already been prefixed).

Usage:
    uv run python disaron_prefixer.py
    uv run python disaron_prefixer.py --dry-run    # print renames without executing
"""
import argparse
import csv
import sys
from pathlib import Path


def load_filename_to_disaron(csv_path: Path) -> dict[str, str]:
    """
    Build a mapping from nom_fichier -> disaron_nom from the CSV.
    If the same filename appears under multiple disaron_nom values,
    the last one wins (log a warning).
    """
    mapping: dict[str, str] = {}
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nom_fichier = (row.get("nom_fichier") or "").strip()
            disaron_nom = (row.get("disaron_nom") or "").strip()
            if not nom_fichier or not disaron_nom:
                continue
            if nom_fichier in mapping and mapping[nom_fichier] != disaron_nom:
                print(
                    f"[WARN] Duplicate filename '{nom_fichier}' found under "
                    f"'{mapping[nom_fichier]}' and '{disaron_nom}'; using '{disaron_nom}'.",
                    file=sys.stderr,
                )
            mapping[nom_fichier] = disaron_nom
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description="Prefix downloaded files with their disaron_nom.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be renamed without actually renaming.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    csv_path = root / "files_to_download_20260317_123722.csv"
    downloads_dir = root / "my-downloader" / "downloads"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    if not downloads_dir.exists():
        print(f"[ERROR] Downloads directory not found: {downloads_dir}", file=sys.stderr)
        sys.exit(1)

    mapping = load_filename_to_disaron(csv_path)
    print(f"Loaded {len(mapping)} filename→disaron_nom mappings from {csv_path.name}")

    renamed = 0
    skipped = 0
    unknown_files: list[str] = []

    for file in sorted(downloads_dir.iterdir()):
        if not file.is_file():
            continue

        original_name = file.name

        # Skip if already prefixed (disaron_nom_ prefix pattern)
        if "_" in original_name:
            potential_prefix = original_name.split("_")[0]
            potential_rest = original_name[len(potential_prefix) + 1:]
            if potential_prefix in mapping.values() and potential_rest in mapping:
                print(f"[SKIP] Already prefixed: {original_name}")
                skipped += 1
                continue

        disaron_nom = mapping.get(original_name)
        if disaron_nom is None:
            print(f"[UNKNOWN] No mapping found for: {original_name}", file=sys.stderr)
            unknown_files.append(original_name)
            continue

        new_name = f"{disaron_nom}_{original_name}"
        new_path = file.parent / new_name

        if args.dry_run:
            print(f"[DRY-RUN] {original_name} -> {new_name}")
        else:
            file.rename(new_path)
            print(f"[RENAMED] {original_name} -> {new_name}")

        renamed += 1

    print(f"\nDone: {renamed} renamed, {skipped} skipped, {len(unknown_files)} unknown.")
    if unknown_files:
        print("Unknown files (no mapping found):")
        for name in unknown_files:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
