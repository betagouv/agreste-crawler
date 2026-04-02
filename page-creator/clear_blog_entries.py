#!/usr/bin/env python
"""
Delete all direct child pages under a given parent page.

Usage:
    uv run python page-creator/clear_blog_entries.py \
        --wagtail-project-root ../agreste --parent-id 30
    uv run python page-creator/clear_blog_entries.py \
        --wagtail-project-root ../agreste --parent-id 30 --no-confirmation
    uv run python page-creator/clear_blog_entries.py \
        --wagtail-project-root ../agreste --parent-id 30 --dry-run
    uv run python page-creator/clear_blog_entries.py \
        --wagtail-project-root ../agreste --parent-id 30 --dry-run --no-confirmation
    uv run python page-creator/clear_blog_entries.py \
        --wagtail-project-root ../agreste \
        --scalingo-env-file /path/to/.env.scalingo --parent-id 30 --dry-run
    uv run python page-creator/clear_blog_entries.py \
        --wagtail-project-root ../agreste \
        --scalingo-env-file /path/to/.env.scalingo \
        --parent-id 30 --no-confirmation
"""

import argparse

from django_env_setup import setup_django

setup_django(__file__)

from django.db import transaction  # noqa: E402
from wagtail.models import Page  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--parent-id",
        type=int,
        required=True,
        help="ID of the parent page",
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
        help="Preview deletions without deleting pages.",
    )
    args = parser.parse_args()

    parent_page = Page.objects.get(id=args.parent_id).specific
    children = list(parent_page.get_children().specific())

    def _run_fix_tree() -> None:
        # Always repair/check tree metadata, even when nothing is deleted.
        Page.fix_tree(destructive=False)
        print("Ran Page.fix_tree(destructive=False).")

    if not children:
        print(f"No child pages found under parent id={args.parent_id}.")
        _run_fix_tree()
        return 0

    total = len(children)
    if not args.no_confirmation:
        answer = input(
            f"About to delete {total} child page(s) "
            f"under parent id={args.parent_id}. "
            "Type 'yes' to confirm: "
        ).strip()
        if answer.lower() != "yes":
            print("Deletion cancelled.")
            _run_fix_tree()
            return 0

    if args.dry_run:
        for child in children:
            print(f"DRY RUN : not deleting page id={child.id}")
    else:
        # Keep the whole operation atomic so we don't leave
        # a half-deleted subtree.
        with transaction.atomic():
            for i, child in enumerate(children, start=1):
                title = child.title
                child_id = child.id
                child.delete()
                print(
                    f"[{i}/{total}] Deleted child page id={child_id} "
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
