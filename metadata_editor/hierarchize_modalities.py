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

For underscore hierarchies, ``codes`` is generated as ``1``, ``1.1``, ``1.1.1``,
…. For dot and flat groups, ``codes`` copies ``metadonnee_modalite``.

Output columns: ``disaron:nom``, ``nomenclature``, ``isHierarchical``,
``metadonnee_modalite``, ``codes``, ``level1`` … ``levelN``. Sorted by
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
    "isHierarchical",
    "metadonnee_modalite",
    "codes",
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


def _underscore_depth(libelle: str, disaron_nom: str) -> int:
    leading = len(libelle) - len(libelle.lstrip("_"))
    # L_0017 uses double underscores per level (depths 1,3,5 → 1,2,3).
    if disaron_nom == "L_0017":
        depth = (leading // 2) + 1
    else:
        depth = leading + 1
    return depth if depth > 0 else 1


def _paths_and_codes_underscore(
    rows: list[dict[str, str]],
    disaron_nom: str,
) -> list[tuple[list[str], str]]:
    """Return (path, generated_code) per row, walking in metadonnee_modalite order."""
    ordered = sorted(rows, key=lambda r: r["metadonnee_modalite"])
    label_stack: list[str] = []
    code_stack: list[str] = []
    sibling_count: dict[str, int] = defaultdict(int)
    result_by_id: dict[int, tuple[list[str], str]] = {}

    for r in ordered:
        depth = _underscore_depth(r["libelle_modalite"], disaron_nom)
        label = _strip_leading_underscores(r["libelle_modalite"])

        if len(label_stack) >= depth:
            label_stack = label_stack[: depth - 1]
            code_stack = code_stack[: depth - 1]
        while len(label_stack) < depth - 1:
            label_stack.append("")
            code_stack.append("")

        parent_key = ".".join(code_stack) if code_stack else ""
        sibling_count[parent_key] += 1
        segment = str(sibling_count[parent_key])
        code = f"{parent_key}.{segment}" if parent_key else segment

        label_stack.append(label)
        code_stack.append(segment)
        result_by_id[id(r)] = (list(label_stack), code)

    return [result_by_id[id(r)] for r in rows]


def hierarchize_group(
    rows: list[dict[str, str]],
    disaron_nom: str,
) -> tuple[str, list[tuple[dict[str, str], list[str], str]]]:
    """Return (mode, list of (row, path, codes))."""
    mode = _detect_mode(rows)
    if mode == "flat":
        return (
            mode,
            [
                (
                    r,
                    [_strip_leading_underscores(r["libelle_modalite"])],
                    r["metadonnee_modalite"],
                )
                for r in rows
            ],
        )
    if mode == "dot":
        by_code = {r["metadonnee_modalite"]: r for r in rows}
        return (
            mode,
            [
                (r, _path_dot(r, by_code), r["metadonnee_modalite"])
                for r in rows
            ],
        )
    paired = _paths_and_codes_underscore(rows, disaron_nom)
    return (
        mode,
        [(r, path, code) for r, (path, code) in zip(rows, paired)],
    )


def hierarchize_rows(
    rows: Iterable[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["disaron:nom"], row["metadonnee_nom"])].append(row)

    pending: list[tuple[str, str, str, str, str, list[str]]] = []
    max_depth = 1
    for (disaron_nom, metadonnee_nom), group in groups.items():
        mode, items = hierarchize_group(group, disaron_nom)
        is_hierarchical = "True" if mode != "flat" else "False"
        for row, path, codes in items:
            max_depth = max(max_depth, len(path))
            pending.append(
                (
                    disaron_nom,
                    metadonnee_nom,
                    is_hierarchical,
                    row["metadonnee_modalite"],
                    codes,
                    path,
                )
            )

    finalized: list[dict[str, str]] = []
    for (
        disaron_nom,
        nomenclature,
        is_hierarchical,
        modalite,
        codes,
        path,
    ) in pending:
        record = {
            "disaron:nom": disaron_nom,
            "nomenclature": nomenclature,
            "isHierarchical": is_hierarchical,
            "metadonnee_modalite": modalite,
            "codes": codes,
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
