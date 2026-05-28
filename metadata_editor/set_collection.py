#!/usr/bin/env python
"""
Update BlogEntryPage categories from a configurable collections column
in a CSV file.

The CSV must contain 'disaron:nom' and a collections column (default:
'collection'). Each
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
    resolve_successes_file,
    run_metadata_update,
)

COLLECTION_COLUMN = "collection"


def _apply_collection(
    page: BlogEntryPage, category_name: str
) -> dict[str, str]:
    from blog.models import Category, CategoryEntryPage

    matches = list(Category.objects.filter(name__iexact=category_name))
    if not matches:
        raise ValueError(f"Category {category_name!r} not found in database.")
    if len(matches) > 1:
        matched_names = ", ".join(repr(category.name) for category in matches)
        raise ValueError(
            f"Multiple categories match {category_name!r} "
            f"(case-insensitive): {matched_names}"
        )
    category = matches[0]
    info = ""
    if category.name != category_name:
        info = (
            f"Input collection {category_name!r} matched "
            f"{category.name!r} case-insensitively."
        )
        print(
            f"[INFO] id={page.id} title={page.title!r}: "
            f"matched collection {category_name!r} to {category.name!r} "
            "case-insensitively."
        )
    # blog_categories uses a custom through model (CategoryEntryPage), so
    # .set() is not available. Manage the through table directly.
    CategoryEntryPage.objects.filter(page=page).delete()
    CategoryEntryPage.objects.create(page=page, category=category)
    return {"value": category.name, "info": info}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--collection-column",
        default=COLLECTION_COLUMN,
        help=(
            "Name of the CSV column containing collection names "
            f"(default: {COLLECTION_COLUMN!r})."
        ),
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "collection_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "collection_successes"
    )
    pages = resolve_pages(args.parent_id)
    values_by_disaron_nom = load_csv_column(
        args.data_file, args.collection_column
    )

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=values_by_disaron_nom,
        apply_value=_apply_collection,
        update_fields=None,
        failures_file=failures_file,
        successes_file=successes_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} BlogEntryPage object(s) "
            f"using collections from column {args.collection_column!r} in "
            f"{args.data_file}."
        ),
        success_log=lambda _i, _n, page, disaron_nom, value: (
            f"id={page.id} disaron_nom={disaron_nom!r} "
            f"collection={value!r} title={page.title!r}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
