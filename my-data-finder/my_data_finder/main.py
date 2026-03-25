import argparse
import csv
import re
from pathlib import Path

from crawlee.crawlers import BasicCrawlingContext, PlaywrightCrawler

from .routes import append_error_row, append_failed_row, configure_fields, router


async def main() -> None:
    """The crawler entry point.

    Reads disaron:nom IDs from 2026-03-32_ids_without_files.csv and visits
    one detail page per ID. Does not follow any additional links.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fields",
        default="",
        help="Comma-separated metadata fields to extract/validate. Empty means all.",
    )
    args = parser.parse_args()
    requested_fields = [f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None
    configure_fields(requested_fields)

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

    crawler = PlaywrightCrawler(
        request_handler=router,
        max_requests_per_crawl=len(urls) if urls else 0,
        max_request_retries=3,
        # The target site currently presents an invalid/untrusted TLS certificate.
        # This prevents Playwright's `net::ERR_CERT_AUTHORITY_INVALID` from failing navigation.
        browser_new_context_options={"ignore_https_errors": True},
    )

    @crawler.failed_request_handler
    async def failed_handler(context: BasicCrawlingContext, error: Exception) -> None:
        # Called once retries are exhausted.
        page_id = None
        if context.request and context.request.url:
            match = re.search(r"/disaron/([^/]+)/detail/?", context.request.url)
            page_id = match.group(1) if match else None
        append_failed_row(page_id)
        append_error_row(
            page_id,
            url=context.request.url if context.request else "",
            error_message=str(error),
            retry_count=getattr(context.request, "retry_count", None),
        )
        crawler.log.error(
            f"Retries exhausted for {context.request.url if context.request else 'unknown'}: {error}"
        )

    if urls:
        await crawler.run(urls)
