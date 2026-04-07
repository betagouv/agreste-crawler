# my-downloader

`my-downloader` is a small batch file downloader used by this repo.

It reads a CSV of download targets, downloads each URL to `my-downloader/downloads/`,
and writes timestamped run reports to `my-downloader/results/`.

## What it does

- Input CSV: `files_to_download.csv` at the repository root.
- Required columns: `disaron_nom`, `nom_fichier`, `url_fichier`.
- For each row, it downloads `url_fichier`.
- Downloads are saved as raw bytes using the filename from the URL path.
- Retries failed requests (`max_request_retries=2`).
- Output reports:
  - `my-downloader/results/results_<timestamp>.csv` (all rows with `success=1/0`)
  - `my-downloader/results/failures_<timestamp>.csv` (failed rows only)

## Setup

Install dependencies from the repository root:

```sh
uv sync
```

## Usage

Run from the `my-downloader` directory:

```sh
cd my-downloader
uv run python -m my_downloader
```

### Example with an existing generated file list

If your file list is named differently (for example `files_to_download_20260317_123722.csv`),
copy it to the expected input filename and run:

```sh
cp ../files_to_download_20260317_123722.csv ../files_to_download.csv
cd my-downloader
uv run python -m my_downloader
```

### Example output files

After a run you will typically see files like:

- `my-downloader/results/results_20260317_123722.csv`
- `my-downloader/results/failures_20260317_123722.csv`

