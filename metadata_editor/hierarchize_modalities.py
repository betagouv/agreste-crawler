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

Exception: ``L_0018`` / ``Produit`` uses the curated override CSV
(``2026-05-18_Disarons_modalities_hierarchized_L_0018.csv``) instead of
auto-detection. Other L_0018 nomenclatures still use the normal path.

``L_0017`` underscore depths use ``underscores // 2 + 1`` (double-underscore
steps map to consecutive levels).

Output columns: ``disaron:nom``, ``disaron:nom_old``, ``nomenclature``,
``isHierarchical``, ``metadonnee_modalite``, ``codes``, ``level1`` … ``levelN``.
Sorted by ``disaron:nom_old``, ``nomenclature``, ``codes`` (numeric dotted
order: ``1,2,…,9,10`` and ``1.9,1.10``). CRLF endings.

``disaron:nom`` is resolved from ``disaron:nom_old`` via
``disaron_mapper`` (inventaire + dedicated mapping CSV). Unmapped names keep
``disaron:nom = disaron:nom_old``.

Defaults:
- ``--input-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities.csv``
- ``--l0018-override-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities_hierarchized_L_0018.csv``
- ``--disaron-mapping-files``: Inventaire v19 + ``2026-10-28_data_disaron_old_new_mapping.csv``
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

from disaron_mapper import (
    DEFAULT_DISARON_MAPPING_FILES,
    load_disaron_nom_map,
    resolve_disaron_nom,
)

FIXED_FIELDNAMES = [
    "disaron:nom",
    "disaron:nom_old",
    "nomenclature",
    "isHierarchical",
    "metadonnee_modalite",
    "codes",
]

L0018_PRODUIT_KEY = ("L_0018", "Produit")


def _codes_sort_key(codes: str) -> tuple:
    """Numeric dotted-code order: 1,2,…9,10 and 1.9,1.10 (not lexical)."""
    if not codes:
        return ()
    parts: list[tuple[int, object]] = []
    for part in codes.split("."):
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            parts.append((0, int(part)))
        else:
            # Non-numeric segments sort after ints at the same depth.
            parts.append((1, part))
    return tuple(parts)


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


def _path_from_override_row(row: dict[str, str]) -> list[str]:
    path: list[str] = []
    i = 1
    while f"level{i}" in row:
        value = row.get(f"level{i}") or ""
        if value == "" and not any(
            (row.get(f"level{j}") or "") for j in range(i + 1, i + 20)
        ):
            break
        path.append(value)
        i += 1
    # Trim trailing empties
    while path and path[-1] == "":
        path.pop()
    return path or [""]


def load_l0018_override(path: Path) -> list[dict[str, str]]:
    """Load curated L_0018 / Produit hierarchy rows."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [
        r
        for r in rows
        if r.get("disaron:nom") == "L_0018" and r.get("nomenclature") == "Produit"
    ]


def hierarchize_rows(
    rows: Iterable[dict[str, str]],
    l0018_override: list[dict[str, str]] | None = None,
    nom_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], int]:
    mapping = nom_map or {}
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["disaron:nom"], row["metadonnee_nom"])].append(row)

    pending: list[tuple[str, str, str, str, str, list[str]]] = []
    max_depth = 1
    for (disaron_nom, metadonnee_nom), group in groups.items():
        if (
            (disaron_nom, metadonnee_nom) == L0018_PRODUIT_KEY
            and l0018_override is not None
        ):
            for row in l0018_override:
                path = _path_from_override_row(row)
                max_depth = max(max_depth, len(path))
                pending.append(
                    (
                        row.get("disaron:nom", disaron_nom),
                        row.get("nomenclature", metadonnee_nom),
                        row.get("isHierarchical", "True"),
                        row["metadonnee_modalite"],
                        row.get("codes", ""),
                        path,
                    )
                )
            continue

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
        disaron_nom_old,
        nomenclature,
        is_hierarchical,
        modalite,
        codes,
        path,
    ) in pending:
        record = {
            "disaron:nom": resolve_disaron_nom(disaron_nom_old, mapping),
            "disaron:nom_old": disaron_nom_old,
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
            r["disaron:nom_old"],
            r["nomenclature"],
            _codes_sort_key(r["codes"]),
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
        "--l0018-override-file",
        default=(
            "metadata_editor/data/2026-05-18_Disarons_modalities_hierarchized_L_0018.csv"
        ),
        help="Curated hierarchy for L_0018 / Produit.",
    )
    parser.add_argument(
        "--disaron-mapping-files",
        nargs="+",
        default=list(DEFAULT_DISARON_MAPPING_FILES),
        help=(
            "CSVs with disaron:nom_old → disaron:nom "
            f"(default: {DEFAULT_DISARON_MAPPING_FILES!r})."
        ),
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

    override_file = Path(args.l0018_override_file).resolve()
    if not override_file.exists():
        raise FileNotFoundError(f"L_0018 override file not found: {override_file}")

    mapping_files = [Path(p).resolve() for p in args.disaron_mapping_files]
    for mapping_file in mapping_files:
        if not mapping_file.exists():
            raise FileNotFoundError(
                f"Disaron mapping file not found: {mapping_file}"
            )

    output_file = Path(args.output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with input_file.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    nom_map = load_disaron_nom_map(mapping_files)
    l0018_override = load_l0018_override(override_file)
    finalized, max_depth = hierarchize_rows(rows, l0018_override, nom_map)
    fieldnames = FIXED_FIELDNAMES + [f"level{i}" for i in range(1, max_depth + 1)]

    with output_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(finalized)

    print(f"Wrote {output_file} ({len(finalized)} rows, {max_depth} levels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
