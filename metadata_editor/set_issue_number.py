#!/usr/bin/env python
"""
Set PublicationPage ``issue_number`` from Disaron ``disaron:Numerotation``.

Reads Nuxeo Disaron documents from ``--input-file`` (default
``2026-05-18_Disarons.json``). Keeps every entry with a non-empty
``disaron:Numerotation`` (does not filter on
``disaron:communaute_restit``).

Maps each ``disaron:nom`` to the CMS ``disaron_id`` via
``disaron_mapper`` (``--disaron-mapping-files``). Before page lookup,
``disaron_finder.classify_disaron`` skips curated upload exceptions,
non-public (``DISAR-PUBLIC`` missing from ``disaron:communaute_restit``),
and post-cutoff noms. Remaining noms look up the matching
``PublicationPage`` under any ``PublicationIndexPage`` and set
``issue_number`` (save revision + re-publish if live).

Identical values are noops. Missing/duplicate pages and conflicting
numerotations for the same mapped nom are logged as failures.

``--parent-id`` and ``--data-file`` from shared args are unused.

Defaults:
- ``--input-file``: ``2026-05-18_Disarons.json``
- ``--disaron-mapping-files``: Inventaire v20 + ``2026-10-28_data_disaron_old_new_mapping.csv``
- ``--will-not-upload-file``: ``2026-10-28_will_not_upload.csv``
- ``--not-uploaded-yet-file``: ``2026-10-28_not_uploaded_yet.csv``
- ``--failures-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_failures.csv``
- ``--successes-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_successes.csv``
- ``--noops-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_noops.csv``
- ``--skipped-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_skipped.csv``

Usage:
    just set_issue_number \\
        --wagtail-project-root ../agreste

    just set_issue_number \\
        --wagtail-project-root ../agreste \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable
from pathlib import Path

from django_setup import setup_django

setup_django(__file__)

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)

from disaron_finder import (  # noqa: E402
    DEFAULT_NOT_UPLOADED_YET_FILE,
    DEFAULT_WILL_NOT_UPLOAD_FILE,
    DisaronMeta,
    classify_disaron,
    classify_missing_by_cutoff,
    load_disaron_meta,
    load_upload_exception_reasons,
    missing_page_before_cutoff_error,
)
from disaron_mapper import (  # noqa: E402
    DEFAULT_DISARON_MAPPING_FILES,
    load_disaron_nom_map,
    resolve_disaron_nom,
)
from metadata_editor.get_raw_text_fields import load_docs  # noqa: E402
from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_noops_file,
    resolve_successes_file,
)

DEFAULT_INPUT_FILE = "2026-05-18_Disarons.json"

SUCCESS_FIELDNAMES = [
    "pageId",
    "disaron_nom",
    "value",
    "info",
    "title",
]
FAILURE_FIELDNAMES = ["pageId", "disaron_nom", "error"]
NOOP_FIELDNAMES = ["pageId", "disaron_nom", "info"]
SKIP_FIELDNAMES = ["pageId", "disaron_nom", "reason"]


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


def _make_data_file_optional(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if "--data-file" in getattr(action, "option_strings", []):
            action.required = False
            action.default = ""
            action.help = (
                "Unused by this script; use --input-file for the Disarons JSON."
            )
            return
    raise RuntimeError("--data-file argument not found on parser")


def _numerotation(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def load_issue_numbers_by_nom(
    docs: Iterable[object],
    mapping: dict[str, str],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """
    Return (issue_number_by_mapped_nom, conflict_failures).

    Includes every document with a non-empty ``disaron:Numerotation``;
    restit / public filtering is left to ``disaron_finder``.
    ``conflict_failures`` lists mapped noms that appear with more than one
    distinct Numerotation.
    """
    by_nom: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        props = doc.get("properties") or {}
        if not isinstance(props, dict):
            continue

        nom_old = props.get("disaron:nom")
        if not isinstance(nom_old, str) or not nom_old.strip():
            continue
        numerotation = _numerotation(props.get("disaron:Numerotation"))
        if not numerotation:
            continue

        disaron_nom = resolve_disaron_nom(nom_old.strip(), mapping)
        existing = by_nom.get(disaron_nom)
        if existing is None:
            by_nom[disaron_nom] = numerotation
        elif existing != numerotation:
            conflicts.setdefault(disaron_nom, {existing}).add(numerotation)

    conflict_failures: list[tuple[str, str]] = []
    for disaron_nom, values in sorted(conflicts.items()):
        by_nom.pop(disaron_nom, None)
        joined = ", ".join(sorted(repr(v) for v in values))
        conflict_failures.append(
            (
                disaron_nom,
                f"conflicting disaron:Numerotation values after mapping: {joined}",
            )
        )
    return by_nom, conflict_failures


def remap_disaron_meta(
    meta_by_old_nom: dict[str, DisaronMeta],
    mapping: dict[str, str],
) -> dict[str, DisaronMeta]:
    """Re-key Disaron meta by mapped ``disaron:nom`` (later old noms win)."""
    out: dict[str, DisaronMeta] = {}
    for old_nom, meta in meta_by_old_nom.items():
        out[resolve_disaron_nom(old_nom, mapping)] = meta
    return out


def resolve_all_publication_pages_qs():
    pages = PublicationPage.objects.none()
    for index in PublicationIndexPage.objects.all().order_by("id"):
        pages = pages | PublicationPage.objects.child_of(index)
    return pages.distinct().order_by("id")


def _apply_issue_number(
    page: PublicationPage,
    issue_number: str,
    *,
    dry_run: bool,
) -> dict[str, str]:
    current = getattr(page, "issue_number", None)
    current_str = "" if current is None else str(current).strip()
    if current_str == issue_number:
        return {
            "noop": True,
            "info": f"noop: issue_number already {issue_number!r}",
            "value": issue_number,
        }

    if dry_run:
        return {
            "value": issue_number,
            "info": (
                f"Would set issue_number from {current_str!r} to {issue_number!r}"
            ),
        }

    page.issue_number = issue_number
    revision = page.save_revision()
    if page.live:
        revision.publish()

    return {
        "value": issue_number,
        "info": f"set issue_number from {current_str!r} to {issue_number!r}",
    }


def run_update(
    *,
    by_nom: dict[str, str],
    conflict_failures: list[tuple[str, str]],
    pages_by_nom: dict[str, PublicationPage],
    duplicate_noms: set[str],
    meta_by_nom: dict[str, DisaronMeta],
    will_not_upload: dict[str, str],
    not_uploaded_yet: dict[str, str],
    failures_file: str,
    successes_file: str,
    noops_file: str,
    skipped_file: str,
    dry_run: bool,
) -> int:
    noms = sorted(by_nom.keys())
    total = len(noms) + len(conflict_failures)
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}About to set issue_number on up to {len(noms)} "
        f"PublicationPage object(s) from Disaron Numerotation. "
        f"Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    noop_count = 0
    failures_count = 0
    skipped_count = 0

    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    successes_path = Path(successes_file)
    successes_path.parent.mkdir(parents=True, exist_ok=True)
    noops_path = Path(noops_file)
    noops_path.parent.mkdir(parents=True, exist_ok=True)
    skipped_path = Path(skipped_file)
    skipped_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        failures_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as failures_f,
        successes_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as successes_f,
        noops_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as noops_f,
        skipped_path.open(
            "w", encoding="utf-8", newline="", buffering=1
        ) as skipped_f,
    ):
        failures_writer = csv.DictWriter(
            failures_f, fieldnames=FAILURE_FIELDNAMES
        )
        failures_writer.writeheader()
        _flush_failures_file(failures_f)

        successes_writer = csv.DictWriter(
            successes_f, fieldnames=SUCCESS_FIELDNAMES
        )
        successes_writer.writeheader()
        _flush_failures_file(successes_f)

        noops_writer = csv.DictWriter(noops_f, fieldnames=NOOP_FIELDNAMES)
        noops_writer.writeheader()
        _flush_failures_file(noops_f)

        skipped_writer = csv.DictWriter(skipped_f, fieldnames=SKIP_FIELDNAMES)
        skipped_writer.writeheader()
        _flush_failures_file(skipped_f)

        def _fail(page_id: str, disaron_nom: str, error: str) -> None:
            nonlocal failures_count
            failures_count += 1
            failures_writer.writerow(
                {
                    "pageId": page_id,
                    "disaron_nom": disaron_nom,
                    "error": error,
                }
            )
            _flush_failures_file(failures_f)
            print(
                f"[{updated + noop_count + skipped_count + failures_count}/{total}] "
                f"Failed id={page_id}: {error}",
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
            _flush_failures_file(skipped_f)
            print(
                f"[{updated + noop_count + skipped_count + failures_count}/{total}] "
                f"Skipped disaron_nom={disaron_nom!r}: {reason}",
                flush=True,
            )

        def _noop(page_id: int, disaron_nom: str, info: str) -> None:
            nonlocal noop_count
            noop_count += 1
            noops_writer.writerow(
                {
                    "pageId": str(page_id),
                    "disaron_nom": disaron_nom,
                    "info": info,
                }
            )
            _flush_failures_file(noops_f)
            print(
                f"[{updated + noop_count + skipped_count + failures_count}/{total}] "
                f"Noop id={page_id} disaron_nom={disaron_nom!r}: {info}",
                flush=True,
            )

        for disaron_nom, error in conflict_failures:
            _fail("", disaron_nom, error)

        for disaron_nom in noms:
            classification = classify_disaron(
                disaron_nom,
                meta_by_nom=meta_by_nom,
                will_not_upload=will_not_upload,
                not_uploaded_yet=not_uploaded_yet,
            )
            if classification.kind != "other":
                _skip(disaron_nom, classification.reason)
                continue

            if disaron_nom in duplicate_noms:
                _fail(
                    "",
                    disaron_nom,
                    "multiple PublicationPages share this disaron_id",
                )
                continue

            page = pages_by_nom.get(disaron_nom)
            if page is None:
                cutoff_kind = classify_missing_by_cutoff(
                    disaron_nom, meta_by_nom
                )
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
                result = _apply_issue_number(
                    page,
                    by_nom[disaron_nom],
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - log and continue
                _fail(str(page.id), disaron_nom, str(exc))
                continue

            if result.get("noop"):
                _noop(page.id, disaron_nom, str(result.get("info") or "noop"))
                continue

            updated += 1
            successes_writer.writerow(
                {
                    "pageId": str(page.id),
                    "disaron_nom": disaron_nom,
                    "value": result.get("value") or "",
                    "info": result.get("info") or "",
                    "title": page.title,
                }
            )
            _flush_failures_file(successes_f)
            action = "Would update" if dry_run else "Updated"
            print(
                f"[{updated + noop_count + skipped_count + failures_count}/{total}] "
                f"{action} id={page.id} disaron_nom={disaron_nom!r} "
                f"issue_number={result.get('value')!r} title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    print(f"Wrote {noop_count} noop row(s) to {noops_file}.")
    print(f"Wrote {skipped_count} skipped row(s) to {skipped_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} PublicationPage object(s); "
        f"noop {noop_count}; skipped {skipped_count}; "
        f"failed {failures_count}."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    _make_parent_id_optional(parser)
    _make_data_file_optional(parser)
    parser.add_argument(
        "--input-file",
        type=str,
        default=DEFAULT_INPUT_FILE,
        help=f"Disarons JSON dump (default: {DEFAULT_INPUT_FILE!r}).",
    )
    parser.add_argument(
        "--disaron-mapping-files",
        nargs="+",
        default=list(DEFAULT_DISARON_MAPPING_FILES),
        help=(
            "CSVs with disaron:nom_old → disaron:nom "
            f"(default: {DEFAULT_DISARON_MAPPING_FILES!r})."
        ),
    )
    parser.add_argument(
        "--will-not-upload-file",
        type=str,
        default=DEFAULT_WILL_NOT_UPLOAD_FILE,
        help=(
            "CSV of disaron:nom values that will not be uploaded "
            f"(default: {DEFAULT_WILL_NOT_UPLOAD_FILE!r})."
        ),
    )
    parser.add_argument(
        "--not-uploaded-yet-file",
        type=str,
        default=DEFAULT_NOT_UPLOADED_YET_FILE,
        help=(
            "CSV of disaron:nom values not uploaded yet "
            f"(default: {DEFAULT_NOT_UPLOADED_YET_FILE!r})."
        ),
    )
    parser.add_argument(
        "--skipped-file",
        type=str,
        default="",
        help=(
            "Path to CSV output for skipped rows (upload exceptions, "
            "non-public, or post-cutoff; columns: pageId, disaron_nom, reason)."
        ),
    )
    args = parser.parse_args()

    input_file = Path(args.input_file)
    if not input_file.is_absolute():
        input_file = (Path.cwd() / input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    mapping_files = [Path(p).resolve() for p in args.disaron_mapping_files]
    for mapping_file in mapping_files:
        if not mapping_file.exists():
            raise FileNotFoundError(
                f"Disaron mapping file not found: {mapping_file}"
            )
    mapping = load_disaron_nom_map(mapping_files)

    by_nom, conflict_failures = load_issue_numbers_by_nom(
        load_docs(input_file),
        mapping,
    )
    print(
        f"Loaded {len(by_nom)} Numerotation value(s) "
        f"from {input_file.name}"
        + (
            f" ({len(conflict_failures)} conflicting nom(s))"
            if conflict_failures
            else ""
        )
        + "."
    )

    meta_by_nom = remap_disaron_meta(load_disaron_meta(input_file), mapping)
    will_not_upload = load_upload_exception_reasons(args.will_not_upload_file)
    not_uploaded_yet = load_upload_exception_reasons(
        args.not_uploaded_yet_file
    )

    pages_by_nom: dict[str, PublicationPage] = {}
    duplicate_noms: set[str] = set()
    pages = resolve_all_publication_pages_qs().filter(
        disaron_id__in=list(by_nom.keys())
    )
    for page in pages:
        disaron_nom = find_disaron_nom(page)
        if not disaron_nom:
            continue
        if disaron_nom in pages_by_nom:
            duplicate_noms.add(disaron_nom)
            continue
        pages_by_nom[disaron_nom] = page

    failures_file = resolve_failures_file(
        args.failures_file, "issue_number_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "issue_number_successes"
    )
    noops_file = resolve_noops_file(args.noops_file, "issue_number_noops")
    skipped_file = resolve_successes_file(
        args.skipped_file, "issue_number_skipped"
    )

    return run_update(
        by_nom=by_nom,
        conflict_failures=conflict_failures,
        pages_by_nom=pages_by_nom,
        duplicate_noms=duplicate_noms,
        meta_by_nom=meta_by_nom,
        will_not_upload=will_not_upload,
        not_uploaded_yet=not_uploaded_yet,
        failures_file=failures_file,
        successes_file=successes_file,
        noops_file=noops_file,
        skipped_file=skipped_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
