#!/usr/bin/env python3
"""
Expand inventaire modalities into hierarchical level columns.

Reads ``metadata_editor/data/2026-05-18_Disarons_modalities.csv`` and writes
a hierarchized CSV. For each ``(disaron:nom, metadonnee_nom)`` group:

1. If any ``metadonnee_modalite`` contains ``.`` → hierarchy from dot codes
2. Else if any ``libelle_modalite`` starts with ``_`` → hierarchy from leading
   underscores (rows walked in ``metadonnee_modalite`` order)
3. Else → flat (``level1`` only)

Leading underscores are stripped from labels in the level columns.

Output columns: ``disaron:nom``, ``nomenclature``, ``metadonnee_modalite``,
``level1`` … ``levelN`` (as many as the global max depth). Sorted by
``disaron:nom``, ``nomenclature``, ``metadonnee_modalite``. CRLF endings.

Defaults:
- ``--input-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities.csv``
- ``--output-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities_hierarchized.csv``

Usage:
    just hierarchize_modalities
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

FIXED_FIELDNAMES = [
    "disaron:nom",
    "nomenclature",
    "metadonnee_modalite",
]


def _strip_leading_underscores(label: str) -> str:
    return label.lstrip("_")


def _detect_mode(rows: list[dict[str, str]]) -> str:
    if any("." in r["metadonnee_modalite"] for r in rows):
        return "dot"
    if any(r["libelle_modalite"].startswith("_") for r in rows):
        return "underscore"
    return "flat"


def _path_dot(row: dict[str, str], by_code: dict[str, dict[str, str]]) -> list[str]:
    code = row["metadonnee_modalite"]
    if not code:
        return [_strip_leading_underscores(row["libelle_modalite"])]
    segments = code.split(".")
    path: list[str] = []
    for i in range(len(segments)):
        prefix = ".".join(segments[: i + 1])
        ancestor = by_code.get(prefix)
        if ancestor is not None:
            path.append(_strip_leading_underscores(ancestor["libelle_modalite"]))
        elif i == len(segments) - 1:
            path.append(_strip_leading_underscores(row["libelle_modalite"]))
        else:
            path.append("")
    return path


def _paths_underscore(rows: list[dict[str, str]]) -> list[list[str]]:
    """Return one path per row, walking in metadonnee_modalite order."""
    ordered = sorted(rows, key=lambda r: r["metadonnee_modalite"])
    stack: list[str] = []
    paths_by_id: dict[int, list[str]] = {}
    for r in ordered:
        depth = len(r["libelle_modalite"]) - len(r["libelle_modalite"].lstrip("_")) + 1
        label = _strip_leading_underscores(r["libelle_modalite"])
        if depth <= 0:
            depth = 1
        if len(stack) >= depth:
            stack = stack[: depth - 1]
        while len(stack) < depth - 1:
            stack.append("")
        stack.append(label)
        paths_by_id[id(r)] = list(stack)
    return [paths_by_id[id(r)] for r in rows]


def hierarchize_group(rows: list[dict[str, str]]) -> list[tuple[dict[str, str], list[str]]]:
    mode = _detect_mode(rows)
    if mode == "flat":
        return [
            (r, [_strip_leading_underscores(r["libelle_modalite"])]) for r in rows
        ]
    if mode == "dot":
        by_code = {r["metadonnee_modalite"]: r for r in rows}
        return [(r, _path_dot(r, by_code)) for r in rows]
    paths = _paths_underscore(rows)
    return list(zip(rows, paths))


def hierarchize_rows(
    rows: Iterable[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["disaron:nom"], row["metadonnee_nom"])].append(row)

    pending: list[tuple[str, str, str, list[str]]] = []
    max_depth = 1
    for (disaron_nom, metadonnee_nom), group in groups.items():
        for row, path in hierarchize_group(group):
            max_depth = max(max_depth, len(path))
            pending.append(
                (
                    disaron_nom,
                    metadonnee_nom,
                    row["metadonnee_modalite"],
                    path,
                )
            )

    finalized: list[dict[str, str]] = []
    for disaron_nom, nomenclature, modalite, path in pending:
        record = {
            "disaron:nom": disaron_nom,
            "nomenclature": nomenclature,
            "metadonnee_modalite": modalite,
        }
        for i in range(1, max_depth + 1):
            record[f"level{i}"] = path[i - 1] if i <= len(path) else ""
        finalized.append(record)

    finalized.sort(
        key=lambda r: (
            r["disaron:nom"],
            r["nomenclature"],
            r["metadonnee_modalite"],
        )
    )
    return finalized, max_depth


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        default="metadata_editor/data/2026-05-18_Disarons_modalities.csv",
        help="Flat modalities CSV from extract_modalities.",
    )
    parser.add_argument(
        "--output-file",
        default="metadata_editor/data/2026-05-18_Disarons_modalities_hierarchized.csv",
        help="Hierarchized output CSV path.",
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    output_file = Path(args.output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with input_file.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    finalized, max_depth = hierarchize_rows(rows)
    fieldnames = FIXED_FIELDNAMES + [f"level{i}" for i in range(1, max_depth + 1)]

    with output_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(finalized)

    print(f"Wrote {output_file} ({len(finalized)} rows, {max_depth} levels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
