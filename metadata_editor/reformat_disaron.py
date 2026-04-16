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


def _wrap_disaron_in_text(text: str) -> tuple[str, int]:
    if DISARON_DIV_RE.search(text):
        return text, 0
    return DISARON_NOM_RE.subn(r'<div id="disaron-nom">\g<0></div>', text)


def _transform_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return _wrap_disaron_in_text(value)
    if isinstance(value, list):
        total = 0
        out: list[Any] = []
        for item in value:
            item_out, n = _transform_value(item)
            total += n
            out.append(item_out)
        return out, total
    if isinstance(value, dict):
        total = 0
        out: dict[str, Any] = {}
        for key, item in value.items():
            item_out, n = _transform_value(item)
            total += n
            out[key] = item_out
        return out, total
    return value, 0


def _transform_stream_data(stream_data: list[Any]) -> tuple[list[Any], int]:
    total = 0
    out: list[Any] = []
    for block in stream_data:
        block_out, n = _transform_value(block)
        total += n
        out.append(block_out)
    return out, total


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

    with (
        failures_path.open("w", encoding="utf-8", newline="") as failures_f,
        success_path.open("w", encoding="utf-8", newline="") as success_f,
    ):
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        success_writer = csv.DictWriter(
            success_f, fieldnames=["pageId", "disaron_nom", "replacements"]
        )
        success_writer.writeheader()

        def _fail(page: BlogEntryPage, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": "",
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
                new_stream, replacements = _transform_stream_data(stream_data)
            except Exception as exc:
                _fail(page, f"body transform failed: {exc}")
                continue

            if replacements == 0:
                no_change += 1
                _fail(page, "no DISARON match in body")
                continue

            page.body = new_stream
            if args.dry_run:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(page, f"validation failed: {exc}")
                    continue
            else:
                try:
                    page.save(update_fields=["body"])
                except Exception as exc:
                    _fail(page, f"save failed: {exc}")
                    continue

            updated += 1
            disaron_match = DISARON_NOM_RE.search(str(page.body))
            disaron_nom = disaron_match.group(0) if disaron_match else ""
            success_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "replacements": str(replacements),
                }
            )
            success_f.flush()
            action = "Would update" if args.dry_run else "Updated"
            print(
                f"[{updated + no_change}/{page_count}] {action} "
                f"id={page.id} title={page.title!r} "
                f"replacements={replacements}"
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {success_file}.")
    summary_action = "Would update" if args.dry_run else "Updated"
    print(
        f"{summary_action} {updated} page(s); "
        f"{no_change} had no DISARON match."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
