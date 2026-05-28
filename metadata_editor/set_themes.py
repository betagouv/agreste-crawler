#!/usr/bin/env python
"""
Update BlogEntryPage themes from the 'themes' column of a CSV file.

The CSV must contain 'disaron:nom' and 'themes' columns. The 'themes'
value can contain multiple labels separated by "|".

Each theme label must correspond to an existing Category that is a child
or grandchild of the Category named "Thématiques".

After the script runs, only themes listed in the CSV will remain on each page.
Any preexisting themes not listed in the CSV are removed.

Usage:
    just set_themes \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --data-file infos-rapides.csv
"""

import argparse
from collections import OrderedDict
from typing import Iterable

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

THEMES_COLUMN = "themes"
THEMATIQUES_CATEGORY_NAME = "Thématiques"

_ALLOWED_THEMES_BY_NAME: dict[str, object] | None = None


def _parse_themes(raw_value: str) -> list[str]:
    # Keep order and drop duplicates/empties from pipe-separated values.
    return list(
        OrderedDict.fromkeys(
            part.strip() for part in raw_value.split("|") if part.strip()
        )
    )


def _get_theme_categories_from_site():
    from django.db.models import Q

    from blog.models import Category

    # Allowed themes must map to categories under "Thématiques":
    # direct children or grandchildren only.
    thematiques = Category.objects.filter(
        name=THEMATIQUES_CATEGORY_NAME
    ).first()
    if thematiques is None:
        raise ValueError(
            f'Category {THEMATIQUES_CATEGORY_NAME!r} not found in database.'
        )

    if hasattr(thematiques, "get_descendants"):
        return thematiques.get_descendants().distinct()

    category_field_names = {f.name for f in Category._meta.get_fields()}
    if "parent" in category_field_names:
        return Category.objects.filter(
            Q(parent=thematiques) | Q(parent__parent=thematiques)
        ).distinct()

    if hasattr(thematiques, "get_children"):
        children = list(thematiques.get_children())
        if not children:
            return Category.objects.none()
        child_ids = [c.id for c in children]
        return Category.objects.filter(
            Q(id__in=child_ids) | Q(parent_id__in=child_ids)
        ).distinct()

    raise ValueError(
        "Unable to resolve Category hierarchy for themes "
        "(missing parent/get_children/get_descendants support)."
    )


def _allowed_themes_by_name() -> dict[str, object]:
    global _ALLOWED_THEMES_BY_NAME
    if _ALLOWED_THEMES_BY_NAME is not None:
        return _ALLOWED_THEMES_BY_NAME

    categories = _get_theme_categories_from_site()
    _ALLOWED_THEMES_BY_NAME = {
        str(category.name).strip(): category for category in categories
    }
    if not _ALLOWED_THEMES_BY_NAME:
        raise ValueError(
            "No child/grandchild categories found under "
            f"{THEMATIQUES_CATEGORY_NAME!r}."
        )
    return _ALLOWED_THEMES_BY_NAME


def _resolve_theme_categories(theme_names: Iterable[str]) -> list[object]:
    allowed = _allowed_themes_by_name()
    missing = [name for name in theme_names if name not in allowed]
    if missing:
        raise ValueError(
            "theme category not found under "
            f"{THEMATIQUES_CATEGORY_NAME!r}: "
            f"{', '.join(repr(m) for m in missing)}"
        )
    return [allowed[name] for name in theme_names]


def _remove_themes(page: BlogEntryPage) -> None:
    from blog.models import CategoryEntryPage

    # Remove only theme links (children/grandchildren of Thématiques).
    theme_category_ids = _get_theme_categories_from_site().values_list(
        "id", flat=True
    )
    CategoryEntryPage.objects.filter(
        page=page, category_id__in=theme_category_ids
    ).delete()


def _apply_themes(page: BlogEntryPage, raw_themes: str) -> None:
    from blog.models import CategoryEntryPage

    theme_names = _parse_themes(raw_themes)
    if not theme_names:
        raise ValueError("themes value is empty after parsing")

    categories = _resolve_theme_categories(theme_names)
    _remove_themes(page)
    for category in categories:
        CategoryEntryPage.objects.create(page=page, category=category)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "themes_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "themes_successes"
    )
    pages = resolve_pages(args.parent_id)
    values_by_disaron_nom = load_csv_column(args.data_file, THEMES_COLUMN)

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=values_by_disaron_nom,
        apply_value=_apply_themes,
        update_fields=None,
        failures_file=failures_file,
        successes_file=successes_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} BlogEntryPage object(s) "
            f"using themes from {args.data_file}."
        ),
        success_log=lambda _i, _n, page, disaron_nom, value: (
            f"id={page.id} disaron_nom={disaron_nom!r} "
            f"themes={_parse_themes(value)!r} title={page.title!r}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
