#!/usr/bin/env python
"""
Update StandardPublication content fields on PublicationPages from a CSV.

Loops over CSV rows with content (not over existing pages). For each
``disaron:nom``, finds the PublicationPage under **any**
``PublicationIndexPage`` whose ``disaron_id`` matches, then sets:

- ``html_principal`` → ``presentation``
- ``html_secondaire`` → ``additional_information``
- ``notes`` or ``disaron:notes`` → ``notes`` (JSON list of strings,
  e.g. ``["a","b"]``, converted to an HTML bullet list)

Empty CSV cells leave the existing field unchanged. Rows with no content are
ignored when loading. Missing ``standard_publication`` block is an error.

When no page is found for a CSV ``disaron:nom``, checks run in order:

1. Inventaire (``Garder`` / ``Exclure`` on ``disaron:nom`` or
   ``disaron:nom_old``): Exclure → skip.
2. Cutoffs from Disarons JSON (strictly later than): publication after
   ``2026-03-01``; data after ``2026-09-30`` (type ``t``, or type ``p``
   with ``SeriesLongues`` in the nom) → skip.
3. ``disaron:communaute_restit`` without ``DISAR-PUBLIC`` → skip
   (non-public).
4. Otherwise → failure (unidentified type, or identified but on/before
   cutoff with no page).

On success, saves a revision and re-publishes if the page was live.

``--parent-id`` from shared args is not used by this script.

Defaults:
- ``--data-file``: ``metadata_editor/data/2026-05-18_Disarons_clean_text_fields.csv``
- ``--inventaire-file``: ``2026-06-02_Inventaire-Nuxeo-v18.csv``
- ``--disarons-json``: ``2026-05-18_Disarons.json``

Usage:
    just set_text_fields \\
        --wagtail-project-root ../agreste

    just set_text_fields \\
        --wagtail-project-root ../agreste \\
        --data-file path/to/content.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from django_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    _get_stream_data,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
)

NOTES_COLUMNS = ("notes", "disaron:notes")
STANDARD_PUBLICATION_TYPE = "standard_publication"

_DEFAULT_DATA_FILE = (
    "metadata_editor/data/2026-05-18_Disarons_clean_text_fields.csv"
)
_DEFAULT_INVENTAIRE_FILE = "2026-06-02_Inventaire-Nuxeo-v18.csv"
_DEFAULT_DISARONS_JSON = "2026-05-18_Disarons.json"

PUBLICATION_CUTOFF = date(2026, 3, 1)
DATA_CUTOFF = date(2026, 9, 30)
SKIP_REASON_PUBLICATION_CUTOFF = "Publication published after 2026-03-01"
SKIP_REASON_DATA_CUTOFF = "Data published after 2026-09-30"
SKIP_REASON_NON_PUBLIC = "Non-public page"
DISAR_PUBLIC = "DISAR-PUBLIC"

CutoffClassifyResult = Literal[
    "skip_publication",
    "skip_data",
    "error_unidentified",
    "not_after_cutoff",
]

# Tolerant extraction when a dump object has invalid JSON (e.g. raw newlines in HTML).
_DISARON_NOM_RE = re.compile(r'"disaron:nom"\s*:\s*"(?P<value>[^"]*)"')
_DISARON_TYPE_RE = re.compile(
    r'"disaron:type_disaron"\s*:\s*"(?P<value>[^"]*)"'
)
_DISARON_DATE_RE = re.compile(
    r'"disaron:Date_premiere_publication"\s*:\s*"(?P<value>[^"]*)"'
)
_DISARON_RESTIT_RE = re.compile(
    r'"disaron:communaute_restit"\s*:\s*\[(?P<value>[^\]]*)\]'
)


@dataclass(frozen=True)
class ContentFields:
    presentation: str | None
    additional_information: str | None
    notes: str | None

    def has_any(self) -> bool:
        return bool(
            self.presentation or self.additional_information or self.notes
        )

    def added_labels(self) -> list[str]:
        labels: list[str] = []
        if self.presentation:
            labels.append("presentation")
        if self.additional_information:
            labels.append("additional_information")
        if self.notes:
            labels.append("notes")
        return labels


def _pick_notes_column(fieldnames: list[str]) -> str:
    present = [name for name in NOTES_COLUMNS if name in fieldnames]
    if not present:
        raise ValueError(
            "Data CSV must contain one of: "
            + ", ".join(repr(c) for c in NOTES_COLUMNS)
        )
    return present[0]


def _notes_to_richtext(raw: str) -> str | None:
    """Parse a JSON list of strings into an HTML ``<ul>`` bullet list.

    Returns ``None`` for an empty list ``[]`` (leave the field unchanged).
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"notes must be a JSON list of strings: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            "notes must be a JSON list of strings, "
            f"got {type(data).__name__}"
        )
    if not data:
        return None
    if not all(isinstance(item, str) for item in data):
        raise ValueError("notes must be a JSON list of strings")
    items = "".join(f"<li>{html.escape(item)}</li>" for item in data)
    return f"<ul>{items}</ul>"


def load_content_by_disaron_nom(data_file: str) -> dict[str, ContentFields]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise FileNotFoundError(f"Data file not found: {csv_path}")

    out: dict[str, ContentFields] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV has no headers.")
        available = set(reader.fieldnames)
        if "disaron:nom" not in available:
            raise ValueError("Data CSV must contain 'disaron:nom' column.")
        required = {"html_principal", "html_secondaire"}
        missing = required - available
        if missing:
            raise ValueError(
                f"Data CSV missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        notes_column = _pick_notes_column(list(reader.fieldnames))

        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                continue

            presentation = (row.get("html_principal") or "").strip() or None
            additional = (row.get("html_secondaire") or "").strip() or None
            raw_notes = (row.get(notes_column) or "").strip()
            try:
                notes = _notes_to_richtext(raw_notes) if raw_notes else None
            except ValueError as exc:
                raise ValueError(
                    f"{disaron_nom!r}: {exc}"
                ) from exc
            content = ContentFields(
                presentation=presentation,
                additional_information=additional,
                notes=notes,
            )
            if not content.has_any():
                continue
            out[disaron_nom] = content

    return out


def load_inventaire_statuses(
    inventaire_file: str | Path,
) -> dict[str, frozenset[str]]:
    """Map nom / nom_old → set of Garder/Exclure statuses for that key."""
    path = Path(inventaire_file)
    if not path.exists():
        raise FileNotFoundError(f"Inventaire file not found: {path}")

    by_key: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Inventaire CSV has no headers: {path}")
        required = {"disaron:nom", "disaron:nom_old", "Garder/Exclure"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Inventaire CSV missing column(s): "
                f"{', '.join(sorted(missing))}"
            )
        for row in reader:
            status = (row.get("Garder/Exclure") or "").strip()
            if not status:
                continue
            for col in ("disaron:nom", "disaron:nom_old"):
                key = (row.get(col) or "").strip()
                if key:
                    by_key[key].add(status)

    return {key: frozenset(statuses) for key, statuses in by_key.items()}


def classify_missing_page(
    disaron_nom: str, inventaire_statuses: dict[str, frozenset[str]]
) -> str:
    """Return ``exclure`` or ``error`` for a CSV nom with no matching page."""
    statuses = inventaire_statuses.get(disaron_nom, frozenset())
    if "Garder" in statuses:
        return "error"
    if "Exclure" in statuses:
        return "exclure"
    return "error"


@dataclass(frozen=True)
class DisaronMeta:
    type_disaron: str
    date_premiere_publication: date | None
    communaute_restit: tuple[str, ...]


def _parse_communaute_restit(raw: object) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(item) for item in raw if item is not None and str(item))
    if isinstance(raw, str) and raw.strip():
        return (raw.strip(),)
    return ()


def _parse_communaute_restit_from_raw_array(raw_inner: str) -> tuple[str, ...]:
    """Parse string literals inside a ``[...]`` array fragment from raw JSON."""
    return tuple(re.findall(r'"([^"]*)"', raw_inner))


def is_non_public(meta: DisaronMeta) -> bool:
    """True when restit does not include ``DISAR-PUBLIC`` (null/empty included)."""
    return DISAR_PUBLIC not in meta.communaute_restit


def _parse_premiere_publication(raw: object) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    day = text[:10]
    try:
        return datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None


def _kind_from_meta(disaron_nom: str, meta: DisaronMeta) -> str | None:
    """Return ``publication``, ``data``, or ``None`` if unidentified."""
    type_disaron = (meta.type_disaron or "").strip()
    if type_disaron == "t" or (
        type_disaron == "p" and "SeriesLongues" in disaron_nom
    ):
        return "data"
    if type_disaron == "p" and "SeriesLongues" not in disaron_nom:
        return "publication"
    return None


def _iter_disaron_json_docs(path: Path):
    """Yield document dicts from a JSON array or concatenated object dump."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        data = None

    if data is not None:
        if isinstance(data, list):
            for doc in data:
                if isinstance(doc, dict):
                    yield doc
            return
        if isinstance(data, dict):
            yield data
            return
        raise ValueError(
            f"Expected a JSON list or object of documents, got {type(data).__name__}"
        )

    # Concatenated `{...},\n{...}` dump (same shape as 2026-05-18_Disarons.json).
    in_string = False
    escape = False
    depth = 0
    started = False
    buffer: list[str] = []
    with path.open(encoding="utf-8") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            for ch in chunk:
                if not started:
                    if ch == "{":
                        started = True
                        depth = 1
                        in_string = False
                        escape = False
                        buffer = ["{"]
                    continue
                buffer.append(ch)
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        raw_obj = "".join(buffer)
                        started = False
                        buffer = []
                        try:
                            doc = json.loads(raw_obj)
                        except json.JSONDecodeError:
                            # Malformed objects still expose nom/type/date as
                            # plain strings; recover those for cutoff checks.
                            nom_m = _DISARON_NOM_RE.search(raw_obj)
                            if not nom_m:
                                continue
                            type_m = _DISARON_TYPE_RE.search(raw_obj)
                            date_m = _DISARON_DATE_RE.search(raw_obj)
                            restit_m = _DISARON_RESTIT_RE.search(raw_obj)
                            restit: list[str] = []
                            if restit_m:
                                restit = list(
                                    _parse_communaute_restit_from_raw_array(
                                        restit_m.group("value")
                                    )
                                )
                            yield {
                                "properties": {
                                    "disaron:nom": nom_m.group("value"),
                                    "disaron:type_disaron": (
                                        type_m.group("value") if type_m else ""
                                    ),
                                    "disaron:Date_premiere_publication": (
                                        date_m.group("value") if date_m else ""
                                    ),
                                    "disaron:communaute_restit": restit,
                                }
                            }
                            continue
                        if isinstance(doc, dict):
                            yield doc


def load_disaron_meta(disarons_json: str | Path) -> dict[str, DisaronMeta]:
    """Map ``disaron:nom`` → type, date, and restit communities from a JSON dump."""
    path = Path(disarons_json)
    if not path.exists():
        raise FileNotFoundError(f"Disarons JSON not found: {path}")

    out: dict[str, DisaronMeta] = {}
    for doc in _iter_disaron_json_docs(path):
        props = doc.get("properties") or {}
        if not isinstance(props, dict):
            continue
        nom = (props.get("disaron:nom") or "").strip()
        if not nom:
            continue
        type_disaron = props.get("disaron:type_disaron")
        out[nom] = DisaronMeta(
            type_disaron="" if type_disaron is None else str(type_disaron),
            date_premiere_publication=_parse_premiere_publication(
                props.get("disaron:Date_premiere_publication")
            ),
            communaute_restit=_parse_communaute_restit(
                props.get("disaron:communaute_restit")
            ),
        )
    return out


def classify_missing_by_cutoff(
    disaron_nom: str, meta_by_nom: dict[str, DisaronMeta]
) -> CutoffClassifyResult:
    """Classify a missing page after inventaire has already failed.

    Returns a cutoff skip, unidentified error, or not-after-cutoff (caller
    should keep the usual no-page failure).
    """
    meta = meta_by_nom.get(disaron_nom)
    if meta is None:
        return "error_unidentified"

    kind = _kind_from_meta(disaron_nom, meta)
    if kind is None:
        return "error_unidentified"
    if meta.date_premiere_publication is None:
        return "error_unidentified"

    if kind == "publication":
        if meta.date_premiere_publication > PUBLICATION_CUTOFF:
            return "skip_publication"
        return "not_after_cutoff"

    if meta.date_premiere_publication > DATA_CUTOFF:
        return "skip_data"
    return "not_after_cutoff"


def missing_page_before_cutoff_error(
    disaron_nom: str, meta_by_nom: dict[str, DisaronMeta]
) -> str:
    """Error text when the page is missing but the item is on/before cutoff."""
    meta = meta_by_nom[disaron_nom]
    kind = _kind_from_meta(disaron_nom, meta)
    assert kind is not None
    assert meta.date_premiere_publication is not None
    cutoff = PUBLICATION_CUTOFF if kind == "publication" else DATA_CUTOFF
    return (
        f"no PublicationPage for disaron_id={disaron_nom!r} "
        f"({kind}, published {meta.date_premiere_publication.isoformat()}, "
        f"on or before cutoff {cutoff.isoformat()})"
    )


def resolve_all_publication_pages() -> list[PublicationPage]:
    """Return PublicationPages that are direct children of any index."""
    pages: list[PublicationPage] = []
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages.extend(
            PublicationPage.objects.child_of(index).order_by("id")
        )
    pages.sort(key=lambda page: page.id)
    return pages


def pages_by_disaron_nom(pages: Any) -> dict[str, PublicationPage]:
    out: dict[str, PublicationPage] = {}
    for page in pages:
        disaron_nom = find_disaron_nom(page)
        if disaron_nom is None:
            continue
        if disaron_nom in out:
            raise ValueError(
                f"Duplicate disaron_id across publication indexes: "
                f"{disaron_nom!r} "
                f"(page ids {out[disaron_nom].id} and {page.id})"
            )
        out[disaron_nom] = page
    return out


def _apply_content_to_stream(
    stream_data: list[Any], content: ContentFields
) -> list[Any]:
    if not content.has_any():
        raise ValueError("nothing to insert (all content fields empty)")

    new_stream = list(stream_data)
    for block_idx, block in enumerate(stream_data):
        if not isinstance(block, dict):
            continue
        if block.get("type") != STANDARD_PUBLICATION_TYPE:
            continue

        value = block.get("value")
        if not isinstance(value, dict):
            raise ValueError(
                f"{STANDARD_PUBLICATION_TYPE} block has no value dict."
            )

        new_value = dict(value)
        if content.presentation is not None:
            new_value["presentation"] = content.presentation
        if content.additional_information is not None:
            new_value["additional_information"] = content.additional_information
        if content.notes is not None:
            new_value["notes"] = content.notes

        new_block = dict(block)
        new_block["value"] = new_value
        new_stream[block_idx] = new_block
        return new_stream

    raise ValueError(
        f"no {STANDARD_PUBLICATION_TYPE} block found in page body."
    )


def _apply_content(page: PublicationPage, content: ContentFields) -> None:
    stream_data = _get_stream_data(page.body)
    page.body = _apply_content_to_stream(stream_data, content)


def _persist_page(page: PublicationPage) -> None:
    revision = page.save_revision()
    if page.live:
        revision.publish()


def resolve_success_file(provided: str, default_suffix: str) -> str:
    if provided:
        return provided
    timestamp = timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")
    return f"metadata_editor/output/{timestamp}_{default_suffix}.csv"


def run_content_update(
    *,
    pages_by_nom: dict[str, PublicationPage],
    content_by_disaron_nom: dict[str, ContentFields],
    inventaire_statuses: dict[str, frozenset[str]],
    meta_by_nom: dict[str, DisaronMeta],
    failures_file: str,
    success_file: str,
    skipped_file: str,
    dry_run: bool,
    confirmation_message: str,
) -> int:
    row_count = len(content_by_disaron_nom)
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}{confirmation_message} Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    processed = 0
    skipped_count = 0
    failures_count = 0
    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    success_path = Path(success_file)
    success_path.parent.mkdir(parents=True, exist_ok=True)
    skipped_path = Path(skipped_file)
    skipped_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as failures_f,
        success_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as success_f,
        skipped_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as skipped_f,
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
            fieldnames=["pageId", "disaron_nom", "reason"],
        )
        skipped_writer.writeheader()
        skipped_f.flush()

        def _fail(page_id: str, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            writer.writerow(
                {
                    "pageId": page_id,
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            _flush_failures_file(failures_f)
            print(
                f"[{processed}/{row_count}] "
                f"Failed disaron_nom={disaron_nom!r}: {error}",
                flush=True,
            )

        def _skip(disaron_nom: str, reason: str) -> None:
            nonlocal skipped_count
            skipped_count += 1
            skipped_writer.writerow(
                {
                    "pageId": "",
                    "disaron_nom": disaron_nom,
                    "reason": reason,
                }
            )
            skipped_f.flush()
            print(
                f"[{processed}/{row_count}] "
                f"Skipped disaron_nom={disaron_nom!r}: {reason}",
                flush=True,
            )

        for disaron_nom, content in sorted(content_by_disaron_nom.items()):
            processed += 1
            page = pages_by_nom.get(disaron_nom)
            if page is None:
                inventaire_kind = classify_missing_page(
                    disaron_nom, inventaire_statuses
                )
                if inventaire_kind == "exclure":
                    _skip(disaron_nom, "Exclure in inventaire")
                    continue

                cutoff_kind = classify_missing_by_cutoff(
                    disaron_nom, meta_by_nom
                )
                if cutoff_kind == "skip_publication":
                    _skip(disaron_nom, SKIP_REASON_PUBLICATION_CUTOFF)
                    continue
                if cutoff_kind == "skip_data":
                    _skip(disaron_nom, SKIP_REASON_DATA_CUTOFF)
                    continue

                meta = meta_by_nom.get(disaron_nom)
                if meta is not None and is_non_public(meta):
                    _skip(disaron_nom, SKIP_REASON_NON_PUBLIC)
                    continue

                if cutoff_kind == "error_unidentified":
                    _fail(
                        "",
                        disaron_nom,
                        "cannot identify as publication or data "
                        f"in Disarons JSON for disaron_id={disaron_nom!r}",
                    )
                else:
                    _fail(
                        "",
                        disaron_nom,
                        missing_page_before_cutoff_error(
                            disaron_nom, meta_by_nom
                        ),
                    )
                continue

            try:
                _apply_content(page, content)
            except Exception as exc:
                _fail(str(page.id), disaron_nom, f"apply failed: {exc}")
                continue

            if not dry_run:
                try:
                    _persist_page(page)
                except Exception as exc:
                    _fail(str(page.id), disaron_nom, f"save failed: {exc}")
                    continue
            else:
                try:
                    page.full_clean()
                except Exception as exc:
                    _fail(
                        str(page.id),
                        disaron_nom,
                        f"validation failed: {exc}",
                    )
                    continue

            updated += 1
            added = ",".join(content.added_labels())
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
                f"[{processed}/{row_count}] {action} "
                f"id={page.id} disaron_nom={disaron_nom!r} "
                f"added={added} title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {success_file}.")
    print(f"Wrote {skipped_count} skipped row(s) to {skipped_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} PublicationPage object(s); "
        f"{failures_count} failure(s); "
        f"{skipped_count} skipped."
    )
    return 0


def _set_data_file_default(
    parser: argparse.ArgumentParser, default: str
) -> None:
    for action in parser._actions:
        if "--data-file" in getattr(action, "option_strings", []):
            action.required = False
            action.default = default
            return
    raise RuntimeError("--data-file argument not found on parser")


def _make_parent_id_optional(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if "--parent-id" in getattr(action, "option_strings", []):
            action.required = False
            action.default = None
            action.help = (
                "Unused by this script; PublicationPages are resolved under "
                "any PublicationIndexPage."
            )
            return
    raise RuntimeError("--parent-id argument not found on parser")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    _set_data_file_default(parser, _DEFAULT_DATA_FILE)
    _make_parent_id_optional(parser)
    parser.add_argument(
        "--inventaire-file",
        type=str,
        default=_DEFAULT_INVENTAIRE_FILE,
        help=(
            "Inventaire CSV used when a CSV disaron:nom has no matching page "
            f"(default: {_DEFAULT_INVENTAIRE_FILE})."
        ),
    )
    parser.add_argument(
        "--disarons-json",
        type=str,
        default=_DEFAULT_DISARONS_JSON,
        help=(
            "Disarons JSON dump used for cutoff skips when inventaire does "
            f"not mark Exclure (default: {_DEFAULT_DISARONS_JSON})."
        ),
    )
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
            "Path to CSV output for skipped rows (Exclure, post-cutoff "
            "publication/data, or non-public; columns: pageId, disaron_nom, "
            "reason)."
        ),
    )
    args = parser.parse_args()

    failures_file = resolve_failures_file(
        args.failures_file, "text_fields_failures"
    )
    success_file = resolve_success_file(
        args.success_file, "text_fields_success"
    )
    skipped_file = resolve_success_file(
        args.skipped_file, "text_fields_skipped"
    )
    pages = resolve_all_publication_pages()
    pages_by_nom = pages_by_disaron_nom(pages)
    content_by_disaron_nom = load_content_by_disaron_nom(args.data_file)
    inventaire_statuses = load_inventaire_statuses(args.inventaire_file)
    meta_by_nom = load_disaron_meta(args.disarons_json)

    return run_content_update(
        pages_by_nom=pages_by_nom,
        content_by_disaron_nom=content_by_disaron_nom,
        inventaire_statuses=inventaire_statuses,
        meta_by_nom=meta_by_nom,
        failures_file=failures_file,
        success_file=success_file,
        skipped_file=skipped_file,
        dry_run=args.dry_run,
        confirmation_message=(
            f"About to process {len(content_by_disaron_nom)} CSV row(s) "
            f"with content from {args.data_file} "
            f"against {len(pages_by_nom)} PublicationPage(s) "
            f"under all PublicationIndexPages."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
