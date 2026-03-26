#!/usr/bin/env python
"""
Create a BlogEntryPage as a child of a BlogIndexPage.

Usage:
    uv run python page-creator/create_blog_entry.py --parent-id 19 --title "My post" --slug "my-post"
    uv run python page-creator/create_blog_entry.py --parent-id 19 --title "My post" --slug "my-post" --publish
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

# Add sibling sites-faciles project root on PYTHONPATH.
_SITES_FACILES_ROOT = Path(__file__).resolve().parents[2] / "sites-faciles"
if str(_SITES_FACILES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SITES_FACILES_ROOT))

# If Django is missing in the current environment, re-run with sites-faciles venv.
if importlib.util.find_spec("django") is None:
    _SITES_FACILES_PYTHON = _SITES_FACILES_ROOT / ".venv" / "bin" / "python"
    current_python = Path(sys.executable)
    if _SITES_FACILES_PYTHON.exists() and current_python != _SITES_FACILES_PYTHON:
        os.execv(str(_SITES_FACILES_PYTHON), [str(_SITES_FACILES_PYTHON), __file__, *sys.argv[1:]])
    raise ModuleNotFoundError(
        "Django is not available. Install dependencies in agreste-crawler "
        "or create ../sites-faciles/.venv."
    )

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone  # noqa: E402
from wagtail.models import Page  # noqa: E402

from blog.models import BlogEntryPage, BlogIndexPage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--parent-id", type=int, required=True, help="ID of the BlogIndexPage parent")
    parser.add_argument("--title", type=str, required=True, help="Page title")
    parser.add_argument("--slug", type=str, required=True, help="URL slug (unique under the blog index)")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish immediately (otherwise the page stays in draft)",
    )
    args = parser.parse_args()

    parent_page = Page.objects.get(id=args.parent_id).specific
    if not isinstance(parent_page, BlogIndexPage):
        print(
            f"Error: Page id={args.parent_id} is not a BlogIndexPage (got {type(parent_page).__name__}).",
            file=sys.stderr,
        )
        return 1

    if BlogEntryPage.objects.child_of(parent_page).filter(slug=args.slug).exists():
        print(f"Error: A blog entry with slug '{args.slug}' already exists under this index.", file=sys.stderr)
        return 1

    page = BlogEntryPage(
        title=args.title,
        slug=args.slug,
        date=timezone.now(),
        show_in_menus=True,
    )
    parent_page.add_child(instance=page)

    if args.publish:
        page.save_revision().publish()
        print(f"Published BlogEntryPage id={page.id} - {page.url}")
    else:
        print(f"Created draft BlogEntryPage id={page.id} (slug={args.slug}). Publish it from the Wagtail admin.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
