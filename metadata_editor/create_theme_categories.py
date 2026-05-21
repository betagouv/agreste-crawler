#!/usr/bin/env python
"""
Create themes as Categories, from a CSV list of themes and their parents.

The CSV must contain ``theme`` and ``parent_theme`` columns. For each row,
the script creates a Category named ``theme`` with ``parent`` set to the
Category named ``parent_theme``. If a Category with that name already exists
under the expected parent, the row is logged as ``already_exists``.

All outcomes (created, would_create, already_exists, failure) are written to
a single log CSV as the script runs. With ``--dry-run``, rows that would be
created are logged as ``would_create`` and nothing is written to the database.

Usage:
    just create_theme_categories \
        --wagtail-project-root ../agreste \
        --scalingo-env-file .env.test \
        --data-file 20260324-Correspondance-Thmatiques-OLD-NEW-v2.csv

    just create_theme_categories \
        --wagtail-project-root ../agreste \
        --scalingo-env-file .env.test \
        --data-file 20260324-Correspondance-Thmatiques-OLD-NEW-v2.csv \
        --dry-run
"""

import argparse
import csv
import os
from pathlib import Path

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402

from blog.models import Category  # noqa: E402

THEME_COLUMN = "theme"
PARENT_THEME_COLUMN = "parent_theme"
LOG_FIELDNAMES = ["theme", "parent_theme", "status", "message"]


def _resolve_log_file(provided: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return (
        f"metadata_editor/output/{timestamp}_create_theme_categories.csv"
    )


def _flush_log_file(log_f) -> None:
    log_f.flush()
    os.fsync(log_f.fileno())


def _load_rows(data_file: str) -> list[dict[str, str]]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        if THEME_COLUMN not in reader.fieldnames:
            raise ValueError(f"Data CSV must contain {THEME_COLUMN!r} column.")
        if PARENT_THEME_COLUMN not in reader.fieldnames:
            raise ValueError(
                f"Data CSV must contain {PARENT_THEME_COLUMN!r} column."
            )
        for row in reader:
            rows.append(
                {
                    THEME_COLUMN: (row.get(THEME_COLUMN) or "").strip(),
                    PARENT_THEME_COLUMN: (
                        row.get(PARENT_THEME_COLUMN) or ""
                    ).strip(),
                }
            )
    return rows


def _process_row(
    theme: str,
    parent_theme: str,
    *,
    dry_run: bool,
) -> tuple[str, str]:
    if not theme or not parent_theme:
        return "failure", "theme and parent_theme are required"

    parent = Category.objects.filter(name=parent_theme).first()
    if parent is None:
        return "failure", f"parent category {parent_theme!r} not found"

    existing = Category.objects.filter(name=theme).first()
    if existing is not None:
        if existing.parent_id == parent.id:
            return (
                "already_exists",
                f"category {theme!r} already exists under {parent_theme!r}",
            )
        existing_parent = (
            existing.parent.name if existing.parent_id else None
        )
        return (
            "failure",
            f"category {theme!r} already exists under "
            f"{existing_parent!r}, expected {parent_theme!r}",
        )

    if dry_run:
        return (
            "would_create",
            f"would create category {theme!r} under {parent_theme!r}",
        )

    category = Category(name=theme, parent=parent, locale=parent.locale)
    category.save()
    return "created", f"created category {theme!r} under {parent_theme!r}"


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
        "--data-file",
        type=str,
        required=True,
        help="CSV file with 'theme' and 'parent_theme' columns.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default="",
        help=(
            "Path to CSV log for all rows "
            "(columns: theme, parent_theme, status, message)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be created without writing to the database.",
    )
    args = parser.parse_args()

    log_file = _resolve_log_file(args.log_file)
    rows = _load_rows(args.data_file)
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    counts = {
        "created": 0,
        "would_create": 0,
        "already_exists": 0,
        "failure": 0,
    }
    mode_prefix = "[DRY RUN] " if args.dry_run else ""

    with log_path.open(
        "w", encoding="utf-8", newline="", buffering=1
    ) as log_f:
        writer = csv.DictWriter(log_f, fieldnames=LOG_FIELDNAMES)
        writer.writeheader()
        _flush_log_file(log_f)

        for index, row in enumerate(rows, start=1):
            theme = row[THEME_COLUMN]
            parent_theme = row[PARENT_THEME_COLUMN]
            try:
                status, message = _process_row(
                    theme, parent_theme, dry_run=args.dry_run
                )
            except Exception as exc:
                status, message = "failure", str(exc)

            counts[status] = counts.get(status, 0) + 1
            writer.writerow(
                {
                    "theme": theme,
                    "parent_theme": parent_theme,
                    "status": status,
                    "message": message,
                }
            )
            _flush_log_file(log_f)
            print(
                f"{mode_prefix}[{index}/{len(rows)}] "
                f"{status}: {theme!r} -> {parent_theme!r} ({message})",
                flush=True,
            )

    print(f"Wrote {len(rows)} log row(s) to {log_file}.")
    print(
        f"{mode_prefix}created={counts.get('created', 0)} "
        f"would_create={counts.get('would_create', 0)} "
        f"already_exists={counts.get('already_exists', 0)} "
        f"failure={counts.get('failure', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
