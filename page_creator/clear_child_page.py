#!/usr/bin/env python
"""
Delete direct child ``BlogEntryPage`` / ``PublicationPage`` pages under a parent.

Usage:
    uv run python -m page_creator.clear_child_page \
        --wagtail-project-root ../agreste --parent-id 30
    uv run python -m page_creator.clear_child_page \
        --wagtail-project-root ../agreste --parent-id 30 --no-confirmation
    uv run python -m page_creator.clear_child_page \
        --wagtail-project-root ../agreste --parent-id 30 --dry-run
    uv run python -m page_creator.clear_child_page \
        --wagtail-project-root ../agreste --parent-id 30 --dry-run --no-confirmation
    uv run python -m page_creator.clear_child_page \
        --wagtail-project-root ../agreste \
        --scalingo-app agreste-test --parent-id 30 --dry-run
    uv run python -m page_creator.clear_child_page \
        --wagtail-project-root ../agreste \
        --scalingo-app agreste-test \
        --parent-id 30 --no-confirmation
"""

from __future__ import annotations

import argparse

from scalingo_env_setup import (
    add_scalingo_app_args,
    configure_database_url,
    ensure_ssh_tunnel,
)

configure_database_url()

from django_setup import setup_django

setup_django(__file__)
ensure_ssh_tunnel()

from django.db import transaction  # noqa: E402
from wagtail.models import Page  # noqa: E402

from publications.models import PublicationPage  # noqa: E402
from sites_conformes.blog.models import BlogEntryPage  # noqa: E402

DELETABLE_CHILD_TYPES = (BlogEntryPage, PublicationPage)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--parent-id",
        type=int,
        required=True,
        help="ID of the parent page (e.g. BlogIndexPage or PublicationIndexPage)",
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
        "--no-confirmation",
        action="store_true",
        help="Skip confirmation prompt and delete immediately.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without deleting pages.",
    )
    args = parser.parse_args()
    _ = (args.scalingo_app, args.database_port)  # consumed via sys.argv earlier

    parent_page = Page.objects.get(id=args.parent_id).specific
    children = [
        child
        for child in parent_page.get_children().specific()
        if isinstance(child, DELETABLE_CHILD_TYPES)
    ]

    def _run_fix_tree() -> None:
        # Always repair/check tree metadata, even when nothing is deleted.
        Page.fix_tree(destructive=False)
        print("Ran Page.fix_tree(destructive=False).")

    if not children:
        print(
            f"No BlogEntryPage/PublicationPage children found "
            f"under parent id={args.parent_id}."
        )
        _run_fix_tree()
        return 0

    total = len(children)
    if not args.no_confirmation:
        answer = input(
            f"About to delete {total} child page(s) "
            f"(BlogEntryPage/PublicationPage) "
            f"under parent id={args.parent_id}. "
            "Type 'yes' to confirm: "
        ).strip()
        if answer.lower() != "yes":
            print("Deletion cancelled.")
            _run_fix_tree()
            return 0

    if args.dry_run:
        for child in children:
            print(
                f"DRY RUN : not deleting {type(child).__name__} "
                f"id={child.id} title={child.title!r}"
            )
    else:
        # Keep the whole operation atomic so we don't leave
        # a half-deleted subtree.
        with transaction.atomic():
            for i, child in enumerate(children, start=1):
                title = child.title
                child_id = child.id
                child_type = type(child).__name__
                child.delete()
                print(
                    f"[{i}/{total}] Deleted {child_type} id={child_id} "
                    f"title={title!r}"
                )

            # Recompute tree metadata after bulk child deletions
            # to avoid subsequent
            # add_child() failures caused by stale numchild/path state.
            _run_fix_tree()

    if args.dry_run:
        print(
            f"DRY RUN complete: {total} child page(s) would be deleted "
            f"under parent id={args.parent_id}."
        )
        _run_fix_tree()
    else:
        print(
            f"Deleted {total} child page(s) "
            f"under parent id={args.parent_id}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
