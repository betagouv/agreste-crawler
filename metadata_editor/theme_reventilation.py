#!/usr/bin/env python
"""
Replace aggregate theme categories on BlogEntryPage objects with finer themes.

Reads a reventilage CSV (columns ``theme_a_reventiler`` and
``themes_reventiles``, pipe-separated for multiple target themes). For each
row, finds child BlogEntryPage objects under ``--parent-id`` that have the
old theme category assigned, removes that category, and adds the new theme
categories.

Failures are logged when a category name is not found, or when a category
is not a child or grandchild of ``Thématiques``. Already-assigned target
categories are kept and noted in the success log (not failures).

Usage:
    just theme_reventilation \\
        --wagtail-project-root ../agreste \\
        --parent-id 18 \\
        --data-file 2026-06-01_reventilage.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from django_setup import setup_django

setup_django(__file__)

from sites_conformes.blog.models import (  # noqa: E402
    BlogEntryPage,
    Category,
    CategoryEntryPage,
)

from metadata_editor.set_metadata import (  # noqa: E402
    find_disaron_nom,
    resolve_failures_file,
    resolve_pages,
    resolve_successes_file,
)

THEMATIQUES_CATEGORY_NAME = "Thématiques"
THEME_A_REVENTILER_COLUMN = "theme_a_reventiler"
THEMES_REVENTILES_COLUMN = "themes_reventiles"

_THEME_CATEGORY_IDS: set[int] | None = None


@dataclass(frozen=True)
class ReventilationRule:
    code_a_reventiler: str
    theme_a_reventiler: str
    themes_reventiles: tuple[str, ...]


def _flush_csv_file(csv_file) -> None:
    csv_file.flush()
    os.fsync(csv_file.fileno())


def _parse_pipe_list(raw_value: str) -> list[str]:
    return list(
        OrderedDict.fromkeys(
            part.strip() for part in raw_value.split("|") if part.strip()
        )
    )


def _get_theme_category_ids() -> set[int]:
    global _THEME_CATEGORY_IDS
    if _THEME_CATEGORY_IDS is not None:
        return _THEME_CATEGORY_IDS

    from django.db.models import Q

    thematiques = Category.objects.filter(
        name=THEMATIQUES_CATEGORY_NAME
    ).first()
    if thematiques is None:
        raise ValueError(
            f"Category {THEMATIQUES_CATEGORY_NAME!r} not found in database."
        )

    if hasattr(thematiques, "get_descendants"):
        categories = thematiques.get_descendants().distinct()
    elif "parent" in {f.name for f in Category._meta.get_fields()}:
        categories = Category.objects.filter(
            Q(parent=thematiques) | Q(parent__parent=thematiques)
        ).distinct()
    elif hasattr(thematiques, "get_children"):
        children = list(thematiques.get_children())
        if not children:
            categories = Category.objects.none()
        else:
            child_ids = [child.id for child in children]
            categories = Category.objects.filter(
                Q(id__in=child_ids) | Q(parent_id__in=child_ids)
            ).distinct()
    else:
        raise ValueError(
            "Unable to resolve Category hierarchy for themes "
            "(missing parent/get_children/get_descendants support)."
        )

    _THEME_CATEGORY_IDS = set(categories.values_list("id", flat=True))
    if not _THEME_CATEGORY_IDS:
        raise ValueError(
            "No child/grandchild categories found under "
            f"{THEMATIQUES_CATEGORY_NAME!r}."
        )
    return _THEME_CATEGORY_IDS


def _is_allowed_theme_category(category: Category) -> bool:
    return category.id in _get_theme_category_ids()


def _load_rules(data_file: str) -> list[ReventilationRule]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    rules: list[ReventilationRule] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        if THEME_A_REVENTILER_COLUMN not in reader.fieldnames:
            raise ValueError(
                f"Data CSV must contain {THEME_A_REVENTILER_COLUMN!r} column."
            )
        if THEMES_REVENTILES_COLUMN not in reader.fieldnames:
            raise ValueError(
                f"Data CSV must contain {THEMES_REVENTILES_COLUMN!r} column."
            )

        for row in reader:
            theme_a_reventiler = (
                row.get(THEME_A_REVENTILER_COLUMN) or ""
            ).strip()
            themes_reventiles = _parse_pipe_list(
                row.get(THEMES_REVENTILES_COLUMN) or ""
            )
            if not theme_a_reventiler or not themes_reventiles:
                continue
            rules.append(
                ReventilationRule(
                    code_a_reventiler=(
                        row.get("code_a_reventiler") or ""
                    ).strip(),
                    theme_a_reventiler=theme_a_reventiler,
                    themes_reventiles=tuple(themes_reventiles),
                )
            )

    if not rules:
        raise ValueError("No reventilation rules found in data CSV.")
    return rules


def _resolve_category(page: BlogEntryPage, category_name: str) -> Category:
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


def _old_theme_entry(
    page: BlogEntryPage, theme_name: str
) -> CategoryEntryPage | None:
    return (
        CategoryEntryPage.objects.filter(
            page_id=page.pk,
            category__locale_id=page.locale_id,
            category__name__iexact=theme_name,
        )
        .select_related("category")
        .first()
    )


def _category_on_page(page: BlogEntryPage, category: Category) -> bool:
    return CategoryEntryPage.objects.filter(
        page_id=page.pk, category_id=category.id
    ).exists()


def _reventilate_page(
    page: BlogEntryPage,
    rule: ReventilationRule,
    *,
    dry_run: bool,
) -> dict[str, str]:
    entry = _old_theme_entry(page, rule.theme_a_reventiler)
    if entry is None:
        return {"noop": True, "info": "page does not have old theme category"}

    old_category = entry.category
    if not _is_allowed_theme_category(old_category):
        raise ValueError(
            f"old category {old_category.name!r} is not a child or grandchild "
            f"of {THEMATIQUES_CATEGORY_NAME!r}"
        )

    new_categories: list[Category] = []
    for theme_name in rule.themes_reventiles:
        category = _resolve_category(page, theme_name)
        if not _is_allowed_theme_category(category):
            raise ValueError(
                f"category {category.name!r} is not a child or grandchild "
                f"of {THEMATIQUES_CATEGORY_NAME!r}"
            )
        new_categories.append(category)

    info_parts: list[str] = []
    to_add: list[Category] = []
    for category in new_categories:
        if _category_on_page(page, category):
            info_parts.append(
                f"category {category.name!r} was already assigned to this page"
            )
        else:
            to_add.append(category)

    if dry_run:
        added_names = [category.name for category in to_add]
        info_parts.append(
            f"would remove {old_category.name!r} and add {added_names!r}"
        )
        return {
            "value": "|".join(category.name for category in new_categories),
            "info": "; ".join(info_parts),
        }

    CategoryEntryPage.objects.filter(
        page_id=page.pk, category_id=old_category.id
    ).delete()
    for category in to_add:
        CategoryEntryPage.objects.create(page=page, category=category)

    return {
        "value": "|".join(category.name for category in new_categories),
        "info": "; ".join(info_parts),
    }


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
        "--data-file",
        type=str,
        required=True,
        help=(
            "Reventilation CSV with "
            f"{THEME_A_REVENTILER_COLUMN!r} and {THEMES_REVENTILES_COLUMN!r}."
        ),
    )
    parser.add_argument(
        "--failures-file",
        type=str,
        default="",
        help="Path to CSV output for failures.",
    )
    parser.add_argument(
        "--successes-file",
        type=str,
        default="",
        help="Path to CSV output for successful rows.",
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "theme_reventilation_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "theme_reventilation_successes"
    )
    rules = _load_rules(args.data_file)
    pages = list(resolve_pages(args.parent_id))
    page_count = len(pages)

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to apply {len(rules)} reventilation rule(s) on "
        f"{page_count} BlogEntryPage object(s). Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    _get_theme_category_ids()

    updated = 0
    skipped = 0
    failures_count = 0
    failures_path = Path(failures_file)
    successes_path = Path(successes_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    successes_path.parent.mkdir(parents=True, exist_ok=True)

    with failures_path.open(
        "w", encoding="utf-8", newline="", buffering=1
    ) as failures_f, successes_path.open(
        "w", encoding="utf-8", newline="", buffering=1
    ) as successes_f:
        failures_writer = csv.DictWriter(
            failures_f,
            fieldnames=[
                "pageId",
                "disaron_nom",
                "theme_a_reventiler",
                "error",
            ],
        )
        successes_writer = csv.DictWriter(
            successes_f,
            fieldnames=[
                "pageId",
                "disaron_nom",
                "theme_a_reventiler",
                "themes_reventiles",
                "info",
            ],
        )
        failures_writer.writeheader()
        successes_writer.writeheader()
        _flush_csv_file(failures_f)
        _flush_csv_file(successes_f)

        progress = 0

        def _fail(
            page_id: int,
            disaron_nom: str,
            theme_a_reventiler: str,
            error: str,
        ) -> None:
            nonlocal skipped, failures_count, progress
            skipped += 1
            failures_count += 1
            progress += 1
            failures_writer.writerow(
                {
                    "pageId": str(page_id),
                    "disaron_nom": disaron_nom,
                    "theme_a_reventiler": theme_a_reventiler,
                    "error": error,
                }
            )
            _flush_csv_file(failures_f)
            print(
                f"[{progress}/{page_count}] Skipped id={page_id}: {error}",
                flush=True,
            )

        for page in pages:
            disaron_nom = find_disaron_nom(page) or ""
            page_matched_rule = False

            for rule in rules:
                entry = _old_theme_entry(page, rule.theme_a_reventiler)
                if entry is None:
                    continue

                page_matched_rule = True
                try:
                    result = _reventilate_page(page, rule, dry_run=args.dry_run)
                except Exception as exc:
                    _fail(
                        page.id,
                        disaron_nom,
                        rule.theme_a_reventiler,
                        str(exc),
                    )
                    break

                if result.get("noop"):
                    continue

                updated += 1
                progress += 1
                successes_writer.writerow(
                    {
                        "pageId": str(page.id),
                        "disaron_nom": disaron_nom,
                        "theme_a_reventiler": rule.theme_a_reventiler,
                        "themes_reventiles": "|".join(
                            rule.themes_reventiles
                        ),
                        "info": str(result.get("info") or ""),
                    }
                )
                _flush_csv_file(successes_f)
                action = "Would update" if args.dry_run else "Updated"
                print(
                    f"[{progress}/{page_count}] {action} id={page.id} "
                    f"disaron_nom={disaron_nom!r} "
                    f"theme_a_reventiler={rule.theme_a_reventiler!r} "
                    f"title={page.title!r}",
                    flush=True,
                )

            if not page_matched_rule:
                progress += 1

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    summary_action = "Would update" if args.dry_run else "Updated"
    print(
        f"{summary_action} {updated} page/rule reventilation(s); "
        f"skipped {skipped}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
