# my-data-finder

Scrapes metadata from Agreste detail pages for a list of `disaron:nom` IDs.

Reads IDs from `2026-03-32_ids_without_files.csv`, visits each page at
`https://agreste.agriculture.gouv.fr/agreste-web/disaron/<id>/detail/`,
and writes extracted metadata to a timestamped CSV in `output/`.

## Setup

Install [uv](https://docs.astral.sh/uv/) if not already available:

```sh
pipx install uv
```

Install dependencies:

```sh
uv sync
```

Install Playwright browsers (first time only):

```sh
uv run playwright install
```

## Usage

Run with all fields:

```sh
uv run python -m my_data_finder
```

Extract only specific fields:

```sh
uv run python -m my_data_finder --fields "dc:title,disaron:Date_premiere_publication,collection,sous-collection"
```

Run without concurrency (one page at a time, gentler on the server):

```sh
uv run python -m my_data_finder --no-concurrency
```

## Available fields

| Field | Description |
|---|---|
| `dc:title` | Document title |
| `disaron:Complement_titre` | Title complement |
| `disaron:chapeau` | Summary/chapeau text |
| `disaron:Auteur` | Authors (JSON list) |
| `disaron:Date_premiere_publication` | First publication date |
| `disaron:Numerotation` | Issue number |
| `themes` | Themes (JSON list) |
| `disaron:annees_reference` | Reference years (JSON list) |
| `disaron:niveau_geographique` | Geographic level (JSON list) |
| `collection` | Collection name |
| `sous-collection` | Sub-collection name (optional) |
| `categorie` | Category |
| `nb de fichiers` | Number of downloadable files |
| `noms des fichiers` | File names (JSON list) |
| `urls des fichiers` | File URLs (JSON list) |

## Output

Results are written to `output/<timestamp>_output.csv`.
Failed requests (after retries) are logged to `output/<timestamp>_errors.csv`.
Raw HTML snapshots for debugging are saved to `output/debug_html_<timestamp>/`.
