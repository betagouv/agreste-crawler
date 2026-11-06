#!/usr/bin/env python
"""
Bugfix: backfill Wagtail Document ``file_size`` and ``file_hash``.

Calls ``Document._set_document_file_metadata()`` then ``save()`` on every
selected document (same as ``create_publications._create_wagtail_document``).
Safe to re-run on documents that already have metadata.

By default all documents are processed. With ``--parent-id``, only documents
referenced by pages under that parent (descendants) are processed.

Successes and failures are written to CSV files (defaults under
``page_creator/output/``).

Usage:
    uv run python -m page_creator.bugfix_document_size \\
        --wagtail-project-root ../agreste --dry-run
    uv run python -m page_creator.bugfix_document_size \\
        --wagtail-project-root ../agreste \\
        --scalingo-app agreste-test
    just bugfix_document_size --wagtail-project-root ../agreste \\
        --parent-id 42 --no-confirmation
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from scalingo_env_setup import (
    add_scalingo_app_args,
    configure_database_url,
    ensure_ssh_tunnel,
)

configure_database_url()

from django_setup import setup_django

setup_django(__file__)
ensure_ssh_tunnel()

from django.contrib.contenttypes.models import ContentType  # noqa: E402
from django.utils import timezone  # noqa: E402
from wagtail.documents import get_document_model  # noqa: E402
from wagtail.models import Page, ReferenceIndex  # noqa: E402

SUCCESS_FIELDNAMES = ["documentId", "title", "file", "file_size", "info"]
FAILURE_FIELDNAMES = ["documentId", "title", "file", "error"]


def _flush(f) -> None:
    f.flush()
    os.fsync(f.fileno())


def _resolve_output_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"page_creator/output/{timestamp}_{default_suffix}.csv"


def _document_ids_referenced_under_parent(parent_id: int) -> set[int]:
    """Document PKs referenced by any descendant page of ``parent_id``."""
    try:
        parent = Page.objects.get(id=parent_id)
    except Page.DoesNotExist as exc:
        raise ValueError(
            f"Page id={parent_id} does not exist in this Wagtail database."
        ) from exc

    page_ids = {
        str(pk) for pk in parent.get_descendants().values_list("pk", flat=True)
    }
    if not page_ids:
        return set()

    Document = get_document_model()
    doc_ct = ContentType.objects.get_for_model(Document)
    raw_ids = (
        ReferenceIndex.objects.filter(
            to_content_type=doc_ct,
            object_id__in=page_ids,
        )
        .values_list("to_object_id", flat=True)
        .distinct()
    )
    return {int(x) for x in raw_ids}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wagtail-project-root",
        type=str,
        default="",
        help=(
            "Root directory of the Wagtail/Django project "
            "(contains config/settings.py)."
        ),
    )
    add_scalingo_app_args(parser)
    parser.add_argument(
        "--parent-id",
        type=int,
        default=None,
        help=(
            "Optional page ID: only fix documents referenced by descendant "
            "pages of this parent. Default: all documents."
        ),
    )
    parser.add_argument(
        "--failures-file",
        type=str,
        default="",
        help=(
            "CSV for failures (columns: documentId, title, file, error). "
            "Default: page_creator/output/<timestamp>_document_size_failures.csv"
        ),
    )
    parser.add_argument(
        "--successes-file",
        type=str,
        default="",
        help=(
            "CSV for successes (columns: documentId, title, file, file_size, "
            "info). Default: page_creator/output/<timestamp>_document_size_successes.csv"
        ),
    )
    parser.add_argument(
        "--no-confirmation",
        action="store_true",
        help="Skip confirmation prompt and update immediately.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List documents that would be updated without writing.",
    )
    args = parser.parse_args()
    _ = (args.scalingo_app, args.database_port)  # consumed via sys.argv earlier

    Document = get_document_model()
    if args.parent_id is None:
        documents = list(Document.objects.all().order_by("id"))
        scope = "all documents"
    else:
        doc_ids = _document_ids_referenced_under_parent(args.parent_id)
        documents = list(
            Document.objects.filter(pk__in=doc_ids).order_by("id")
        )
        scope = f"documents referenced under parent id={args.parent_id}"

    total = len(documents)

    if total == 0:
        print(f"No documents found ({scope}).")
        return 0

    failures_file = _resolve_output_file(
        args.failures_file, "document_size_failures"
    )
    successes_file = _resolve_output_file(
        args.successes_file, "document_size_successes"
    )
    failures_path = Path(failures_file)
    successes_path = Path(successes_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    successes_path.parent.mkdir(parents=True, exist_ok=True)

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"Found {total} document(s) ({scope}).")
    print(f"Successes → {successes_file}")
    print(f"Failures  → {failures_file}")

    if not args.no_confirmation:
        answer = input(
            f"{mode_prefix}About to set file metadata on {total} document(s). "
            "Type 'yes' to confirm: "
        ).strip()
        if answer.lower() != "yes":
            print(f"{mode_prefix}Update cancelled.")
            return 0

    updated = 0
    failed = 0

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
        _flush(failures_f)

        successes_writer = csv.DictWriter(
            successes_f, fieldnames=SUCCESS_FIELDNAMES
        )
        successes_writer.writeheader()
        _flush(successes_f)

        for i, doc in enumerate(documents, start=1):
            file_name = getattr(doc.file, "name", "") or ""
            if args.dry_run:
                updated += 1
                info = "would set metadata"
                successes_writer.writerow(
                    {
                        "documentId": str(doc.id),
                        "title": doc.title,
                        "file": file_name,
                        "file_size": (
                            "" if doc.file_size is None else str(doc.file_size)
                        ),
                        "info": info,
                    }
                )
                _flush(successes_f)
                print(
                    f"[{i}/{total}] DRY RUN : {info} "
                    f"id={doc.id} title={doc.title!r} file={file_name!r} "
                    f"file_size={doc.file_size!r}",
                    flush=True,
                )
                continue

            try:
                doc._set_document_file_metadata()
                doc.save()
            except Exception as exc:  # noqa: BLE001 - log and continue
                failed += 1
                error = str(exc)
                failures_writer.writerow(
                    {
                        "documentId": str(doc.id),
                        "title": doc.title,
                        "file": file_name,
                        "error": error,
                    }
                )
                _flush(failures_f)
                print(
                    f"[{i}/{total}] Failed id={doc.id} title={doc.title!r} "
                    f"file={file_name!r}: {error}",
                    flush=True,
                )
                continue

            updated += 1
            successes_writer.writerow(
                {
                    "documentId": str(doc.id),
                    "title": doc.title,
                    "file": file_name,
                    "file_size": (
                        "" if doc.file_size is None else str(doc.file_size)
                    ),
                    "info": "updated",
                }
            )
            _flush(successes_f)
            print(
                f"[{i}/{total}] Updated id={doc.id} title={doc.title!r} "
                f"file={file_name!r} file_size={doc.file_size!r}",
                flush=True,
            )

    if args.dry_run:
        print(f"DRY RUN complete: {updated} document(s) would be updated.")
    else:
        print(f"Updated {updated} document(s); failed {failed}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    print(f"Wrote {failed} failure row(s) to {failures_file}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
