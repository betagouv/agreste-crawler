from pathlib import Path
from urllib.parse import urlparse
import csv

from crawlee.crawlers import BeautifulSoupCrawlingContext
from crawlee.router import Router

router = Router[BeautifulSoupCrawlingContext]()


@router.default_handler
async def default_handler(context: BeautifulSoupCrawlingContext) -> None:
    """
    Download the current URL as a file into the local `downloads/` directory.

    This handler does not follow any additional links.
    """
    url = context.request.url
    context.log.info(f'Downloading {url} ...')

    # Derive filename from URL path
    parsed = urlparse(url)
    name = Path(parsed.path).name or "downloaded_file"

    downloads_dir = Path("downloads")
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target_path = downloads_dir / name

    # Get raw HTTP response body and save it as-is
    try:
        content = await context.http_response.read()
        target_path.write_bytes(content)
    except Exception as e:
        context.log.error(f'Failed to download {url}: {e}')
        return

    # Log explicit success row alongside failures logged in main.py
    results_path = Path(__file__).resolve().parents[2] / "download_results.csv"
    with results_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "success"])
        writer.writerow({"url": url, "success": 1})

    context.log.info(f'Saved to {target_path}')
