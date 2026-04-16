#!/usr/bin/env python
"""
Wrap disaron identifiers in BlogEntryPage bodies.

For each BlogEntryPage under a given BlogIndexPage (--parent-id), find text
matching DISARON_NOM_RE and wrap it as:

    <div id="disaron-nom">IraLeg25167</div>

The replacement is applied inside StreamField content (including nested
blocks), and pages are saved with update_fields=["body"] unless --dry-run
is passed.

Usage:
    just reformat_disaron \
        --wagtail-project-root ../agreste \
        --parent-id 30
"""

import argparse
import csv
import re
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    DISARON_NOM_RE,
    resolve_failures_file,
    resolve_pages,
)
from django.utils import timezone  # noqa: E402

DISARON_DIV_RE = re.compile(r'id\s*=\s*["\']disaron-nom["\']', re.IGNORECASE)


def _get_stream_data(body_value: Any) -> list[Any]:
    if hasattr(body_value, "stream_data"):
        return list(body_value.stream_data)
    if hasattr(body_value, "raw_data"):
        return list(body_value.raw_data)
    raise AttributeError(
        "Unsupported StreamField value: expected StreamValue with "
        "'stream_data' or 'raw_data'."
    )


RICH_TEXT_BLOCK_TYPES = {"richtext", "paragraph", "text"}
HTML_BLOCK_TYPES = {"html"}


def _reformat_blocks(stream_data: list[Any]) -> tuple[list[Any], int, str]:
    """
    Find rich-text blocks containing a DISARON token, remove them, and insert an
    HTML block containing <div id="disaron-nom">TOKEN</div>.

    Returns: (new_stream_data, replacement_count, first_disaron_nom_found)
    """
    out: list[Any] = []
    replacements = 0
    first_disaron = ""

    for block in stream_data:
        if (
            isinstance(block, dict)
            and block.get("type") in RICH_TEXT_BLOCK_TYPES
            and isinstance(block.get("value"), str)
        ):
            value: str = block["value"]
            if DISARON_DIV_RE.search(value):
                out.append(block)
                continue
            match = DISARON_NOM_RE.search(value)
            if match:
                disaron_nom = match.group(0)
                if not first_disaron:
                    first_disaron = disaron_nom

                new_block: dict[str, Any] = {
                    "type": "html",
                    "value": f'<div id="disaron-nom">{disaron_nom}</div>',
                }
                if "id" in block:
                    new_block["id"] = block["id"]
                out.append(new_block)
                replacements += 1
                continue

        out.append(block)

    return out, replacements, first_disaron


def _extract_disaron_from_existing_html(stream_data: list[Any]) -> str:
    """
    If the page was already reformatted, the disaron is expected to live in an
    HTML block containing id=\"disaron-nom\".
    """
    for block in stream_data:
        if (
            isinstance(block, dict)
            and block.get("type") in HTML_BLOCK_TYPES
            and isinstance(block.get("value"), str)
        ):
            value: str = block["value"]
            if not DISARON_DIV_RE.search(value):
                continue
            match = DISARON_NOM_RE.search(value)
            if match:
                return match.group(0)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing to the database.",
    )
    parser.add_argument(
        "--parent-id",
        type=int,
        required=True,
        help=(
            "ID of the BlogIndexPage parent used to select "
            "BlogEntryPage children."
        ),
    )
    parser.add_argument(
        "--failures-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for failures "
            "(columns: pageId, disaron_nom, error)."
        ),
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "reformat_disaron_failures"
    )
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    success_file = f"metadata_editor/output/{timestamp}_reformat_disaron_success.csv"
    noop_file = f"metadata_editor/output/{timestamp}_reformat_disaron_noop.csv"
    pages = resolve_pages(args.parent_id)
    page_count = pages.count()

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to wrap DISARON identifiers in {page_count} "
        "BlogEntryPage body(ies). Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    no_change = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)

    success_path = Path(success_file)
    success_path.parent.mkdir(parents=True, exist_ok=True)
    noop_path = Path(noop_file)
    noop_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open("w", encoding="utf-8", newline="") as failures_f,
        success_path.open("w", encoding="utf-8", newline="") as success_f,
        noop_path.open("w", encoding="utf-8", newline="") as noop_f,
    ):
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        success_writer = csv.DictWriter(
            success_f, fieldnames=["pageId", "disaron_nom", "replacements"]
        )
        success_writer.writeheader()
        noop_writer = csv.DictWriter(
            noop_f, fieldnames=["pageId", "disaron_nom"]
        )
        noop_writer.writeheader()

        def _fail(page: BlogEntryPage, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            failures_f.flush()
            print(
                f"[{updated + no_change}/{page_count}] "
                f"Skipped id={page.id}: {error}"
            )

        for page in pages:
            try:
                stream_data = _get_stream_data(page.body)
                new_stream, replacements, found_disaron = _reformat_blocks(stream_data)
            except Exception as exc:
                _fail(page, "", f"body transform failed: {exc}")
                continue

            if replacements == 0:
                already = _extract_disaron_from_existing_html(stream_data)
                if already:
                    no_change += 1
                    noop_writer.writerow(
                        {
                            "pageId": str(page.id),
                            "disaron_nom": already,
                        }
                    )
                    noop_f.flush()
                    continue

                no_change += 1
                _fail(page, "", "no DISARON match in rich text or html blocks")
                continue

            page.body = new_stream
            if args.dry_run:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(page, found_disaron, f"validation failed: {exc}")
                    continue
            else:
                try:
                    page.save(update_fields=["body"])
                except Exception as exc:
                    _fail(page, found_disaron, f"save failed: {exc}")
                    continue

            updated += 1
            success_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": found_disaron,
                    "replacements": str(replacements),
                }
            )
            success_f.flush()
            action = "Would update" if args.dry_run else "Updated"
            print(
                f"[{updated + no_change}/{page_count}] {action} "
                f"id={page.id} disaron={found_disaron} title={page.title!r} "
                f"replacements={replacements}"
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {success_file}.")
    print(f"Wrote {no_change} noop row(s) to {noop_file}.")
    summary_action = "Would update" if args.dry_run else "Updated"
    print(
        f"{summary_action} {updated} page(s); "
        f"{no_change} had no DISARON match."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
