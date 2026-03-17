from pathlib import Path
import csv
import json

from crawlee.crawlers import BeautifulSoupCrawler, BasicCrawlingContext
from crawlee.http_clients import ImpitHttpClient

from .routes import router


async def main() -> None:
    """Entry point for the downloader.

    Reads URLs from files_to_download_clean.csv (column 'urls des fichiers'),
    downloads each of them using the BeautifulSoup crawler and the
    simple download handler in routes.py, and logs final success/failure
    per URL to download_results.csv.
    """
    # main.py is under .../agreste-crawler/my-downloader/my_downloader/
    # Project root (agreste-crawler) is two levels up from the package dir.
    project_root = Path(__file__).resolve().parents[2]
    csv_path = project_root / "files_to_download_clean.csv"
    results_path = project_root / "download_results.csv"

    urls: list[str] = []

    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw = (row.get("urls des fichiers") or "").strip()
                if not raw:
                    continue
                try:
                    row_urls = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row_urls, list):
                    urls.extend(str(u) for u in row_urls if u)
                elif isinstance(row_urls, str):
                    urls.append(row_urls)

    # Prepare results CSV (overwrite per run)
    with results_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "success"])
        writer.writeheader()

    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=len(urls) if urls else 0,
        http_client=ImpitHttpClient(),
        max_request_retries=2,
    )

    @crawler.failed_request_handler
    async def failed_handler(ctx: BasicCrawlingContext, error: Exception) -> None:
        # Called after all retries are exhausted
        crawler.log.error(f"Failed after retries: {ctx.request.url} ({error})")
        with results_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["url", "success"])
            writer.writerow({"url": ctx.request.url, "success": 0})

    if urls:
        # For successful requests, log success here after crawler finishes each request.
        # Crawlee Python does not expose a per-success hook directly on the crawler,
        # but we can log successes in the default handler (routes.py) if needed.
        await crawler.run(urls)
