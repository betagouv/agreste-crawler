#!/usr/bin/env python
"""
Export BlogEntryPage metadata to CSV.

For each BlogEntryPage under a given BlogIndexPage (--parent-id), export:
- pageId
- disaron_nom (from <div id="disaron-nom"> in an html block)
- titre (page title)
- complement_titre (from <h2 id="complement-titre"> in an html block)
- chapeau (from <div id="chapeau"> in an html block)

Flags with examples:
    --parent-id 30
            ID of the BlogIndexPage parent (required).
    --wagtail-project-root ../agreste
            Path to Django/Wagtail project root.
    --scalingo-env-file .env.prod
            Env file loaded before Django setup.
    --output-file data_exporter/output/export.csv
            Output CSV file path. If omitted, a timestamped path is used.
"""

import argparse
import csv
import re
from html import unescape
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export BlogEntryPage metadata to CSV."
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
    args = parser.parse_args()

    pages = _resolve_pages(args.parent_id)
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = (
        Path(args.output_file)
        if args.output_file
        else Path(f"data_exporter/output/{timestamp}_export_to_csv.csv")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["pageId", "disaron_nom", "titre", "complement_titre", "chapeau"]
    written = 0

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

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

            writer.writerow(
                {
                    "pageId": page.id,
                    "disaron_nom": disaron_nom,
                    "titre": page.title,
                    "complement_titre": complement_titre,
                    "chapeau": chapeau,
                }
            )
            written += 1

    print(f"Exported {written} row(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
