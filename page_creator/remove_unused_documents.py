#!/usr/bin/env python
"""
Delete Wagtail documents that have no inbound references anywhere.

Only documents that do not appear in the reference index (no StreamField,
rich text, FK, or other indexed link from any model) are removed.

Usage:
    uv run python -m page_creator.remove_unused_documents \
        --wagtail-project-root ../agreste --dry-run
    uv run python -m page_creator.remove_unused_documents \
        --wagtail-project-root ../agreste
    uv run python -m page_creator.remove_unused_documents \
        --wagtail-project-root ../agreste --no-confirmation
"""

import argparse

from django_setup import setup_django

setup_django(__file__)

from django.contrib.contenttypes.models import ContentType  # noqa: E402
from django.db import transaction  # noqa: E402
from wagtail.documents import get_document_model  # noqa: E402
from wagtail.models import ReferenceIndex  # noqa: E402


def _document_ids_referenced_anywhere() -> set[int]:
    """Primary keys of documents that appear in at least one ReferenceIndex row."""
    Document = get_document_model()
    doc_ct = ContentType.objects.get_for_model(Document)
    raw_ids = (
        ReferenceIndex.objects.filter(to_content_type=doc_ct)
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
    parser.add_argument(
        "--scalingo-env-file",
        type=str,
        default="",
        help="Load environment values from this env file before Django setup.",
    )
    parser.add_argument(
        "--no-confirmation",
        action="store_true",
        help="Skip confirmation prompt and delete immediately.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List documents that would be deleted without deleting them.",
    )
    args = parser.parse_args()

    Document = get_document_model()
    referenced_ids = _document_ids_referenced_anywhere()
    candidates = list(
        Document.objects.exclude(pk__in=referenced_ids).order_by("id")
    )

    if not candidates:
        print(
            "No orphaned documents found "
            "(every document has at least one reference in the index)."
        )
        return 0

    total = len(candidates)
    n_total = Document.objects.count()
    print(
        f"Found {total} document(s) with no references anywhere "
        f"(out of {n_total} total)."
    )

    if not args.no_confirmation:
        answer = input(
            f"About to delete {total} unused document(s). "
            "Type 'yes' to confirm: "
        ).strip()
        if answer.lower() != "yes":
            print("Deletion cancelled.")
            return 0

    if args.dry_run:
        for doc in candidates:
            print(
                f"DRY RUN : would delete document id={doc.id} "
                f"title={doc.title!r}"
            )
        print(f"DRY RUN complete: {total} document(s) would be deleted.")
        return 0

    with transaction.atomic():
        for i, doc in enumerate(candidates, start=1):
            doc_id = doc.id
            title = doc.title
            doc.delete()
            print(f"[{i}/{total}] Deleted document id={doc_id} title={title!r}")

    print(f"Deleted {total} unused document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

