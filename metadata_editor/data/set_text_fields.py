#!/usr/bin/env python
"""
Update StandardPublication content fields on PublicationPages from a CSV.

For each PublicationPage under --parent-id (a PublicationIndexPage), matched
on ``page.disaron_id`` == CSV ``disaron:nom``:

- ``html_principal`` → ``presentation``
- ``html_secondaire`` → ``additional_information``
- ``notes`` or ``disaron:notes`` → ``notes`` (JSON list of strings,
  e.g. ``["a","b"]``, converted to an HTML bullet list)

Empty CSV cells leave the existing field unchanged. Pages with no content in
the CSV are skipped. Missing ``standard_publication`` block is an error.

On success, saves a revision and re-publishes if the page was live.

Usage:
    just set_text_fields \\
        --wagtail-project-root ../agreste \\
        --parent-id 42 \\
        --data-file path/to/content.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402

from publications.models import PublicationPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    _get_stream_data,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
)
from metadata_editor.set_themes import resolve_publication_pages  # noqa: E402

NOTES_COLUMNS = ("notes", "disaron:notes")
STANDARD_PUBLICATION_TYPE = "standard_publication"


@dataclass(frozen=True)
class ContentFields:
    presentation: str | None
    additional_information: str | None
    notes: str | None

    def has_any(self) -> bool:
        return bool(
            self.presentation or self.additional_information or self.notes
        )

    def added_labels(self) -> list[str]:
        labels: list[str] = []
        if self.presentation:
            labels.append("presentation")
        if self.additional_information:
            labels.append("additional_information")
        if self.notes:
            labels.append("notes")
        return labels


def _pick_notes_column(fieldnames: list[str]) -> str:
    present = [name for name in NOTES_COLUMNS if name in fieldnames]
    if not present:
        raise ValueError(
            "Data CSV must contain one of: "
            + ", ".join(repr(c) for c in NOTES_COLUMNS)
        )
    return present[0]


def _notes_to_richtext(raw: str) -> str | None:
    """Parse a JSON list of strings into an HTML ``<ul>`` bullet list.

    Returns ``None`` for an empty list ``[]`` (leave the field unchanged).
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"notes must be a JSON list of strings: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            "notes must be a JSON list of strings, "
            f"got {type(data).__name__}"
        )
    if not data:
        return None
    if not all(isinstance(item, str) for item in data):
        raise ValueError("notes must be a JSON list of strings")
    items = "".join(f"<li>{html.escape(item)}</li>" for item in data)
    return f"<ul>{items}</ul>"


def load_content_by_disaron_nom(data_file: str) -> dict[str, ContentFields]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    out: dict[str, ContentFields] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        available = set(reader.fieldnames)
        if "disaron:nom" not in available:
            raise ValueError("Data CSV must contain 'disaron:nom' column.")
        required = {"html_principal", "html_secondaire"}
        missing = required - available
        if missing:
            raise ValueError(
                f"Data CSV missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        notes_column = _pick_notes_column(list(reader.fieldnames))

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                continue

            presentation = (row.get("html_principal") or "").strip() or None
            additional = (row.get("html_secondaire") or "").strip() or None
            raw_notes = (row.get(notes_column) or "").strip()
            try:
                notes = _notes_to_richtext(raw_notes) if raw_notes else None
            except ValueError as exc:
                raise ValueError(
                    f"{disaron_nom!r}: {exc}"
                ) from exc
            content = ContentFields(
                presentation=presentation,
                additional_information=additional,
                notes=notes,
            )
            if not content.has_any():
                continue
            out[disaron_nom] = content

    return out


def _apply_content_to_stream(
    stream_data: list[Any], content: ContentFields
) -> list[Any]:
    if not content.has_any():
        raise ValueError("nothing to insert (all content fields empty)")

    new_stream = list(stream_data)
    for block_idx, block in enumerate(stream_data):
        if not isinstance(block, dict):
            continue
        if block.get("type") != STANDARD_PUBLICATION_TYPE:
            continue

        value = block.get("value")
        if not isinstance(value, dict):
            raise ValueError(
                f"{STANDARD_PUBLICATION_TYPE} block has no value dict."
            )

        new_value = dict(value)
        if content.presentation is not None:
            new_value["presentation"] = content.presentation
        if content.additional_information is not None:
            new_value["additional_information"] = content.additional_information
        if content.notes is not None:
            new_value["notes"] = content.notes

        new_block = dict(block)
        new_block["value"] = new_value
        new_stream[block_idx] = new_block
        return new_stream

    raise ValueError(
        f"no {STANDARD_PUBLICATION_TYPE} block found in page body."
    )


def _apply_content(page: PublicationPage, content: ContentFields) -> None:
    stream_data = _get_stream_data(page.body)
    page.body = _apply_content_to_stream(stream_data, content)


def _persist_page(page: PublicationPage) -> None:
    revision = page.save_revision()
    if page.live:
        revision.publish()


def resolve_success_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def run_content_update(
    *,
    pages: Any,
    content_by_disaron_nom: dict[str, ContentFields],
    failures_file: str,
    success_file: str,
    skipped_file: str,
    dry_run: bool,
    confirmation_message: str,
) -> int:
    page_count = pages.count()
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}{confirmation_message} Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    skipped = 0
    skipped_no_content = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    success_path = Path(success_file)
    success_path.parent.mkdir(parents=True, exist_ok=True)
    skipped_path = Path(skipped_file)
    skipped_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as failures_f,
        success_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as success_f,
        skipped_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as skipped_f,
    ):
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        _flush_failures_file(failures_f)

        success_writer = csv.DictWriter(
            success_f,
            fieldnames=["pageId", "disaron_nom", "added", "title"],
        )
        success_writer.writeheader()
        success_f.flush()

        skipped_writer = csv.DictWriter(
            skipped_f,
            fieldnames=["pageId", "disaron_nom", "title"],
        )
        skipped_writer.writeheader()
        skipped_f.flush()

        def _fail(page_id: int, disaron_nom: str, error: str) -> None:
            nonlocal skipped, failures_count
            skipped += 1
            failures_count += 1
            writer.writerow(
                {
                    "pageId": str(page_id),
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            _flush_failures_file(failures_f)
            print(
                f"[{updated + skipped}/{page_count}] "
                f"Skipped id={page_id}: {error}",
                flush=True,
            )

        for page in pages:
            disaron_nom = find_disaron_nom(page)
            if disaron_nom is None:
                _fail(page.id, "", "page has no disaron_id")
                continue

            content = content_by_disaron_nom.get(disaron_nom)
            if content is None:
                skipped_no_content += 1
                skipped_writer.writerow(
                    {
                        "pageId": str(page.id),
                        "disaron_nom": disaron_nom,
                        "title": page.title,
                    }
                )
                skipped_f.flush()
                continue

            try:
                _apply_content(page, content)
            except Exception as exc:
                _fail(page.id, disaron_nom, f"apply failed: {exc}")
                continue

            if not dry_run:
                try:
                    _persist_page(page)
                except Exception as exc:
                    _fail(page.id, disaron_nom, f"save failed: {exc}")
                    continue
            else:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(page.id, disaron_nom, f"validation failed: {exc}")
                    continue

            updated += 1
            added = ",".join(content.added_labels())
            success_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "added": added,
                    "title": page.title,
                }
            )
            success_f.flush()
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                f"id={page.id} disaron_nom={disaron_nom!r} "
                f"added={added} title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {success_file}.")
    print(f"Wrote {skipped_no_content} skipped row(s) to {skipped_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} PublicationPage object(s); "
        f"skipped {skipped} failure(s); "
        f"skipped {skipped_no_content} page(s) with no content in CSV."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--success-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for successes "
            "(columns: pageId, disaron_nom, added, title)."
        ),
    )
    parser.add_argument(
        "--skipped-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for pages skipped (no content in CSV) "
            "(columns: pageId, disaron_nom, title)."
        ),
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "text_fields_failures"
    )
    success_file = resolve_success_file(
        args.success_file, "text_fields_success"
    )
    skipped_file = resolve_success_file(
        args.skipped_file, "text_fields_skipped_no_content"
    )
    pages = resolve_publication_pages(args.parent_id)
    content_by_disaron_nom = load_content_by_disaron_nom(args.data_file)

    return run_content_update(
        pages=pages,
        content_by_disaron_nom=content_by_disaron_nom,
        failures_file=failures_file,
        success_file=success_file,
        skipped_file=skipped_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} PublicationPage object(s) "
            f"using content from {args.data_file} "
            f"({len(content_by_disaron_nom)} disaron(s) with content)."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
