#!/usr/bin/env python
"""
Set PublicationPage ``issue_number`` from Inventaire ``disaron:Numerotation``.

Reads an inventaire CSV (default ``2026-11-05_Inventaire-Nuxeo-v20.csv``).
Keeps rows with ``Garder/Exclure == Garder`` and a nonempty
``disaron:Numerotation``. Looks up each page by ``disaron:nom``
(``PublicationPage.disaron_id`` under any ``PublicationIndexPage``) and sets
``issue_number`` (save revision + re-publish if live).

Identical values are noops. Missing/duplicate pages and conflicting
Numerotation for the same ``disaron:nom`` are logged as failures.

``--parent-id``, ``--data-file``, and ``--scalingo-env-file`` from shared
args are unused. Use ``--scalingo-app`` (and optional ``--database-port``,
default 10001) to fetch Scalingo Postgres credentials and open an SSH tunnel.

Defaults:
- ``--input-file``: ``2026-11-05_Inventaire-Nuxeo-v20.csv``
- ``--failures-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_inventaire_failures.csv``
- ``--successes-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_inventaire_successes.csv``
- ``--noops-file``: ``metadata_editor/output/<YYYY-MM-DD_HH-MM-SS>_issue_number_inventaire_noops.csv``

Usage:
    just set_issue_number_from_inventaire \\
        --wagtail-project-root ../agreste

    just set_issue_number_from_inventaire \\
        --wagtail-project-root ../agreste \\
        --scalingo-app agreste-test

    just set_issue_number_from_inventaire \\
        --wagtail-project-root ../agreste \\
        --scalingo-app agreste-test \\
        --database-port 10001 \\
        --dry-run
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scalingo_env_setup import (
    DEFAULT_DATABASE_PORT,
    configure_database_url,
    ensure_ssh_tunnel,
)

configure_database_url()

from django_setup import setup_django

setup_django(__file__)
ensure_ssh_tunnel()

from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)

from metadata_editor.set_metadata import (  # noqa: E402
    _flush_failures_file,
    add_common_args,
    find_disaron_nom,
    resolve_failures_file,
    resolve_noops_file,
    resolve_successes_file,
)

DEFAULT_INPUT_FILE = "2026-11-05_Inventaire-Nuxeo-v20.csv"
GARDER_VALUE = "Garder"

SUCCESS_FIELDNAMES = [
    "pageId",
    "disaron_nom",
    "value",
    "info",
    "title",
]
FAILURE_FIELDNAMES = ["pageId", "disaron_nom", "error"]
NOOP_FIELDNAMES = ["pageId", "disaron_nom", "info"]


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
                "Unused by this script; use --input-file for the inventaire CSV."
            )
            return
    raise RuntimeError("--data-file argument not found on parser")


def _disable_scalingo_env_file(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if "--scalingo-env-file" in getattr(action, "option_strings", []):
            action.help = (
                "Unused by this script; use --scalingo-app "
                "(and optional --database-port) instead."
            )
            return
    raise RuntimeError("--scalingo-env-file argument not found on parser")


def _numerotation(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def load_issue_numbers_from_inventaire(
    inventaire_file: Path,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """
    Return (issue_number_by_disaron_nom, conflict_failures).

    Keeps ``Garder`` rows with nonempty ``disaron:Numerotation``, keyed by
    stripped ``disaron:nom``.
    """
    if not inventaire_file.exists():
        raise FileNotFoundError(f"Inventaire file not found: {inventaire_file}")

    by_nom: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}

    with inventaire_file.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Inventaire CSV has no headers: {inventaire_file}")
        required = {
            "disaron:nom",
            "disaron:Numerotation",
            "Garder/Exclure",
        }
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Inventaire CSV missing column(s): "
                f"{', '.join(sorted(missing))}: {inventaire_file}"
            )
        for row in reader:
            if (row.get("Garder/Exclure") or "").strip() != GARDER_VALUE:
                continue
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                continue
            numerotation = _numerotation(row.get("disaron:Numerotation"))
            if not numerotation:
                continue
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
                f"conflicting disaron:Numerotation values: {joined}",
            )
        )
    return by_nom, conflict_failures


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
    failures_file: str,
    successes_file: str,
    noops_file: str,
    dry_run: bool,
) -> int:
    noms = sorted(by_nom.keys())
    total = len(noms) + len(conflict_failures)
    mode_prefix = "[DRY RUN] " if dry_run else ""
    answer = input(
        f"{mode_prefix}About to set issue_number on up to {len(noms)} "
        f"PublicationPage object(s) from inventaire Numerotation. "
        f"Type 'yes' to confirm: "
    ).strip()
    if answer.lower() != "yes":
        print(f"{mode_prefix}Update cancelled.")
        return 0

    updated = 0
    noop_count = 0
    failures_count = 0

    failures_path = Path(failures_file)
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    successes_path = Path(successes_file)
    successes_path.parent.mkdir(parents=True, exist_ok=True)
    noops_path = Path(noops_file)
    noops_path.parent.mkdir(parents=True, exist_ok=True)

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
                f"[{updated + noop_count + failures_count}/{total}] "
                f"Failed id={page_id}: {error}",
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
                f"[{updated + noop_count + failures_count}/{total}] "
                f"Noop id={page_id} disaron_nom={disaron_nom!r}: {info}",
                flush=True,
            )

        for disaron_nom, error in conflict_failures:
            _fail("", disaron_nom, error)

        for disaron_nom in noms:
            if disaron_nom in duplicate_noms:
                _fail(
                    "",
                    disaron_nom,
                    "multiple PublicationPages share this disaron_id",
                )
                continue

            page = pages_by_nom.get(disaron_nom)
            if page is None:
                _fail("", disaron_nom, "no PublicationPage with this disaron_id")
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
                f"[{updated + noop_count + failures_count}/{total}] "
                f"{action} id={page.id} disaron_nom={disaron_nom!r} "
                f"issue_number={result.get('value')!r} title={page.title!r}",
                flush=True,
            )

    print(f"Wrote {failures_count} failure row(s) to {failures_file}.")
    print(f"Wrote {updated} success row(s) to {successes_file}.")
    print(f"Wrote {noop_count} noop row(s) to {noops_file}.")
    summary_action = "Would update" if dry_run else "Updated"
    print(
        f"{summary_action} {updated} PublicationPage object(s); "
        f"noop {noop_count}; failed {failures_count}."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    _make_parent_id_optional(parser)
    _make_data_file_optional(parser)
    _disable_scalingo_env_file(parser)
    parser.add_argument(
        "--scalingo-app",
        type=str,
        default="",
        help=(
            "Scalingo app name: fetch SCALINGO_POSTGRESQL_URL, set "
            "DATABASE_URL to 127.0.0.1:<database-port>, and open an SSH tunnel."
        ),
    )
    parser.add_argument(
        "--database-port",
        type=int,
        default=DEFAULT_DATABASE_PORT,
        help=(
            "Local SSH tunnel port used in DATABASE_URL "
            f"(default: {DEFAULT_DATABASE_PORT})."
        ),
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default=DEFAULT_INPUT_FILE,
        help=f"Inventaire CSV (default: {DEFAULT_INPUT_FILE!r}).",
    )
    args = parser.parse_args()
    _ = (args.scalingo_app, args.database_port)  # consumed via sys.argv earlier

    input_file = Path(args.input_file)
    if not input_file.is_absolute():
        input_file = (Path.cwd() / input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    by_nom, conflict_failures = load_issue_numbers_from_inventaire(input_file)
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
        args.failures_file, "issue_number_inventaire_failures"
    )
    successes_file = resolve_successes_file(
        args.successes_file, "issue_number_inventaire_successes"
    )
    noops_file = resolve_noops_file(
        args.noops_file, "issue_number_inventaire_noops"
    )

    return run_update(
        by_nom=by_nom,
        conflict_failures=conflict_failures,
        pages_by_nom=pages_by_nom,
        duplicate_noms=duplicate_noms,
        failures_file=failures_file,
        successes_file=successes_file,
        noops_file=noops_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
