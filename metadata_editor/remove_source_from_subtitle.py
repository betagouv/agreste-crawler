#!/usr/bin/env python
"""
Remove ``standard_publication.subtitle`` when it duplicates a statistical source.

Walks PublicationPages under any PublicationIndexPage that have Collection
``Données``. For each page, if it has a ``standard_publication`` block with a
nonempty subtitle, has at least one ``statistical_sources`` entry, and the
stripped subtitle equals the ``name`` of one of those sources, clears the
subtitle (save revision + re-publish if live) and logs a success.

All other Données pages are logged as failures with a specific reason.

``--parent-id`` and ``--data-file`` from shared args are unused.

Defaults:
- ``--successes-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_remove_source_subtitle_successes.csv``
- ``--failures-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_remove_source_subtitle_failures.csv``

Usage:
    just remove_source_from_subtitle \\
        --wagtail-project-root ../agreste

    just remove_source_from_subtitle \\
        --wagtail-project-root ../agreste \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from publications.models import (  # noqa: E402
    Collection,
    PublicationIndexPage,
    PublicationPage,
)

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    _get_stream_data,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_successes_file,
)

STANDARD_PUBLICATION_TYPE = "standard_publication"
DONNEES_COLLECTION_NAME = "Données"
SUCCESS_FIELDNAMES = [
    "pageId",
    "disaron_nom",
    "subtitle",
    "statistical_source",
    "title",
]
FAILURE_FIELDNAMES = ["pageId", "disaron_nom", "error"]


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
            action.help = "Unused by this script (no input CSV)."
            return
    raise RuntimeError("--data-file argument not found on parser")


def resolve_donnees_publication_pages():
    donnees = Collection.objects.filter(name=DONNEES_COLLECTION_NAME).first()
    if donnees is None:
        raise ValueError(
            f"Collection {DONNEES_COLLECTION_NAME!r} not found in database."
        )

    pages = PublicationPage.objects.none()
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages = pages | PublicationPage.objects.child_of(index)
    return (
        pages.filter(collections=donnees)
        .distinct()
        .order_by("id")
        .prefetch_related("statistical_sources")
    )


def _find_standard_publication_index(
    stream_data: list[Any],
) -> tuple[int, dict[str, Any]] | None:
    for block_idx, block in enumerate(stream_data):
        if not isinstance(block, dict):
            continue
        if block.get("type") != STANDARD_PUBLICATION_TYPE:
            continue
        value = block.get("value")
        if isinstance(value, dict):
            return block_idx, value
    return None


def _clear_subtitle_in_stream(
    stream_data: list[Any], block_idx: int, value: dict[str, Any]
) -> list[Any]:
    new_stream = list(stream_data)
    new_value = dict(value)
    new_value["subtitle"] = ""
    new_block = dict(stream_data[block_idx])
    new_block["value"] = new_value
    new_stream[block_idx] = new_block
    return new_stream


def _evaluate_page(
    page: PublicationPage,
) -> tuple[str, str] | str:
    """
    Return ``(subtitle, matched_source_name)`` when subtitle should be
    cleared, or an error string on failure.
    """
    try:
        stream_data = _get_stream_data(page.body)
    except Exception as exc:
        return f"could not read body stream data: {exc}"

    found = _find_standard_publication_index(stream_data)
    if found is None:
        return f"no {STANDARD_PUBLICATION_TYPE} block found in page body"

    _block_idx, value = found
    subtitle = (value.get("subtitle") or "").strip()
    if not subtitle:
        return "subtitle is empty"

    sources = list(page.statistical_sources.all())
    if not sources:
        return "no statistical_sources assigned"

    matched = next(
        (
            source
            for source in sources
            if (source.name or "").strip() == subtitle
        ),
        None,
    )
    if matched is None:
        names = ", ".join(repr((s.name or "").strip()) for s in sources)
        return (
            f"subtitle {subtitle!r} does not match any statistical_source "
            f"name ({names})"
        )

    return subtitle, matched.name


def run_remove_source_from_subtitle(
    *,
    pages,
    failures_file: str,
    successes_file: str,
    dry_run: bool,
) -> int:
    page_count = pages.count()
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}About to clear matching source subtitles on "
        f"{page_count} Données PublicationPage object(s). "
        "Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    failures_count = 0

    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    successes_path = Path(successes_file)
    successes_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as failures_f,
        successes_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as successes_f,
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

        def _fail(page: PublicationPage, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            failures_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            _flush_failures_file(failures_f)
            print(
                f"[{updated + failures_count}/{page_count}] "
                f"Skipped id={page.id}: {error}",
                flush=True,
            )

        for page in pages:
            disaron_nom = find_disaron_nom(page) or ""
            result = _evaluate_page(page)
            if isinstance(result, str):
                _fail(page, disaron_nom, result)
                continue

            subtitle, source_name = result
            if not dry_run:
                try:
                    stream_data = _get_stream_data(page.body)
                    found = _find_standard_publication_index(stream_data)
                    assert found is not None
                    block_idx, value = found
                    page.body = _clear_subtitle_in_stream(
                        stream_data, block_idx, value
                    )
                    revision = page.save_revision()
                    if page.live:
                        revision.publish()
                except Exception as exc:
                    _fail(page, disaron_nom, f"save failed: {exc}")
                    continue

            updated += 1
            successes_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "subtitle": subtitle,
                    "statistical_source": source_name,
                    "title": page.title,
                }
            )
            _flush_failures_file(successes_f)
            action = "Would clear" if dry_run else "Cleared"
            print(
                f"[{updated + failures_count}/{page_count}] {action} "
                f"id={page.id} disaron_nom={disaron_nom!r} "
                f"subtitle={subtitle!r} source={source_name!r} "
                f"title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    summary_action = "Would clear" if dry_run else "Cleared"
    print(
        f"{summary_action} subtitle on {updated} PublicationPage object(s); "
        f"failed {failures_count}."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    _make_parent_id_optional(parser)
    _make_data_file_optional(parser)
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "remove_source_subtitle_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "remove_source_subtitle_successes"
    )
    pages = resolve_donnees_publication_pages()

    return run_remove_source_from_subtitle(
        pages=pages,
        failures_file=failures_file,
        successes_file=successes_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
