from pathlib import Path
import csv
from datetime import datetime

from crawlee.crawlers import BeautifulSoupCrawler, BasicCrawlingContext
from crawlee.http_clients import ImpitHttpClient

from .routes import router


async def main() -> None:
    """Entry point for the downloader.

    Reads URLs from files_to_download.csv (columns 'disaron_nom', 'nom_fichier',
    'url_fichier'), downloads each of them using the BeautifulSoup crawler and
    the simple download handler in routes.py, and logs final success/failure
    per URL to results_<timestamp>.csv.
    """
    # main.py is under .../agreste-crawler/my-downloader/my_downloader/
    # Project root (agreste-crawler) is two levels up from the package dir.
    project_root = Path(__file__).resolve().parents[2]
    urls_path = project_root / "files_to_download.csv"

    entries: list[dict[str, str]] = []

    if urls_path.exists():
        with urls_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
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

    urls = [e["url_fichier"] for e in entries]

    # Prepare timestamped results CSV for this run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = project_root / f"results_{timestamp}.csv"
    with results_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["disaron_nom", "nom_fichier", "success", "url_fichier"],
        )
        writer.writeheader()

    # Track failed URLs (after all retries)
    failed_urls: set[str] = set()

    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=len(urls) if urls else 0,
        http_client=ImpitHttpClient(),
        max_request_retries=2,
    )

    @crawler.failed_request_handler
    async def failed_handler(ctx: BasicCrawlingContext, error: Exception) -> None:
        """Called after all retries are exhausted for a request."""
        crawler.log.error(f"Failed after retries: {ctx.request.url} ({error})")
        failed_urls.add(ctx.request.url)

    if urls:
        await crawler.run(urls)

    # After the crawl, write per-URL success/failure rows
    if entries:
        with results_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["disaron_nom", "nom_fichier", "success", "url_fichier"],
            )
            for entry in entries:
                url = entry["url_fichier"]
                success = 0 if url in failed_urls else 1
                writer.writerow(
                    {
                        "disaron_nom": entry["disaron_nom"],
                        "nom_fichier": entry["nom_fichier"],
                        "success": success,
                        "url_fichier": url,
                    }
                )
