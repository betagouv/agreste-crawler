#!/usr/bin/env python
"""
Migrate PublicationPage multicolumns bodies to freeform publication blocks.

Scans ``PublicationPage`` children of every ``PublicationIndexPage``. For each
page body, converts eligible ``multicolumns`` blocks (empty title, width-8 +
width-4 columns) into ``freeform_publication`` blocks. Left-column children
accepted by ``ColumnBlock`` become freeform content; right-column document
tiles become ``downloadable_documents``. Other blocks stay in place or are
logged as kept/skipped.

Defaults:
- ``--log-file``: ``data_migration/output/migrate_to_freeform_<timestamp>.csv``
  (skipped pages also written to ``*_skipped.csv``)

Usage:
    just migrate_to_freeform_publications \\
        --wagtail-project-root ../agreste \\
        --dry-run

    just migrate_to_freeform_publications \\
        --wagtail-project-root ../agreste \\
        --scalingo-app agreste-test \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from scalingo_env_setup import (
    add_scalingo_app_args,
    configure_database_url,
    ensure_ssh_tunnel,
)

configure_database_url()

from django_setup import setup_django

setup_django(__file__)
ensure_ssh_tunnel()

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from publications.blocks.freeform_publication import (  # noqa: E402
    FREEFORM_PUBLICATION_BLOCK,
)
from publications.blocks.standard_publication import (  # noqa: E402
    STANDARD_PUBLICATION_BLOCK,
)
from publications.migrations.data_migrations.migrate_to_standard_publications import (  # noqa: E402
    LEFT_COLUMN_WIDTH,
    MULTICOLUMNS_BLOCK,
    RIGHT_COLUMN_WIDTH,
    TILE_TITLE_TO_DOWNLOAD_TYPE,
)
from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)
from sites_conformes.core.blocks.layout import ColumnBlock  # noqa: E402

KEPT = "kept"
MIGRATED = "migrated"
NO_MULTICOLUMNS = "no_multicolumns"
SKIPPED = "skipped"
CSV_COLUMNS = ["id", "disaron_id", "action", "block_type", "reason"]
ONLY_STANDARD_PUBLICATION = "page contains only a standard publication block"

DEFAULT_OUTPUT_DIR = Path("data_migration/output")


def migration_log_path(*, override: Path | str | None = None, started_at=None) -> Path:
    if override is not None:
        return Path(override)
    started_at = started_at or timezone.now()
    stamp = started_at.strftime("%Y-%m-%dT%H-%M-%S")
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_OUTPUT_DIR / f"migrate_to_freeform_{stamp}.csv"


def skipped_log_path(log_path: Path) -> Path:
    return log_path.with_name(f"{log_path.stem}_skipped{log_path.suffix or '.csv'}")


@dataclass
class BlockNote:
    action: str
    block_type: str = ""
    reason: str = ""


@dataclass
class PageMigrationResult:
    page_id: int
    disaron_id: str
    page_url: str
    block_results: list[BlockNote] = field(default_factory=list)

    @property
    def migrated_count(self) -> int:
        return sum(1 for result in self.block_results if result.action == MIGRATED)

    @property
    def skipped_count(self) -> int:
        return sum(1 for result in self.block_results if result.action == SKIPPED)

    @property
    def kept_count(self) -> int:
        return sum(1 for result in self.block_results if result.action == KEPT)


@dataclass
class MigrationSummary:
    dry_run: bool
    pages_scanned: int = 0
    pages_changed: int = 0
    pages_without_multicolumns: int = 0
    blocks_migrated: int = 0
    blocks_skipped: int = 0
    blocks_kept: int = 0
    page_results: list[PageMigrationResult] = field(default_factory=list)


def _accepted_content_types() -> set[str]:
    return set(ColumnBlock().child_blocks)


def _column_content_by_width(columns: list, width: str) -> list | None:
    content = None
    for column in columns:
        if column.get("type") != "column":
            return None
        if column.get("value", {}).get("width") != width:
            continue
        if content is not None:
            return None
        content = column.get("value", {}).get("content", [])
    return content


def _download_from_tile(block: dict) -> dict | None:
    if block.get("type") != "tile":
        return None
    tile_value = block.get("value") or {}
    download_type = TILE_TITLE_TO_DOWNLOAD_TYPE.get(tile_value.get("title", ""))
    link = tile_value.get("link") or {}
    document_id = link.get("document")
    if download_type is None or not document_id or link.get("link_type") not in (
        None,
        "",
        "document",
    ):
        return None
    return {"download_type": download_type, "document": document_id}


def try_convert_multicolumns_block(
    block: dict,
    *,
    accepted_types: set[str],
) -> tuple[dict | None, list[dict], list[BlockNote], str | None]:
    """Return a free-form block, blocks that stay after it, notes, and a skip reason."""
    if block.get("type") != MULTICOLUMNS_BLOCK:
        return None, [], [], "not a multicolumns block"

    value = block.get("value") or {}
    if str(value.get("title") or "").strip():
        return None, [], [], "multicolumns title must be empty"

    columns = value.get("columns") or []
    if len(columns) != 2:
        return None, [], [], "expected one width-8 column and one width-4 column"

    left_content = _column_content_by_width(columns, LEFT_COLUMN_WIDTH)
    right_content = _column_content_by_width(columns, RIGHT_COLUMN_WIDTH)
    if left_content is None or right_content is None:
        return None, [], [], "expected one width-8 column and one width-4 column"

    content = []
    unplaced = []
    notes: list[BlockNote] = []
    for child in left_content:
        child_type = child.get("type", "?")
        if child_type in accepted_types:
            content.append(child)
        else:
            unplaced.append(child)
            notes.append(
                BlockNote(
                    action=KEPT,
                    block_type=child_type,
                    reason="left column type is not accepted by ColumnBlock",
                )
            )

    downloadable_documents = []
    for child in right_content:
        download = _download_from_tile(child)
        if download is None:
            unplaced.append(child)
            notes.append(
                BlockNote(
                    action=KEPT,
                    block_type=child.get("type", "?"),
                    reason="right column block is not a document tile",
                )
            )
            continue
        downloadable_documents.append(download)

    freeform_block = {
        "type": FREEFORM_PUBLICATION_BLOCK,
        "id": str(uuid.uuid4()),
        "value": {
            "content": content,
            "downloadable_documents": downloadable_documents,
        },
    }
    return freeform_block, unplaced, notes, None


def transform_body_stream(
    stream_data: list,
    *,
    accepted_types: set[str] | None = None,
) -> tuple[list, list[BlockNote]]:
    if accepted_types is None:
        accepted_types = _accepted_content_types()

    if len(stream_data) == 1 and stream_data[0].get("type") == STANDARD_PUBLICATION_BLOCK:
        return stream_data, [
            BlockNote(
                action=SKIPPED,
                block_type=STANDARD_PUBLICATION_BLOCK,
                reason=ONLY_STANDARD_PUBLICATION,
            )
        ]

    if not any(block.get("type") == MULTICOLUMNS_BLOCK for block in stream_data):
        return stream_data, [
            BlockNote(action=NO_MULTICOLUMNS, reason="no multicolumns block")
        ]

    transformed = []
    notes: list[BlockNote] = []
    for block in stream_data:
        if block.get("type") != MULTICOLUMNS_BLOCK:
            transformed.append(block)
            notes.append(
                BlockNote(
                    action=KEPT,
                    block_type=block.get("type", "?"),
                    reason="left in place",
                )
            )
            continue

        converted, unplaced, unplaced_notes, skip_reason = try_convert_multicolumns_block(
            block,
            accepted_types=accepted_types,
        )
        if converted is None:
            transformed.append(block)
            notes.append(
                BlockNote(
                    action=SKIPPED,
                    block_type=MULTICOLUMNS_BLOCK,
                    reason=skip_reason or "",
                )
            )
            continue

        transformed.append(converted)
        transformed.extend(unplaced)
        notes.append(BlockNote(action=MIGRATED, block_type=MULTICOLUMNS_BLOCK))
        notes.extend(unplaced_notes)

    return transformed, notes


def _stored_body(page) -> list:
    if hasattr(page, "body_text"):
        body_text = page.body_text
        if not body_text:
            return []
        body = json.loads(body_text) if isinstance(body_text, str) else body_text
        return body if isinstance(body, list) else []
    raw = getattr(getattr(page, "body", None), "raw_data", None)
    return list(raw) if raw else []


def _migrate_page_body(page, *, dry_run: bool) -> PageMigrationResult:
    transformed, notes = transform_body_stream(_stored_body(page))
    result = PageMigrationResult(
        page_id=page.pk,
        disaron_id=getattr(page, "disaron_id", "") or "",
        page_url=page.url,
        block_results=notes,
    )
    if result.migrated_count == 0 or dry_run:
        return result

    page.body = transformed
    page.save_revision(log_action=True).publish()
    return result


def _page_was_skipped(page_result: PageMigrationResult) -> bool:
    return page_result.migrated_count == 0 and page_result.skipped_count > 0


def _log_page(writer, page_result: PageMigrationResult) -> None:
    if writer is None:
        return
    for note in page_result.block_results:
        writer.writerow(
            [
                page_result.page_id,
                page_result.disaron_id,
                note.action,
                note.block_type,
                note.reason,
            ]
        )


def _csv_writer(log_file: TextIO | None):
    if log_file is None:
        return None
    writer = csv.writer(log_file)
    writer.writerow(CSV_COLUMNS)
    log_file.flush()
    return writer


def run_migration(
    pages,
    *,
    dry_run: bool,
    log_file: TextIO | None = None,
    skipped_log_file: TextIO | None = None,
) -> MigrationSummary:
    summary = MigrationSummary(dry_run=dry_run)
    writer = _csv_writer(log_file)
    skipped_writer = _csv_writer(skipped_log_file)

    page_iter = pages.iterator() if hasattr(pages, "iterator") else iter(pages)

    with transaction.atomic():
        for page in page_iter:
            summary.pages_scanned += 1
            page_result = _migrate_page_body(page, dry_run=dry_run)
            summary.page_results.append(page_result)
            summary.blocks_migrated += page_result.migrated_count
            summary.blocks_skipped += page_result.skipped_count
            summary.blocks_kept += page_result.kept_count
            if any(
                note.action == NO_MULTICOLUMNS for note in page_result.block_results
            ):
                summary.pages_without_multicolumns += 1
            if page_result.migrated_count:
                summary.pages_changed += 1
            if _page_was_skipped(page_result):
                _log_page(skipped_writer, page_result)
                if skipped_log_file is not None:
                    skipped_log_file.flush()
            else:
                _log_page(writer, page_result)
                if log_file is not None:
                    log_file.flush()

        if dry_run:
            transaction.set_rollback(True)

    return summary


def resolve_publication_pages():
    """PublicationPages that are direct children of any PublicationIndexPage."""
    pages = PublicationPage.objects.none()
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages = pages | PublicationPage.objects.child_of(index)
    return pages.order_by("id").distinct()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wagtail-project-root",
        type=str,
        default="../agreste",
        help=(
            "Root directory of the Wagtail/Django project "
            "(contains config/settings.py). "
            "Default: ../agreste."
        ),
    )
    add_scalingo_app_args(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and log without writing page revisions.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="",
        help=(
            "CSV log path. Default: "
            "data_migration/output/migrate_to_freeform_<timestamp>.csv"
        ),
    )
    args = parser.parse_args()
    _ = (args.scalingo_app, args.database_port)  # consumed via sys.argv earlier

    pages = resolve_publication_pages()
    page_count = pages.count()
    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to migrate multicolumns → freeform on "
        f"{page_count} PublicationPage(s) under all PublicationIndexPages. "
        "Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Migration cancelled.")
        return 0

    log_path = migration_log_path(
        override=Path(args.log_file).resolve() if args.log_file else None
    )
    skipped_path = skipped_log_path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        log_path.open("w", encoding="utf-8", newline="") as log_file,
        skipped_path.open("w", encoding="utf-8", newline="") as skipped_log_file,
    ):
        summary = run_migration(
            pages,
            dry_run=args.dry_run,
            log_file=log_file,
            skipped_log_file=skipped_log_file,
        )

    action = "Would change" if args.dry_run else "Changed"
    print(
        f"{mode_prefix}Scanned {summary.pages_scanned} page(s); "
        f"{action} {summary.pages_changed}; "
        f"without multicolumns {summary.pages_without_multicolumns}; "
        f"blocks migrated={summary.blocks_migrated} "
        f"skipped={summary.blocks_skipped} kept={summary.blocks_kept}."
    )
    print(f"Wrote log {log_path}")
    print(f"Wrote skipped log {skipped_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
