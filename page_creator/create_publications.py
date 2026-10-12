#!/usr/bin/env python
"""
Create `PublicationPage` entries under a `PublicationIndexPage`.

Each page contains a single `standard_publication` block with Data-typed
downloadable documents. Pages are published by default.

Flags with examples:
    --parent-id 42
            Parent page id (`PublicationIndexPage`) (required).
    --wagtail-project-root ../agreste
            Path to Django/Wagtail project root.
    --scalingo-env-file /path/to/.env.scalingo
            Env file loaded before Django setup (remote DB).
    --data-file path/to/publications.csv
            CSV of pages to create (required).
    --documents-file path/to/documents.csv
            CSV mapping disaron:nom → renamed_filename (required).
    --documents-dir my-downloader/downloads
            Directory containing files named as renamed_filename (required).
    --labels-file 2026-10-08_libelles_source_signature_L025.csv
            Source-signature → subtitle labels (optional default).
    --publish / --no-publish
            Publish pages (default: publish).
    --dry-run
            Validate and print planned actions; no DB writes/uploads.
    --force-file-uploads
            Always upload new Wagtail docs (boolean flag).
    --debug
            Print debug logs (boolean flag).
    --no-confirmation
            Skip confirmation prompt (boolean flag).

Expected CSV columns:

1) `--data-file` (required):
- `dc:title`
- `disaron:nom`
- `disaron:source_signature`
- `disaron:Date_premiere_publication` (format: `YYYY/MM/DD HH:MM`)
- `disaron:categorie` (`CHDET` or `SERIES`)

2) `--documents-file` (required):
- `disaron:nom`
- `renamed_filename`
  One file per line; multiple lines may share the same disaron:nom.
  Files are read from `--documents-dir` with no renaming/prefixing.

Page Collections from `disaron:categorie`:
- `CHDET` → "Données", "Chiffres détaillés"
- `SERIES` → "Données", "Séries longues conjoncturelles"

Wagtail Documents are stored in document Collection "Données".

Minimal example:
    uv run python -m page_creator.create_publications \
        --wagtail-project-root ../agreste --parent-id 42 \
        --data-file path/to/publications.csv \
        --documents-file my-downloader/2026-10-07_tableaux_files_to_download.csv \
        --documents-dir my-downloader/downloads
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from django_setup import setup_django

setup_django(__file__)

from django.core.files.base import File  # noqa: E402
from django.utils import timezone  # noqa: E402
from django.utils.text import slugify  # noqa: E402
from wagtail.documents.models import Document  # noqa: E402
from wagtail.models import Collection as WagtailCollection  # noqa: E402
from wagtail.models import Page  # noqa: E402

from publications.models import (  # noqa: E402
    Collection as PageCollection,
)
from publications.models import (  # noqa: E402
    PublicationIndexPage,
    PublicationPage,
)

DOCUMENTS_COLLECTION_NAME = "Données"
DEFAULT_LABELS_FILE = "2026-10-08_libelles_source_signature_L025.csv"

CATEGORIE_TO_PAGE_COLLECTION_NAMES: dict[str, tuple[str, ...]] = {
    "CHDET": ("Données", "Chiffres détaillés"),
    "SERIES": ("Données", "Séries longues conjoncturelles"),
}

DATE_FORMAT = "%Y/%m/%d %H:%M"


def _parse_publication_date(raw_value: str) -> datetime:
    value = raw_value.strip()
    try:
        naive_dt = datetime.strptime(value, DATE_FORMAT)
    except ValueError as exc:
        raise ValueError(
            f"Unsupported disaron:Date_premiere_publication value: {raw_value!r}. "
            f"Expected format {DATE_FORMAT} (YYYY/MM/DD HH:MM)."
        ) from exc
    return timezone.make_aware(naive_dt, timezone.get_current_timezone())


def _generate_unique_slug(parent_page: PublicationIndexPage, title: str) -> str:
    base_slug = slugify(title) or "publication"
    slug = base_slug
    suffix = 2
    while PublicationPage.objects.child_of(parent_page).filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _read_rows_from_data_file(
    data_file: str,
) -> tuple[list[dict[str, str]], list[str], Path]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise ValueError(f"Data file not found: {csv_path}")

    required = {
        "dc:title",
        "disaron:nom",
        "disaron:source_signature",
        "disaron:Date_premiere_publication",
        "disaron:categorie",
    }
    rows_out: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Data CSV must contain headers.")
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            raise ValueError(
                "Data CSV missing required column(s): " + ", ".join(missing)
            )
        fieldnames = list(reader.fieldnames)
        for row in reader:
            rows_out.append({key: (row.get(key) or "") for key in fieldnames})

    if not rows_out:
        raise ValueError("CSV is empty (no data rows).")
    return rows_out, fieldnames, csv_path


def _build_page_row(input_row: dict[str, str], line_no: int) -> dict[str, object]:
    title = (input_row.get("dc:title") or "").strip()
    if not title:
        raise ValueError(f"[line {line_no}] Missing dc:title")

    disaron_nom = (input_row.get("disaron:nom") or "").strip()
    if not disaron_nom:
        raise ValueError(f"[line {line_no}] Missing disaron:nom")

    source_signature = (input_row.get("disaron:source_signature") or "").strip()
    if not source_signature:
        raise ValueError(f"[line {line_no}] Missing disaron:source_signature")

    categorie = (input_row.get("disaron:categorie") or "").strip()
    if not categorie:
        raise ValueError(f"[line {line_no}] Missing disaron:categorie")
    if categorie not in CATEGORIE_TO_PAGE_COLLECTION_NAMES:
        raise ValueError(
            f"[line {line_no}] Unknown disaron:categorie {categorie!r}. "
            f"Expected one of: {', '.join(sorted(CATEGORIE_TO_PAGE_COLLECTION_NAMES))}."
        )

    raw_date = (input_row.get("disaron:Date_premiere_publication") or "").strip()
    if not raw_date:
        raise ValueError(
            f"[line {line_no}] Missing disaron:Date_premiere_publication"
        )
    try:
        publication_date = _parse_publication_date(raw_date)
    except ValueError as exc:
        raise ValueError(
            f"[line {line_no}] Invalid disaron:Date_premiere_publication: {exc}"
        ) from exc

    return {
        "title": title,
        "disaron_nom": disaron_nom,
        "source_signature": source_signature,
        "categorie": categorie,
        "publication_date": publication_date,
    }


def _read_documents_by_disaron_nom(documents_file: str) -> dict[str, list[str]]:
    csv_path = Path(documents_file)
    if not csv_path.exists():
        raise ValueError(f"Documents file not found: {csv_path}")

    mapping: dict[str, list[str]] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Documents CSV must contain headers.")
        fieldnames = set(reader.fieldnames)
        if "disaron:nom" not in fieldnames or "renamed_filename" not in fieldnames:
            raise ValueError(
                "Documents CSV must contain columns 'disaron:nom' and "
                "'renamed_filename'."
            )
        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            renamed_filename = (row.get("renamed_filename") or "").strip()
            if not disaron_nom or not renamed_filename:
                continue
            mapping.setdefault(disaron_nom, []).append(renamed_filename)

    if not mapping:
        raise ValueError("Documents CSV has no usable rows.")
    return mapping


def _load_source_signature_labels(labels_file: str) -> dict[str, str]:
    csv_path = Path(labels_file)
    if not csv_path.is_absolute():
        csv_path = (Path.cwd() / csv_path).resolve()
    if not csv_path.exists():
        raise ValueError(f"Labels file not found: {csv_path}")

    labels: dict[str, str] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {
            "contenunomenclature:modalite_stockage",
            "dc:title",
        }
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                "Labels CSV must contain columns "
                "'contenunomenclature:modalite_stockage' and 'dc:title'."
            )
        for row in reader:
            key = (row.get("contenunomenclature:modalite_stockage") or "").strip()
            title = (row.get("dc:title") or "").strip()
            if key and title:
                labels[key] = title

    if not labels:
        raise ValueError("Labels CSV has no usable rows.")
    return labels


def _get_wagtail_documents_collection() -> WagtailCollection:
    collection = WagtailCollection.objects.filter(name=DOCUMENTS_COLLECTION_NAME).first()
    if collection is None:
        raise RuntimeError(
            f"Wagtail document Collection {DOCUMENTS_COLLECTION_NAME!r} not found. "
            "Create it in the Wagtail admin before running this script."
        )
    return collection


def _get_required_page_collections() -> dict[str, PageCollection]:
    needed_names = sorted(
        {
            name
            for names in CATEGORIE_TO_PAGE_COLLECTION_NAMES.values()
            for name in names
        }
    )
    found: dict[str, PageCollection] = {}
    missing: list[str] = []
    for name in needed_names:
        collection = PageCollection.objects.filter(name=name).first()
        if collection is None:
            missing.append(name)
        else:
            found[name] = collection
    if missing:
        raise RuntimeError(
            "Publication Collection(s) not found: "
            + ", ".join(repr(n) for n in missing)
            + ". Create them in the Wagtail admin before running this script."
        )
    return found


def _find_existing_document(
    stored_filename: str, collection: WagtailCollection, debug: bool = False
) -> Document | None:
    relative_path = f"documents/{stored_filename}"
    if debug:
        print(
            f"[DEBUG] Looking up existing document by file={relative_path!r} "
            f"collection={collection.name!r}"
        )
    existing = (
        Document.objects.filter(file=relative_path, collection=collection)
        .order_by("id")
        .first()
    )
    if debug:
        if existing is None:
            print(f"[DEBUG] No existing document found for file={relative_path!r}")
        else:
            print(
                f"[DEBUG] Reusing existing document id={existing.id} "
                f"file={relative_path!r}"
            )
    return existing


def _create_wagtail_document(
    file_path: Path, title: str, collection: WagtailCollection
) -> Document:
    with file_path.open("rb") as f:
        doc_file = File(f, name=file_path.name)
        doc = Document(
            title=title,
            file=doc_file,
            collection=collection,
        )
        doc.save()
    return doc


def _get_or_create_document(
    renamed_filename: str,
    documents_dir: Path,
    collection: WagtailCollection,
    force_file_uploads: bool = False,
    debug: bool = False,
) -> Document:
    if not force_file_uploads:
        existing = _find_existing_document(
            renamed_filename, collection, debug=debug
        )
        if existing is not None:
            return existing

    source_path = documents_dir / renamed_filename
    if not source_path.exists():
        raise ValueError(f"Document file not found in --documents-dir: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"Document path is not a file: {source_path}")

    return _create_wagtail_document(source_path, renamed_filename, collection)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--parent-id",
        type=int,
        required=True,
        help="ID of the PublicationIndexPage parent",
    )
    parser.add_argument(
        "--wagtail-project-root",
        type=str,
        default="",
        help="Root directory of the Wagtail/Django project (contains config/settings.py).",
    )
    parser.add_argument(
        "--scalingo-env-file",
        type=str,
        default="",
        help="Load environment values from this env file before Django setup.",
    )
    parser.add_argument(
        "--data-file",
        type=str,
        required=True,
        help="CSV of publication pages to create.",
    )
    parser.add_argument(
        "--documents-file",
        type=str,
        required=True,
        help="CSV with columns disaron:nom, renamed_filename.",
    )
    parser.add_argument(
        "--documents-dir",
        type=str,
        required=True,
        help="Directory containing files named as renamed_filename.",
    )
    parser.add_argument(
        "--labels-file",
        type=str,
        default=DEFAULT_LABELS_FILE,
        help=(
            "CSV mapping source_signature codes to subtitle labels "
            f"(default: {DEFAULT_LABELS_FILE})."
        ),
    )
    parser.add_argument(
        "--publish",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Publish pages immediately (default: true). Use --no-publish for drafts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print planned actions without creating pages or uploading docs.",
    )
    parser.add_argument(
        "--force-file-uploads",
        action="store_true",
        help=(
            "Always upload a new Wagtail document for each file, even if one "
            "already exists."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show debug logs for document lookup/reuse.",
    )
    parser.add_argument(
        "--no-confirmation",
        action="store_true",
        help="Skip confirmation prompt before creating pages.",
    )
    args = parser.parse_args()

    def _append_csv_row(path: Path, fieldnames: list[str], row: dict[str, str]) -> None:
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    try:
        data_file_rows, data_file_fieldnames, _data_file_path = _read_rows_from_data_file(
            args.data_file
        )
        documents_by_nom = _read_documents_by_disaron_nom(args.documents_file)
        labels = _load_source_signature_labels(args.labels_file)
    except ValueError as exc:
        parser.error(str(exc))

    documents_dir_path = Path(args.documents_dir)
    if not documents_dir_path.exists():
        parser.error(f"Documents directory not found: {documents_dir_path}")
    if not documents_dir_path.is_dir():
        parser.error(f"--documents-dir is not a directory: {documents_dir_path}")

    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, PublicationIndexPage):
        print(
            f"Error: Page id={args.parent_id} is not a PublicationIndexPage "
            f"(got {type(parent_page).__name__}).",
            file=sys.stderr,
        )
        return 1

    try:
        page_collections_by_name = _get_required_page_collections()
        documents_collection = _get_wagtail_documents_collection()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    pending_pages: list[tuple[int, dict[str, object], dict[str, str]]] = []
    failure_count = 0
    input_total_rows = len(data_file_rows)

    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    suffix = "_dry_run" if args.dry_run else ""
    output_path = output_dir / f"{timestamp}_create_publications{suffix}.csv"
    failures_path = (
        output_dir / f"{timestamp}_create_publications{suffix}_failures.csv"
    )
    other_columns = [
        c
        for c in data_file_fieldnames
        if c not in {"disaron:nom", "success", "error_message"}
    ]
    output_fieldnames = ["disaron:nom", "success", "error_message", *other_columns]
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
    with failures_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["disaron:nom", "success", "error_message"]
        )
        writer.writeheader()

    for idx, raw_row in enumerate(data_file_rows):
        line_no = idx + 2
        disaron_nom = (raw_row.get("disaron:nom") or "").strip()
        display_nom = disaron_nom or "<missing disaron:nom>"
        try:
            page_row = _build_page_row(raw_row, line_no)
            signature = str(page_row["source_signature"])
            if signature not in labels:
                raise ValueError(
                    f"[line {line_no}] Unknown disaron:source_signature "
                    f"{signature!r} (not found in labels file)."
                )
            page_row["subtitle"] = labels[signature]

            noms_fichiers = documents_by_nom.get(str(page_row["disaron_nom"]))
            if noms_fichiers is None:
                raise ValueError(
                    f"No matching disaron:nom found in --documents-file for "
                    f"{page_row['disaron_nom']!r}"
                )
            if not noms_fichiers:
                raise ValueError(
                    f"No documents listed for disaron:nom={page_row['disaron_nom']!r}"
                )
            for nom_fichier in noms_fichiers:
                source_path = documents_dir_path / nom_fichier
                if not source_path.exists():
                    raise ValueError(
                        f"Document file not found in --documents-dir: {source_path}"
                    )
                if not source_path.is_file():
                    raise ValueError(f"Document path is not a file: {source_path}")
            page_row["filenames"] = noms_fichiers
        except ValueError as exc:
            msg = str(exc)
            _append_csv_row(
                output_path,
                output_fieldnames,
                {**raw_row, "success": "0", "error_message": msg},
            )
            _append_csv_row(
                failures_path,
                ["disaron:nom", "success", "error_message"],
                {
                    "disaron:nom": disaron_nom,
                    "success": "0",
                    "error_message": msg,
                },
            )
            print(
                f"[{idx + 1}/{input_total_rows}] {display_nom} Error: {msg}",
                file=sys.stderr,
            )
            failure_count += 1
            continue
        pending_pages.append((idx, page_row, raw_row))

    if not pending_pages:
        print("No valid row to process in --data-file.")
        print(
            f"Wrote detailed output to {output_path} and failures to {failures_path}."
        )
        return 1 if failure_count else 0

    pages_total = len(pending_pages)
    if args.dry_run:
        print(
            f"[DRY-RUN] Would create {pages_total} PublicationPage(s) under "
            f"parent id={args.parent_id} "
            f"({'publish' if args.publish else 'draft'})."
        )
        print(
            f"[DRY-RUN] Document collection={DOCUMENTS_COLLECTION_NAME!r}; "
            f"labels loaded={len(labels)}; "
            f"validation failures so far={failure_count}."
        )
        for i, (_idx, row, raw_row) in enumerate(pending_pages, start=1):
            coll_names = CATEGORIE_TO_PAGE_COLLECTION_NAMES[str(row["categorie"])]
            filenames = row["filenames"]
            assert isinstance(filenames, list)
            print(
                f"[DRY-RUN] [{i}/{pages_total}] disaron:nom={row['disaron_nom']!r} "
                f"title={row['title']!r} categorie={row['categorie']!r} "
                f"collections={list(coll_names)} "
                f"subtitle={row['subtitle']!r} "
                f"docs={len(filenames)} {filenames}"
            )
            _append_csv_row(
                output_path,
                output_fieldnames,
                {**raw_row, "success": "1", "error_message": ""},
            )
        print(
            f"[DRY-RUN] Done: {pages_total} page(s) planned, "
            f"{failure_count} validation failure(s)."
        )
        print(
            f"Wrote detailed output to {output_path} and failures to {failures_path}."
        )
        return 1 if failure_count else 0

    if not args.no_confirmation:
        answer = input(
            f"About to create {pages_total} page(s) under parent id={args.parent_id}"
            f"{' and publish them' if args.publish else ' as drafts'}. "
            "Type 'yes' to confirm: "
        ).strip()
        if answer.lower() != "yes":
            print("Creation cancelled.")
            return 0

    created_or_published = 0

    for i, (_output_idx, row, raw_row) in enumerate(pending_pages, start=1):
        title = str(row["title"])
        disaron_nom = str(row["disaron_nom"])
        display_nom = disaron_nom
        categorie = str(row["categorie"])
        subtitle = str(row["subtitle"])
        publication_date = row["publication_date"]
        filenames = row["filenames"]
        assert isinstance(filenames, list)

        try:
            if PublicationPage.objects.filter(disaron_id=disaron_nom).exists():
                raise ValueError(
                    f"A PublicationPage with disaron_id={disaron_nom!r} already exists."
                )

            documents: list[Document] = []
            for nom_fichier in filenames:
                document = _get_or_create_document(
                    str(nom_fichier),
                    documents_dir_path,
                    documents_collection,
                    force_file_uploads=args.force_file_uploads,
                    debug=args.debug,
                )
                documents.append(document)

            body = [
                (
                    "standard_publication",
                    {
                        "subtitle": subtitle,
                        "downloadable_documents": [
                            {"download_type": "data", "document": doc}
                            for doc in documents
                        ],
                    },
                )
            ]

            slug = _generate_unique_slug(parent_page, title)
            page = PublicationPage(
                title=title,
                slug=slug,
                disaron_id=disaron_nom,
                date=publication_date,
                body=body,
                show_in_menus=True,
                live=False,
            )
            parent_page.add_child(instance=page)

            collection_objs = [
                page_collections_by_name[name]
                for name in CATEGORIE_TO_PAGE_COLLECTION_NAMES[categorie]
            ]
            page.collections.add(*collection_objs)

            if args.publish:
                page.save_revision().publish()
                print(
                    f"[{i}/{pages_total}] {display_nom} Published PublicationPage "
                    f"id={page.id} - {page.url}"
                )
            else:
                if page.live:
                    page.unpublish()
                page.save_revision()
                print(
                    f"[{i}/{pages_total}] {display_nom} Created draft PublicationPage "
                    f"id={page.id} (slug={slug})."
                )

            page.refresh_from_db(fields=["live"])
            expected_live = bool(args.publish)
            if page.live != expected_live:
                raise RuntimeError(
                    f"Unexpected live state for page id={page.id}: "
                    f"expected live={expected_live}, got live={page.live}."
                )
        except Exception as exc:
            message = (
                f"{type(exc).__name__}: {exc}. "
                f"Context: disaron:nom={disaron_nom!r}, title={title!r}"
            )
            print(
                f"[{i}/{pages_total}] {display_nom} Error: {message}",
                file=sys.stderr,
            )
            _append_csv_row(
                output_path,
                output_fieldnames,
                {**raw_row, "success": "0", "error_message": message},
            )
            _append_csv_row(
                failures_path,
                ["disaron:nom", "success", "error_message"],
                {
                    "disaron:nom": disaron_nom,
                    "success": "0",
                    "error_message": message,
                },
            )
            failure_count += 1
            continue

        created_or_published += 1
        _append_csv_row(
            output_path,
            output_fieldnames,
            {**raw_row, "success": "1", "error_message": ""},
        )

    print(
        f"Wrote detailed output to {output_path} and failures to {failures_path}."
    )
    print(
        f"Done: {created_or_published}/{pages_total} page(s) created or published, "
        f"{failure_count} failure(s)."
    )
    return 1 if failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
