#!/usr/bin/env python
"""
Set PublicationPage ``nomenclatures`` from the hierarchized modalities CSV.

Reads ``disaron:nom``, ``nomenclature``, ``codes``, ``metadonnee_modalite``,
and ``level1``…``levelN``. For each disaron, builds a StreamField value of
``nomenclature`` blocks:

- ``name`` ← ``nomenclature``
- modality rows: ``code`` ← ``codes``, ``name`` ← ``level{depth}``,
  ``code_old`` ← ``metadonnee_modalite``

Replaces the whole ``nomenclatures`` field per matching page (does not merge).
Does not touch the ``modalities`` M2M taxonomy.

Walks unique ``disaron:nom`` values from the CSV (not every CMS page). Missing
or duplicate pages are logged as failures. Identical stream content is a noop.

``--parent-id`` from shared args is unused.

Defaults:
- ``--data-file``: ``metadata_editor/data/2026-05-18_Disarons_modalities_hierarchized.csv``
- ``--failures-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_nomenclatures_failures.csv``
- ``--successes-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_nomenclatures_successes.csv``
- ``--noops-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_nomenclatures_noops.csv``

Usage:
    just set_nomenclatures \\
        --wagtail-project-root ../agreste

    just set_nomenclatures \\
        --wagtail-project-root ../agreste \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_noops_file,
    resolve_successes_file,
)

DEFAULT_DATA_FILE = (
    "metadata_editor/data/2026-05-18_Disarons_modalities_hierarchized.csv"
)
SUCCESS_FIELDNAMES = [
    "pageId",
    "disaron_nom",
    "value",
    "info",
    "title",
]
FAILURE_FIELDNAMES = ["pageId", "disaron_nom", "error"]
NOOP_FIELDNAMES = ["pageId", "disaron_nom", "info"]

ModalityRow = dict[str, str]
NomenclatureBlock = dict[str, Any]
NomenclaturesByNom = dict[str, list[NomenclatureBlock]]


def _make_parent_id_optional(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if "--parent-id" in getattr(action, "option_strings", []):
            action.required = False
            action.default = None
            action.help = (
                "Unused by this script; PublicationPages are resolved under "
                "any PublicationIndexPage."
            )
            return
    raise RuntimeError("--parent-id argument not found on parser")


def _set_data_file_default(parser: argparse.ArgumentParser, default: str) -> None:
    for action in parser._actions:
        if "--data-file" in getattr(action, "option_strings", []):
            action.required = False
            action.default = default
            action.help = (
                "Hierarchized modalities CSV with disaron:nom, nomenclature, "
                f"codes, metadonnee_modalite, levels (default: {default!r})."
            )
            return
    raise RuntimeError("--data-file argument not found on parser")


def _level_columns(fieldnames: list[str] | None) -> list[str]:
    if not fieldnames:
        return []
    cols: list[tuple[int, str]] = []
    for name in fieldnames:
        if name.startswith("level") and name[5:].isdigit():
            cols.append((int(name[5:]), name))
    return [name for _, name in sorted(cols)]


def _modality_name(row: dict[str, str], level_cols: list[str]) -> str:
    codes = (row.get("codes") or "").strip()
    depth = len(codes.split(".")) if codes else 1
    level_key = f"level{depth}"
    name = (row.get(level_key) or "").strip()
    if name:
        return name
    deepest = ""
    for col in level_cols:
        value = (row.get(col) or "").strip()
        if value:
            deepest = value
    return deepest


def _modality_row(row: dict[str, str], level_cols: list[str]) -> ModalityRow:
    return {
        "code": (row.get("codes") or "").strip(),
        "code_old": (row.get("metadonnee_modalite") or "").strip(),
        "name": _modality_name(row, level_cols),
    }


def load_nomenclatures_by_nom(data_file: str) -> NomenclaturesByNom:
    """
    Return disaron:nom → list of nomenclature stream blocks (type/value dicts).

    Within each disaron, nomenclature order follows first appearance in the CSV.
    Modality rows keep CSV order (already numeric-code sorted by hierarchize).
    """
    csv_path = Path(data_file)
    if not csv_path.is_absolute():
        csv_path = (Path.cwd() / csv_path).resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    required = {
        "disaron:nom",
        "nomenclature",
        "codes",
        "metadonnee_modalite",
    }
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Data CSV has no headers: {csv_path}")
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                "Data CSV missing column(s): "
                + ", ".join(sorted(missing))
            )
        level_cols = _level_columns(list(reader.fieldnames))
        if not level_cols:
            raise ValueError("Data CSV has no levelN columns.")

        # nom -> OrderedDict[nomenclature_name -> list[modality]]
        grouped: dict[str, OrderedDict[str, list[ModalityRow]]] = defaultdict(
            OrderedDict
        )
        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            nomenclature = (row.get("nomenclature") or "").strip()
            if not disaron_nom or not nomenclature:
                continue
            modality = _modality_row(row, level_cols)
            if not modality["code"] or not modality["name"]:
                raise ValueError(
                    f"Invalid modality row for {disaron_nom!r} / "
                    f"{nomenclature!r}: code={modality['code']!r} "
                    f"name={modality['name']!r}"
                )
            by_nc = grouped[disaron_nom]
            if nomenclature not in by_nc:
                by_nc[nomenclature] = []
            by_nc[nomenclature].append(modality)

    out: NomenclaturesByNom = {}
    for disaron_nom, by_nc in grouped.items():
        out[disaron_nom] = [
            {
                "type": "nomenclature",
                "value": {
                    "name": name,
                    "modalities": modalities,
                },
            }
            for name, modalities in by_nc.items()
        ]
    return out


def resolve_all_publication_pages_qs():
    pages = PublicationPage.objects.none()
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages = pages | PublicationPage.objects.child_of(index)
    return pages.distinct().order_by("id")


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _modalities_from_table(table: Any) -> list[ModalityRow]:
    """Normalize a TypedTable / list / dict modalities value to row dicts."""
    if table is None:
        return []

    if isinstance(table, list):
        rows: list[ModalityRow] = []
        for item in table:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "code": _text(item.get("code")),
                    "code_old": _text(item.get("code_old")),
                    "name": _text(item.get("name")),
                }
            )
        return rows

    columns = getattr(table, "columns", None)
    row_data = getattr(table, "row_data", None)
    if columns is not None and row_data is not None:
        indexes: dict[str, int] = {}
        for index, column in enumerate(columns):
            block = column.get("block") if isinstance(column, dict) else None
            block_name = getattr(block, "name", None)
            if isinstance(column, dict) and not block_name:
                block_name = column.get("name") or column.get("heading")
            if block_name:
                indexes.setdefault(str(block_name), index)
        # Also map by heading text for robustness.
        for index, column in enumerate(columns):
            if not isinstance(column, dict):
                continue
            heading = column.get("heading")
            if isinstance(heading, str) and heading.strip():
                indexes.setdefault(heading.strip().lower(), index)

        code_i = indexes.get("code", indexes.get("Code", 0))
        name_i = indexes.get("name", indexes.get("Name", 1))
        code_old_i = indexes.get(
            "code_old",
            indexes.get("code_old".lower(), indexes.get("Code old", None)),
        )

        rows = []
        for row in row_data:
            values = row.get("values") if isinstance(row, dict) else None
            if not isinstance(values, list):
                continue
            code_old = ""
            if code_old_i is not None and code_old_i < len(values):
                code_old = _text(values[code_old_i])
            rows.append(
                {
                    "code": _text(values[code_i]) if code_i < len(values) else "",
                    "code_old": code_old,
                    "name": _text(values[name_i]) if name_i < len(values) else "",
                }
            )
        return rows

    if isinstance(table, dict) and "row_data" in table:
        # Recurse via a simple namespace object? Just parse dict form.
        class _T:
            pass

        t = _T()
        t.columns = table.get("columns")  # type: ignore[attr-defined]
        t.row_data = table.get("row_data")  # type: ignore[attr-defined]
        return _modalities_from_table(t)

    return []


def serialize_nomenclatures(stream_value: Any) -> list[tuple[str, list[ModalityRow]]]:
    """Return comparable [(nomenclature_name, modalities), ...] from a StreamField."""
    if stream_value is None:
        return []

    items: list[Any]
    if hasattr(stream_value, "raw_data"):
        items = list(stream_value.raw_data)
    elif hasattr(stream_value, "stream_data"):
        items = list(stream_value.stream_data)
    elif isinstance(stream_value, list):
        items = stream_value
    else:
        try:
            items = list(stream_value)
        except TypeError:
            return []

    out: list[tuple[str, list[ModalityRow]]] = []
    for item in items:
        if isinstance(item, dict):
            block_type = item.get("type")
            value = item.get("value") or {}
        else:
            block_type = getattr(item, "block_type", None)
            value = getattr(item, "value", None)

        if block_type not in (None, "nomenclature"):
            # Still accept unnamed struct-like values from StreamChild.
            pass
        if not isinstance(value, dict):
            # StreamChild.value may be a StructValue
            try:
                name = _text(value["name"])  # type: ignore[index]
                modalities = _modalities_from_table(value["modalities"])  # type: ignore[index]
            except Exception:
                continue
            out.append((name, modalities))
            continue

        name = _text(value.get("name"))
        modalities = _modalities_from_table(value.get("modalities"))
        out.append((name, modalities))
    return out


def desired_comparable(
    blocks: list[NomenclatureBlock],
) -> list[tuple[str, list[ModalityRow]]]:
    out: list[tuple[str, list[ModalityRow]]] = []
    for block in blocks:
        value = block.get("value") or {}
        name = _text(value.get("name"))
        modalities = [
            {
                "code": _text(m.get("code")),
                "code_old": _text(m.get("code_old")),
                "name": _text(m.get("name")),
            }
            for m in (value.get("modalities") or [])
        ]
        out.append((name, modalities))
    return out


def _apply_nomenclatures(
    page: PublicationPage,
    blocks: list[NomenclatureBlock],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    desired = desired_comparable(blocks)
    current = serialize_nomenclatures(page.nomenclatures)
    names = [name for name, _ in desired]
    summary = f"nomenclatures={len(desired)} ({'|'.join(names)})"

    if current == desired:
        return {
            "noop": True,
            "info": "noop: nomenclatures already match CSV",
            "value": summary,
        }

    if dry_run:
        return {
            "value": summary,
            "info": f"Would set nomenclatures ({len(desired)} block(s))",
        }

    page.nomenclatures = blocks
    revision = page.save_revision()
    if page.live:
        revision.publish()

    return {
        "value": summary,
        "info": f"set nomenclatures ({len(desired)} block(s))",
    }


def run_update(
    *,
    by_nom: NomenclaturesByNom,
    pages_by_nom: dict[str, PublicationPage],
    duplicate_noms: set[str],
    failures_file: str,
    successes_file: str,
    noops_file: str,
    dry_run: bool,
) -> int:
    noms = sorted(by_nom.keys())
    total = len(noms)
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}About to update nomenclatures on up to {total} "
        f"PublicationPage object(s) from the modalities CSV. "
        f"Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    noop_count = 0
    failures_count = 0

    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    successes_path = Path(successes_file)
    successes_path.parent.mkdir(parents=True, exist_ok=True)
    noops_path = Path(noops_file)
    noops_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as failures_f,
        successes_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as successes_f,
        noops_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as noops_f,
    ):
        failures_writer = csv.DictWriter(
            failures_f, fieldnames=FAILURE_FIELDNAMES
        )
        failures_writer.writeheader()
        _flush_failures_file(failures_f)

        successes_writer = csv.DictWriter(
            successes_f, fieldnames=SUCCESS_FIELDNAMES
        )
        successes_writer.writeheader()
        _flush_failures_file(successes_f)

        noops_writer = csv.DictWriter(noops_f, fieldnames=NOOP_FIELDNAMES)
        noops_writer.writeheader()
        _flush_failures_file(noops_f)

        def _fail(page_id: str, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            failures_writer.writerow(
                {
                    "pageId": page_id,
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            _flush_failures_file(failures_f)
            print(
                f"[{updated + noop_count + failures_count}/{total}] "
                f"Skipped id={page_id}: {error}",
                flush=True,
            )

        def _noop(page_id: int, disaron_nom: str, info: str) -> None:
            nonlocal noop_count
            noop_count += 1
            noops_writer.writerow(
                {
                    "pageId": str(page_id),
                    "disaron_nom": disaron_nom,
                    "info": info,
                }
            )
            _flush_failures_file(noops_f)
            print(
                f"[{updated + noop_count + failures_count}/{total}] "
                f"Noop id={page_id} disaron_nom={disaron_nom!r}: {info}",
                flush=True,
            )

        for disaron_nom in noms:
            if disaron_nom in duplicate_noms:
                _fail(
                    "",
                    disaron_nom,
                    "multiple PublicationPages share this disaron_id",
                )
                continue

            page = pages_by_nom.get(disaron_nom)
            if page is None:
                _fail("", disaron_nom, "no PublicationPage with this disaron_id")
                continue

            try:
                result = _apply_nomenclatures(
                    page,
                    by_nom[disaron_nom],
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - log and continue
                _fail(str(page.id), disaron_nom, str(exc))
                continue

            if result.get("noop"):
                _noop(page.id, disaron_nom, str(result.get("info") or "noop"))
                continue

            updated += 1
            successes_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "value": result.get("value") or "",
                    "info": result.get("info") or "",
                    "title": page.title,
                }
            )
            _flush_failures_file(successes_f)
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated + noop_count + failures_count}/{total}] "
                f"{action} id={page.id} disaron_nom={disaron_nom!r} "
                f"{result.get('value')!r} title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    print(f"Wrote {noop_count} noop row(s) to {noops_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} PublicationPage object(s); "
        f"noop {noop_count}; skipped {failures_count}."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    _make_parent_id_optional(parser)
    _set_data_file_default(parser, DEFAULT_DATA_FILE)
    args = parser.parse_args()

    by_nom = load_nomenclatures_by_nom(args.data_file)

    pages_by_nom: dict[str, PublicationPage] = {}
    duplicate_noms: set[str] = set()
    pages = resolve_all_publication_pages_qs().filter(
        disaron_id__in=list(by_nom.keys())
    )
    for page in pages:
        disaron_nom = find_disaron_nom(page)
        if not disaron_nom:
            continue
        if disaron_nom in pages_by_nom:
            duplicate_noms.add(disaron_nom)
            continue
        pages_by_nom[disaron_nom] = page

    failures_file = resolve_failures_file(
        args.failures_file, "nomenclatures_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "nomenclatures_successes"
    )
    noops_file = resolve_noops_file(args.noops_file, "nomenclatures_noops")

    return run_update(
        by_nom=by_nom,
        pages_by_nom=pages_by_nom,
        duplicate_noms=duplicate_noms,
        failures_file=failures_file,
        successes_file=successes_file,
        noops_file=noops_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
