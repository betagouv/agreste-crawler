from crawlee.crawlers import BeautifulSoupCrawler
from crawlee.http_clients import ImpitHttpClient
from .routes import router


async def main() -> None:
    """The crawler entry point."""
    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=10,
        http_client=ImpitHttpClient(),
    )

    await crawler.run(
        [
            "https://agreste.agriculture.gouv.fr/agreste-web/download/publication/publie/IraAbo011/2019_011_InforapBovins.pdf",
            "https://agreste.agriculture.gouv.fr/agreste-web/download/publication/publie/IraAbo012/2019_012_InforapPorcins.pdf",
        ]
    )

