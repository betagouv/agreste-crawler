#!/usr/bin/env python
"""
Update BlogEntryPage post dates from a CSV data file.

The CSV must contain 'disaron:nom' and 'disaron:Date_premiere_publication'
columns.  Each BlogEntryPage's disaron identifier is looked up in that mapping
and its date field is updated accordingly.

Usage:
    just set_publication_date \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --data-file infos-rapides.csv
"""

import argparse
from datetime import datetime

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402

from blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    add_common_args,
    load_csv_column,
    resolve_failures_file,
    resolve_pages,
    run_metadata_update,
)

DATE_COLUMN = "disaron:Date_premiere_publication"


def _parse_date(raw_value: str) -> datetime:
    value = raw_value.strip()
    parse_formats = (
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    )
    for fmt in parse_formats:
        try:
            naive_dt = datetime.strptime(value, fmt)
            return timezone.make_aware(naive_dt, timezone.get_current_timezone())
        except ValueError:
            continue
    raise ValueError(
        f"Unsupported disaron:Date_premiere_publication value: {raw_value!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()

    failures_file = resolve_failures_file(args.failures_file, "date_failures")
    pages = resolve_pages(args.parent_id)

    raw_dates = load_csv_column(args.data_file, DATE_COLUMN)
    dates_by_disaron_nom: dict[str, datetime] = {}
    for nom, raw in raw_dates.items():
        dates_by_disaron_nom[nom] = _parse_date(raw)

    return run_metadata_update(
        pages=pages,
        values_by_disaron_nom=dates_by_disaron_nom,
        apply_value=lambda page, value: setattr(page, "date", value),
        update_fields=["date"],
        failures_file=failures_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} BlogEntryPage object(s) "
            f"using dates from {args.data_file}."
        ),
        success_log=lambda _i, _n, page, disaron_nom, value: (
            f"id={page.id} disaron_nom={disaron_nom!r} "
            f"title={page.title!r} date={value}"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
