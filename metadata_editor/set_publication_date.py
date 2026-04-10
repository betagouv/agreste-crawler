#!/usr/bin/env python
"""
Update BlogEntryPage post dates to the current time.

Usage:
    just set_publication_date \
        --wagtail-project-root ../agreste --parent-id 30
"""

import argparse
import re

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402

DISARON_NOM_RE = re.compile(r"\b[A-Z][a-z]{2}[A-Z][a-z]{2}\d+\b")


def find_disaron_nom(page: BlogEntryPage) -> str | None:
    """
    Extract a disaron-like identifier from page body content.

    Expected format: XxxXxx followed by digits (e.g., IraLeg25167).
    """
    body_text = str(page.body)
    match = DISARON_NOM_RE.search(body_text)
    if match is None:
        return None
    return match.group(0)


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
    args = parser.parse_args()

    _ = args  # Arguments are consumed by setup_django via sys.argv.
    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        raise ValueError(
            f"Page id={args.parent_id} is not a BlogIndexPage "
            f"(got {type(parent_page).__name__})."
        )

    now = timezone.now()
    pages = BlogEntryPage.objects.child_of(parent_page).order_by("id")
    page_count = pages.count()

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to update {page_count} "
        f"BlogEntryPage object(s) to date={now}. "
        "Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    for page in pages:
        disaron_nom = find_disaron_nom(page)
        print(
            f"[{updated + 1}/{page_count}] "
            f"BlogEntryPage id={page.id} disaron_nom={disaron_nom!r}"
        )
        page.date = now
        if not args.dry_run:
            page.save(update_fields=["date"])
        updated += 1
        action = "Would update" if args.dry_run else "Updated"
        print(
            f"[{updated}/{page_count}] {action} "
            f"id={page.id} disaron_nom={disaron_nom!r} title={page.title!r}"
        )

    summary_action = "Would update" if args.dry_run else "Updated"
    print(f"{summary_action} {updated} BlogEntryPage object(s) to date={now}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
