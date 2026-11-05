"""Load and resolve ``disaron:nom_old`` → ``disaron:nom`` mappings.

Nuxeo Disaron dumps and older extracts often identify documents by an old
identifier (``disaron:nom_old`` / the JSON ``disaron:nom`` at dump time). The
CMS and inventaire use a cleaned identifier (``disaron:nom``). This module
builds that old→new map from one or more CSVs and resolves a nom for callers.

Default sources (``DEFAULT_DISARON_MAPPING_FILES``), merged in order so later
files override earlier ones for the same ``nom_old``:

1. Inventaire Nuxeo (``Garder/Exclure``-aware) — primary curated mapping.
2. ``2026-10-28_data_disaron_old_new_mapping.csv`` — extra renames not in
   inventaire.

CSV rules (``load_old_new_nom_map``):

- Requires columns ``disaron:nom_old`` and ``disaron:nom`` (values stripped).
- Rows with a blank old or new nom are ignored.
- If ``Garder/Exclure`` is present, ``Garder`` rows are preferred for a given
  old; if none are ``Garder``, all candidates are considered.
- More than one distinct new nom for the same old after that filter raises
  ``AmbiguousNomError``.

``resolve_disaron_nom`` returns the mapped nom, or the stripped input when
there is no mapping entry (identity fallback).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

DEFAULT_DISARON_MAPPING_FILES = [
    "2026-11-05_Inventaire-Nuxeo-v19.csv",
    "2026-10-28_data_disaron_old_new_mapping.csv",
]


class AmbiguousNomError(ValueError):
    """Raised when a mapping CSV yields more than one disaron:nom for a nom_old."""


def load_old_new_nom_map(path: Path | str) -> dict[str, str]:
    """Map stripped nom_old → stripped nom from a CSV.

    Requires ``disaron:nom`` and ``disaron:nom_old``. If ``Garder/Exclure`` is
    present, rows with ``Garder`` are preferred. Crashes if more than one
    distinct nom remains for a given old after that preference.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Disaron mapping file not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Disaron mapping CSV has no headers: {csv_path}")
        required = {"disaron:nom", "disaron:nom_old"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Disaron mapping CSV missing column(s): "
                f"{', '.join(sorted(missing))}: {csv_path}"
            )
        has_garder = "Garder/Exclure" in reader.fieldnames
        rows = list(reader)

    by_old: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        old = (row.get("disaron:nom_old") or "").strip()
        nom = (row.get("disaron:nom") or "").strip()
        if not old or not nom:
            continue
        garder = (row.get("Garder/Exclure") or "").strip() if has_garder else ""
        by_old[old].append((nom, garder))

    mapping: dict[str, str] = {}
    for old, candidates in by_old.items():
        if has_garder:
            garder_noms = {nom for nom, garder in candidates if garder == "Garder"}
            noms = garder_noms if garder_noms else {nom for nom, _ in candidates}
        else:
            noms = {nom for nom, _ in candidates}
        if len(noms) > 1:
            raise AmbiguousNomError(
                f"{csv_path.name}: ambiguous disaron:nom for "
                f"disaron:nom_old={old!r}: "
                + ", ".join(sorted(repr(n) for n in noms))
            )
        mapping[old] = next(iter(noms))
    return mapping


def load_inventaire_nom_map(inventaire_file: Path | str) -> dict[str, str]:
    """Map stripped nom_old → stripped nom from inventaire (Garder-aware)."""
    return load_old_new_nom_map(inventaire_file)


def load_disaron_nom_map(paths: Sequence[Path | str]) -> dict[str, str]:
    """Merge old→new maps from ``paths`` in order; later files override earlier."""
    merged: dict[str, str] = {}
    for path in paths:
        merged.update(load_old_new_nom_map(path))
    return merged


def resolve_disaron_nom(nom_old: str, mapping: dict[str, str]) -> str:
    """Return mapped nom, or stripped ``nom_old`` when unmapped."""
    old = nom_old.strip()
    return mapping.get(old, old)
