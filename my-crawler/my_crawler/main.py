from crawlee.crawlers import BeautifulSoupCrawler
from crawlee.http_clients import ImpitHttpClient
from .routes import router

async def main() -> None:
    """The crawler entry point."""
    crawler = BeautifulSoupCrawler(
        request_handler=router,
        max_requests_per_crawl=100,
        http_client=ImpitHttpClient(),
    )


    await crawler.run(
        [
            #'https://agreste.agriculture.gouv.fr/agreste-web/disaron/IraLai2627/detail/',
            #'https://agreste.agriculture.gouv.fr/agreste-web/disaron/!searchurl/3ff250f3-bff8-4e29-992e-196fb31f3c78/search/'
            'https://agreste.agriculture.gouv.fr/agreste-web/disaron/IraAbo2621/detail/',
        ]
    )
