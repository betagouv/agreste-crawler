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
            'https://crawlee.dev',
        ]
    )
