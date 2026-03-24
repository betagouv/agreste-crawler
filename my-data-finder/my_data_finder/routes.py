import csv
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from crawlee.crawlers import BeautifulSoupCrawlingContext
from crawlee.router import Router

DOWNLOAD_EXTENSIONS = {".pdf", ".xls", ".xlsx", ".xlsm", ".doc", ".docx", ".7z", ".zip"}

router = Router[BeautifulSoupCrawlingContext]()

_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
_OUTPUT_PATH: Path | None = None
_ERROR_PATH: Path | None = None
_RUN_TIMESTAMP: str | None = None
_DEBUG_ATTEMPTS: dict[str, int] = {}


def _get_output_path() -> Path:
    """Return the output path for this run, creating it on first call."""
    global _OUTPUT_PATH
    if _OUTPUT_PATH is None:
        timestamp = _get_run_timestamp()
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH = _OUTPUT_DIR / f"{timestamp}_output.csv"
    return _OUTPUT_PATH


def _get_error_path() -> Path:
    """Return the error CSV path for this run, creating it on first call."""
    global _ERROR_PATH
    if _ERROR_PATH is None:
        run_ts = _get_run_timestamp()
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _ERROR_PATH = _OUTPUT_DIR / f"{run_ts}_errors.csv"
    return _ERROR_PATH


def _get_run_timestamp() -> str:
    """Return a stable timestamp for the current run."""
    global _RUN_TIMESTAMP
    if _RUN_TIMESTAMP is None:
        from datetime import datetime
        _RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _RUN_TIMESTAMP


def _get_debug_dir() -> Path:
    """Return the debug HTML directory for this run."""
    run_ts = _get_run_timestamp()
    debug_dir = _OUTPUT_DIR / f"debug_html_{run_ts}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir


def _extract_page_id(url: str) -> str | None:
    match = re.search(r"/disaron/([^/]+)/detail/?", url)
    return match.group(1) if match else None


def append_failed_row(page_id: str | None) -> None:
    """
    Append a failure row when max retries are exhausted.
    Only disaron:nom is filled; other columns stay empty.
    """
    if not page_id:
        return
    output_path = _get_output_path()
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    fieldnames = [
        "disaron:nom",
        "error",
        "dc:title",
        "disaron:Complement_titre",
        "disaron:chapeau",
        "disaron:Auteur",
        "disaron:Date_premiere_publication",
        "disaron:Numerotation",
        "nb de fichiers",
        "noms des fichiers",
        "urls des fichiers",
    ]
    with output_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "disaron:nom": page_id,
                "error": 1,
                "dc:title": "",
                "disaron:Complement_titre": "",
                "disaron:chapeau": "",
                "disaron:Auteur": "",
                "disaron:Date_premiere_publication": "",
                "disaron:Numerotation": "",
                "nb de fichiers": "",
                "noms des fichiers": "",
                "urls des fichiers": "",
            }
        )


def append_error_row(
    page_id: str | None,
    *,
    url: str,
    error_message: str,
    retry_count: int | None,
) -> None:
    """
    Append a detailed error row for failed pages (after retries exhausted).
    """
    error_path = _get_error_path()
    write_header = not error_path.exists() or error_path.stat().st_size == 0
    debug_dir = _get_debug_dir()
    final_try = (retry_count + 1) if isinstance(retry_count, int) else None
    debug_html_path = (
        str(debug_dir / f"{page_id or 'unknown'}__try{final_try}.html")
        if final_try is not None
        else ""
    )
    fieldnames = [
        "disaron:nom",
        "url",
        "retry_count",
        "error_message",
        "debug_html_path",
    ]
    with error_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "disaron:nom": page_id or "",
                "url": url,
                "retry_count": retry_count if retry_count is not None else "",
                "error_message": error_message,
                "debug_html_path": debug_html_path,
            }
        )


def _page_contains_hidden_id(context: BeautifulSoupCrawlingContext, expected_id: str) -> bool:
    """
    Sanity check: expected disaron ID must appear in a hidden <p style=\"display:none\">.
    Otherwise, it means the site has returned an error, or loaded the wrong page (this happens).
    """
    hidden_p_tags = context.soup.find_all(
        "p",
        style=lambda s: isinstance(s, str) and "display:none" in s.replace(" ", "").lower(),
    )
    for p in hidden_p_tags:
        text = p.get_text(strip=True)
        if expected_id in text:
            return True
    return False


@router.default_handler
async def default_handler(context: BeautifulSoupCrawlingContext) -> None:
    """Default request handler."""
    url = context.request.loaded_url or context.request.url
    context.log.info(f'Processing {url} ...')

    # Try the loaded URL first, then fall back to the original request URL
    page_id = _extract_page_id(url) or _extract_page_id(context.request.url)
    context.log.info(f'Extracted page_id: {page_id}')

    # Dump raw HTML for debugging selector issues.
    debug_dir = _get_debug_dir()
    debug_key = page_id or context.request.url or "unknown"
    retry_count = getattr(context.request, "retry_count", None)
    if isinstance(retry_count, int):
        attempt_num = retry_count + 1
    else:
        attempt_num = _DEBUG_ATTEMPTS.get(debug_key, 0) + 1
    _DEBUG_ATTEMPTS[debug_key] = attempt_num
    debug_name = f"{page_id or 'unknown'}__try{attempt_num}.html"
    (debug_dir / debug_name).write_text(str(context.soup), encoding="utf-8")

    if page_id and not _page_contains_hidden_id(context, page_id):
        # Raise to trigger Crawlee retry logic for this request.
        raise ValueError(
            f"Sanity check failed for {page_id}: hidden <p style='display:none'> does not contain the ID."
        )

    if page_id:
        # Extract additional metadata fields from the detail page.
        dc_title = ""
        complement_titre = ""
        chapeau = ""
        auteurs: list[str] = []
        date_premiere_publication = ""
        numerotation = ""

        title_el = context.soup.select_one("#mainform\\:j_idt78")
        if title_el:
            dc_title = title_el.get_text(strip=True)

        complement_el = context.soup.select_one("#mainform\\:j_idt80")
        if complement_el:
            complement_titre = complement_el.get_text(strip=True)

        chapeau_el = context.soup.select_one("#mainform\\:j_idt85")
        if chapeau_el:
            chapeau = chapeau_el.get_text(strip=True)

        auteur_container = context.soup.select_one("#mainform\\:j_idt88")
        if auteur_container:
            auteurs = [
                p.get_text(strip=True)
                for p in auteur_container.find_all("p")
                if p.get_text(strip=True)
            ]

        date_container = context.soup.select_one("#datePublication")
        if date_container:
            span_el = date_container.find("span")
            if span_el:
                date_premiere_publication = span_el.get_text(strip=True)

        numerotation_el = context.soup.select_one("#NumerotationValeur")
        if numerotation_el:
            numerotation = numerotation_el.get_text(strip=True)
            if numerotation.startswith("N°"):
                numerotation = numerotation[2:].strip()

        # Find links inside the specific JSF container id=mainform:j_idt119
        file_links: list[str] = []
        container = context.soup.select_one("#mainform\\:j_idt119")

        if not container:
            context.log.warning(f"No matching container (#mainform:j_idt119) on {page_id}")
        else:
            anchors = container.find_all("a", href=True)
            context.log.info(f"Found {len(anchors)} anchors in #mainform:j_idt119 on {page_id}")
            for a in anchors:
                href: str = a["href"]
                ext = Path(href.split("?")[0]).suffix.lower()
                accepted = ext in DOWNLOAD_EXTENSIONS
                context.log.info(
                    f"[{page_id}] href='{href}' ext='{ext or '(none)'}' accepted={accepted}"
                )
                if accepted:
                    file_links.append(urljoin(context.request.url, href))

        context.log.info(f"Accepted {len(file_links)} downloadable links on {page_id}")

        missing_fields: list[str] = []
        if not dc_title:
            missing_fields.append("dc:title")
        if not complement_titre:
            missing_fields.append("disaron:Complement_titre")
        if not chapeau:
            missing_fields.append("disaron:chapeau")
        if not auteurs:
            missing_fields.append("disaron:Auteur")
        if not date_premiere_publication:
            missing_fields.append("disaron:Date_premiere_publication")
        if not numerotation:
            missing_fields.append("disaron:Numerotation")
        if len(file_links) == 0:
            missing_fields.append("urls des fichiers")

        is_error = len(missing_fields) > 0
        if is_error:
            append_error_row(
                page_id,
                url=url,
                error_message=f"Missing fields: {', '.join(missing_fields)}",
                retry_count=retry_count if isinstance(retry_count, int) else None,
            )

        filenames = [Path(link.split("?")[0]).name for link in file_links]

        output_path = _get_output_path()
        write_header = not output_path.exists() or output_path.stat().st_size == 0
        fieldnames = [
            "disaron:nom",
            "error",
            "dc:title",
            "disaron:Complement_titre",
            "disaron:chapeau",
            "disaron:Auteur",
            "disaron:Date_premiere_publication",
            "disaron:Numerotation",
            "nb de fichiers",
            "noms des fichiers",
            "urls des fichiers",
        ]
        with output_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow({
                "disaron:nom": page_id,
                "error": 1 if is_error else 0,
                "dc:title": dc_title,
                "disaron:Complement_titre": complement_titre,
                "disaron:chapeau": chapeau,
                "disaron:Auteur": json.dumps(auteurs, ensure_ascii=False),
                "disaron:Date_premiere_publication": date_premiere_publication,
                "disaron:Numerotation": numerotation,
                "nb de fichiers": len(file_links),
                "noms des fichiers": json.dumps(filenames, ensure_ascii=False),
                "urls des fichiers": json.dumps(file_links, ensure_ascii=False),
            })

    # Do not follow any links — only the explicitly provided detail URLs are visited.
    # await context.enqueue_links()
