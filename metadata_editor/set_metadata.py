"""
Shared core for metadata-update scripts
(set_publication_date, set_collection, ...).

Each script builds a `values_by_disaron_nom` mapping, an `apply_value`
callable that mutates a page object, and delegates the full update loop
to `run_metadata_update`.
"""

import argparse
import csv
import os
import re
from contextlib import ExitStack
from html import unescape
from pathlib import Path
from typing import Any, Callable

from django.utils import timezone
from wagtail.models import Page

from sites_conformes.blog.models import BlogEntryPage, BlogIndexPage

DISARON_NOM_RE = re.compile(
    r"\b[A-Z][a-z]{2}[A-Z][a-z]{2}\d+(?:bis|ter)?\b",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
DISARON_NOM_DIV_RE = re.compile(
    r"<div[^>]*\bid\s*=\s*['\"]disaron-nom['\"][^>]*>(?P<inner>.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)

# Sentinel: disaron is in CSV but the value column is empty (no update).
NOOP = object()


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
    parser.add_argument(
        "--successes-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for successful rows "
            "(columns: pageId, disaron_nom, value, info)."
        ),
    )
    parser.add_argument(
        "--noops-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for noop rows "
            "(columns: pageId, disaron_nom, info)."
        ),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_stream_data(body_value: Any) -> list[Any]:
    if hasattr(body_value, "stream_data"):
        return list(body_value.stream_data)
    if hasattr(body_value, "raw_data"):
        return list(body_value.raw_data)
    raise AttributeError(
        "Unsupported StreamField value: expected StreamValue with "
        "'stream_data' or 'raw_data'."
    )


def _iter_html_values(node: Any):
    """Yield raw HTML strings from nested StreamField data."""
    if isinstance(node, dict):
        if node.get("type") == "html":
            value = node.get("value")
            if isinstance(value, str):
                yield value
        for value in node.values():
            if isinstance(value, (dict, list, tuple)):
                yield from _iter_html_values(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            if isinstance(item, (dict, list, tuple)):
                yield from _iter_html_values(item)
            elif (
                isinstance(item, tuple)
                and len(item) == 2
                and item[0] == "html"
                and isinstance(item[1], str)
            ):
                yield item[1]


def _extract_disaron_from_html(html: str) -> str | None:
    match = DISARON_NOM_DIV_RE.search(html)
    if not match:
        return None
    text = TAG_RE.sub("", match.group("inner"))
    text = unescape(text).strip()
    return text or None


def _flush_failures_file(failures_f) -> None:
    """Push buffered failure rows to disk so tailing the CSV works mid-run."""
    failures_f.flush()
    os.fsync(failures_f.fileno())


def find_disaron_nom(page: BlogEntryPage) -> str | None:
    """
    Extract a disaron identifier from page body content.

    Looks for <div id="disaron-nom">TOKEN</div> in html StreamField blocks.
    """
    try:
        stream_data = _get_stream_data(page.body)
    except AttributeError:
        return None

    for html in _iter_html_values(stream_data):
        disaron_nom = _extract_disaron_from_html(html)
        if disaron_nom:
            return disaron_nom
    return None


def resolve_failures_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def resolve_successes_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def resolve_noops_file(provided: str, default_suffix: str) -> str:
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
    *,
    include_empty_as_noop: bool = False,
) -> dict[str, Any]:
    """
    Read a CSV and return a mapping of disaron:nom -> raw string value
    from `value_column`.

    When ``include_empty_as_noop`` is True, rows with a ``disaron:nom`` but
    an empty ``value_column`` are stored as ``NOOP`` instead of being omitted.
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
            if not disaron_nom:
                continue
            if raw_value:
                out[disaron_nom] = raw_value
            elif include_empty_as_noop:
                out[disaron_nom] = NOOP

    return out


# ---------------------------------------------------------------------------
# Core update loop
# ---------------------------------------------------------------------------

def run_metadata_update(
    *,
    pages: Any,
    values_by_disaron_nom: dict[str, Any],
    apply_value: Callable[[BlogEntryPage, Any], Any],
    update_fields: list[str] | None,
    failures_file: str,
    successes_file: str,
    noops_file: str = "",
    dry_run: bool,
    confirmation_message: str,
    success_log: Callable[[int, int, BlogEntryPage, str, Any], str],
    noop_info: str | None = None,
) -> int:
    """
    Generic per-page update loop.

    - Prompts for confirmation before starting.
    - Looks up disaron_nom from <div id="disaron-nom"> in each page body.
    - Looks up the mapped value from `values_by_disaron_nom`.
    - Calls `apply_value(page, value)` to mutate the page.
    - Saves with `update_fields` (skipped on --dry-run).
      If `update_fields` is None, no `page.save()` is called (use this when
      `apply_value` already commits to the DB, e.g. M2M .set()).
    - Streams failures to `failures_file` as they occur (flushed to disk
      after each row so the file can be tailed while the script runs).

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
    noop_count = 0
    skipped = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)

    successes_path = Path(successes_file)
    successes_path.parent.mkdir(parents=True, exist_ok=True)

    noops_path = Path(noops_file) if noops_file else None
    if noops_path is not None:
        noops_path.parent.mkdir(parents=True, exist_ok=True)

    with ExitStack() as stack:
        failures_f = stack.enter_context(
            failures_path.open("w", encoding="utf-8", newline="", buffering=1)
        )
        successes_f = stack.enter_context(
            successes_path.open("w", encoding="utf-8", newline="", buffering=1)
        )
        noops_f = None
        if noops_path is not None:
            noops_f = stack.enter_context(
                noops_path.open("w", encoding="utf-8", newline="", buffering=1)
            )

        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        _flush_failures_file(failures_f)
        successes_writer = csv.DictWriter(
            successes_f,
            fieldnames=["pageId", "disaron_nom", "value", "info"],
        )
        successes_writer.writeheader()
        _flush_failures_file(successes_f)
        noops_writer = None
        if noops_f is not None:
            noops_writer = csv.DictWriter(
                noops_f, fieldnames=["pageId", "disaron_nom", "info"]
            )
            noops_writer.writeheader()
            _flush_failures_file(noops_f)

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
            _flush_failures_file(failures_f)
            print(
                f"[{updated + skipped}/{page_count}] "
                f"Skipped id={page_id}: {error}",
                flush=True,
            )

        def _record_noop(page_id: int, disaron_nom: str, noop_message: str) -> None:
            nonlocal noop_count
            noop_count += 1
            noop_row = {
                "pageId": str(page_id),
                "disaron_nom": disaron_nom,
                "info": noop_message,
            }
            if noops_writer is not None:
                noops_writer.writerow(noop_row)
                _flush_failures_file(noops_f)
            else:
                successes_writer.writerow({**noop_row, "value": ""})
                _flush_failures_file(successes_f)
            print(
                f"[{updated + noop_count + skipped}/{page_count}] "
                f"Noop id={page_id} disaron_nom={disaron_nom!r}: "
                f"{noop_message}",
                flush=True,
            )

        for page in pages:
            disaron_nom = find_disaron_nom(page)
            if disaron_nom is None:
                _fail(
                    page.id,
                    "",
                    'could not find <div id="disaron-nom"> in page body',
                )
                continue

            if disaron_nom not in values_by_disaron_nom:
                _fail(
                    page.id,
                    disaron_nom,
                    f"disaron_nom={disaron_nom!r} not found in CSV",
                )
                continue

            value = values_by_disaron_nom[disaron_nom]
            if value is NOOP:
                _record_noop(
                    page.id,
                    disaron_nom,
                    noop_info or "noop: value column is empty",
                )
                continue

            try:
                apply_result = apply_value(page, value)
            except Exception as exc:
                _fail(page.id, disaron_nom, f"apply failed: {exc}")
                continue

            if isinstance(apply_result, dict) and apply_result.get("noop"):
                _record_noop(
                    page.id,
                    disaron_nom,
                    str(apply_result.get("info") or "noop"),
                )
                continue

            if not dry_run and update_fields is not None:
                try:
                    page.save(update_fields=update_fields)
                except Exception as exc:
                    _fail(page.id, disaron_nom, f"save failed: {exc}")
                    continue

            updated += 1
            success_value = str(value)
            success_info = ""
            if isinstance(apply_result, dict):
                success_value = str(apply_result.get("value", value))
                success_info = str(apply_result.get("info", "") or "")
            successes_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "value": success_value,
                    "info": success_info,
                }
            )
            _flush_failures_file(successes_f)
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                + success_log(updated, page_count, page, disaron_nom, value)
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    if noop_count and noops_file:
        print(f"Wrote {noop_count} noop row(s) to {noops_file}.")
    elif noop_count:
        print(f"Logged {noop_count} noop row(s) in {successes_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} BlogEntryPage object(s); "
        f"noop {noop_count}; skipped {skipped}."
    )
    return 0
