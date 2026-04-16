#!/usr/bin/env python
"""
Normalize BlogEntryPage body blocks by adding/adjusting HTML ids.

For each BlogEntryPage under --parent-id:
- Ensure there is an HTML element with id="disaron-nom" somewhere in body.
  If not found, log the page to failures CSV and continue.
- Find a column block with width 8/12 (commonly stored as "8" or "8/12").
  In that column, expect two rich text blocks:
    1) The first contains an <h4>...</h4>. Replace that rich text block with an
       HTML block containing:
          <h2 id="complement-titre">...</h2>
       using the text of the h4 (tags removed).
    2) Replace the second rich text block with an HTML block wrapping its
       existing HTML in:
          <div id="chapeau"> ... </div>

Usage:
    just add_ids_to_pages \
        --wagtail-project-root ../agreste \
        --parent-id 30
"""

import argparse
import csv
import re
from html import escape, unescape
from pathlib import Path
from typing import Any

from django_setup import setup_django

setup_django(__file__)

from blog.models import BlogEntryPage  # noqa: E402

from metadata_editor.set_metadata import (  # noqa: E402
    resolve_failures_file,
    resolve_pages,
)
from django.utils import timezone  # noqa: E402

DISARON_DIV_RE = re.compile(r'id\s*=\s*["\']disaron-nom["\']', re.IGNORECASE)
H4_RE = re.compile(r"<h4[^>]*>(?P<inner>.*?)</h4>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

RICH_TEXT_BLOCK_TYPES = {"richtext", "paragraph", "text"}
HTML_BLOCK_TYPES = {"html"}


def _get_stream_data(body_value: Any) -> list[Any]:
    if hasattr(body_value, "stream_data"):
        return list(body_value.stream_data)
    if hasattr(body_value, "raw_data"):
        return list(body_value.raw_data)
    raise AttributeError(
        "Unsupported StreamField value: expected StreamValue with "
        "'stream_data' or 'raw_data'."
    )


def _has_disaron_nom(stream_data: list[Any]) -> bool:
    for block in stream_data:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in HTML_BLOCK_TYPES:
            continue
        value = block.get("value")
        if isinstance(value, str) and DISARON_DIV_RE.search(value):
            return True
    return False


def _is_width_8(width: Any) -> bool:
    if width is None:
        return False
    width_str = str(width).strip()
    return width_str in {"8", "8/12", "8/12 ", "8 / 12"}


def _html_block(
    value: str, *, keep_id_from: dict | None = None
) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "html", "value": value}
    if keep_id_from and "id" in keep_id_from:
        block["id"] = keep_id_from["id"]
    return block


def _extract_h4_text(rich_html: str) -> str | None:
    match = H4_RE.search(rich_html)
    if not match:
        return None
    inner = match.group("inner")
    # Remove any nested tags and normalize entities.
    text = TAG_RE.sub("", inner)
    text = unescape(text).strip()
    return text or None


def _content_has_noop_html_pair(content: list[Any]) -> bool:
    """
    Noop detection inside the 8/12 column content:
    - first matching html block contains an h2 with id=\"complement-titre\"
    - the next matching html block after that contains a div with id=\"chapeau\"
    """
    first_idx: int | None = None
    for idx, item in enumerate(content):
        if not isinstance(item, dict) or item.get("type") != "html":
            continue
        v = item.get("value")
        if isinstance(v, str) and 'id="complement-titre"' in v and "<h2" in v:
            first_idx = idx
            break
    if first_idx is None:
        return False
    for item in content[first_idx + 1 :]:
        if not isinstance(item, dict) or item.get("type") != "html":
            continue
        v = item.get("value")
        if isinstance(v, str) and 'id="chapeau"' in v and "<div" in v:
            return True
    return False


def _transform_left_column_content(
    content: list[Any],
) -> tuple[list[Any], str, int]:
    """
    Transform the 8/12 column content.
    Returns: (new_content, disaron_nom_found_in_content, replacement_count)
    """
    # Find the first two rich-text blocks in the column's content.
    rich_indices: list[int] = []
    for idx, item in enumerate(content):
        if isinstance(item, dict):
            if item.get("type") in RICH_TEXT_BLOCK_TYPES and isinstance(
                item.get("value"), str
            ):
                rich_indices.append(idx)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            # Some StreamField representations may be [type, value]
            block_type, block_value = item
            if (
                str(block_type) in RICH_TEXT_BLOCK_TYPES
                and isinstance(block_value, str)
            ):
                rich_indices.append(idx)

    if len(rich_indices) < 2:
        if _content_has_noop_html_pair(content):
            return list(content), "", 0
        raise ValueError(
            "Expected at least 2 rich text blocks in 8/12 column, "
            f"got {len(rich_indices)}."
        )

    first_idx, second_idx = rich_indices[0], rich_indices[1]

    def _as_block_dict(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        if isinstance(item, (list, tuple)) and len(item) == 2:
            return {"type": str(item[0]), "value": item[1]}
        raise TypeError("Unsupported block representation in column content.")

    first_block = _as_block_dict(content[first_idx])
    second_block = _as_block_dict(content[second_idx])

    h4_text = _extract_h4_text(str(first_block.get("value") or ""))
    if not h4_text:
        raise ValueError("First rich text block does not contain an <h4> tag.")

    new_first = _html_block(
        f'<h2 id="complement-titre">{escape(h4_text)}</h2>',
        keep_id_from=first_block,
    )
    second_value = str(second_block.get("value") or "")
    new_second = _html_block(
        f'<div id="chapeau">{second_value}</div>',
        keep_id_from=second_block,
    )

    new_content = list(content)
    new_content[first_idx] = new_first
    new_content[second_idx] = new_second
    return new_content, "", 2


def _transform_body(stream_data: list[Any]) -> tuple[list[Any], int]:
    """
    Apply the requested transformations to the body stream data.
    Returns (new_stream_data, replacements_count).
    """
    replacements = 0
    new_stream = list(stream_data)

    for block_idx, block in enumerate(stream_data):
        if not isinstance(block, dict):
            continue
        if block.get("type") != "multicolumns":
            continue
        value = block.get("value")
        if not isinstance(value, dict):
            continue
        columns = value.get("columns")
        if not isinstance(columns, list):
            continue

        new_columns = list(columns)
        changed = False

        for col_idx, col in enumerate(columns):
            if not isinstance(col, dict):
                continue
            if col.get("type") != "column":
                continue
            col_value = col.get("value")
            if not isinstance(col_value, dict):
                continue

            if not _is_width_8(col_value.get("width")):
                continue

            content = col_value.get("content")
            if not isinstance(content, list):
                raise ValueError("8/12 column has no list 'content'.")

            new_content, _unused, added = _transform_left_column_content(
                content
            )
            replacements += added
            changed = True

            new_col_value = dict(col_value)
            new_col_value["content"] = new_content
            new_col = dict(col)
            new_col["value"] = new_col_value
            new_columns[col_idx] = new_col
            break

        if changed:
            new_value = dict(value)
            new_value["columns"] = new_columns
            new_block = dict(block)
            new_block["value"] = new_value
            new_stream[block_idx] = new_block
            break

    return new_stream, replacements


def _is_already_formatted(stream_data: list[Any]) -> bool:
    """
    Best-effort noop detection.

    Returns True if we can find an 8/12 column whose first two rich-text blocks
    have already been replaced by html blocks containing the expected ids.
    """
    for block in stream_data:
        if not isinstance(block, dict) or block.get("type") != "multicolumns":
            continue
        value = block.get("value")
        if not isinstance(value, dict):
            continue
        columns = value.get("columns")
        if not isinstance(columns, list):
            continue
        for col in columns:
            if not isinstance(col, dict) or col.get("type") != "column":
                continue
            col_value = col.get("value")
            if not isinstance(col_value, dict):
                continue
            if not _is_width_8(col_value.get("width")):
                continue
            content = col_value.get("content")
            if not isinstance(content, list):
                continue

            return _content_has_noop_html_pair(content)
    return False


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
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "add_ids_and_filename_failures"
    )
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    success_file = (
        f"metadata_editor/output/{timestamp}_add_ids_and_filename_success.csv"
    )
    noop_file = (
        f"metadata_editor/output/{timestamp}_add_ids_and_filename_noop.csv"
    )
    pages = resolve_pages(args.parent_id)
    page_count = pages.count()

    mode_prefix = "[DRY RUN] " if args.dry_run else ""
    answer = input(
        f"{mode_prefix}About to update {page_count} BlogEntryPage body(ies). "
        "Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    noop_count = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    success_path = Path(success_file)
    success_path.parent.mkdir(parents=True, exist_ok=True)
    noop_path = Path(noop_file)
    noop_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open("w", encoding="utf-8", newline="") as failures_f,
        success_path.open("w", encoding="utf-8", newline="") as success_f,
        noop_path.open("w", encoding="utf-8", newline="") as noop_f,
    ):
        writer = csv.DictWriter(
            failures_f, fieldnames=["pageId", "disaron_nom", "error"]
        )
        writer.writeheader()
        success_writer = csv.DictWriter(
            success_f, fieldnames=["pageId", "disaron_nom", "replacements"]
        )
        success_writer.writeheader()
        noop_writer = csv.DictWriter(
            noop_f, fieldnames=["pageId", "disaron_nom"]
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

        for page in pages:
            try:
                stream_data = _get_stream_data(page.body)
            except Exception as exc:
                _fail(page, "", f"could not read body stream data: {exc}")
                continue

            if not _has_disaron_nom(stream_data):
                _fail(page, "", "could not find html element with id=disaron-nom")
                continue

            try:
                new_stream, replacements = _transform_body(stream_data)
            except Exception as exc:
                _fail(page, "", f"body transform failed: {exc}")
                continue

            if replacements == 0:
                if _is_already_formatted(stream_data):
                    noop_count += 1
                    # Best-effort disaron_nom extraction from body
                    from metadata_editor.set_metadata import DISARON_NOM_RE  # type: ignore

                    match = DISARON_NOM_RE.search(str(page.body))
                    disaron_nom = match.group(0) if match else ""
                    noop_writer.writerow(
                        {
                            "pageId": str(page.id),
                            "disaron_nom": disaron_nom,
                        }
                    )
                    noop_f.flush()
                    continue
                _fail(page, "", "no matching multicolumns 8/12 column found")
                continue

            page.body = new_stream
            if args.dry_run:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(page, "", f"validation failed: {exc}")
                    continue
            else:
                try:
                    page.save(update_fields=["body"])
                except Exception as exc:
                    _fail(page, "", f"save failed: {exc}")
                    continue

            updated += 1
            match = DISARON_NOM_RE.search(str(page.body))
            disaron_nom = match.group(0) if match else ""
            success_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "replacements": str(replacements),
                }
            )
            success_f.flush()
            action = "Would update" if args.dry_run else "Updated"
            print(
                f"[{updated}/{page_count}] {action} "
                f"id={page.id} title={page.title!r} "
                f"replacements={replacements}"
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {success_file}.")
    print(f"Wrote {noop_count} noop row(s) to {noop_file}.")
    summary_action = "Would update" if args.dry_run else "Updated"
    print(f"{summary_action} {updated} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
