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
from pathlib import Path

from django_env_setup import setup_django

setup_django(__file__)

from django.utils import timezone  # noqa: E402
from django.utils.text import slugify  # noqa: E402
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
                        "complement_titre": (row.get("disaron:Complement_titre") or "").strip(),
                        "chapeau": (row.get("disaron:chapeau") or "").strip(),
                    }
                )

    if not rows_out:
        raise ValueError("CSV does not contain any non-empty value in 'dc:title'.")
    return rows_out


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
        rows = [{"title": title, "complement_titre": "", "chapeau": ""}]

    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        print(
            f"Error: Page id={args.parent_id} is not a BlogIndexPage (got {type(parent_page).__name__}).",
            file=sys.stderr,
        )
        return 1

    for i, row in enumerate(rows, start=1):
        title = row["title"]
        complement_titre = row["complement_titre"]
        chapeau = row["chapeau"]
        slug = (args.slug or "").strip() or _generate_unique_slug(parent_page, title)
        if BlogEntryPage.objects.child_of(parent_page).filter(slug=slug).exists():
            print(
                f"Error: A blog entry with slug '{slug}' already exists under this index.",
                file=sys.stderr,
            )
            return 1

        body: list[tuple[str, str]] = []
        if complement_titre:
            body.append(("paragraph", complement_titre))
        if chapeau:
            body.append(("paragraph", chapeau))
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
