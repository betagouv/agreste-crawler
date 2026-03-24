import csv
from pathlib import Path

from crawlee.crawlers import BeautifulSoupCrawler
from crawlee.http_clients import ImpitHttpClient

from .routes import router


async def main() -> None:
    """The crawler entry point.

    Reads disaron:nom IDs from 2026-03-32_ids_without_files.csv and visits
    one detail page per ID. Does not follow any additional links.
    """
    ids_path = Path(__file__).resolve().parents[1] / "2026-03-32_ids_without_files.csv"

    urls: list[str] = []
    with ids_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nom = (row.get("disaron:nom") or "").strip()
            if nom:
                urls.append(
                    f"https://agreste.agriculture.gouv.fr/agreste-web/disaron/{nom}/detail/"
                )

    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=len(urls) if urls else 0,
        http_client=ImpitHttpClient(),
    )

    if urls:
        await crawler.run(urls)
