#!/usr/bin/env python
"""
Build my-downloader input CSV from Nuxeo-style `disaron:donnees` rows.

Input columns expected:
- disaron:nom
- disaron:donnees

Supported `disaron:donnees` examples:

1) JSON-like list of file objects (escaped in CSV):
   "[{\"Type_de_fichier\":\"2_FP\",\"Date_chargement\":\"2025-12-17T15:52:45.184Z\",\"Fichier\":\"cd2025-20_ProdCom2024.pdf\"},{\"Type_de_fichier\":\"5_FAS\",\"Date_chargement\":\"2025-12-17T14:23:45.284Z\",\"Fichier\":\"cd2025-20_DonnéesProdCom2024.xlsx\"}]"

2) Malformed bracket list:
   [NESE48 - Favoriser le déploiement de PSE en Agriculture.pdf]

Empty list is also supported.

Output columns:
- disaron:nom
- nb de fichiers
- noms des fichiers
- urls des fichiers

Example usage:
    python my-downloader/downloader_preprocessor.py \
      --input-csv "2026-04-27_Inventaire_Nuxeo_v7_donnees.csv" \
      --output-csv "my-downloader/files_to_download.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import quote

BASE_DOWNLOAD_URL = (
    "https://agreste.agriculture.gouv.fr/agreste-web/download/publication/publie"
)

FICHIER_RE = re.compile(r'"Fichier"\s*:\s*"([^"]+)"')


def _normalize_filenames(names: list[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        n = (name or "").strip()
        if n:
            out.append(n)
    return out


def _parse_json_like_donnees(raw: str) -> list[str]:
    value = (raw or "").strip()
    if not value or value == "[]":
        return []

    # Strategy 1: parse as-is JSON list of objects.
    candidates = [
        value,
        value.replace(r"\,", ","),
        value.replace(r"\"", '"').replace(r"\,", ","),
    ]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, list):
            names: list[str] = []
            for item in parsed:
                if isinstance(item, dict):
                    file_name = (item.get("Fichier") or "").strip()
                    if file_name:
                        names.append(file_name)
            if names:
                return names

    # Strategy 2: regex extract "Fichier":"..." entries.
    normalized = value.replace(r"\"", '"').replace(r"\,", ",")
    names = [m.group(1).strip() for m in FICHIER_RE.finditer(normalized)]
    return [n for n in names if n]


def _parse_bracket_list_donnees(raw: str) -> list[str]:
    """
    Parse malformed bracket form, e.g.:
    [NESE48 - Favoriser le déploiement de PSE en Agriculture.pdf]
    """
    value = (raw or "").strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    inner = value[1:-1].strip()
    if not inner:
        return []
    # Keep as one filename by default.
    return [inner]


def _extract_filenames(raw_donnees: str) -> list[str]:
    names = _parse_json_like_donnees(raw_donnees)
    if not names:
        names = _parse_bracket_list_donnees(raw_donnees)
    # Deduplicate, keep first occurrence order.
    seen: set[str] = set()
    deduped: list[str] = []
    for name in _normalize_filenames(names):
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def _build_urls(disaron_nom: str, filenames: list[str]) -> list[str]:
    disaron_enc = quote(disaron_nom, safe="")
    urls: list[str] = []
    for name in filenames:
        filename_enc = quote(name, safe="")
        urls.append(f"{BASE_DOWNLOAD_URL}/{disaron_enc}/{filename_enc}")
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-csv",
        required=True,
        help="CSV with columns disaron:nom, disaron:donnees",
    )
    parser.add_argument(
        "--output-csv",
        required=True,
        help=(
            "Output CSV path with columns disaron:nom, nb de fichiers, "
            "noms des fichiers, urls des fichiers"
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input_csv)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with (
        input_path.open("r", encoding="utf-8", newline="") as in_f,
        output_path.open("w", encoding="utf-8", newline="") as out_f,
    ):
        reader = csv.DictReader(in_f)
        fieldnames = reader.fieldnames or []
        if "disaron:nom" not in fieldnames:
            raise ValueError("Input CSV must contain 'disaron:nom' column.")
        if "disaron:donnees" not in fieldnames:
            raise ValueError(
                "Input CSV must contain 'disaron:donnees' column."
            )

        writer = csv.DictWriter(
            out_f,
            fieldnames=[
                "disaron:nom",
                "nb de fichiers",
                "noms des fichiers",
                "urls des fichiers",
            ],
        )
        writer.writeheader()

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                continue
            raw_donnees = (row.get("disaron:donnees") or "").strip()
            names = _extract_filenames(raw_donnees)
            urls = _build_urls(disaron_nom, names)
            writer.writerow(
                {
                    "disaron:nom": disaron_nom,
                    "nb de fichiers": str(len(names)),
                    "noms des fichiers": json.dumps(names, ensure_ascii=False),
                    "urls des fichiers": json.dumps(urls, ensure_ascii=False),
                }
            )
            written += 1

    print(f"Wrote {written} row(s) to {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
