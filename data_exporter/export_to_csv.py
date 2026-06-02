#!/usr/bin/env python
"""
Export BlogEntryPage metadata and categories to CSV.

Scans every ``BlogEntryPage`` child of a ``BlogIndexPage`` (``--parent-id``)
and writes one CSV row per page.

Exported columns
----------------
- pageId — Wagtail page id
- disaron_nom — from ``<div id="disaron-nom">`` in an html stream block
- titre — page title
- complement_titre — from ``<h2 id="complement-titre">``
- chapeau — from ``<div id="chapeau">``
- date_premiere_publication — ``page.date`` (ISO 8601)
- documents — JSON array of document titles linked from tile blocks
- collections — category names whose parent is ``Collections`` (``|``,
  sorted A→Z)
- sous-collections — category names whose grandparent is ``Collections``
  (``|``, sorted A→Z)
- themes — category names under ``Thématiques`` (child or grandchild;
  ``|``, ordered like themes_codes)
- themes_codes — ``NEW CODES`` from the correspondence CSV, looked up by
  matching the category name to ``THEMATIQUES NEW`` or ``theme`` (``|``,
  sorted A→Z by code)

Theme code lookup uses ``--correspondence-file`` (default:
``20260324-Correspondance-Thmatiques-OLD-NEW-v2.csv``). Every theme on a page
must resolve to a code; otherwise the row is skipped, an error is printed, a
``<output>_failures.csv`` file is written, and the process exits with code 1.

Usage::

    just export_to_csv \\
        --wagtail-project-root ../agreste \\
        --parent-id 18 \\
        --scalingo-env-file .env.test

    just export_to_csv \\
        --wagtail-project-root ../agreste \\
        --parent-id 18 \\
        --output-file data_exporter/output/export.csv \\
        --correspondence-file 20260324-Correspondance-Thmatiques-OLD-NEW-v2.csv
"""

import argparse
import csv
import json
import re
from html import unescape
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.documents.models import Document  # noqa: E402
from wagtail.models import Page  # noqa: E402

from sites_conformes.blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402

COLLECTIONS_ROOT_NAME = "Collections"
THEMATIQUES_ROOT_NAME = "Thématiques"
DEFAULT_CORRESPONDENCE_FILE = (
    "20260324-Correspondance-Thmatiques-OLD-NEW-v2.csv"
)


TAG_RE = re.compile(r"<[^>]+>")
ID_ELEMENT_RE_TEMPLATE = (
    r"<(?P<tag>[a-zA-Z0-9]+)[^>]*\bid\s*=\s*['\"]{id_value}['\"][^>]*>"
    r"(?P<inner>.*?)</(?P=tag)>"
)


def _get_stream_data(body_value: Any) -> list[Any]:
    if hasattr(body_value, "stream_data"):
        return list(body_value.stream_data)
    if hasattr(body_value, "raw_data"):
        return list(body_value.raw_data)
    raise AttributeError(
        "Unsupported StreamField value: expected StreamValue with "
        "'stream_data' or 'raw_data'."
    )


def _iter_html_values(node: Any):
    """Yield raw html strings from nested stream data structures."""
    if isinstance(node, dict):
        if node.get("type") == "html":
            value = node.get("value")
            if isinstance(value, str):
                yield value
        for value in node.values():
            if isinstance(value, (dict, list, tuple)):
                yield from _iter_html_values(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            if isinstance(item, (dict, list, tuple)):
                yield from _iter_html_values(item)
            elif (
                isinstance(item, tuple)
                and len(item) == 2
                and item[0] == "html"
                and isinstance(item[1], str)
            ):
                yield item[1]


def _to_plain_text(html_inner: str) -> str:
    text = TAG_RE.sub("", html_inner)
    return unescape(text).strip()


def _extract_by_id(html_values: list[str], id_value: str, expected_tag: str | None) -> str:
    escaped_id = re.escape(id_value)
    pattern = re.compile(
        ID_ELEMENT_RE_TEMPLATE.format(id_value=escaped_id),
        re.IGNORECASE | re.DOTALL,
    )
    for html in html_values:
        match = pattern.search(html)
        if not match:
            continue
        tag = (match.group("tag") or "").lower()
        if expected_tag is not None and tag != expected_tag.lower():
            continue
        return _to_plain_text(match.group("inner"))
    return ""


def _resolve_pages(parent_id: int):
    parent_page = Page.objects.get(id=parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        raise ValueError(
            f"Page id={parent_id} is not a BlogIndexPage "
            f"(got {type(parent_page).__name__})."
        )
    return BlogEntryPage.objects.child_of(parent_page).order_by("id")


def _iter_tile_document_ids(node: Any):
    """Yield document ids linked by tile blocks in nested stream data."""
    if isinstance(node, dict):
        if node.get("type") == "tile" and isinstance(node.get("value"), dict):
            tile_value = node["value"]
            link_value = tile_value.get("link")
            if isinstance(link_value, dict):
                document_id = link_value.get("document")
                if isinstance(document_id, int):
                    yield document_id
                elif isinstance(document_id, str) and document_id.isdigit():
                    yield int(document_id)
        for value in node.values():
            if isinstance(value, (dict, list, tuple)):
                yield from _iter_tile_document_ids(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            if isinstance(item, (dict, list, tuple)):
                yield from _iter_tile_document_ids(item)


def _load_theme_code_by_name(correspondence_file: Path) -> dict[str, str]:
    """Map theme display name (THEMATIQUES NEW / theme) -> NEW CODES."""
    if not correspondence_file.exists():
        raise FileNotFoundError(
            f"Theme correspondence file not found: {correspondence_file}"
        )

    mapping: dict[str, str] = {}
    with correspondence_file.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("NEW CODES") or "").strip()
            if not code:
                continue
            for column in ("THEMATIQUES NEW", "theme"):
                name = (row.get(column) or "").strip()
                if name:
                    mapping[name] = code
                    mapping[name.casefold()] = code
    return mapping


def _category_kind(category: Category) -> str | None:
    parent = category.parent
    grandparent = parent.parent if parent else None

    if parent and parent.name == COLLECTIONS_ROOT_NAME:
        return "collection"
    if grandparent and grandparent.name == COLLECTIONS_ROOT_NAME:
        return "sous_collection"

    if parent and parent.name == THEMATIQUES_ROOT_NAME:
        return "theme"
    if grandparent and grandparent.name == THEMATIQUES_ROOT_NAME:
        return "theme"
    return None


def _lookup_theme_code(
    name: str, theme_code_by_name: dict[str, str]
) -> str | None:
    if name in theme_code_by_name:
        return theme_code_by_name[name]
    return theme_code_by_name.get(name.casefold())


def _format_theme_fields(
    theme_names: list[str],
    theme_code_by_name: dict[str, str],
) -> tuple[str, str]:
    pairs: list[tuple[str, str]] = []
    missing: list[str] = []

    for name in sorted(set(theme_names)):
        code = _lookup_theme_code(name, theme_code_by_name)
        if not code:
            missing.append(name)
        else:
            pairs.append((code, name))

    if missing:
        raise ValueError(
            "theme(s) without NEW CODES in correspondence file: "
            + ", ".join(repr(name) for name in missing)
        )

    pairs.sort(key=lambda item: item[0])
    return (
        "|".join(name for _, name in pairs),
        "|".join(code for code, _ in pairs),
    )


def _page_category_fields(
    page: BlogEntryPage,
    theme_code_by_name: dict[str, str],
) -> dict[str, str]:
    collections: list[str] = []
    sous_collections: list[str] = []
    themes: list[str] = []

    for entry in page.entry_categories.select_related("category").all():
        category = entry.category
        kind = _category_kind(category)
        if kind == "collection":
            collections.append(category.name)
        elif kind == "sous_collection":
            sous_collections.append(category.name)
        elif kind == "theme":
            themes.append(category.name)

    collections.sort()
    sous_collections.sort()
    themes_sorted, themes_codes = _format_theme_fields(themes, theme_code_by_name)

    return {
        "collections": "|".join(collections),
        "sous-collections": "|".join(sous_collections),
        "themes": themes_sorted,
        "themes_codes": themes_codes,
    }


def _linked_document_titles(stream_data: list[Any]) -> list[str]:
    ids = list(_iter_tile_document_ids(stream_data))
    if not ids:
        return []
    docs_by_id = Document.objects.in_bulk(set(ids))
    titles: list[str] = []
    for doc_id in ids:
        doc = docs_by_id.get(doc_id)
        if doc is not None:
            titles.append(doc.title)
    return titles


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wagtail-project-root",
        type=str,
        default="",
        help="Root directory of the Wagtail/Django project (contains config/settings.py).",
    )
    parser.add_argument(
        "--scalingo-env-file",
        type=str,
        default="",
        help="Load environment values from this env file before Django setup.",
    )
    parser.add_argument(
        "--parent-id",
        type=int,
        required=True,
        help="ID of the BlogIndexPage parent.",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="",
        help=(
            "Output CSV path. Defaults to "
            "data_exporter/output/<timestamp>_export_to_csv.csv."
        ),
    )
    parser.add_argument(
        "--correspondence-file",
        type=str,
        default=DEFAULT_CORRESPONDENCE_FILE,
        help=(
            "CSV with THEMATIQUES NEW and NEW CODES columns "
            "for themes_codes lookup."
        ),
    )
    args = parser.parse_args()

    correspondence_path = Path(args.correspondence_file).expanduser()
    if not correspondence_path.is_absolute():
        correspondence_path = (Path.cwd() / correspondence_path).resolve()
    theme_code_by_name = _load_theme_code_by_name(correspondence_path)

    pages = _resolve_pages(args.parent_id).prefetch_related(
        "entry_categories__category",
        "entry_categories__category__parent",
        "entry_categories__category__parent__parent",
    )
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = (
        Path(args.output_file)
        if args.output_file
        else Path(f"data_exporter/output/{timestamp}_export_to_csv.csv")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "pageId",
        "disaron_nom",
        "titre",
        "complement_titre",
        "chapeau",
        "date_premiere_publication",
        "documents",
        "collections",
        "sous-collections",
        "themes",
        "themes_codes",
    ]
    written = 0
    errors = 0
    failures_path = output_path.with_name(
        output_path.stem + "_failures" + output_path.suffix
    )

    with output_path.open("w", encoding="utf-8", newline="") as f, failures_path.open(
        "w", encoding="utf-8", newline=""
    ) as failures_f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        failures_writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        failures_writer.writeheader()

        for page in pages:
            stream_data = _get_stream_data(page.body)
            html_values = list(_iter_html_values(stream_data))

            disaron_nom = _extract_by_id(
                html_values, id_value="disaron-nom", expected_tag="div"
            )
            complement_titre = _extract_by_id(
                html_values, id_value="complement-titre", expected_tag="h2"
            )
            chapeau = _extract_by_id(
                html_values, id_value="chapeau", expected_tag="div"
            )
            document_titles = _linked_document_titles(stream_data)

            try:
                category_fields = _page_category_fields(
                    page, theme_code_by_name
                )
            except ValueError as exc:
                errors += 1
                failures_writer.writerow(
                    {
                        "pageId": page.id,
                        "disaron_nom": disaron_nom,
                        "error": str(exc),
                    }
                )
                print(
                    f"ERROR id={page.id} disaron_nom={disaron_nom!r}: {exc}",
                    flush=True,
                )
                continue

            writer.writerow(
                {
                    "pageId": page.id,
                    "disaron_nom": disaron_nom,
                    "titre": page.title,
                    "complement_titre": complement_titre,
                    "chapeau": chapeau,
                    "date_premiere_publication": (
                        page.date.isoformat() if page.date else ""
                    ),
                    "documents": json.dumps(document_titles, ensure_ascii=False),
                    **category_fields,
                }
            )
            written += 1

    print(f"Exported {written} row(s) to {output_path}")
    if errors:
        print(
            f"Skipped {errors} row(s) due to theme code errors. "
            f"See {failures_path}."
        )
        return 1
    failures_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
