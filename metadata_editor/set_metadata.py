"""
Shared core for metadata-update scripts (set_publication_date, set_collection, …).

Each script builds a `values_by_disaron_nom` mapping, an `apply_value`
callable that mutates a page object, and delegates the full update loop
to `run_metadata_update`.
"""

import argparse
import csv
import re
from pathlib import Path
from typing import Any, Callable

from django.utils import timezone
from wagtail.models import Page

from blog.models import BlogEntryPage, BlogIndexPage

DISARON_NOM_RE = re.compile(
    r"\b[A-Z][a-z]{2}[A-Z][a-z]{2}\d+(?:bis|ter)?\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------

def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments shared by all metadata-update scripts."""
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
        help="CSV file with 'disaron:nom' and a metadata value column.",
    )
    parser.add_argument(
        "--failures-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for failures "
            "(columns: pageId, disaron_nom, error)."
        ),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def find_disaron_nom(page: BlogEntryPage) -> str | None:
    """
    Extract a disaron identifier from page body content.

    Expected formats:
    - XxxXxx followed by digits (e.g. IraLeg25167)
    - XxxXxx followed by digits and 'bis'/'ter' (rare edge cases)
    """
    match = DISARON_NOM_RE.search(str(page.body))
    return match.group(0) if match else None


def resolve_failures_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def resolve_pages(parent_id: int):
    parent_page = Page.objects.get(id=parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        raise ValueError(
            f"Page id={parent_id} is not a BlogIndexPage "
            f"(got {type(parent_page).__name__})."
        )
    return BlogEntryPage.objects.child_of(parent_page).order_by("id")


def load_csv_column(
    data_file: str,
    value_column: str,
) -> dict[str, str]:
    """
    Read a CSV and return a mapping of disaron:nom -> raw string value
    from `value_column`.
    """
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    out: dict[str, str] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        if "disaron:nom" not in reader.fieldnames:
            raise ValueError("Data CSV must contain 'disaron:nom' column.")
        if value_column not in reader.fieldnames:
            raise ValueError(
                f"Data CSV must contain {value_column!r} column."
            )
        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            raw_value = (row.get(value_column) or "").strip()
            if disaron_nom and raw_value:
                out[disaron_nom] = raw_value

    return out


# ---------------------------------------------------------------------------
# Core update loop
# ---------------------------------------------------------------------------

def run_metadata_update(
    *,
    pages: Any,
    values_by_disaron_nom: dict[str, Any],
    apply_value: Callable[[BlogEntryPage, Any], None],
    update_fields: list[str] | None,
    failures_file: str,
    dry_run: bool,
    confirmation_message: str,
    success_log: Callable[[int, int, BlogEntryPage, str, Any], str],
) -> int:
    """
    Generic per-page update loop.

    - Prompts for confirmation before starting.
    - Looks up disaron_nom in each page body.
    - Looks up the mapped value from `values_by_disaron_nom`.
    - Calls `apply_value(page, value)` to mutate the page.
    - Saves with `update_fields` (skipped on --dry-run).
      If `update_fields` is None, no `page.save()` is called (use this when
      `apply_value` already commits to the DB, e.g. M2M .set()).
    - Streams failures to `failures_file` as they occur.

    Returns 0 on success.
    """
    page_count = pages.count()
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}{confirmation_message} Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    skipped = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)

    with failures_path.open("w", encoding="utf-8", newline="") as failures_f:
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()

        def _fail(page_id: int, disaron_nom: str, error: str) -> None:
            nonlocal skipped, failures_count
            skipped += 1
            failures_count += 1
            writer.writerow(
                {
                    "pageId": str(page_id),
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            failures_f.flush()
            print(
                f"[{updated + skipped}/{page_count}] "
                f"Skipped id={page_id}: {error}"
            )

        for page in pages:
            disaron_nom = find_disaron_nom(page)
            if disaron_nom is None:
                _fail(page.id, "", "could not find disaron_nom in page body")
                continue

            value = values_by_disaron_nom.get(disaron_nom)
            if value is None:
                _fail(
                    page.id,
                    disaron_nom,
                    f"disaron_nom={disaron_nom!r} not found in CSV",
                )
                continue

            apply_value(page, value)

            if not dry_run and update_fields is not None:
                try:
                    page.save(update_fields=update_fields)
                except Exception as exc:
                    _fail(page.id, disaron_nom, f"save failed: {exc}")
                    continue

            updated += 1
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                + success_log(updated, page_count, page, disaron_nom, value)
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} BlogEntryPage object(s); "
        f"skipped {skipped}."
    )
    return 0
