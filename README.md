# Agreste Crawler Utilities

This repository contains several Python utilities used to crawl DISARON pages, download files, and create Wagtail pages from CSV data.

## Quick Start

From the repository root:

```bash
uv sync
```

## Main Commands

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
uv run python page-creator/create_blog_entry.py --parent-id 30 --title "My post"
```

Create one page per CSV row:

```bash
uv run python page-creator/create_blog_entry.py --parent-id 30 --data-file page-creator/data/infos-rapides.csv
```

Create pages and wire document tiles from a second CSV + local files:

```bash
uv run python page-creator/create_blog_entry.py \
  --parent-id 30 \
  --data-file page-creator/data/infos-rapides.csv \
  --documents-file files_to_download_20260317_123722.csv \
  --documents-dir my-downloader/downloads
```

Use `.env.scalingo` environment values:

```bash
uv run python page-creator/create_blog_entry.py --scalingo-env-file /path/to/.env.scalingo --parent-id 30 --title "My post"
```

### Clear Blog Entries

Delete direct children under a parent page:

```bash
uv run python page-creator/clear_blog_entries.py --parent-id 30
```

Dry run:

```bash
uv run python page-creator/clear_blog_entries.py --parent-id 30 --dry-run
```

Skip confirmation:

```bash
uv run python page-creator/clear_blog_entries.py --parent-id 30 --no-confirmation
```

Use `.env.scalingo`:

```bash
uv run python page-creator/clear_blog_entries.py --scalingo-env-file /path/to/.env.scalingo --parent-id 30 --dry-run
```

### Author Lister

Build an author index from DISARON data:

```bash
uv run python author-lister.py --input-csv infos-rapides.csv --output-csv authors.csv
```

## Notes

- `create_blog_entry.py` validates draft/publish state and raises an error if the resulting `live` state is not what was requested.
- `clear_blog_entries.py` runs `Page.fix_tree(destructive=False)` to repair Wagtail tree metadata after operations.
- Per-project READMEs are available in `my-downloader/`, `my-data-finder/`, and `my-crawler/`.
