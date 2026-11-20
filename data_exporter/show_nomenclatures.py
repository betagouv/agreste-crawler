#!/usr/bin/env python
"""
Print PublicationPage ``nomenclatures`` for a given ``disaron_id``.

Usage:
    just show_nomenclatures --disaron-id COMEXTBOIS \\
        --wagtail-project-root ../agreste

    just show_nomenclatures --disaron-id COMEXTBOIS \\
        --nomenclature "Groupe d'indicateurs" \\
        --wagtail-project-root ../agreste

    just show_nomenclatures --disaron-id COMEXTBOIS \\
        --wagtail-project-root ../agreste \\
        --scalingo-app agreste-test \\
        --raw
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from scalingo_env_setup import add_scalingo_app_args, configure_database_url, ensure_ssh_tunnel

configure_database_url()

from django_setup import setup_django

setup_django(__file__)
ensure_ssh_tunnel()

from publications.models import PublicationPage  # noqa: E402


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _modalities_from_table(table: Any) -> list[dict[str, str]]:
    """Normalize a TypedTable / list / dict modalities value to row dicts."""
    if table is None:
        return []

    if isinstance(table, list):
        rows: list[dict[str, str]] = []
        for item in table:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "code": _text(item.get("code")),
                    "code_old": _text(item.get("code_old")),
                    "name": _text(item.get("name")),
                }
            )
        return rows

    columns = getattr(table, "columns", None)
    row_data = getattr(table, "row_data", None)
    if isinstance(table, dict) and "row_data" in table:
        columns = table.get("columns")
        row_data = table.get("row_data")

    if columns is not None and row_data is not None:
        indexes: dict[str, int] = {}
        for index, column in enumerate(columns):
            block = column.get("block") if isinstance(column, dict) else None
            block_name = getattr(block, "name", None)
            if isinstance(column, dict) and not block_name:
                block_name = column.get("name") or column.get("heading")
            if block_name:
                indexes.setdefault(str(block_name), index)
        for index, column in enumerate(columns):
            if not isinstance(column, dict):
                continue
            heading = column.get("heading")
            if isinstance(heading, str) and heading.strip():
                indexes.setdefault(heading.strip().lower(), index)

        code_i = indexes.get("code", indexes.get("Code", 0))
        name_i = indexes.get("name", indexes.get("Name", 1))
        code_old_i = indexes.get(
            "code_old",
            indexes.get("Code old", None),
        )

        rows = []
        for row in row_data:
            values = row.get("values") if isinstance(row, dict) else None
            if not isinstance(values, list):
                continue
            code_old = ""
            if code_old_i is not None and code_old_i < len(values):
                code_old = _text(values[code_old_i])
            rows.append(
                {
                    "code": _text(values[code_i]) if code_i < len(values) else "",
                    "code_old": code_old,
                    "name": _text(values[name_i]) if name_i < len(values) else "",
                }
            )
        return rows

    return []


def _block_name(block: Any) -> str:
    if isinstance(block, dict):
        value = block.get("value") or {}
        if isinstance(value, dict):
            return _text(value.get("name"))
        return ""
    value = getattr(block, "value", None)
    if value is None:
        return ""
    try:
        return _text(value["name"])
    except Exception:
        return _text(getattr(value, "name", ""))


def _filter_blocks(blocks: list[Any], nomenclature: str | None) -> list[Any]:
    if not nomenclature:
        return blocks
    wanted = nomenclature.strip()
    matched = [b for b in blocks if _block_name(b) == wanted]
    if not matched:
        available = sorted({_block_name(b) for b in blocks if _block_name(b)})
        raise SystemExit(
            f"No nomenclature named {wanted!r}. "
            f"Available: {available}"
        )
    return matched


def _print_nomenclatures(
    page: PublicationPage,
    *,
    nomenclature: str | None = None,
) -> None:
    blocks = _filter_blocks(list(page.nomenclatures), nomenclature)
    print(
        f"page id={page.id} title={page.title!r} "
        f"disaron_id={page.disaron_id!r} "
        f"nomenclatures={len(blocks)} block(s)"
        + (f" (filter={nomenclature!r})" if nomenclature else "")
    )
    for block in blocks:
        value = block.value
        try:
            name = _text(value["name"])
            modalities = _modalities_from_table(value["modalities"])
        except Exception as exc:
            print(f"  (unreadable block: {exc})")
            continue
        print(f"\n=== {name} ({len(modalities)} modalities) ===")
        for m in modalities:
            print(
                f"  code={m['code']!r} "
                f"code_old={m['code_old']!r} "
                f"name={m['name']!r}"
            )


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "raw_data"):
        return list(obj.raw_data)
    if hasattr(obj, "stream_data"):
        return list(obj.stream_data)
    try:
        return list(obj)
    except TypeError:
        return str(obj)


def _print_raw(
    page: PublicationPage,
    *,
    nomenclature: str | None = None,
) -> None:
    raw = getattr(page.nomenclatures, "raw_data", None)
    if raw is None:
        raw = getattr(page.nomenclatures, "stream_data", None)
    # RawDataView is not JSON-serializable; materialize to plain lists/dicts.
    if raw is not None and not isinstance(raw, (list, dict)):
        raw = list(raw)
    if isinstance(raw, list):
        raw = _filter_blocks(raw, nomenclature)
    print(
        f"page id={page.id} title={page.title!r} "
        f"disaron_id={page.disaron_id!r}"
        + (f" filter={nomenclature!r}" if nomenclature else "")
    )
    print(json.dumps(raw, ensure_ascii=False, indent=2, default=_json_default))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--disaron-id",
        required=True,
        help="PublicationPage.disaron_id to look up.",
    )
    parser.add_argument(
        "--nomenclature",
        default="",
        help=(
            "Optional nomenclature name to show "
            "(exact match on the block name)."
        ),
    )
    parser.add_argument(
        "--wagtail-project-root",
        type=str,
        default="../agreste",
        help=(
            "Root directory of the Wagtail/Django project "
            "(contains config/settings.py). "
            "Default: ../agreste."
        ),
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print StreamField raw_data as JSON instead of a summary.",
    )
    add_scalingo_app_args(parser)
    args = parser.parse_args()
    _ = (args.scalingo_app, args.database_port, args.wagtail_project_root)

    disaron_id = args.disaron_id.strip()
    nomenclature = args.nomenclature.strip() or None
    pages = list(PublicationPage.objects.filter(disaron_id=disaron_id))
    if not pages:
        raise SystemExit(f"No PublicationPage with disaron_id={disaron_id!r}")
    if len(pages) > 1:
        ids = [p.id for p in pages]
        print(
            f"Warning: {len(pages)} pages share disaron_id={disaron_id!r}: {ids}"
        )

    for page in pages:
        if args.raw:
            _print_raw(page, nomenclature=nomenclature)
        else:
            _print_nomenclatures(page, nomenclature=nomenclature)
        if len(pages) > 1:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
