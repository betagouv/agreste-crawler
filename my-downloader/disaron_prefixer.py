#!/usr/bin/env python3
"""
Prefix downloaded files with their disaron code.

For each file in a downloads directory (default: my-downloader/downloads),
look up the corresponding disaron code from a CSV and rename the file to:
    <disaron_nom>_<original_filename>
if it has not already been prefixed.

Supported --file-list CSV formats:
1) One file per line:
    - disaron_nom
    - nom_fichier
2) One disaron per line with multiple filenames:
    - disaron:nom
    - nb de fichiers
    - noms des fichiers
   where "noms des fichiers" can be a JSON/Python-style list string.

Usage:
    uv run python my-downloader/disaron_prefixer.py
    uv run python my-downloader/disaron_prefixer.py --dry-run
    uv run python my-downloader/disaron_prefixer.py \
        --file-list my-downloader/files_to_download_infos_rapides_completees.csv
    uv run python my-downloader/disaron_prefixer.py \
        --downloads-dir my-downloader/non_infos_rapides_1_1234_files
"""

import argparse
import ast
import csv
from datetime import datetime
import json
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
        fieldnames = set(reader.fieldnames or [])

        # Format A: one file per line
        if {"nom_fichier", "disaron_nom"}.issubset(fieldnames):
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

        # Format B: one disaron per line
        if {"disaron:nom", "nb de fichiers", "noms des fichiers"}.issubset(
            fieldnames
        ):
            for row in reader:
                disaron_nom = (row.get("disaron:nom") or "").strip()
                raw_names = (row.get("noms des fichiers") or "").strip()
                if not disaron_nom or not raw_names:
                    continue

                names: list[str] = []
                try:
                    parsed = json.loads(raw_names)
                    if isinstance(parsed, list):
                        names = [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
                if not names:
                    try:
                        parsed = ast.literal_eval(raw_names)
                        if isinstance(parsed, list):
                            names = [
                                str(x).strip() for x in parsed if str(x).strip()
                            ]
                    except Exception:
                        pass
                if not names:
                    names = [raw_names]

                for nom_fichier in names:
                    if nom_fichier in mapping and mapping[nom_fichier] != disaron_nom:
                        print(
                            f"[WARN] Duplicate filename '{nom_fichier}' found under "
                            f"'{mapping[nom_fichier]}' and '{disaron_nom}'; using '{disaron_nom}'.",
                            file=sys.stderr,
                        )
                    mapping[nom_fichier] = disaron_nom
            return mapping

        raise ValueError(
            "Unsupported --file-list format. Expected either columns "
            "'disaron_nom, nom_fichier' or "
            "'disaron:nom, nb de fichiers, noms des fichiers'."
        )
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prefix files in my-downloader/downloads with disaron_nom from a CSV "
            "(reads either columns: disaron_nom, nom_fichier OR "
            "disaron:nom, nb de fichiers, noms des fichiers)."
        )
    )
    parser.add_argument(
        "--file-list",
        type=str,
        default="",
        help=(
            "CSV containing file mappings. Supported formats: "
            "(1) disaron_nom, nom_fichier or "
            "(2) disaron:nom, nb de fichiers, noms des fichiers. "
            "Defaults to my-downloader/files_to_download.csv."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be renamed without actually renaming.",
    )
    parser.add_argument(
        "--downloads-dir",
        type=str,
        default="",
        help=(
            "Directory containing downloaded files to prefix. "
            "Defaults to my-downloader/downloads."
        ),
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    if args.file_list:
        csv_path = Path(args.file_list).expanduser()
    else:
        csv_path = root / "files_to_download.csv"
    if not csv_path.is_absolute():
        csv_path = (Path.cwd() / csv_path).resolve()
    if args.downloads_dir:
        downloads_dir = Path(args.downloads_dir).expanduser()
        if not downloads_dir.is_absolute():
            downloads_dir = (Path.cwd() / downloads_dir).resolve()
    else:
        downloads_dir = root / "downloads"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    if not downloads_dir.exists():
        print(
            f"[ERROR] Downloads directory not found: {downloads_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        mapping = load_filename_to_disaron(csv_path)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Loaded {len(mapping)} filename→disaron_nom "
        f"mappings from {csv_path.name}"
    )

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
            potential_rest = original_name[len(potential_prefix) + 1 :]
            if potential_prefix in mapping.values() and potential_rest in mapping:
                print(f"[SKIP] Already prefixed: {original_name}")
                skipped += 1
                continue

        disaron_nom = mapping.get(original_name)
        if disaron_nom is None:
            print(
                f"[UNKNOWN] No mapping found for: {original_name}",
                file=sys.stderr,
            )
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

    print(
        f"\nDone: {renamed} renamed, {skipped} skipped, "
        f"{len(unknown_files)} unknown."
    )
    if unknown_files:
        results_dir = root / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        failures_path = results_dir / f"{timestamp}_prefixer_failures.csv"
        with failures_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["nom_fichier"])
            writer.writeheader()
            for name in unknown_files:
                writer.writerow({"nom_fichier": name})

        print("Unknown files (no mapping found):")
        for name in unknown_files:
            print(f"  - {name}")
        print(f"Wrote {len(unknown_files)} failure row(s) to {failures_path}")


if __name__ == "__main__":
    main()
