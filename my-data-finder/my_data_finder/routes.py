import csv
import re
from pathlib import Path

from crawlee.crawlers import BeautifulSoupCrawlingContext
from crawlee.router import Router

router = Router[BeautifulSoupCrawlingContext]()

_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
_OUTPUT_PATH: Path | None = None


def _get_output_path() -> Path:
    """Return the output path for this run, creating it on first call."""
    global _OUTPUT_PATH
    if _OUTPUT_PATH is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH = _OUTPUT_DIR / f"{timestamp}_output.csv"
    return _OUTPUT_PATH


def _extract_page_id(url: str) -> str | None:
    match = re.search(r"/disaron/([^/]+)/detail/?", url)
    return match.group(1) if match else None


@router.default_handler
async def default_handler(context: BeautifulSoupCrawlingContext) -> None:
    """Default request handler."""
    url = context.request.loaded_url or context.request.url
    context.log.info(f'Processing {url} ...')

    # Try the loaded URL first, then fall back to the original request URL
    page_id = _extract_page_id(url) or _extract_page_id(context.request.url)
    context.log.info(f'Extracted page_id: {page_id}')

    if page_id:
        output_path = _get_output_path()
        write_header = not output_path.exists() or output_path.stat().st_size == 0
        with output_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["disaron:nom"])
            if write_header:
                writer.writeheader()
            writer.writerow({"disaron:nom": page_id})

    # Do not follow any links — only the explicitly provided detail URLs are visited.
    # await context.enqueue_links()
