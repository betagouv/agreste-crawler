#!/usr/bin/env python3
"""
Generate timestamped Disaron links CSV files from a raw Disarons JSON dump.

Default output directory:
- metadata_editor/output

Outputs:
- <timestamp>_Disarons_links.csv
- <timestamp>_Disarons_links_a.csv
- <timestamp>_Disarons_links_img.csv

The img file also contains:
- width, height
- width_source, height_source
  (img_attribute, img_style, ancestor_style, or empty)

Usage:
    just extract_disaron_links

    just extract_disaron_links \
        --input-file data/2026-05-18_Disarons.json \
        --output-dir metadata_editor/output
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


HTML_FIELDS = (
    ("html_principal", "disaron:html_principal"),
    ("html_secondaire", "disaron:html_secondaire"),
)

DISARON_NOM_RE = re.compile(r'"disaron:nom"\s*:\s*"(?P<value>[^"]*)"')
HTML_FIELD_RES = {
    "disaron:html_principal": re.compile(
        r'"disaron:html_principal"\s*:\s*"(?P<value>.*?)"\s*,\s*"disaron:',
        re.DOTALL,
    ),
    "disaron:html_secondaire": re.compile(
        r'"disaron:html_secondaire"\s*:\s*"(?P<value>.*?)"\s*,\s*"disaron:',
        re.DOTALL,
    ),
}


@dataclass
class DimValue:
    value: str
    source: str


def _parse_style(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _image_dimension_sources(img_tag) -> tuple[DimValue, DimValue]:
    width_attr = (img_tag.get("width") or "").strip()
    height_attr = (img_tag.get("height") or "").strip()
    if width_attr or height_attr:
        return (
            DimValue(width_attr, "img_attribute" if width_attr else ""),
            DimValue(height_attr, "img_attribute" if height_attr else ""),
        )

    img_style = _parse_style((img_tag.get("style") or "").strip())
    width_style = img_style.get("width", "")
    height_style = img_style.get("height", "")
    if width_style or height_style:
        return (
            DimValue(width_style, "img_style" if width_style else ""),
            DimValue(height_style, "img_style" if height_style else ""),
        )

    parent = img_tag.parent
    while parent is not None and getattr(parent, "name", None):
        parent_style = _parse_style((parent.get("style") or "").strip())
        width_parent = parent_style.get("width", "")
        height_parent = parent_style.get("height", "")
        if width_parent or height_parent:
            return (
                DimValue(
                    width_parent, "ancestor_style" if width_parent else ""
                ),
                DimValue(
                    height_parent, "ancestor_style" if height_parent else ""
                ),
            )
        parent = parent.parent

    return (DimValue("", ""), DimValue("", ""))


def _normalize_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "stats-pprd.national.agri":
        return "stats.national.agri"
    if (
        host.endswith(".agriculture.gouv.fr")
        and host != "agreste.agriculture.gouv.fr"
    ):
        return "agriculture.gouv.fr (other)"
    return host


def _iter_disaron_docs(input_file: Path):
    """
    Iterate document objects from heterogeneous dump formats:
    - one JSON object per line
    - pretty-printed objects spanning multiple lines
    - JSON array wrappers and comma separators
    """
    in_string = False
    escape = False
    depth = 0
    started = False
    buffer: list[str] = []

    with input_file.open("r", encoding="utf-8") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            for ch in chunk:
                if not started:
                    if ch == "{":
                        started = True
                        depth = 1
                        in_string = False
                        escape = False
                        buffer = ["{"]
                    continue

                buffer.append(ch)

                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue

                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        raw_obj = "".join(buffer)
                        started = False
                        buffer = []
                        try:
                            doc = json.loads(raw_obj)
                        except json.JSONDecodeError:
                            # Tolerant fallback for malformed objects
                            # (typically unescaped newlines in HTML strings).
                            props: dict[str, str] = {}
                            nom_match = DISARON_NOM_RE.search(raw_obj)
                            if nom_match:
                                props["disaron:nom"] = nom_match.group("value")
                            for field, pattern in HTML_FIELD_RES.items():
                                m = pattern.search(raw_obj)
                                if m:
                                    raw_html = m.group("value")
                                    props[field] = (
                                        raw_html.replace(r"\/", "/")
                                        .replace(r"\"", '"')
                                        .replace(r"\n", "\n")
                                    )
                            if props.get("disaron:nom"):
                                yield {"properties": props}
                            continue
                        if isinstance(doc, dict):
                            yield doc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        default="data/2026-05-18_Disarons.json",
        help="Raw Disarons JSON dump.",
    )
    parser.add_argument(
        "--output-dir",
        default="metadata_editor/output",
        help="Directory for generated CSV files.",
    )
    args = parser.parse_args()

    input_file = Path(args.input_file).resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    all_path = output_dir / f"{prefix}_Disarons_links.csv"
    a_path = output_dir / f"{prefix}_Disarons_links_a.csv"
    img_path = output_dir / f"{prefix}_Disarons_links_img.csv"

    rows_all: list[dict[str, str]] = []
    rows_a: list[dict[str, str]] = []
    rows_img: list[dict[str, str]] = []

    for doc in _iter_disaron_docs(input_file):
        props = doc.get("properties") or {}
        if not isinstance(props, dict):
            continue
        disaron_nom = (props.get("disaron:nom") or "").strip()
        if not disaron_nom:
            continue

        for source_name, html_field in HTML_FIELDS:
            html_value = props.get(html_field) or ""
            if not isinstance(html_value, str) or not html_value.strip():
                continue

            soup = BeautifulSoup(html_value, "html.parser")

            for tag in soup.find_all("a"):
                href = unescape((tag.get("href") or "").strip())
                if not href:
                    continue
                row = {
                    "disaron:nom": disaron_nom,
                    "source": source_name,
                    "element": "a",
                    "attribute": "href",
                    "url": href,
                    "domain": _normalize_domain(href),
                }
                rows_all.append(row)
                rows_a.append(row.copy())

            for tag in soup.find_all("img"):
                src = unescape((tag.get("src") or "").strip())
                if not src:
                    continue
                width, height = _image_dimension_sources(tag)
                row_base = {
                    "disaron:nom": disaron_nom,
                    "source": source_name,
                    "element": "img",
                    "attribute": "src",
                    "url": src,
                    "domain": _normalize_domain(src),
                }
                rows_all.append(row_base.copy())
                rows_img.append(
                    {
                        **row_base,
                        "width": width.value,
                        "height": height.value,
                        "width_source": width.source,
                        "height_source": height.source,
                    }
                )

    with all_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "disaron:nom",
                "source",
                "element",
                "attribute",
                "url",
                "domain",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_all)

    with a_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "disaron:nom",
                "source",
                "element",
                "attribute",
                "url",
                "domain",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_a)

    with img_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "disaron:nom",
                "source",
                "element",
                "attribute",
                "url",
                "domain",
                "width",
                "height",
                "width_source",
                "height_source",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_img)

    print(f"Wrote {all_path} ({len(rows_all)} rows)")
    print(f"Wrote {a_path} ({len(rows_a)} rows)")
    print(f"Wrote {img_path} ({len(rows_img)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
