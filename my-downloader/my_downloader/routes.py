from pathlib import Path
from urllib.parse import urlparse
import csv

from crawlee.crawlers import BeautifulSoupCrawlingContext
from crawlee.router import Router

router = Router[BeautifulSoupCrawlingContext]()

_RESULTS_PATH: Path | None = None
_FAILURES_PATH: Path | None = None
_ENTRIES_BY_URL: dict[str, list[dict[str, str]]] = {}


def configure_run_output(
    results_path: Path,
    failures_path: Path,
    entries: list[dict[str, str]],
) -> None:
    """
    Configure per-run output files and URL->entry lookup.
    """
    global _RESULTS_PATH, _FAILURES_PATH, _ENTRIES_BY_URL
    _RESULTS_PATH = results_path
    _FAILURES_PATH = failures_path
    _ENTRIES_BY_URL = {}
    for entry in entries:
        url = (entry.get("url_fichier") or "").strip()
        if not url:
            continue
        _ENTRIES_BY_URL.setdefault(url, []).append(entry)


def _pop_entry_for_url(url: str) -> dict[str, str]:
    candidates = _ENTRIES_BY_URL.get(url) or []
    if candidates:
        return candidates.pop(0)
    # Fallback when URL is unknown in input mapping.
    parsed = urlparse(url)
    return {
        "disaron_nom": "",
        "nom_fichier": Path(parsed.path).name or "",
        "url_fichier": url,
    }


def append_failure_row_for_url(url: str) -> None:
    if _FAILURES_PATH is None:
        return
    entry = _pop_entry_for_url(url)
    disaron_nom = entry.get("disaron_nom", "")
    detail_url = ""
    if disaron_nom:
        detail_url = (
            "https://agreste.agriculture.gouv.fr/agreste-web/disaron/"
            f"{disaron_nom}/detail/"
        )
    row = {
        "disaron_nom": disaron_nom,
        "nom_fichier": entry.get("nom_fichier", ""),
        "success": 0,
        "url_fichier": entry.get("url_fichier", url),
        "url": detail_url,
    }
    with _FAILURES_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "disaron_nom",
                "nom_fichier",
                "success",
                "url_fichier",
                "url",
            ],
        )
        writer.writerow(row)


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

    # Stream success row for this URL immediately.
    if _RESULTS_PATH is not None:
        entry = _pop_entry_for_url(url)
        disaron_nom = entry.get("disaron_nom", "")
        detail_url = ""
        if disaron_nom:
            detail_url = (
                "https://agreste.agriculture.gouv.fr/agreste-web/disaron/"
                f"{disaron_nom}/detail/"
            )
        row = {
            "disaron_nom": disaron_nom,
            "nom_fichier": entry.get("nom_fichier", ""),
            "success": 1,
            "url_fichier": entry.get("url_fichier", url),
            "url": detail_url,
        }
        with _RESULTS_PATH.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "disaron_nom",
                    "nom_fichier",
                    "success",
                    "url_fichier",
                    "url",
                ],
            )
            writer.writerow(row)

    context.log.info(f'Saved to {target_path}')
