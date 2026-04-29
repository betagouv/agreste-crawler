from pathlib import Path
import csv
from datetime import datetime
import json
import ast

from crawlee.crawlers import BeautifulSoupCrawler, BasicCrawlingContext
from crawlee.http_clients import ImpitHttpClient
from crawlee._types import ConcurrencySettings

from .routes import append_failure_row_for_url, configure_run_output, router


def _parse_list_cell(raw: str) -> list[str]:
    value = (raw or "").strip()
    if not value:
        return []

    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass

    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass

    return [value]


def _read_entries(urls_path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    with urls_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])

        # Format A: one file per line
        if {"disaron_nom", "nom_fichier", "url_fichier"}.issubset(fieldnames):
            for row in reader:
                disaron_nom = (row.get("disaron_nom") or "").strip()
                nom_fichier = (row.get("nom_fichier") or "").strip()
                url = (row.get("url_fichier") or "").strip()
                if not disaron_nom or not nom_fichier or not url:
                    continue
                entries.append(
                    {
                        "disaron_nom": disaron_nom,
                        "nom_fichier": nom_fichier,
                        "url_fichier": url,
                    }
                )
            return entries

        # Format B: one disaron_nom per line
        if {
            "disaron:nom",
            "nb de fichiers",
            "noms des fichiers",
            "urls des fichiers",
        }.issubset(fieldnames):
            for row in reader:
                disaron_nom = (row.get("disaron:nom") or "").strip()
                if not disaron_nom:
                    continue
                names = _parse_list_cell(row.get("noms des fichiers") or "")
                urls = _parse_list_cell(row.get("urls des fichiers") or "")
                for nom_fichier, url in zip(names, urls, strict=False):
                    nom_fichier = nom_fichier.strip()
                    url = url.strip()
                    if not nom_fichier or not url:
                        continue
                    entries.append(
                        {
                            "disaron_nom": disaron_nom,
                            "nom_fichier": nom_fichier,
                            "url_fichier": url,
                        }
                    )
            return entries

    return entries


async def main() -> None:
    """Entry point for the downloader.

    Reads URLs from my-downloader/files_to_download.csv.

    Supported input formats:
    - One file per line: columns 'disaron_nom', 'nom_fichier', 'url_fichier'
    - One disaron per line: columns 'disaron:nom', 'nb de fichiers',
      'noms des fichiers', 'urls des fichiers'

    Downloads each file URL using the BeautifulSoup crawler and the simple
    download handler in routes.py, then logs final success/failure per URL to
    results_<timestamp>.csv.
    """
    # main.py is under .../agreste-crawler/my-downloader/my_downloader/
    # Downloader root is one level up from the package dir.
    downloader_root = Path(__file__).resolve().parents[1]
    urls_path = downloader_root / "files_to_download.csv"

    entries: list[dict[str, str]] = []

    if urls_path.exists():
        entries = _read_entries(urls_path)

    urls = [e["url_fichier"] for e in entries]

    # Prepare timestamped results CSVs for this run
    # (in my-downloader/results directory).
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(__file__).resolve().parents[1] / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / f"results_{timestamp}.csv"
    failures_path = results_dir / f"failures_{timestamp}.csv"
    with results_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "disaron_nom",
                "nom_fichier",
                "success",
                "url_fichier",
                "url",
            ],
        )
        writer.writeheader()
    with failures_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "disaron_nom",
                "nom_fichier",
                "success",
                "url_fichier",
                "url",
            ],
        )
        writer.writeheader()

    # Configure streaming output writes during crawl.
    configure_run_output(
        results_path=results_path,
        failures_path=failures_path,
        entries=entries,
    )

    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=len(urls) if urls else 0,
        http_client=ImpitHttpClient(),
        max_request_retries=2,
        concurrency_settings=ConcurrencySettings(
            min_concurrency=1,
            max_concurrency=1,
            desired_concurrency=1,
        ),
    )

    @crawler.failed_request_handler
    async def failed_handler(
        ctx: BasicCrawlingContext, error: Exception
    ) -> None:
        """Called after all retries are exhausted for a request."""
        crawler.log.error(f"Failed after retries: {ctx.request.url} ({error})")
        append_failure_row_for_url(ctx.request.url)

    if urls:
        await crawler.run(urls)

