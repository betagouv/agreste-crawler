#!/usr/bin/env python
"""
Create a BlogEntryPage as a child of a BlogIndexPage.

Usage:
    uv run python page-creator/create_blog_entry.py --parent-id 19 --title "My post" --slug "my-post"
    uv run python page-creator/create_blog_entry.py --parent-id 19 --title "My post" --slug "my-post" --publish
"""

import argparse
import csv
import sys
from html import escape
from pathlib import Path

from django_env_setup import setup_django

setup_django(__file__)

from django.core.files import File  # noqa: E402
from django.utils import timezone  # noqa: E402
from django.utils.text import slugify  # noqa: E402
from wagtail.documents import get_document_model  # noqa: E402
from wagtail.models import Collection  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402


def _generate_unique_slug(parent_page: BlogIndexPage, title: str) -> str:
    base_slug = slugify(title) or "blog-entry"
    slug = base_slug
    suffix = 2
    while BlogEntryPage.objects.child_of(parent_page).filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _read_rows_from_data_file(data_file: str) -> list[dict[str, str]]:
    csv_path = Path(data_file)
    if not csv_path.exists():
        raise ValueError(f"Data file not found: {csv_path}")

    rows_out: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "dc:title" not in reader.fieldnames:
            raise ValueError("CSV must contain a 'dc:title' column.")
        if "disaron:nom" not in reader.fieldnames:
            raise ValueError("CSV must contain a 'disaron:nom' column.")
        if "disaron:Complement_titre" not in reader.fieldnames:
            raise ValueError("CSV must contain a 'disaron:Complement_titre' column.")
        if "disaron:chapeau" not in reader.fieldnames:
            raise ValueError("CSV must contain a 'disaron:chapeau' column.")
        for row in reader:
            title = (row.get("dc:title") or "").strip()
            if title:
                rows_out.append(
                    {
                        "title": title,
                        "disaron_nom": (row.get("disaron:nom") or "").strip(),
                        "complement_titre": (row.get("disaron:Complement_titre") or "").strip(),
                        "chapeau": (row.get("disaron:chapeau") or "").strip(),
                    }
                )

    if not rows_out:
        raise ValueError("CSV does not contain any non-empty value in 'dc:title'.")
    return rows_out


def _read_documents_by_disaron_nom(documents_file: str) -> dict[str, list[str]]:
    csv_path = Path(documents_file)
    if not csv_path.exists():
        raise ValueError(f"Documents file not found: {csv_path}")

    mapping: dict[str, list[str]] = {}
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("Documents CSV must contain headers.")
        has_disaron_nom = "disaron_nom" in reader.fieldnames
        has_disaron_colon_nom = "disaron:nom" in reader.fieldnames
        if not has_disaron_nom and not has_disaron_colon_nom:
            raise ValueError("Documents CSV must contain 'disaron_nom' or 'disaron:nom' column.")
        if "nom_fichier" not in reader.fieldnames:
            raise ValueError("Documents CSV must contain a 'nom_fichier' column.")

        for row in reader:
            disaron_nom = (
                (row.get("disaron_nom") or row.get("disaron:nom") or "").strip()
            )
            nom_fichier = (row.get("nom_fichier") or "").strip()
            if not disaron_nom or not nom_fichier:
                continue
            mapping.setdefault(disaron_nom, []).append(nom_fichier)

    return mapping


def _tile_title_for_filename(filename: str) -> str:
    if Path(filename).suffix.lower() == ".pdf":
        return "Télécharger la publication"
    return "Télécharger les données"


def _prefixed_document_filename(disaron_nom: str, nom_fichier: str) -> str:
    return f"{disaron_nom}_{nom_fichier}"


def _get_or_create_document(disaron_nom: str, nom_fichier: str, documents_dir: Path):
    stored_filename = _prefixed_document_filename(disaron_nom, nom_fichier)
    source_path = documents_dir / stored_filename
    if not source_path.exists():
        raise ValueError(f"Document file not found in --documents-dir: {source_path}")
    if not source_path.is_file():
        raise ValueError(f"Document path is not a file: {source_path}")

    document_model = get_document_model()
    existing_document = document_model.objects.filter(title=stored_filename).first()
    if existing_document:
        return existing_document

    root_collection = Collection.get_first_root_node()
    with source_path.open("rb") as f:
        document = document_model(title=stored_filename, collection=root_collection)
        document.file.save(stored_filename, File(f), save=True)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--parent-id", type=int, required=True, help="ID of the BlogIndexPage parent")
    parser.add_argument("--title", type=str, default="", help="Page title")
    parser.add_argument(
        "--slug",
        type=str,
        default="",
        help="URL slug (unique under the blog index). If omitted, generated from title.",
    )
    parser.add_argument(
        "--data-file",
        type=str,
        default="",
        help="CSV file path. Uses the first non-empty value from 'dc:title' as page title.",
    )
    parser.add_argument(
        "--documents-file",
        type=str,
        default="",
        help="CSV file path with document rows (must include disaron_nom/disaron:nom and nom_fichier).",
    )
    parser.add_argument(
        "--documents-dir",
        type=str,
        default="",
        help="Directory where files named by nom_fichier are read and uploaded to Wagtail Documents.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish immediately (otherwise the page stays in draft)",
    )
    args = parser.parse_args()

    if args.data_file:
        if args.title or args.slug:
            parser.error("--data-file cannot be used with --title or --slug.")
        try:
            rows = _read_rows_from_data_file(args.data_file)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        title = (args.title or "").strip()
        if not title:
            parser.error("--title is required unless --data-file is specified.")
        rows = [{"title": title, "disaron_nom": "", "complement_titre": "", "chapeau": ""}]

    documents_by_nom: dict[str, list[str]] = {}
    documents_dir_path: Path | None = None
    if args.documents_file:
        try:
            documents_by_nom = _read_documents_by_disaron_nom(args.documents_file)
        except ValueError as exc:
            parser.error(str(exc))
        if not args.documents_dir:
            parser.error("--documents-dir is required when --documents-file is specified.")
        documents_dir_path = Path(args.documents_dir)
        if not documents_dir_path.exists():
            parser.error(f"Documents directory not found: {documents_dir_path}")
        if not documents_dir_path.is_dir():
            parser.error(f"--documents-dir is not a directory: {documents_dir_path}")

    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        print(
            f"Error: Page id={args.parent_id} is not a BlogIndexPage (got {type(parent_page).__name__}).",
            file=sys.stderr,
        )
        return 1

    for i, row in enumerate(rows, start=1):
        title = row["title"]
        disaron_nom = row["disaron_nom"]
        complement_titre = row["complement_titre"]
        chapeau = row["chapeau"]
        noms_fichiers = documents_by_nom.get(disaron_nom, [])
        right_column_content: list[tuple[str, dict[str, object]]] = []
        for nom_fichier in noms_fichiers:
            tile_link: dict[str, object] = {
                "link_type": "document",
                "external_url": "",
                "page": None,
                "document": None,
                "anchor": "",
            }
            if documents_dir_path is not None:
                try:
                    document = _get_or_create_document(disaron_nom, nom_fichier, documents_dir_path)
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    return 1
                tile_link["document"] = document
            right_column_content.append(
                (
                    "tile",
                    {
                        "title": _tile_title_for_filename(nom_fichier),
                        "heading_tag": "h3",
                        "description": f"<p>{escape(nom_fichier)}</p>",
                        "link": tile_link,
                        "top_detail_badges_tags": [],
                    },
                )
            )
        slug = (args.slug or "").strip() or _generate_unique_slug(parent_page, title)
        if BlogEntryPage.objects.child_of(parent_page).filter(slug=slug).exists():
            print(
                f"Error: A blog entry with slug '{slug}' already exists under this index.",
                file=sys.stderr,
            )
            return 1

        left_column_content: list[tuple[str, str]] = []
        if complement_titre:
            left_column_content.append(("text", f"<h4>{escape(complement_titre)}</h4>"))
        if chapeau:
            left_column_content.append(("text", escape(chapeau)))

        body = [
            (
                "multicolumns",
                {
                    "bg_image": None,
                    "bg_color_class": "",
                    "title": "",
                    "heading_tag": "h2",
                    "top_margin": 5,
                    "bottom_margin": 5,
                    "vertical_align": "",
                    "columns": [
                        (
                            "column",
                            {
                                "width": "8",
                                "content": left_column_content,
                            },
                        ),
                        (
                            "column",
                            {
                                "width": "4",
                                "content": right_column_content,
                            },
                        ),
                    ],
                },
            )
        ]
        if disaron_nom:
            body.append(("paragraph", escape(disaron_nom)))
        page = BlogEntryPage(
            title=title,
            slug=slug,
            body=body,
            date=timezone.now(),
            show_in_menus=True,
        )
        parent_page.add_child(instance=page)

        if args.publish:
            page.save_revision().publish()
            print(f"[{i}/{len(rows)}] Published BlogEntryPage id={page.id} - {page.url}")
        else:
            print(
                f"[{i}/{len(rows)}] Created draft BlogEntryPage id={page.id} (slug={slug}). "
                "Publish it from the Wagtail admin."
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
