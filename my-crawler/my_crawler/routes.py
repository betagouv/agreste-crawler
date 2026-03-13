import json
import re
from pathlib import Path
from urllib.parse import urljoin

from crawlee.crawlers import BeautifulSoupCrawlingContext
from crawlee.router import Router

router = Router[BeautifulSoupCrawlingContext]()


def extract_page_id(url: str) -> str | None:
    """
    Extract the Agreste page ID from URLs like:
    https://.../disaron/IraLai2627/detail/  -> IraLai2627
    https://.../disaron/IraAbo2621/detail/  -> IraAbo2621
    """
    match = re.search(r"/disaron/([^/]+)/detail/?", url)
    return match.group(1) if match else None


@router.default_handler
async def default_handler(context: BeautifulSoupCrawlingContext) -> None:
    """Default request handler."""
    context.log.info(f'Processing {context.request.url} ...')

    # Extract and store basic page info in the dataset
    page_url = context.request.loaded_url or context.request.url
    page_id = extract_page_id(page_url)
    title_tag = context.soup.find('title')

    # Base data always stored
    data: dict[str, object | None] = {
        'url': page_url,
        'id': page_id,
        'page_title': title_tag.text if title_tag else None,
    }

    # For detail pages like .../disaron/<ID>/detail/, extract extra fields
    if page_id is not None:
        # New, more precise selectors from the page structure
        detail_title_el = context.soup.select_one('#mainform\\:j_idt78')
        detail_subtitle_el = context.soup.select_one('#mainform\\:j_idt80')
        summary_el = context.soup.select_one('#mainform\\:j_idt85')
        authors_paragraphs = context.soup.select('#mainform\\:j_idt88 p')
        themes_rows = context.soup.select('#mainform\\:themesTable tr')
        years_rows = context.soup.select('#mainform\\:anneesReferenceListTable tr')
        geographies_rows = context.soup.select('#mainform\\:nivGeoListTable tr')

        summary = summary_el.get_text(strip=True) if summary_el else None
        # meta_id no longer has a dedicated selector; keep None for now
        meta_id = None
        authors = [p.get_text(strip=True) for p in authors_paragraphs]

        # Skip header rows (first row) and collect non-empty cell texts
        def _rows_to_values(rows):
            values: list[str] = []
            for tr in rows[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all('td')]
                text = ' '.join(c for c in cells if c)
                if text:
                    values.append(text)
            return values

        themes = _rows_to_values(themes_rows) if themes_rows else []
        years = _rows_to_values(years_rows) if years_rows else []
        geographies = _rows_to_values(geographies_rows) if geographies_rows else []

        data.update(
            {
                'title': detail_title_el.get_text(strip=True) if detail_title_el else None,
                'subtitle': detail_subtitle_el.get_text(strip=True) if detail_subtitle_el else None,
                'summary': summary,
                'meta_id': meta_id,
                'authors': authors,
                'themes': themes,
                'years': years,
                'geographies': geographies,
            }
        )

        # Also append/update a single JSON file keyed by ID
        record = {
            'id': page_id,
            'title': data.get('title'),
            'subtitle': data.get('subtitle'),
            'summary': data.get('summary'),
            'authors': data.get('authors'),
            'themes': data.get('themes'),
            'years': data.get('years'),
            'geographies': data.get('geographies'),
        }

        pages_file = Path('pages.json')
        pages: dict[str, object] = {}
        if pages_file.exists():
            try:
                pages = json.loads(pages_file.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                pages = {}

        pages[str(page_id)] = record
        pages_file.write_text(
            json.dumps(pages, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    await context.push_data(data)

    # Enqueue discovered links for further crawling:
    # - only URLs under the allowed prefixes
    # - but skip search URLs under /disaron/!searchurl
    await context.enqueue_links(
        strategy='all',
        include=[
            re.compile(r"^https://agreste\.agriculture\.gouv\.fr/agreste-web"),
        #    re.compile(r"^https://agreste\.agriculture\.gouv\.fr/agreste-web/download"),
        #    re.compile(r"^https://agreste\.agriculture\.gouv\.fr/agreste-web/disaron"),
        ],
    )

    # --- Download PDFs and spreadsheets found on the page ---
    # Store them under files/<ID>/, falling back to files/unknown if no ID.
    base_files_dir = Path('files')
    target_dir = base_files_dir / (page_id or 'unknown')
    target_dir.mkdir(parents=True, exist_ok=True)

    file_urls: list[str] = []
    for a in context.soup.select("a[href$='.pdf'], a[href$='.xls'], a[href$='.xlsx']"):
        href = a.get('href')
        if not href:
            continue
        absolute_url = urljoin(page_url, href)
        file_urls.append(absolute_url)

    for file_url in file_urls:
        context.log.info(f'Downloading file: {file_url}')
        response = await context.send_request(file_url)
        if response.status_code != 200:
            context.log.warning(f'Failed to download {file_url}: status {response.status_code}')
            continue

        inferred_name = file_url.rstrip('/').split('/')[-1] or 'downloaded_file'
        target_path = target_dir / inferred_name

        content = await response.read()
        target_path.write_bytes(content)
        context.log.info(f'Saved {file_url} to {target_path}')
