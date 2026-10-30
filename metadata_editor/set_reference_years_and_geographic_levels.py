#!/usr/bin/env python
"""
Set PublicationPage ``reference_years`` and ``geographic_levels``.

Walks PublicationPages under any PublicationIndexPage. Looks up each page's
``disaron_id`` in one or more input CSVs (``disaron:nom``) and reads:

- ``disaron:annees_reference`` → ``ReferenceYear`` objects (``|``-separated)
- ``disaron:niveau_geographique`` → ``GeographicLevel`` objects (``|``-separated)

Reuse existing taxonomy objects by slug; create when missing:

- ReferenceYear: ``name=<token>``, ``slug=annee-<token>``, ``parent=None``
- GeographicLevel: ``slug=niveau-geographique-<token>`` (code kept as-is,
  e.g. ``niveau-geographique-DEP``), ``name=<label>`` from the geo labels
  CSV, ``parent=None``

Rules:

- Same ``disaron:nom`` in more than one input file → failure, no update.
- ``disaron:nom`` in no input file → failure.
- Empty CSV field → leave the existing M2M unchanged (never clear).
- Nonempty field → ``.set(...)`` that M2M to the resolved objects, then
  save revision (+ publish if live).

``--parent-id`` and ``--data-file`` from shared args are unused.

Defaults:
- ``--data-files``:
  - ``2026-09-30_dump_tableaux_de_colin_publies.csv``
  - ``2026-10-12_series_longues.csv``
  - ``2026-06-02_Inventaire-Nuxeo-v18.csv``
- ``--geo-labels-file``: ``metadata_editor/data/2026-10-30_niveaux_geographiques.csv``
- ``--failures-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_ref_years_geo_failures.csv``
- ``--successes-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_ref_years_geo_successes.csv``
- ``--noops-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_ref_years_geo_noops.csv``

Usage:
    just set_reference_years_and_geographic_levels \\
        --wagtail-project-root ../agreste

    just set_reference_years_and_geographic_levels \\
        --wagtail-project-root ../agreste \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from publications.models import (  # noqa: E402
    GeographicLevel,
    PublicationIndexPage,
    PublicationPage,
    ReferenceYear,
)

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_noops_file,
    resolve_successes_file,
)

YEARS_COLUMN = "disaron:annees_reference"
GEO_COLUMN = "disaron:niveau_geographique"
GEO_CODE_COLUMN = "disaron:niveau_geographique"
GEO_LABEL_COLUMN = "label"
DEFAULT_DATA_FILES = [
    "2026-09-30_dump_tableaux_de_colin_publies.csv",
    "2026-10-12_series_longues.csv",
    "2026-06-02_Inventaire-Nuxeo-v18.csv",
]
DEFAULT_GEO_LABELS_FILE = (
    "metadata_editor/data/2026-10-30_niveaux_geographiques.csv"
)
SUCCESS_FIELDNAMES = [
    "pageId",
    "disaron_nom",
    "reference_years",
    "geographic_levels",
    "info",
    "title",
]
FAILURE_FIELDNAMES = ["pageId", "disaron_nom", "error"]
NOOP_FIELDNAMES = ["pageId", "disaron_nom", "info"]


@dataclass(frozen=True)
class CsvFields:
    years_raw: str
    geo_raw: str
    source_file: str


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


def _make_data_file_optional(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if "--data-file" in getattr(action, "option_strings", []):
            action.required = False
            action.default = ""
            action.help = (
                "Unused by this script; use --data-files instead."
            )
            return
    raise RuntimeError("--data-file argument not found on parser")


def _parse_pipe_tokens(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split("|"):
        token = part.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _load_geo_labels(labels_file: str) -> dict[str, str]:
    csv_path = Path(labels_file)
    if not csv_path.is_absolute():
        csv_path = (Path.cwd() / csv_path).resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"Geo labels file not found: {csv_path}")

    labels: dict[str, str] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Geo labels CSV has no headers: {csv_path}")
        required = {GEO_CODE_COLUMN, GEO_LABEL_COLUMN}
        if not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                "Geo labels CSV must contain columns "
                f"{GEO_CODE_COLUMN!r} and {GEO_LABEL_COLUMN!r}."
            )
        for row in reader:
            code = (row.get(GEO_CODE_COLUMN) or "").strip()
            label = (row.get(GEO_LABEL_COLUMN) or "").strip()
            if code and label:
                labels[code] = label

    if not labels:
        raise ValueError("Geo labels CSV has no usable rows.")
    return labels


def load_input_files(
    data_files: list[str],
) -> tuple[dict[str, CsvFields], set[str]]:
    """
    Return (unique_rows_by_nom, duplicate_noms).

    A nom that appears in more than one file is listed in ``duplicate_noms``
    and omitted from the unique map.
    """
    by_nom: dict[str, CsvFields] = {}
    duplicate_noms: set[str] = set()
    seen_in_file: dict[str, str] = {}

    for data_file in data_files:
        csv_path = Path(data_file)
        if not csv_path.exists():
            raise FileNotFoundError(f"Data file not found: {csv_path}")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError(f"Data CSV has no headers: {csv_path}")
            fieldnames = set(reader.fieldnames)
            if "disaron:nom" not in fieldnames:
                raise ValueError(
                    f"Data CSV must contain 'disaron:nom' column: {csv_path}"
                )
            if YEARS_COLUMN not in fieldnames:
                raise ValueError(
                    f"Data CSV must contain {YEARS_COLUMN!r} column: {csv_path}"
                )
            if GEO_COLUMN not in fieldnames:
                raise ValueError(
                    f"Data CSV must contain {GEO_COLUMN!r} column: {csv_path}"
                )
            for row in reader:
                disaron_nom = (row.get("disaron:nom") or "").strip()
                if not disaron_nom:
                    continue
                if disaron_nom in duplicate_noms:
                    continue
                if disaron_nom in seen_in_file:
                    if seen_in_file[disaron_nom] != str(csv_path):
                        duplicate_noms.add(disaron_nom)
                        by_nom.pop(disaron_nom, None)
                    # Same file: keep first row for that nom.
                    continue
                seen_in_file[disaron_nom] = str(csv_path)
                by_nom[disaron_nom] = CsvFields(
                    years_raw=(row.get(YEARS_COLUMN) or "").strip(),
                    geo_raw=(row.get(GEO_COLUMN) or "").strip(),
                    source_file=str(csv_path),
                )

    return by_nom, duplicate_noms


def resolve_all_publication_pages_qs():
    pages = PublicationPage.objects.none()
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages = pages | PublicationPage.objects.child_of(index)
    return pages.distinct().order_by("id").prefetch_related(
        "reference_years",
        "geographic_levels",
    )


def _year_slug(token: str) -> str:
    return f"annee-{token}"


def _geo_slug(token: str) -> str:
    return f"niveau-geographique-{token}"


def _get_or_create_taxonomy(
    model: type[Any],
    *,
    slug: str,
    name: str,
    locale_id: int | None,
    dry_run: bool,
) -> tuple[Any | None, str]:
    existing = model.objects.filter(slug=slug)
    if locale_id is not None:
        localized = existing.filter(locale_id=locale_id).first()
        if localized is not None:
            return localized, f"reused {model.__name__} slug={slug!r}"
    obj = existing.first()
    if obj is not None:
        return obj, f"reused {model.__name__} slug={slug!r}"

    if dry_run:
        return None, f"would create {model.__name__} slug={slug!r} name={name!r}"

    obj = model.objects.create(
        slug=slug,
        name=name,
        parent=None,
        locale_id=locale_id,
    )
    return obj, f"created {model.__name__} slug={slug!r} name={name!r}"


def _apply_fields(
    page: PublicationPage,
    fields: CsvFields,
    *,
    geo_labels: dict[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    year_tokens = _parse_pipe_tokens(fields.years_raw)
    geo_tokens = _parse_pipe_tokens(fields.geo_raw)

    if not year_tokens and not geo_tokens:
        return {
            "noop": True,
            "info": "noop: both annees_reference and niveau_geographique empty",
            "value": "",
        }

    if geo_tokens:
        missing = [token for token in geo_tokens if not geo_labels.get(token)]
        if missing:
            raise ValueError(
                "unknown or unlabeled niveau_geographique code(s): "
                + ", ".join(repr(m) for m in missing)
            )

    info_parts: list[str] = []
    years_changed = False
    geo_changed = False
    year_objs: list[Any] = []
    geo_objs: list[Any] = []

    if year_tokens:
        desired_year_slugs = {_year_slug(token) for token in year_tokens}
        for token in year_tokens:
            obj, note = _get_or_create_taxonomy(
                ReferenceYear,
                slug=_year_slug(token),
                name=token,
                locale_id=page.locale_id,
                dry_run=dry_run,
            )
            info_parts.append(note)
            if obj is not None:
                year_objs.append(obj)
        current_year_slugs = set(
            page.reference_years.values_list("slug", flat=True)
        )
        years_changed = current_year_slugs != desired_year_slugs

    if geo_tokens:
        desired_geo_slugs = {_geo_slug(token) for token in geo_tokens}
        for token in geo_tokens:
            obj, note = _get_or_create_taxonomy(
                GeographicLevel,
                slug=_geo_slug(token),
                name=geo_labels[token],
                locale_id=page.locale_id,
                dry_run=dry_run,
            )
            info_parts.append(note)
            if obj is not None:
                geo_objs.append(obj)
        current_geo_slugs = set(
            page.geographic_levels.values_list("slug", flat=True)
        )
        geo_changed = current_geo_slugs != desired_geo_slugs

    if not years_changed and not geo_changed:
        return {
            "noop": True,
            "info": (
                "noop: reference_years/geographic_levels already match CSV. "
                + " ".join(info_parts)
            ).strip(),
            "value": (
                f"years={ '|'.join(year_tokens) } "
                f"geo={ '|'.join(geo_tokens) }"
            ),
        }

    if dry_run:
        return {
            "value": (
                f"years={ '|'.join(year_tokens) } "
                f"geo={ '|'.join(geo_tokens) }"
            ),
            "info": (
                f"Would set "
                f"{'reference_years ' if years_changed else ''}"
                f"{'geographic_levels ' if geo_changed else ''}"
                f"({' '.join(info_parts)})"
            ).strip(),
            "years": "|".join(year_tokens),
            "geo": "|".join(geo_tokens),
        }

    if years_changed:
        page.reference_years.set(year_objs)
    if geo_changed:
        page.geographic_levels.set(geo_objs)
    revision = page.save_revision()
    if page.live:
        revision.publish()

    return {
        "value": (
            f"years={ '|'.join(year_tokens) } "
            f"geo={ '|'.join(geo_tokens) }"
        ),
        "info": " ".join(info_parts),
        "years": "|".join(year_tokens),
        "geo": "|".join(geo_tokens),
    }


def run_update(
    *,
    pages,
    by_nom: dict[str, CsvFields],
    duplicate_noms: set[str],
    geo_labels: dict[str, str],
    failures_file: str,
    successes_file: str,
    noops_file: str,
    dry_run: bool,
) -> int:
    page_count = pages.count()
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}About to update reference_years/geographic_levels on "
        f"{page_count} PublicationPage object(s). Type 'yes' to confirm: "
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

        def _fail(page_id: int, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            failures_writer.writerow(
                {
                    "pageId": str(page_id),
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            _flush_failures_file(failures_f)
            print(
                f"[{updated + noop_count + failures_count}/{page_count}] "
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
                f"[{updated + noop_count + failures_count}/{page_count}] "
                f"Noop id={page_id} disaron_nom={disaron_nom!r}: {info}",
                flush=True,
            )

        for page in pages:
            disaron_nom = find_disaron_nom(page) or ""
            if not disaron_nom:
                _fail(page.id, "", "page has no disaron_id")
                continue
            if disaron_nom in duplicate_noms:
                _fail(
                    page.id,
                    disaron_nom,
                    "disaron_nom present in multiple input files",
                )
                continue
            if disaron_nom not in by_nom:
                _fail(
                    page.id,
                    disaron_nom,
                    "disaron_nom not found in any input file",
                )
                continue

            try:
                result = _apply_fields(
                    page,
                    by_nom[disaron_nom],
                    geo_labels=geo_labels,
                    dry_run=dry_run,
                )
            except Exception as exc:
                _fail(page.id, disaron_nom, f"apply failed: {exc}")
                continue

            if result.get("noop"):
                _noop(page.id, disaron_nom, str(result.get("info") or "noop"))
                continue

            updated += 1
            successes_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "reference_years": str(result.get("years") or ""),
                    "geographic_levels": str(result.get("geo") or ""),
                    "info": str(result.get("info") or ""),
                    "title": page.title,
                }
            )
            _flush_failures_file(successes_f)
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated + noop_count + failures_count}/{page_count}] "
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
    _make_data_file_optional(parser)
    parser.add_argument(
        "--data-files",
        nargs="+",
        default=list(DEFAULT_DATA_FILES),
        help=(
            "One or more CSVs with disaron:nom, "
            f"{YEARS_COLUMN}, and {GEO_COLUMN} "
            f"(default: {DEFAULT_DATA_FILES!r})."
        ),
    )
    parser.add_argument(
        "--geo-labels-file",
        type=str,
        default=DEFAULT_GEO_LABELS_FILE,
        help=(
            "CSV mapping niveau_geographique codes to labels "
            f"(default: {DEFAULT_GEO_LABELS_FILE!r})."
        ),
    )
    args = parser.parse_args()

    by_nom, duplicate_noms = load_input_files(args.data_files)
    geo_labels = _load_geo_labels(args.geo_labels_file)
    if duplicate_noms:
        print(
            f"Warning: {len(duplicate_noms)} disaron:nom value(s) appear in "
            "multiple input files and will be logged as failures."
        )

    failures_file = resolve_failures_file(
        args.failures_file, "ref_years_geo_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "ref_years_geo_successes"
    )
    noops_file = resolve_noops_file(args.noops_file, "ref_years_geo_noops")
    pages = resolve_all_publication_pages_qs()

    return run_update(
        pages=pages,
        by_nom=by_nom,
        duplicate_noms=duplicate_noms,
        geo_labels=geo_labels,
        failures_file=failures_file,
        successes_file=successes_file,
        noops_file=noops_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
