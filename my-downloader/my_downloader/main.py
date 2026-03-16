from pathlib import Path
import csv
import json

from crawlee.crawlers import BeautifulSoupCrawler
from crawlee.http_clients import ImpitHttpClient

from .routes import router


async def main() -> None:
    """Entry point for the downloader.

    Reads URLs from files_to_download_clean.csv (column 'urls des fichiers')
    and downloads each of them using the BeautifulSoup crawler and the
    simple download handler in routes.py.
    """
    # main.py is under .../agreste-crawler/my-downloader/my_downloader/
    # Project root (agreste-crawler) is two levels up from the package dir.
    project_root = Path(__file__).resolve().parents[2]
    csv_path = project_root / "files_to_download_clean.csv"

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

    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=len(urls) if urls else 0,
        http_client=ImpitHttpClient(),
    )

    if urls:
        await crawler.run(urls)
