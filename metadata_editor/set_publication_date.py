#!/usr/bin/env python
"""
Update BlogEntryPage post dates to the current time.

Usage:
    just set_publication_date \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --data-file infos-rapides.csv
"""

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402

DISARON_NOM_RE = re.compile(r"\b[A-Z][a-z]{2}[A-Z][a-z]{2}\d+\b")


def find_disaron_nom(page: BlogEntryPage) -> str | None:
    """
    Extract a disaron-like identifier from page body content.

    Expected format: XxxXxx followed by digits (e.g., IraLeg25167).
    """
    body_text = str(page.body)
    match = DISARON_NOM_RE.search(body_text)
    if match is None:
        return None
    return match.group(0)


def _parse_publication_date(raw_value: str) -> datetime:
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
    raise ValueError(f"Unsupported disaron:Date_premiere_publication value: {raw_value!r}")


def _load_dates_by_disaron_nom(data_file: str) -> dict[str, datetime]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    out: dict[str, datetime] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        if "disaron:nom" not in reader.fieldnames:
            raise ValueError("Data CSV must contain 'disaron:nom' column.")
        if "disaron:Date_premiere_publication" not in reader.fieldnames:
            raise ValueError(
                "Data CSV must contain 'disaron:Date_premiere_publication' column."
            )

        for i, row in enumerate(reader, start=2):
            disaron_nom = (row.get("disaron:nom") or "").strip()
            raw_date = (row.get("disaron:Date_premiere_publication") or "").strip()
            if not disaron_nom or not raw_date:
                continue
            try:
                out[disaron_nom] = _parse_publication_date(raw_date)
            except ValueError as exc:
                raise ValueError(f"Invalid date at CSV line {i}: {exc}") from exc

    return out


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
            "CSV file with 'disaron:nom' and "
            "'disaron:Date_premiere_publication' columns."
        ),
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
    args = parser.parse_args()

    _ = args  # Arguments are consumed by setup_django via sys.argv.
    failures_file = args.failures_file
    if not failures_file:
        timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
        failures_file = f"metadata_editor/output/{timestamp}_date_failures.csv"

    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        raise ValueError(
            f"Page id={args.parent_id} is not a BlogIndexPage "
            f"(got {type(parent_page).__name__})."
        )

    dates_by_disaron_nom = _load_dates_by_disaron_nom(args.data_file)
    pages = BlogEntryPage.objects.child_of(parent_page).order_by("id")
    page_count = pages.count()

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to update {page_count} "
        f"BlogEntryPage object(s) using dates from {args.data_file}. "
        "Type 'yes' to confirm: "
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
        failures_writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        failures_writer.writeheader()

        for page in pages:
            disaron_nom = find_disaron_nom(page)
            if disaron_nom is None:
                skipped += 1
                failures_count += 1
                failures_writer.writerow(
                    {
                        "pageId": str(page.id),
                        "disaron_nom": "",
                        "error": "could not find disaron_nom in page body",
                    }
                )
                print(
                    f"[{updated + skipped}/{page_count}] Skipped id={page.id}: "
                    "could not find disaron_nom in page body."
                )
                continue
            publication_date = dates_by_disaron_nom.get(disaron_nom)
            if publication_date is None:
                skipped += 1
                failures_count += 1
                failures_writer.writerow(
                    {
                        "pageId": str(page.id),
                        "disaron_nom": disaron_nom,
                        "error": "disaron_nom not found in CSV",
                    }
                )
                print(
                    f"[{updated + skipped}/{page_count}] Skipped id={page.id}: "
                    f"disaron_nom={disaron_nom!r} not found in CSV."
                )
                continue

            page.date = publication_date
            if not args.dry_run:
                try:
                    page.save(update_fields=["date"])
                except Exception as exc:
                    skipped += 1
                    failures_count += 1
                    failures_writer.writerow(
                        {
                            "pageId": str(page.id),
                            "disaron_nom": disaron_nom,
                            "error": f"save failed: {exc}",
                        }
                    )
                    print(
                        f"[{updated + skipped}/{page_count}] Skipped id={page.id}: "
                        f"save failed for disaron_nom={disaron_nom!r}: {exc}"
                    )
                    continue
            updated += 1
            action = "Would update" if args.dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                f"id={page.id} disaron_nom={disaron_nom!r} "
                f"title={page.title!r} date={publication_date}"
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")

    summary_action = "Would update" if args.dry_run else "Updated"
    print(
        f"{summary_action} {updated} BlogEntryPage object(s); "
        f"skipped {skipped}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
