#!/usr/bin/env python
"""
Update BlogEntryPage categories from a configurable collections column
in a CSV file.

The CSV must contain 'disaron:nom' and a collections column (default:
'collection'). Each
BlogEntryPage's disaron identifier is looked up in that mapping and the
corresponding collection category is added. Pre-existing categories are
left unchanged. Rows with an empty collection column, or where the
collection is already assigned, are logged as noop (in a separate CSV).

Usage:
    just set_collection \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --data-file infos-rapides.csv
"""

import argparse

from django_setup import setup_django

setup_django(__file__)

from sites_conformes.blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    add_common_args,
    load_csv_column,
    resolve_failures_file,
    resolve_noops_file,
    resolve_pages,
    resolve_successes_file,
    run_metadata_update,
)

COLLECTION_COLUMN = "collection"


def _category_on_page(page: BlogEntryPage, category_name: str) -> bool:
    from sites_conformes.blog.models import CategoryEntryPage

    return CategoryEntryPage.objects.filter(
        page_id=page.pk,
        category__locale_id=page.locale_id,
        category__name__iexact=category_name,
    ).exists()


def _resolve_category(page: BlogEntryPage, category_name: str):
    from sites_conformes.blog.models import Category

    matches = list(
        Category.objects.filter(
            name__iexact=category_name,
            locale_id=page.locale_id,
        )
    )
    if not matches:
        raise ValueError(
            f"Category {category_name!r} not found in database "
            f"for locale {page.locale_id}."
        )
    if len(matches) > 1:
        matched_names = ", ".join(repr(category.name) for category in matches)
        raise ValueError(
            f"Multiple categories match {category_name!r} "
            f"(case-insensitive): {matched_names}"
        )
    return matches[0]


def _apply_collection(
    page: BlogEntryPage, category_name: str, *, dry_run: bool = False
) -> dict[str, str]:
    from sites_conformes.blog.models import CategoryEntryPage

    category = _resolve_category(page, category_name)
    info_parts: list[str] = []
    if category.name != category_name:
        msg = (
            f"Input collection {category_name!r} matched "
            f"{category.name!r} case-insensitively."
        )
        info_parts.append(msg)
        print(
            f"[INFO] id={page.id} title={page.title!r}: "
            f"matched collection {category_name!r} to {category.name!r} "
            "case-insensitively."
        )

    if _category_on_page(page, category_name):
        msg = f"noop: category {category.name!r} was already assigned to this page."
        if info_parts:
            msg = f"{msg} {' '.join(info_parts)}"
        return {"noop": True, "info": msg, "value": category.name}

    if dry_run:
        info_parts.append(
            f"Would assign category {category.name!r} to this page."
        )
        return {"value": category.name, "info": " ".join(info_parts)}

    # blog_categories uses a custom through model (CategoryEntryPage), so
    # .set() is not available. Manage the through table directly.
    CategoryEntryPage.objects.create(page=page, category=category)
    return {"value": category.name, "info": " ".join(info_parts)}


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
    noops_file = resolve_noops_file(args.noops_file, "collection_noops")
    pages = resolve_pages(args.parent_id)
    values_by_disaron_nom = load_csv_column(
        args.data_file,
        args.collection_column,
        include_empty_as_noop=True,
    )

    def apply_value(page: BlogEntryPage, value: str) -> dict[str, str]:
        return _apply_collection(page, value, dry_run=args.dry_run)

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=values_by_disaron_nom,
        apply_value=apply_value,
        update_fields=None,
        failures_file=failures_file,
        successes_file=successes_file,
        noops_file=noops_file,
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
        noop_info=f"noop: {args.collection_column!r} is empty",
    )


if __name__ == "__main__":
    raise SystemExit(main())
