#!/usr/bin/env python
"""
Update PublicationPage themes from a CSV file.

The CSV must contain:
- an id column: ``disaron_nom`` or ``disaron:nom``
- a themes column: ``themes_labels`` or ``themes``

Theme values may list multiple labels separated by ``|``.

Each label must match an existing ``publications.Theme`` (by name).
When a theme is set on a page, its parent themes (ancestor chain) are
also set. After the script runs, only the CSV themes and their ancestors
remain on each page.

Pages are matched on ``page.disaron_id`` under a PublicationIndexPage.

Usage:
    uv run python -m metadata_editor.set_themes \
        --wagtail-project-root ../agreste \
        --parent-id 42 \
        --data-file theme-mapper/output.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

from django_setup import setup_django

setup_django(__file__)

from wagtail.models import Page  # noqa: E402

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
    Theme,
)

from metadata_editor.set_metadata import (  # noqa: E402
    add_common_args,
    resolve_failures_file,
    resolve_successes_file,
    run_metadata_update,
)

ID_COLUMNS = ("disaron_nom", "disaron:nom")
THEMES_COLUMNS = ("themes_labels", "themes")


def _parse_themes(raw_value: str) -> list[str]:
    return list(
        OrderedDict.fromkeys(
            part.strip() for part in raw_value.split("|") if part.strip()
        )
    )


def _pick_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str:
    present = [name for name in candidates if name in fieldnames]
    if not present:
        raise ValueError(
            "Data CSV must contain one of: "
            + ", ".join(repr(c) for c in candidates)
        )
    return present[0]


def load_themes_csv(data_file: str) -> dict[str, str]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    out: dict[str, str] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        id_column = _pick_column(list(reader.fieldnames), ID_COLUMNS)
        themes_column = _pick_column(list(reader.fieldnames), THEMES_COLUMNS)
        for row in reader:
            disaron_nom = (row.get(id_column) or "").strip()
            raw_themes = (row.get(themes_column) or "").strip()
            if not disaron_nom or not raw_themes:
                continue
            out[disaron_nom] = raw_themes
    return out


def resolve_publication_pages(parent_id: int):
    try:
        parent_page = Page.objects.get(id=parent_id).specific
    except Page.DoesNotExist as exc:
        raise ValueError(
            f"Page id={parent_id} does not exist in this Wagtail database."
        ) from exc
    if not isinstance(parent_page, PublicationIndexPage):
        raise ValueError(
            f"Page id={parent_id} is not a PublicationIndexPage "
            f"(got {type(parent_page).__name__})."
        )
    return PublicationPage.objects.child_of(parent_page).order_by("id")


def _themes_by_name() -> dict[str, Theme]:
    themes = {str(theme.name).strip(): theme for theme in Theme.objects.all()}
    if not themes:
        raise ValueError("No Theme objects found in the database.")
    return themes


def _resolve_themes(theme_names: Iterable[str]) -> list[Theme]:
    allowed = _themes_by_name()
    missing = [name for name in theme_names if name not in allowed]
    if missing:
        raise ValueError(
            "theme not found: " + ", ".join(repr(m) for m in missing)
        )
    return [allowed[name] for name in theme_names]


def _with_ancestors(themes: Iterable[Theme]) -> list[Theme]:
    """Return themes plus each theme's ancestor chain, without duplicates."""
    result: list[Theme] = []
    seen: set[int] = set()
    for theme in themes:
        chain: list[Theme] = []
        current: Theme | None = theme
        while current is not None and current.pk not in seen:
            chain.append(current)
            seen.add(current.pk)
            current = current.parent
        result.extend(chain)
    return result


def _apply_themes(
    page: PublicationPage, raw_themes: str, *, dry_run: bool = False
) -> dict[str, str]:
    theme_names = _parse_themes(raw_themes)
    if not theme_names:
        raise ValueError("themes value is empty after parsing")

    themes = _with_ancestors(_resolve_themes(theme_names))
    theme_labels = [theme.name for theme in themes]
    if dry_run:
        return {
            "value": "|".join(theme_labels),
            "info": f"Would set {len(themes)} theme(s) (including ancestors).",
        }

    # ParentalManyToManyField relations are only persisted via a revision.
    page.themes.set(themes)
    revision = page.save_revision()
    if page.live:
        revision.publish()

    return {"value": "|".join(theme_labels), "info": ""}


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
    pages = resolve_publication_pages(args.parent_id)
    values_by_disaron_nom = load_themes_csv(args.data_file)

    def apply_value(page: PublicationPage, value: str) -> dict[str, str]:
        return _apply_themes(page, value, dry_run=args.dry_run)

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=values_by_disaron_nom,
        apply_value=apply_value,
        update_fields=None,
        failures_file=failures_file,
        successes_file=successes_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} PublicationPage object(s) "
            f"using themes from {args.data_file}."
        ),
        success_log=lambda _i, _n, page, disaron_nom, value: (
            f"id={page.id} disaron_nom={disaron_nom!r} "
            f"themes={_parse_themes(value)!r} title={page.title!r}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
