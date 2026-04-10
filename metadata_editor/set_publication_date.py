#!/usr/bin/env python
"""
Update BlogEntryPage post dates to the current time.

Usage:
    just set_publication_date \
        --wagtail-project-root ../agreste --parent-id 30
"""

import argparse

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402


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

    if args.dry_run:
        print(
            f"[DRY RUN] Would update {page_count} "
            f"BlogEntryPage object(s) to date={now}."
        )
        return 0

    answer = input(
        f"About to update {page_count} BlogEntryPage object(s) to date={now}. "
        "Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print("Update cancelled.")
        return 0

    updated = 0
    for page in pages:
        page.date = now
        page.save(update_fields=["date"])
        updated += 1
        print(
            f"[{updated}/{page_count}] Updated BlogEntryPage "
            f"id={page.id} title={page.title!r}"
        )

    print(f"Updated {updated} BlogEntryPage object(s) to date={now}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
