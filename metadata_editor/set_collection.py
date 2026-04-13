#!/usr/bin/env python
"""
Update BlogEntryPage categories from the 'collection' column of a CSV file.

The CSV must contain 'disaron:nom' and 'collection' columns.  Each
BlogEntryPage's disaron identifier is looked up in that mapping and its
categories field is updated to the corresponding collection name.

Usage:
    just set_collection \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --data-file infos-rapides.csv
"""

import argparse

from django_setup import setup_django

setup_django(__file__)

from blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    add_common_args,
    load_csv_column,
    resolve_failures_file,
    resolve_pages,
    run_metadata_update,
)

COLLECTION_COLUMN = "collection"


def _apply_collection(page: BlogEntryPage, category_name: str) -> None:
    from blog.models import Category, CategoryEntryPage

    category = Category.objects.filter(name=category_name).first()
    if category is None:
        raise ValueError(f"Category {category_name!r} not found in database.")
    # blog_categories uses a custom through model (CategoryEntryPage), so
    # .set() is not available. Manage the through table directly.
    CategoryEntryPage.objects.filter(page=page).delete()
    CategoryEntryPage.objects.create(page=page, category=category)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "collection_failures"
    )
    pages = resolve_pages(args.parent_id)
    values_by_disaron_nom = load_csv_column(args.data_file, COLLECTION_COLUMN)

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=values_by_disaron_nom,
        apply_value=_apply_collection,
        update_fields=None,
        failures_file=failures_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} BlogEntryPage object(s) "
            f"using collections from {args.data_file}."
        ),
        success_log=lambda _i, _n, page, disaron_nom, value: (
            f"id={page.id} disaron_nom={disaron_nom!r} "
            f"collection={value!r} title={page.title!r}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
