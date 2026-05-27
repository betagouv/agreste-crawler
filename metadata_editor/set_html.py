#!/usr/bin/env python
"""
Add Disaron HTML bodies to BlogEntryPage first-column content.

Reads `2026-05-25_Disarons_with_html_clean.csv` (or any CSV with the same
columns). Rows without HTML are ignored. For each BlogEntryPage under
--parent-id, matching on <div id="disaron-nom">:

- `html_principal` → html block wrapped in <div id="html_principal">…</div>
- `html_secondaire` → html block with <h2>Informations complémentaires</h2>
  then content in <div id="html_secondaire">…</div>

Blocks are appended to the first column of the first `multicolumns` block.
Re-running replaces existing blocks that contain those div ids.

Usage:
    just set_html \
        --wagtail-project-root ../agreste \
        --parent-id 30 \
        --data-file 2026-05-25_Disarons_with_html_clean.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402

from blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    _get_stream_data,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_pages,
)

HTML_PRINCIPAL_ID_RE = re.compile(
    r'id\s*=\s*["\']html_principal["\']', re.IGNORECASE
)
HTML_SECONDAIRE_ID_RE = re.compile(
    r'id\s*=\s*["\']html_secondaire["\']', re.IGNORECASE
)


@dataclass(frozen=True)
class DisaronHtml:
    html_principal: str | None
    html_secondaire: str | None


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"true", "1", "yes"}


def _row_has_html(row: dict[str, str]) -> bool:
    if _truthy(row.get("has_html_principal")) or _truthy(
        row.get("has_html_secondaire")
    ):
        return True
    return bool((row.get("html_principal") or "").strip()) or bool(
        (row.get("html_secondaire") or "").strip()
    )


def load_html_by_disaron_nom(data_file: str) -> dict[str, DisaronHtml]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    out: dict[str, DisaronHtml] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        available = set(reader.fieldnames)
        required = {
            "has_html_principal",
            "has_html_secondaire",
            "html_principal",
            "html_secondaire",
        }
        missing = required - available
        if missing:
            raise ValueError(
                f"Data CSV missing required column(s): {', '.join(sorted(missing))}"
            )
        if (
            "disaron:nom" not in available
            and "disaron:nom_old" not in available
        ):
            raise ValueError(
                "Data CSV must contain at least one identifier column: "
                "'disaron:nom' or 'disaron:nom_old'."
            )

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                disaron_nom = (row.get("disaron:nom_old") or "").strip()
            if not disaron_nom or not _row_has_html(row):
                continue

            principal = (row.get("html_principal") or "").strip()
            secondaire = (row.get("html_secondaire") or "").strip()
            if _truthy(row.get("has_html_principal")) and not principal:
                raise ValueError(
                    f"{disaron_nom!r}: has_html_principal is true but html_principal "
                    "is empty."
                )
            if _truthy(row.get("has_html_secondaire")) and not secondaire:
                raise ValueError(
                    f"{disaron_nom!r}: has_html_secondaire is true but "
                    "html_secondaire is empty."
                )

            out[disaron_nom] = DisaronHtml(
                html_principal=principal or None,
                html_secondaire=secondaire or None,
            )

    return out


def _html_block(value: str) -> dict[str, Any]:
    return {"type": "html", "value": value}


def _wrap_principal(html: str) -> str:
    return (
        '<div id="html_principal">'
        "<h2>Présentation</h2>"
        f"{html}"
        "</div>"
    )


def _wrap_secondaire(html: str) -> str:
    return (
        '<div id="html_secondaire">'
        "<h2>Informations complémentaires</h2>"
        f"{html}"
        "</div>"
    )


def _strip_managed_html_blocks(content: list[Any]) -> list[Any]:
    kept: list[Any] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "html":
            kept.append(item)
            continue
        value = item.get("value")
        if not isinstance(value, str):
            kept.append(item)
            continue
        if HTML_PRINCIPAL_ID_RE.search(value) or HTML_SECONDAIRE_ID_RE.search(
            value
        ):
            continue
        kept.append(item)
    return kept


def _blocks_for_html(html: DisaronHtml) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if html.html_principal:
        blocks.append(_html_block(_wrap_principal(html.html_principal)))
    if html.html_secondaire:
        blocks.append(_html_block(_wrap_secondaire(html.html_secondaire)))
    return blocks


def _apply_html_to_first_column(
    stream_data: list[Any], html: DisaronHtml
) -> list[Any]:
    new_blocks = _blocks_for_html(html)
    if not new_blocks:
        raise ValueError("nothing to insert (both html fields empty)")

    new_stream = list(stream_data)
    for block_idx, block in enumerate(stream_data):
        if not isinstance(block, dict) or block.get("type") != "multicolumns":
            continue
        value = block.get("value")
        if not isinstance(value, dict):
            continue
        columns = value.get("columns")
        if not isinstance(columns, list) or not columns:
            raise ValueError("multicolumns block has no columns.")

        first_col = columns[0]
        if not isinstance(first_col, dict) or first_col.get("type") != "column":
            raise ValueError("first multicolumns entry is not a column block.")

        col_value = first_col.get("value")
        if not isinstance(col_value, dict):
            raise ValueError("first column has no value dict.")

        content = col_value.get("content")
        if not isinstance(content, list):
            raise ValueError("first column has no list 'content'.")

        new_content = _strip_managed_html_blocks(content)
        new_content.extend(new_blocks)

        new_col_value = dict(col_value)
        new_col_value["content"] = new_content
        new_col = dict(first_col)
        new_col["value"] = new_col_value

        new_columns = list(columns)
        new_columns[0] = new_col
        new_value = dict(value)
        new_value["columns"] = new_columns
        new_block = dict(block)
        new_block["value"] = new_value
        new_stream[block_idx] = new_block
        return new_stream

    raise ValueError("no multicolumns block found in page body.")


def _apply_html(page: BlogEntryPage, html: DisaronHtml) -> None:
    stream_data = _get_stream_data(page.body)
    page.body = _apply_html_to_first_column(stream_data, html)


def resolve_success_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def run_html_update(
    *,
    pages: Any,
    html_by_disaron_nom: dict[str, DisaronHtml],
    failures_file: str,
    success_file: str,
    skipped_file: str,
    dry_run: bool,
    confirmation_message: str,
) -> int:
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
    skipped_no_html = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    success_path = Path(success_file)
    success_path.parent.mkdir(parents=True, exist_ok=True)
    skipped_path = Path(skipped_file)
    skipped_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open("w", encoding="utf-8", newline="", buffering=1) as failures_f,
        success_path.open("w", encoding="utf-8", newline="", buffering=1) as success_f,
        skipped_path.open("w", encoding="utf-8", newline="", buffering=1) as skipped_f,
    ):
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        _flush_failures_file(failures_f)

        success_writer = csv.DictWriter(
            success_f,
            fieldnames=["pageId", "disaron_nom", "added", "title"],
        )
        success_writer.writeheader()
        success_f.flush()

        skipped_writer = csv.DictWriter(
            skipped_f,
            fieldnames=["pageId", "disaron_nom", "title"],
        )
        skipped_writer.writeheader()
        skipped_f.flush()

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

        for page in pages:
            disaron_nom = find_disaron_nom(page)
            if disaron_nom is None:
                _fail(
                    page.id,
                    "",
                    'could not find <div id="disaron-nom"> in page body',
                )
                continue

            html = html_by_disaron_nom.get(disaron_nom)
            if html is None:
                skipped_no_html += 1
                skipped_writer.writerow(
                    {
                        "pageId": str(page.id),
                        "disaron_nom": disaron_nom,
                        "title": page.title,
                    }
                )
                skipped_f.flush()
                continue

            try:
                _apply_html(page, html)
            except Exception as exc:
                _fail(page.id, disaron_nom, f"apply failed: {exc}")
                continue

            if not dry_run:
                try:
                    page.save(update_fields=["body"])
                except Exception as exc:
                    _fail(page.id, disaron_nom, f"save failed: {exc}")
                    continue
            else:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(page.id, disaron_nom, f"validation failed: {exc}")
                    continue

            updated += 1
            parts = []
            if html.html_principal:
                parts.append("html_principal")
            if html.html_secondaire:
                parts.append("html_secondaire")
            added = ",".join(parts)
            success_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "added": added,
                    "title": page.title,
                }
            )
            success_f.flush()
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                f"id={page.id} disaron_nom={disaron_nom!r} "
                f"added={added} title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {success_file}.")
    print(f"Wrote {skipped_no_html} skipped row(s) to {skipped_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} BlogEntryPage object(s); "
        f"skipped {skipped} failure(s); "
        f"skipped {skipped_no_html} page(s) with no HTML in CSV."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--success-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for successes "
            "(columns: pageId, disaron_nom, added, title)."
        ),
    )
    parser.add_argument(
        "--skipped-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for pages skipped (no HTML in CSV) "
            "(columns: pageId, disaron_nom, title)."
        ),
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(args.failures_file, "html_failures")
    success_file = resolve_success_file(args.success_file, "html_success")
    skipped_file = resolve_success_file(args.skipped_file, "html_skipped_no_html")
    pages = resolve_pages(args.parent_id)
    html_by_disaron_nom = load_html_by_disaron_nom(args.data_file)

    return run_html_update(
        pages=pages,
        html_by_disaron_nom=html_by_disaron_nom,
        failures_file=failures_file,
        success_file=success_file,
        skipped_file=skipped_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to update {pages.count()} BlogEntryPage object(s) "
            f"using HTML from {args.data_file} "
            f"({len(html_by_disaron_nom)} disaron(s) with HTML)."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
