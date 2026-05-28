#!/usr/bin/env python
"""
Wrap BlogEntryPage chapeau HTML in a <p> tag.

For each BlogEntryPage under --parent-id:
- Find <div id="chapeau">...</div> inside html StreamField blocks.
- If found and its inner HTML is not already wrapped in a <p>...</p>,
  replace it with:
      <div id="chapeau"><p>...</p></div>
- If no <div id="chapeau"> is found anywhere in the body, treat it as an
  error and log the page in a failures CSV.

Usage:
    just wrap_chapeau \
        --wagtail-project-root ../agreste \
        --parent-id 30
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    DISARON_NOM_RE,
    _get_stream_data,
    find_disaron_nom,
    resolve_failures_file,
    resolve_pages,
)
from django.utils import timezone  # noqa: E402


CHAPEAU_DIV_RE = re.compile(
    r"(<div[^>]*\bid=[\"']chapeau[\"'][^>]*>)"
    r"(?P<inner>.*?)(</div>)",
    re.IGNORECASE | re.DOTALL,
)


def _wrap_inner_in_p(inner: str) -> tuple[str, bool]:
    """
    Return (new_inner, changed).

    If the inner content already starts with a <p> tag (ignoring leading
    whitespace), it is returned unchanged with changed=False.
    Otherwise it is wrapped in a <p>...</p>.
    """
    stripped = inner.lstrip()
    if stripped.lower().startswith("<p"):
        return inner, False
    return f"<p>{inner}</p>", True


def _transform_html_value(html: str) -> tuple[str, int, int]:
    """
    Transform a single html block value.

    Returns (new_html, replacements, chapeau_div_count).
    """
    replacements = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal replacements
        start, inner, end = match.group(1), match.group("inner"), match.group(3)
        new_inner, changed = _wrap_inner_in_p(inner)
        if changed:
            replacements += 1
        return f"{start}{new_inner}{end}"

    new_html, count = CHAPEAU_DIV_RE.subn(_repl, html)
    # `count` is number of chapeau divs matched in this html value.
    return new_html, replacements, count


def _transform_node(node: Any) -> tuple[Any, int, int]:
    """
    Recursively transform StreamField-like data.

    Returns (new_node, replacements_count, chapeau_div_count).
    """
    # Dict representation: {"type": "...", "value": ...}
    if isinstance(node, dict):
        if node.get("type") == "html" and isinstance(node.get("value"), str):
            value: str = node["value"]
            new_value, replacements, chapeau_count = _transform_html_value(value)
            if replacements > 0:
                new_node = dict(node)
                new_node["value"] = new_value
                return new_node, replacements, chapeau_count
            return node, 0, chapeau_count

        new_dict: dict[Any, Any] = {}
        total_replacements = 0
        total_chapeau_count = 0
        for key, value in node.items():
            new_value, replacements, chapeau_count = _transform_node(value)
            new_dict[key] = new_value
            total_replacements += replacements
            total_chapeau_count += chapeau_count
        return new_dict, total_replacements, total_chapeau_count

    # Tuple representation: ("html", "<div id='chapeau'>...")
    if (
        isinstance(node, tuple)
        and len(node) == 2
        and str(node[0]) == "html"
        and isinstance(node[1], str)
    ):
        new_value, replacements, chapeau_count = _transform_html_value(node[1])
        if replacements > 0:
            return ("html", new_value), replacements, chapeau_count
        return node, 0, chapeau_count

    # Recurse lists
    if isinstance(node, list):
        new_list: list[Any] = []
        total_replacements = 0
        total_chapeau_count = 0
        for item in node:
            new_item, replacements, chapeau_count = _transform_node(item)
            new_list.append(new_item)
            total_replacements += replacements
            total_chapeau_count += chapeau_count
        return new_list, total_replacements, total_chapeau_count

    # Keep scalar nodes unchanged
    return node, 0, 0


def _transform_body(stream_data: list[Any]) -> tuple[list[Any], int, int]:
    """
    Apply the chapeau wrapping transform to body stream data.

    Returns (new_stream_data, replacements_count, chapeau_div_count).
    """
    new_node, replacements, chapeau_count = _transform_node(stream_data)
    if not isinstance(new_node, list):
        raise ValueError("Unexpected transformed body shape; expected list.")
    return new_node, replacements, chapeau_count


def resolve_noop_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def load_expected_chapeau_by_disaron_nom(
    inventaire_file: str,
) -> dict[str, bool]:
    """
    Return mapping disaron:nom -> expects_chapeau based on disaron:chapeau.
    """
    csv_path = Path(inventaire_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Inventaire file not found: {csv_path}")

    expected: dict[str, bool] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Inventaire CSV has no headers.")
        if "disaron:nom" not in reader.fieldnames:
            raise ValueError(
                "Inventaire CSV must contain 'disaron:nom' column."
            )
        if "disaron:chapeau" not in reader.fieldnames:
            raise ValueError(
                "Inventaire CSV must contain 'disaron:chapeau' column."
            )

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                continue
            chapeau_value = (row.get("disaron:chapeau") or "").strip()
            expected[disaron_nom] = bool(chapeau_value)

    return expected


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
        "--failures-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for failures "
            "(columns: pageId, disaron_nom, error)."
        ),
    )
    parser.add_argument(
        "--noop-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for noop pages "
            "(columns: pageId, disaron_nom, reason)."
        ),
    )
    parser.add_argument(
        "--inventaire-file",
        type=str,
        default="2026-05-07_Inventaire-Nuxeo-v11.csv",
        help=(
            "Inventaire CSV path with columns 'disaron:nom' and "
            "'disaron:chapeau'. Used when no chapeau div is found."
        ),
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(args.failures_file, "wrap_chapeau_failures")
    noop_file = resolve_noop_file(args.noop_file, "wrap_chapeau_noop")
    pages = resolve_pages(args.parent_id)
    expected_chapeau = load_expected_chapeau_by_disaron_nom(args.inventaire_file)
    page_count = pages.count()

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to wrap chapeau content in "
        f"{page_count} BlogEntryPage body(ies). "
        "Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    failures_count = 0
    noop_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    noop_path = Path(noop_file)
    noop_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open("w", encoding="utf-8", newline="") as failures_f,
        noop_path.open("w", encoding="utf-8", newline="") as noop_f,
    ):
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        noop_writer = csv.DictWriter(
            noop_f, fieldnames=["pageId", "disaron_nom", "reason"]
        )
        noop_writer.writeheader()

        def _fail(page: BlogEntryPage, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            failures_f.flush()
            print(
                f"[{updated + failures_count}/{page_count}] "
                f"Skipped id={page.id}: {error}"
            )

        def _noop(page: BlogEntryPage, disaron_nom: str, reason: str) -> None:
            nonlocal noop_count
            noop_count += 1
            noop_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "reason": reason,
                }
            )
            noop_f.flush()
            print(
                f"[{updated + failures_count + noop_count}/{page_count}] "
                f"Noop id={page.id}: {reason}"
            )

        for page in pages:
            try:
                stream_data = _get_stream_data(page.body)
            except Exception as exc:
                _fail(
                    page,
                    "",
                    f"could not read body stream data: {exc}",
                )
                continue

            disaron_nom = find_disaron_nom(page) or ""
            if not disaron_nom:
                _fail(
                    page,
                    "",
                    'could not find <div id="disaron-nom"> in page body',
                )
                continue

            try:
                new_stream, replacements, chapeau_count = _transform_body(stream_data)
            except Exception as exc:
                _fail(
                    page,
                    "",
                    f"body transform failed: {exc}",
                )
                continue

            if chapeau_count == 0:
                # No <div id="chapeau"> found: error only if inventaire expects one.
                expects_chapeau = expected_chapeau.get(disaron_nom)
                if expects_chapeau is None:
                    _fail(
                        page,
                        disaron_nom,
                        (
                            "disaron_nom not found in inventaire "
                            f"{args.inventaire_file!r}"
                        ),
                    )
                elif expects_chapeau:
                    _fail(
                        page,
                        disaron_nom,
                        (
                            "no <div id=\"chapeau\"> found in body but "
                            "inventaire disaron:chapeau is non-empty"
                        ),
                    )
                else:
                    _noop(
                        page,
                        disaron_nom,
                        "no chapeau expected from inventaire; none found",
                    )
                continue
            if chapeau_count > 1:
                _fail(
                    page,
                    disaron_nom,
                    (
                        "multiple <div id=\"chapeau\"> found in body "
                        f"({chapeau_count}); skipped"
                    ),
                )
                continue

            if replacements == 0:
                # Chapeau already wrapped in <p>...</p>; log as noop.
                _noop(page, disaron_nom, "chapeau already wrapped")
                continue

            page.body = new_stream
            disaron_nom_match = DISARON_NOM_RE.search(str(page.body))
            disaron_nom_for_log = (
                disaron_nom_match.group(0) if disaron_nom_match else disaron_nom
            )

            if args.dry_run:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(
                        page,
                        disaron_nom_for_log,
                        f"validation failed: {exc}",
                    )
                    continue
            else:
                try:
                    page.save(update_fields=["body"])
                except Exception as exc:
                    _fail(
                        page,
                        disaron_nom_for_log,
                        f"save failed: {exc}",
                    )
                    continue

            updated += 1
            action = "Would update" if args.dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                f"id={page.id} disaron_nom={disaron_nom_for_log!r} "
                f"replacements={replacements}"
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {noop_count} noop row(s) to {noop_file}.")
    summary_action = "Would update" if args.dry_run else "Updated"
    print(f"{summary_action} {updated} page(s); noop {noop_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
