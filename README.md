# Agreste Crawler Utilities

This repository contains several Python utilities used to crawl DISARON pages, download files, and create Wagtail pages from CSV data.

## Quick Start

From the repository root:

```bash
uv sync
```

## Main Commands

### Just Recipes

You can run the main scripts via `just` with snake_case recipe names:

```bash
just clear_blog_entries --wagtail-project-root ../agreste --parent-id 30 --dry-run
just create_blog_entry --wagtail-project-root ../agreste --parent-id 30 --title "My post"
just author_lister --input-csv infos-rapides.csv --output-csv authors.csv
just update_blog_entry_dates --wagtail-project-root ../agreste
```

### My Downloader

Run the downloader module:

```bash
uv run -m my_downloader
```

Alternative (from its folder):

```bash
cd "my-downloader" && uv run -m my_downloader
```

### Page Creator

Create one page from a title:

```bash
just create_blog_entry --wagtail-project-root ../agreste --parent-id 30 --title "My post"
```

Create one page per CSV row:

```bash
just create_blog_entry --wagtail-project-root ../agreste --parent-id 30 --data-file page_creator/data/infos-rapides.csv
```

Create pages and upload associated documents from a second CSV + local files:

```bash
just create_blog_entry \
  --wagtail-project-root ../agreste \
  --parent-id 30 \
  --data-file page_creator/data/infos-rapides.csv \
  --documents-file files_to_download_20260317_123722.csv \
  --documents-dir my-downloader/downloads
```

Use `.env.scalingo` environment values:

```bash
just create_blog_entry --wagtail-project-root ../agreste --scalingo-env-file /path/to/.env.scalingo --parent-id 30 --title "My post"
```

### Clear Blog Entries

Delete direct children under a parent page:

```bash
just clear_blog_entries --wagtail-project-root ../agreste --parent-id 30
```

Dry run:

```bash
just clear_blog_entries --wagtail-project-root ../agreste --parent-id 30 --dry-run
```

Skip confirmation:

```bash
just clear_blog_entries --wagtail-project-root ../agreste --parent-id 30 --no-confirmation
```

Use `.env.scalingo`:

```bash
just clear_blog_entries --wagtail-project-root ../agreste --scalingo-env-file /path/to/.env.scalingo --parent-id 30 --dry-run
```

### Author Lister

Build an author index from DISARON data:

```bash
just author_lister --input-csv infos-rapides.csv --output-csv authors.csv
```

### Update Blog Entry Dates

Set all `BlogEntryPage.date` values to now:

```bash
just update_blog_entry_dates --wagtail-project-root ../agreste
```

## Notes

- `create_blog_entry.py` validates draft/publish state and raises an error if the resulting `live` state is not what was requested.
- `clear_blog_entries.py` runs `Page.fix_tree(destructive=False)` to repair Wagtail tree metadata after operations.
- `--wagtail-project-root` is required for page-creator scripts and must point to the Django project root (`config/settings.py`).
- Per-project READMEs are available in `my-downloader/`, `my-data-finder/`, and `my-crawler/`.
