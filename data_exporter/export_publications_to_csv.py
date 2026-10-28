#!/usr/bin/env python
"""
Export PublicationPage metadata to CSV.

Scans every ``PublicationPage`` under every ``PublicationIndexPage`` and
writes one CSV row per page.

Exported columns
----------------
- pageId — Wagtail page id
- disaron_id — ``page.disaron_id``
- type — ``data`` if any assigned Collection is (or is under) slug ``donnees``,
  else ``publication``
- standard — ``yes`` if body has a ``standard_publication`` block, else ``no``
- title — page title
- subtitle — StandardPublication ``subtitle``, else empty
- summary — StandardPublication ``summary``, else empty
- date_premiere_publication — ``page.date`` (ISO 8601)
- documents — JSON array of document titles from StandardPublication
  ``downloadable_documents``
- collections — slugs of assigned Collections with no parent (``|``, sorted)
- sous-collections — slugs of assigned Collections with a parent (``|``, sorted)
- themes — slugs of assigned Themes with no parent (``|``, sorted)
- sous-themes — slugs of assigned Themes with a parent (``|``, sorted)

Defaults:
- ``--output-file``: ``data_exporter/output/<YYYY-MM-DD_HH-MM-SS>_export_publications_to_csv.csv``

Usage::

    just export_publications_to_csv \\
        --wagtail-project-root ../agreste

    just export_publications_to_csv \\
        --wagtail-project-root ../agreste \\
        --output-file data_exporter/output/publications.csv \\
        --scalingo-env-file .env.test
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.documents.models import Document  # noqa: E402

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)

STANDARD_PUBLICATION_TYPE = "standard_publication"
DONNEES_COLLECTION_SLUG = "donnees"

FIELDNAMES = [
    "pageId",
    "disaron_id",
    "type",
    "standard",
    "title",
    "subtitle",
    "summary",
    "date_premiere_publication",
    "documents",
    "collections",
    "sous-collections",
    "themes",
    "sous-themes",
]


def _get_stream_data(body_value: Any) -> list[Any]:
    if hasattr(body_value, "stream_data"):
        return list(body_value.stream_data)
    if hasattr(body_value, "raw_data"):
        return list(body_value.raw_data)
    raise AttributeError(
        "Unsupported StreamField value: expected StreamValue with "
        "'stream_data' or 'raw_data'."
    )


def resolve_all_publication_pages() -> list[PublicationPage]:
    pages: list[PublicationPage] = []
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages.extend(
            list(
                PublicationPage.objects.child_of(index)
                .order_by("id")
                .prefetch_related(
                    "collections",
                    "collections__parent",
                    "themes",
                    "themes__parent",
                )
            )
        )
    pages.sort(key=lambda page: page.id)
    return pages


def _find_standard_publication(stream_data: list[Any]) -> dict[str, Any] | None:
    for block in stream_data:
        if not isinstance(block, dict):
            continue
        if block.get("type") != STANDARD_PUBLICATION_TYPE:
            continue
        value = block.get("value")
        if isinstance(value, dict):
            return value
    return None


def _document_id_from_item(item: Any) -> int | None:
    if isinstance(item, int):
        return item
    if isinstance(item, str) and item.isdigit():
        return int(item)
    if isinstance(item, dict):
        document = item.get("document")
        if isinstance(document, int):
            return document
        if isinstance(document, str) and document.isdigit():
            return int(document)
        if hasattr(document, "pk"):
            return int(document.pk)
        if isinstance(document, dict):
            for key in ("id", "pk"):
                raw = document.get(key)
                if isinstance(raw, int):
                    return raw
                if isinstance(raw, str) and raw.isdigit():
                    return int(raw)
    if hasattr(item, "pk"):
        return int(item.pk)
    return None


def _document_titles(standard_value: dict[str, Any] | None) -> list[str]:
    if not standard_value:
        return []
    raw_docs = standard_value.get("downloadable_documents") or []
    if not isinstance(raw_docs, list):
        return []

    ids: list[int] = []
    for item in raw_docs:
        doc_id = _document_id_from_item(item)
        if doc_id is not None:
            ids.append(doc_id)

    if not ids:
        return []

    docs_by_id = Document.objects.in_bulk(set(ids))
    titles: list[str] = []
    for doc_id in ids:
        doc = docs_by_id.get(doc_id)
        if doc is not None:
            titles.append(doc.title)
    return titles


def _split_by_parent(items: Any) -> tuple[list[str], list[str]]:
    roots: list[str] = []
    children: list[str] = []
    for item in items.all():
        slug = (getattr(item, "slug", None) or "").strip()
        if not slug:
            continue
        parent = getattr(item, "parent", None)
        if parent is None:
            roots.append(slug)
        else:
            children.append(slug)
    roots.sort()
    children.sort()
    return roots, children


def _page_type(page: PublicationPage) -> str:
    for collection in page.collections.all():
        slug = (getattr(collection, "slug", None) or "").strip()
        if slug == DONNEES_COLLECTION_SLUG:
            return "data"
        parent = getattr(collection, "parent", None)
        parent_slug = (getattr(parent, "slug", None) or "").strip() if parent else ""
        if parent_slug == DONNEES_COLLECTION_SLUG:
            return "data"
    return "publication"


def _page_row(page: PublicationPage) -> dict[str, str]:
    stream_data = _get_stream_data(page.body)
    standard = _find_standard_publication(stream_data)
    has_standard = standard is not None

    collections, sous_collections = _split_by_parent(page.collections)
    themes, sous_themes = _split_by_parent(page.themes)
    document_titles = _document_titles(standard)

    return {
        "pageId": str(page.id),
        "disaron_id": (getattr(page, "disaron_id", None) or "").strip(),
        "type": _page_type(page),
        "standard": "yes" if has_standard else "no",
        "title": page.title or "",
        "subtitle": (standard.get("subtitle") or "").strip() if standard else "",
        "summary": (standard.get("summary") or "").strip() if standard else "",
        "date_premiere_publication": (
            page.date.isoformat() if getattr(page, "date", None) else ""
        ),
        "documents": json.dumps(document_titles, ensure_ascii=False),
        "collections": "|".join(collections),
        "sous-collections": "|".join(sous_collections),
        "themes": "|".join(themes),
        "sous-themes": "|".join(sous_themes),
    }


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
        "--output-file",
        type=str,
        default="",
        help=(
            "Output CSV path. Defaults to "
            "data_exporter/output/<timestamp>_export_publications_to_csv.csv."
        ),
    )
    args = parser.parse_args()

    pages = resolve_all_publication_pages()
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = (
        Path(args.output_file)
        if args.output_file
        else Path(
            f"data_exporter/output/{timestamp}_export_publications_to_csv.csv"
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for page in pages:
            writer.writerow(_page_row(page))
            written += 1

    print(f"Exported {written} row(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
