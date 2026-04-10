#!/usr/bin/env python
"""
Find/fix malformed disaron identifiers in BlogEntryPage bodies.

Usage:
    just disaron_fixer \
        --wagtail-project-root ../agreste \
        --parent-id 30

    # Preview only (no DB write)
    just disaron_fixer \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --dry-run
"""

import argparse
import csv
import re
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402

PROPER_DISARON_RE = re.compile(r"\bIra[A-Z][a-z]{2}\d+\b")
BAD_DISARON_RE = re.compile(r"\bira([a-z]{3})(\d+)\b", re.IGNORECASE)


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_walk_strings(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_walk_strings(item))
        return out
    return []


def _replace_token_in_value(value: Any, bad_token: str, fixed_token: str) -> Any:
    if isinstance(value, str):
        return re.sub(
            rf"\b{re.escape(bad_token)}\b",
            fixed_token,
            value,
            flags=re.IGNORECASE,
        )
    if isinstance(value, list):
        return [
            _replace_token_in_value(item, bad_token, fixed_token)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_token_in_value(item, bad_token, fixed_token)
            for key, item in value.items()
        }
    return value


def _extract_proper_disaron(page: BlogEntryPage) -> str | None:
    match = PROPER_DISARON_RE.search(str(page.body))
    if match is None:
        return None
    return match.group(0)


def _extract_bad_disarons_from_stream_data(stream_data: list[Any]) -> list[str]:
    found: list[str] = []
    for text in _walk_strings(stream_data):
        for match in BAD_DISARON_RE.finditer(text):
            token = match.group(0)
            if token not in found:
                found.append(token)
    return found


def _to_fixed_disaron(bad_disaron: str) -> str:
    match = BAD_DISARON_RE.fullmatch(bad_disaron)
    if match is None:
        return bad_disaron
    letters = match.group(1)
    number = match.group(2)
    return f"Ira{letters.capitalize()}{number}"


def _get_stream_data(body_value: Any) -> list[Any]:
    """
    Return mutable stream data for both StreamValue variants.
    """
    if hasattr(body_value, "stream_data"):
        return list(body_value.stream_data)
    if hasattr(body_value, "raw_data"):
        return list(body_value.raw_data)
    raise AttributeError(
        "Unsupported StreamField value: expected StreamValue with "
        "'stream_data' or 'raw_data'."
    )


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
        "--parent-id",
        type=int,
        required=True,
        help=(
            "ID of the BlogIndexPage parent used to select "
            "BlogEntryPage children."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview fixes without saving body replacements.",
    )
    args = parser.parse_args()

    _ = args  # Arguments are consumed by setup_django via sys.argv.
    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        raise ValueError(
            f"Page id={args.parent_id} is not a BlogIndexPage "
            f"(got {type(parent_page).__name__})."
        )

    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    failures_file = (
        f"metadata_editor/output/{timestamp}_disaron_fixer_failures.csv"
    )
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)

    pages = BlogEntryPage.objects.child_of(parent_page).order_by("id")
    page_count = pages.count()
    fixed_count = 0
    failures_count = 0

    with failures_path.open("w", encoding="utf-8", newline="") as failures_f:
        writer = csv.DictWriter(
            failures_f,
            fieldnames=["pageId", "bad_disaron_nom", "fixed_disaron_nom", "error"],
        )
        writer.writeheader()

        for index, page in enumerate(pages, start=1):
            proper_disaron = _extract_proper_disaron(page)
            if proper_disaron is not None:
                print(
                    f"[{index}/{page_count}] id={page.id} "
                    f"already has proper disaron_nom={proper_disaron!r}"
                )
                continue

            stream_data = _get_stream_data(page.body)
            bad_disarons = _extract_bad_disarons_from_stream_data(stream_data)
            if not bad_disarons:
                failures_count += 1
                writer.writerow(
                    {
                        "pageId": str(page.id),
                        "bad_disaron_nom": "",
                        "fixed_disaron_nom": "",
                    "error": (
                        "no properly formed disaron_nom and "
                        "no bad iraxxx+digits found"
                    ),
                    }
                )
                failures_f.flush()
                print(
                    f"[{index}/{page_count}] id={page.id} "
                    "no disaron_nom found (proper or fixable)."
                )
                continue

            page_updated = False
            for bad_disaron in bad_disarons:
                fixed_disaron = _to_fixed_disaron(bad_disaron)
                answer = input(
                    f"[{index}/{page_count}] Page id={page.id} "
                    f"title={page.title!r}: "
                    f"replace {bad_disaron!r} -> {fixed_disaron!r}? "
                    "Type 'yes' to confirm: "
                ).strip()
                if answer.lower() != "yes":
                    failures_count += 1
                    writer.writerow(
                        {
                            "pageId": str(page.id),
                            "bad_disaron_nom": bad_disaron,
                            "fixed_disaron_nom": fixed_disaron,
                            "error": "replacement not confirmed by user",
                        }
                    )
                    failures_f.flush()
                    print(
                        f"[{index}/{page_count}] Skipped replacement "
                        f"{bad_disaron!r} -> {fixed_disaron!r}."
                    )
                    continue

                print(
                    f"[{index}/{page_count}] Confirmed replacement "
                    f"{bad_disaron!r} -> {fixed_disaron!r} "
                    f"for page id={page.id}."
                )
                writer.writerow(
                    {
                        "pageId": str(page.id),
                        "bad_disaron_nom": bad_disaron,
                        "fixed_disaron_nom": fixed_disaron,
                        "error": "replacement confirmed by user",
                    }
                )
                failures_f.flush()
                stream_data = _replace_token_in_value(
                    stream_data, bad_disaron, fixed_disaron
                )
                page_updated = True

            if not page_updated:
                continue

            if args.dry_run:
                fixed_count += 1
                print(
                    f"[{index}/{page_count}] [DRY RUN] Would save fixes "
                    f"for page id={page.id}."
                )
                continue

            page.body = stream_data
            try:
                page.save(update_fields=["body"])
            except Exception as exc:
                failures_count += 1
                writer.writerow(
                    {
                        "pageId": str(page.id),
                        "bad_disaron_nom": ";".join(bad_disarons),
                        "fixed_disaron_nom": "",
                        "error": f"save failed: {exc}",
                    }
                )
                failures_f.flush()
                print(
                    f"[{index}/{page_count}] Save failed for page "
                    f"id={page.id}: {exc}"
                )
                continue

            fixed_count += 1
            print(f"[{index}/{page_count}] Saved fixes for page id={page.id}.")

    action = "Would fix" if args.dry_run else "Fixed"
    print(f"{action} {fixed_count} page(s).")
    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
