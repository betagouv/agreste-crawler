#!/usr/bin/env python
"""
Set PublicationPage statistical_sources from Disaron source_signature codes.

Walks PublicationPages under any PublicationIndexPage. Codes come from
``disaron:source_signature`` in ``--data-file``; human-readable labels come
from ``--labels-file`` (``contenunomenclature:modalite_stockage`` →
``dc:title``).

For each code, reuses an existing ``StatisticalSource`` with slug
``source-<code>``, or creates one with that slug, ``name=<label>``, and
``parent=None``. Assigns it via ``page.statistical_sources.set([...])``,
then saves a revision (and re-publishes if live).

Also writes pages listed in the input CSV with an empty
``disaron:source_signature`` to ``--missing-file``.

``--parent-id`` from shared args is unused.

Defaults:
- ``--data-file``: ``2026-09-30_dump_tableaux_de_colin_publies.csv``
- ``--labels-file``: ``2026-10-08_libelles_source_signature_L025.csv``
- ``--missing-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_missing_source.csv``
- ``--failures-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_source_failures.csv``
- ``--successes-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_source_successes.csv``
- ``--noops-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_source_noops.csv``

Usage:
    just set_source \\
        --wagtail-project-root ../agreste

    just set_source \\
        --wagtail-project-root ../agreste \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
    StatisticalSource,
)

from metadata_editor.set_metadata import (  # noqa: E402
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_noops_file,
    resolve_successes_file,
    run_metadata_update,
)

SOURCE_SIGNATURE_COLUMN = "disaron:source_signature"
DEFAULT_DATA_FILE = "2026-09-30_dump_tableaux_de_colin_publies.csv"
DEFAULT_LABELS_FILE = "2026-10-08_libelles_source_signature_L025.csv"
MISSING_FIELDNAMES = ["pageId", "disaron_nom", "title"]


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
                "CSV with 'disaron:nom' and 'disaron:source_signature' "
                f"(default: {default!r})."
            )
            return
    raise RuntimeError("--data-file argument not found on parser")


def _load_labels(labels_file: str) -> dict[str, str]:
    csv_path = Path(labels_file)
    if not csv_path.is_absolute():
        csv_path = (Path.cwd() / csv_path).resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"Labels file not found: {csv_path}")

    labels: dict[str, str] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {
            "contenunomenclature:modalite_stockage",
            "dc:title",
        }
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                "Labels CSV must contain columns "
                "'contenunomenclature:modalite_stockage' and 'dc:title'."
            )
        for row in reader:
            key = (row.get("contenunomenclature:modalite_stockage") or "").strip()
            title = (row.get("dc:title") or "").strip()
            if key and title:
                labels[key] = title

    if not labels:
        raise ValueError("Labels CSV has no usable rows.")
    return labels


def load_source_signatures(
    data_file: str,
) -> tuple[dict[str, str], list[str]]:
    """
    Return (codes_by_disaron_nom, empty_signature_noms).

    ``empty_signature_noms`` keeps input order for rows that have a
    ``disaron:nom`` but an empty ``disaron:source_signature``.
    """
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    codes: dict[str, str] = {}
    empty_noms: list[str] = []
    seen_empty: set[str] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        if "disaron:nom" not in reader.fieldnames:
            raise ValueError("Data CSV must contain 'disaron:nom' column.")
        if SOURCE_SIGNATURE_COLUMN not in reader.fieldnames:
            raise ValueError(
                f"Data CSV must contain {SOURCE_SIGNATURE_COLUMN!r} column."
            )
        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            raw_value = (row.get(SOURCE_SIGNATURE_COLUMN) or "").strip()
            if not disaron_nom:
                continue
            if raw_value:
                codes[disaron_nom] = raw_value
            elif disaron_nom not in seen_empty:
                seen_empty.add(disaron_nom)
                empty_noms.append(disaron_nom)

    return codes, empty_noms


def resolve_all_publication_pages_qs():
    pages = PublicationPage.objects.none()
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages = pages | PublicationPage.objects.child_of(index)
    return pages.distinct().order_by("id")


def _source_slug(code: str) -> str:
    return f"source-{code}"


def _get_or_create_source(
    page: PublicationPage,
    code: str,
    label: str,
    *,
    dry_run: bool,
) -> tuple[StatisticalSource | None, str]:
    """Return (source_or_None_on_dry_create, info_note)."""
    slug = _source_slug(code)
    existing = StatisticalSource.objects.filter(slug=slug)
    if page.locale_id is not None:
        localized = existing.filter(locale_id=page.locale_id).first()
        if localized is not None:
            return localized, f"reused StatisticalSource slug={slug!r}"
    source = existing.first()
    if source is not None:
        return source, f"reused StatisticalSource slug={slug!r}"

    if dry_run:
        return None, f"would create StatisticalSource slug={slug!r} name={label!r}"

    source = StatisticalSource.objects.create(
        slug=slug,
        name=label,
        parent=None,
        locale_id=page.locale_id,
    )
    return source, f"created StatisticalSource slug={slug!r} name={label!r}"


def _apply_source(
    page: PublicationPage,
    code: str,
    *,
    labels: dict[str, str],
    dry_run: bool = False,
) -> dict[str, str]:
    label = labels.get(code)
    if not label:
        raise ValueError(f"unknown disaron:source_signature code {code!r}")

    source, info = _get_or_create_source(page, code, label, dry_run=dry_run)
    slug = _source_slug(code)

    current_ids = set(
        page.statistical_sources.values_list("pk", flat=True)
    )
    if source is not None and current_ids == {source.pk}:
        return {
            "noop": True,
            "info": (
                f"noop: statistical_sources already set to {slug!r}. {info}"
            ).strip(),
            "value": slug,
        }

    if dry_run:
        return {
            "value": slug,
            "info": (
                f"Would set statistical_sources to {slug!r} ({label!r}). {info}"
            ).strip(),
        }

    assert source is not None
    page.statistical_sources.set([source])
    revision = page.save_revision()
    if page.live:
        revision.publish()

    return {"value": slug, "info": info}


def resolve_missing_file(provided: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_missing_source.csv"


def write_missing_sources(
    *,
    pages,
    empty_signature_noms: list[str],
    missing_file: str,
) -> int:
    """
    Write pages listed in the input CSV with an empty source_signature.

    Returns the number of missing rows written.
    """
    missing_path = Path(missing_file)
    missing_path.parent.mkdir(parents=True, exist_ok=True)

    pages_by_nom: dict[str, PublicationPage] = {}
    if empty_signature_noms:
        for page in (
            pages.filter(disaron_id__in=empty_signature_noms)
            .order_by("id")
            .only("id", "title", "disaron_id")
        ):
            disaron_nom = find_disaron_nom(page)
            if disaron_nom and disaron_nom not in pages_by_nom:
                pages_by_nom[disaron_nom] = page

    count = 0
    with missing_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MISSING_FIELDNAMES)
        writer.writeheader()
        for disaron_nom in empty_signature_noms:
            page = pages_by_nom.get(disaron_nom)
            if page is None:
                continue
            writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "title": page.title,
                }
            )
            count += 1

    print(f"Wrote {count} missing-source row(s) to {missing_file}.")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    _make_parent_id_optional(parser)
    _set_data_file_default(parser, DEFAULT_DATA_FILE)
    parser.add_argument(
        "--labels-file",
        type=str,
        default=DEFAULT_LABELS_FILE,
        help=(
            "CSV mapping source_signature codes to labels "
            f"(default: {DEFAULT_LABELS_FILE!r})."
        ),
    )
    parser.add_argument(
        "--missing-file",
        type=str,
        default="",
        help=(
            "Path to CSV for input rows with empty disaron:source_signature "
            "(columns: pageId, disaron_nom, title)."
        ),
    )
    args = parser.parse_args()

    labels = _load_labels(args.labels_file)
    codes_by_disaron_nom, empty_signature_noms = load_source_signatures(
        args.data_file
    )
    all_pages = resolve_all_publication_pages_qs()
    missing_file = resolve_missing_file(args.missing_file)
    write_missing_sources(
        pages=all_pages,
        empty_signature_noms=empty_signature_noms,
        missing_file=missing_file,
    )

    pages = all_pages.filter(
        disaron_id__in=list(codes_by_disaron_nom.keys())
    ).order_by("id")

    failures_file = resolve_failures_file(
        args.failures_file, "source_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "source_successes"
    )
    noops_file = resolve_noops_file(args.noops_file, "source_noops")

    def apply_value(page: PublicationPage, code: str) -> dict[str, str]:
        return _apply_source(
            page, code, labels=labels, dry_run=args.dry_run
        )

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=codes_by_disaron_nom,
        apply_value=apply_value,
        update_fields=None,
        failures_file=failures_file,
        successes_file=successes_file,
        noops_file=noops_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} PublicationPage object(s) "
            f"using {SOURCE_SIGNATURE_COLUMN!r} from {args.data_file}."
        ),
        success_log=lambda _i, _n, page, disaron_nom, value: (
            f"id={page.id} disaron_nom={disaron_nom!r} "
            f"source={value!r} title={page.title!r}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
