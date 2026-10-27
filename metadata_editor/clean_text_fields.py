#!/usr/bin/env python3
"""
Clean Disaron text-field CSV and add ``disaron:nom`` from inventaire.

Reads a CSV produced by ``get_raw_text_fields`` (columns:
``disaron:nom_old``, ``has_html_principal``, ``has_html_secondaire``,
``has_notes``, ``html_principal``, ``html_secondaire``, ``notes``) and writes
the same columns with ``disaron:nom`` prepended.

``disaron:nom`` is resolved from the inventaire CSV via ``disaron:nom_old``.
Matching inventaire rows with ``Garder/Exclure == Garder`` are preferred over
``Exclure``. If more than one distinct ``disaron:nom`` remains after that
filter, the script crashes. If no inventaire match is found, ``disaron:nom``
is filled with ``disaron:nom_old``.

HTML cleaning (``html_principal`` / ``html_secondaire``): remove ``<p>`` nodes
with no meaningful content (blank text after normalizing ``&nbsp;`` / ``\\xa0``,
and no media/structure children such as ``img``, ``a``, ``iframe``, ``table``,
``video``, ``svg``). Does not strip ``&nbsp;`` inside paragraphs that still
have real text or media. Removals are written to a log CSV.

Input ``disaron:nom_old`` values must be unique (stripped). Output
``disaron:nom`` and ``disaron:nom_old`` must each be unique. Inventaire
duplicates are allowed.

TODO: links and images still point at old Agreste content and should be
updated later.

Defaults:
- ``--input-file``: ``metadata_editor/data/2026-05-18_Disarons_raw_text_fields.csv``
- ``--inventaire-file``: ``2026-06-02_Inventaire-Nuxeo-v18.csv``
- ``--output-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_clean_text_fields.csv``
- ``--log-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_clean_text_fields_log.csv``

Usage:
    just clean_text_fields

    just clean_text_fields \\
        --input-file metadata_editor/data/2026-05-18_Disarons_raw_text_fields.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

INPUT_FIELDS = [
    "disaron:nom_old",
    "has_html_principal",
    "has_html_secondaire",
    "has_notes",
    "html_principal",
    "html_secondaire",
    "notes",
]

OUTPUT_FIELDS = ["disaron:nom", *INPUT_FIELDS]

LOG_FIELDS = [
    "disaron:nom_old",
    "disaron:nom",
    "field",
    "removed_html",
]

HTML_FIELDS = ("html_principal", "html_secondaire")

MEDIA_TAGS = frozenset(
    {"img", "a", "iframe", "table", "video", "svg", "object", "embed"}
)


class DuplicateError(ValueError):
    """Raised when a required unique column has duplicates."""


class AmbiguousNomError(ValueError):
    """Raised when inventaire yields more than one disaron:nom for a nom_old."""


def _bool_cell(flag: bool) -> str:
    return "true" if flag else "false"


def _find_duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dups: set[str] = set()
    for value in values:
        if value in seen:
            dups.add(value)
        else:
            seen.add(value)
    return sorted(dups)


def _require_unique(label: str, column: str, values: list[str]) -> None:
    dups = _find_duplicates(values)
    if dups:
        preview = ", ".join(repr(v) for v in dups[:20])
        more = f" (+{len(dups) - 20} more)" if len(dups) > 20 else ""
        raise DuplicateError(
            f"{label}: duplicate {column} values: {preview}{more}"
        )


def _is_empty_paragraph(tag: Tag) -> bool:
    if tag.name != "p":
        return False
    if tag.find(MEDIA_TAGS):
        return False
    text = tag.get_text().replace("\xa0", " ").replace("&nbsp;", " ")
    return not text.strip()


def clean_html(html: str) -> tuple[str, list[str]]:
    """Return cleaned HTML and a list of removed ``<p>`` outer HTML snippets."""
    if not html or not html.strip():
        return "", []

    soup = BeautifulSoup(html, "html.parser")
    removed: list[str] = []
    for ptag in list(soup.find_all("p")):
        if _is_empty_paragraph(ptag):
            removed.append(str(ptag))
            ptag.decompose()

    # Prefer fragment serialization: avoid wrapping <html><body> if input was a fragment.
    if soup.body is not None and not html.lstrip().lower().startswith("<html"):
        parts: list[str] = []
        for child in soup.body.contents:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            else:
                parts.append(str(child))
        cleaned = "".join(parts)
    else:
        cleaned = str(soup)

    return cleaned, removed


def load_inventaire_nom_map(inventaire_file: Path) -> dict[str, str]:
    """Map stripped nom_old -> stripped nom, preferring Garder rows.

    Crashes if more than one distinct nom remains after Garder preference.
    """
    with inventaire_file.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Inventaire CSV has no headers: {inventaire_file}")
        required = {"disaron:nom", "disaron:nom_old"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Inventaire CSV missing column(s): {', '.join(sorted(missing))}"
            )
        rows = list(reader)

    by_old: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        old = (row.get("disaron:nom_old") or "").strip()
        nom = (row.get("disaron:nom") or "").strip()
        if not old or not nom:
            continue
        garder = (row.get("Garder/Exclure") or "").strip()
        by_old[old].append((nom, garder))

    mapping: dict[str, str] = {}
    for old, candidates in by_old.items():
        garder_noms = {nom for nom, garder in candidates if garder == "Garder"}
        if garder_noms:
            noms = garder_noms
        else:
            noms = {nom for nom, _ in candidates}
        if len(noms) > 1:
            raise AmbiguousNomError(
                f"Inventaire: ambiguous disaron:nom for disaron:nom_old={old!r}: "
                + ", ".join(sorted(repr(n) for n in noms))
            )
        mapping[old] = next(iter(noms))
    return mapping


def load_input_rows(input_file: Path) -> list[dict[str, str]]:
    with input_file.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Input CSV has no headers: {input_file}")
        missing = [c for c in INPUT_FIELDS if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"Input CSV missing column(s): {', '.join(missing)}"
            )
        rows = list(reader)

    olds = [(row.get("disaron:nom_old") or "").strip() for row in rows]
    if any(not old for old in olds):
        raise ValueError("Input CSV has empty disaron:nom_old value(s)")
    _require_unique("input", "disaron:nom_old", olds)
    return rows


def process_rows(
    rows: list[dict[str, str]],
    nom_map: dict[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    out_rows: list[dict[str, str]] = []
    log_rows: list[dict[str, str]] = []

    for row in rows:
        old = (row.get("disaron:nom_old") or "").strip()
        nom = nom_map.get(old, old)

        cleaned_fields: dict[str, str] = {}
        for field in HTML_FIELDS:
            cleaned, removed = clean_html(row.get(field) or "")
            cleaned_fields[field] = cleaned
            for snippet in removed:
                log_rows.append(
                    {
                        "disaron:nom_old": old,
                        "disaron:nom": nom,
                        "field": field,
                        "removed_html": snippet,
                    }
                )

        out_rows.append(
            {
                "disaron:nom": nom,
                "disaron:nom_old": old,
                "has_html_principal": _bool_cell(
                    bool(cleaned_fields["html_principal"].strip())
                ),
                "has_html_secondaire": _bool_cell(
                    bool(cleaned_fields["html_secondaire"].strip())
                ),
                "has_notes": (row.get("has_notes") or "").strip(),
                "html_principal": cleaned_fields["html_principal"],
                "html_secondaire": cleaned_fields["html_secondaire"],
                "notes": row.get("notes") or "",
            }
        )

    _require_unique(
        "output",
        "disaron:nom_old",
        [r["disaron:nom_old"] for r in out_rows],
    )
    _require_unique(
        "output",
        "disaron:nom",
        [r["disaron:nom"] for r in out_rows],
    )
    return out_rows, log_rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            lineterminator="\r\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        default="metadata_editor/data/2026-05-18_Disarons_raw_text_fields.csv",
        help="Raw text-fields CSV from get_raw_text_fields.",
    )
    parser.add_argument(
        "--inventaire-file",
        default="2026-06-02_Inventaire-Nuxeo-v18.csv",
        help="Inventaire CSV used to resolve disaron:nom.",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help=(
            "Output CSV path. Default: "
            "metadata_editor/output/<timestamp>_clean_text_fields.csv"
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Change log CSV path. Default: "
            "metadata_editor/output/<timestamp>_clean_text_fields_log.csv"
        ),
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).resolve()
    inventaire_file = Path(args.inventaire_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    if not inventaire_file.exists():
        raise FileNotFoundError(f"Inventaire file not found: {inventaire_file}")

    prefix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if args.output_file:
        output_file = Path(args.output_file).resolve()
    else:
        output_file = (
            Path("metadata_editor/output").resolve()
            / f"{prefix}_clean_text_fields.csv"
        )
    if args.log_file:
        log_file = Path(args.log_file).resolve()
    else:
        log_file = (
            Path("metadata_editor/output").resolve()
            / f"{prefix}_clean_text_fields_log.csv"
        )

    nom_map = load_inventaire_nom_map(inventaire_file)
    rows = load_input_rows(input_file)
    out_rows, log_rows = process_rows(rows, nom_map)

    _write_csv(output_file, OUTPUT_FIELDS, out_rows)
    _write_csv(log_file, LOG_FIELDS, log_rows)

    print(f"Wrote {output_file} ({len(out_rows)} rows)")
    print(f"Wrote {log_file} ({len(log_rows)} removals)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
