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
    urls_path = project_root / "files_to_download.csv"
    results_path = project_root / "download_results.csv"

    urls: list[str] = []

    if urls_path.exists():
        # Handle both CSV with header (url_fichier column) and plain text
        with urls_path.open(encoding="utf-8", newline="") as f:
            # Peek first line to decide
            first = f.readline()
            rest = f.read()
            content = first + rest

        # Try CSV with header
        from io import StringIO

        s = StringIO(content)
        reader = csv.DictReader(s)
        if reader.fieldnames and "url_fichier" in reader.fieldnames:
            for row in reader:
                url = (row.get("url_fichier") or "").strip()
                if url:
                    urls.append(url)
        else:
            # Fallback: one URL per non-empty line
            for line in content.splitlines():
                line = line.strip()
                if line:
                    urls.append(line)

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
