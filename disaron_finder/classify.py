"""Classify Disaron noms for upload skips vs page-lookup candidates."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

PUBLICATION_CUTOFF = date(2026, 3, 1)
DATA_CUTOFF = date(2026, 9, 30)
SKIP_REASON_PUBLICATION_CUTOFF = "Publication published after 2026-03-01"
SKIP_REASON_DATA_CUTOFF = "Data published after 2026-09-30"
SKIP_REASON_NON_PUBLIC = "Non-public page"
SKIP_LABEL_WILL_NOT_UPLOAD = "will not upload"
SKIP_LABEL_NOT_UPLOADED_YET = "not uploaded yet"
DISAR_PUBLIC = "DISAR-PUBLIC"

DEFAULT_WILL_NOT_UPLOAD_FILE = "2026-10-28_will_not_upload.csv"
DEFAULT_NOT_UPLOADED_YET_FILE = "2026-10-28_not_uploaded_yet.csv"

CutoffClassifyResult = Literal[
    "skip_publication",
    "skip_data",
    "error_unidentified",
    "not_after_cutoff",
]

DisaronClassificationKind = Literal[
    "will_not_upload",
    "not_uploaded_yet",
    "non_public",
    "after_publication_cutoff",
    "after_data_cutoff",
    "other",
]

# Tolerant extraction when a dump object has invalid JSON (e.g. raw newlines in HTML).
_DISARON_NOM_RE = re.compile(r'"disaron:nom"\s*:\s*"(?P<value>[^"]*)"')
_DISARON_TYPE_RE = re.compile(
    r'"disaron:type_disaron"\s*:\s*"(?P<value>[^"]*)"'
)
_DISARON_DATE_RE = re.compile(
    r'"disaron:Date_premiere_publication"\s*:\s*"(?P<value>[^"]*)"'
)
_DISARON_RESTIT_RE = re.compile(
    r'"disaron:communaute_restit"\s*:\s*\[(?P<value>[^\]]*)\]'
)


@dataclass(frozen=True)
class DisaronMeta:
    type_disaron: str
    date_premiere_publication: date | None
    communaute_restit: tuple[str, ...]


@dataclass(frozen=True)
class DisaronClassification:
    kind: DisaronClassificationKind
    reason: str  # skip message for skip kinds; "" for other


def _parse_communaute_restit(raw: object) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(item) for item in raw if item is not None and str(item))
    if isinstance(raw, str) and raw.strip():
        return (raw.strip(),)
    return ()


def _parse_communaute_restit_from_raw_array(raw_inner: str) -> tuple[str, ...]:
    """Parse string literals inside a ``[...]`` array fragment from raw JSON."""
    return tuple(re.findall(r'"([^"]*)"', raw_inner))


def is_non_public(meta: DisaronMeta) -> bool:
    """True when restit does not include ``DISAR-PUBLIC`` (null/empty included)."""
    return DISAR_PUBLIC not in meta.communaute_restit


def _parse_premiere_publication(raw: object) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    day = text[:10]
    try:
        return datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return None


def _kind_from_meta(disaron_nom: str, meta: DisaronMeta) -> str | None:
    """Return ``publication``, ``data``, or ``None`` if unidentified."""
    type_disaron = (meta.type_disaron or "").strip()
    if type_disaron == "t" or (
        type_disaron == "p" and "SeriesLongues" in disaron_nom
    ):
        return "data"
    if type_disaron == "p" and "SeriesLongues" not in disaron_nom:
        return "publication"
    return None


def _iter_disaron_json_docs(path: Path):
    """Yield document dicts from a JSON array or concatenated object dump."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        data = None

    if data is not None:
        if isinstance(data, list):
            for doc in data:
                if isinstance(doc, dict):
                    yield doc
            return
        if isinstance(data, dict):
            yield data
            return
        raise ValueError(
            f"Expected a JSON list or object of documents, got {type(data).__name__}"
        )

    # Concatenated `{...},\n{...}` dump (same shape as 2026-05-18_Disarons.json).
    in_string = False
    escape = False
    depth = 0
    started = False
    buffer: list[str] = []
    with path.open(encoding="utf-8") as f:
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
                            # Malformed objects still expose nom/type/date as
                            # plain strings; recover those for cutoff checks.
                            nom_m = _DISARON_NOM_RE.search(raw_obj)
                            if not nom_m:
                                continue
                            type_m = _DISARON_TYPE_RE.search(raw_obj)
                            date_m = _DISARON_DATE_RE.search(raw_obj)
                            restit_m = _DISARON_RESTIT_RE.search(raw_obj)
                            restit: list[str] = []
                            if restit_m:
                                restit = list(
                                    _parse_communaute_restit_from_raw_array(
                                        restit_m.group("value")
                                    )
                                )
                            yield {
                                "properties": {
                                    "disaron:nom": nom_m.group("value"),
                                    "disaron:type_disaron": (
                                        type_m.group("value") if type_m else ""
                                    ),
                                    "disaron:Date_premiere_publication": (
                                        date_m.group("value") if date_m else ""
                                    ),
                                    "disaron:communaute_restit": restit,
                                }
                            }
                            continue
                        if isinstance(doc, dict):
                            yield doc


def load_disaron_meta(disarons_json: str | Path) -> dict[str, DisaronMeta]:
    """Map ``disaron:nom`` → type, date, and restit communities from a JSON dump."""
    path = Path(disarons_json)
    if not path.exists():
        raise FileNotFoundError(f"Disarons JSON not found: {path}")

    out: dict[str, DisaronMeta] = {}
    for doc in _iter_disaron_json_docs(path):
        props = doc.get("properties") or {}
        if not isinstance(props, dict):
            continue
        nom = (props.get("disaron:nom") or "").strip()
        if not nom:
            continue
        type_disaron = props.get("disaron:type_disaron")
        out[nom] = DisaronMeta(
            type_disaron="" if type_disaron is None else str(type_disaron),
            date_premiere_publication=_parse_premiere_publication(
                props.get("disaron:Date_premiere_publication")
            ),
            communaute_restit=_parse_communaute_restit(
                props.get("disaron:communaute_restit")
            ),
        )
    return out


def classify_missing_by_cutoff(
    disaron_nom: str, meta_by_nom: dict[str, DisaronMeta]
) -> CutoffClassifyResult:
    """Classify a missing page for cutoff skips.

    Returns a cutoff skip, unidentified error, or not-after-cutoff (caller
    should keep the usual no-page failure).
    """
    meta = meta_by_nom.get(disaron_nom)
    if meta is None:
        return "error_unidentified"

    kind = _kind_from_meta(disaron_nom, meta)
    if kind is None:
        return "error_unidentified"
    if meta.date_premiere_publication is None:
        return "error_unidentified"

    if kind == "publication":
        if meta.date_premiere_publication > PUBLICATION_CUTOFF:
            return "skip_publication"
        return "not_after_cutoff"

    if meta.date_premiere_publication > DATA_CUTOFF:
        return "skip_data"
    return "not_after_cutoff"


def missing_page_before_cutoff_error(
    disaron_nom: str, meta_by_nom: dict[str, DisaronMeta]
) -> str:
    """Error text when the page is missing but the item is on/before cutoff."""
    meta = meta_by_nom[disaron_nom]
    kind = _kind_from_meta(disaron_nom, meta)
    assert kind is not None
    assert meta.date_premiere_publication is not None
    cutoff = PUBLICATION_CUTOFF if kind == "publication" else DATA_CUTOFF
    return (
        f"no PublicationPage for disaron_id={disaron_nom!r} "
        f"({kind}, published {meta.date_premiere_publication.isoformat()}, "
        f"on or before cutoff {cutoff.isoformat()})"
    )


def load_upload_exception_reasons(
    path: str | Path,
) -> dict[str, str]:
    """Map ``disaron:nom`` → reason string (empty if the reason cell is blank)."""
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Upload-exception CSV not found: {csv_path}")

    out: dict[str, str] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Upload-exception CSV has no headers: {csv_path}")
        required = {"disaron:nom", "reason"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Upload-exception CSV missing column(s): "
                f"{', '.join(sorted(missing))}"
            )
        for row in reader:
            disaron_nom = (row.get("disaron:nom") or "").strip()
            if not disaron_nom:
                continue
            out[disaron_nom] = (row.get("reason") or "").strip()
    return out


def format_upload_exception_skip(label: str, reason: str) -> str:
    if reason:
        return f"{label}: {reason}"
    return label


def classify_disaron(
    disaron_nom: str,
    *,
    meta_by_nom: dict[str, DisaronMeta],
    will_not_upload: dict[str, str],
    not_uploaded_yet: dict[str, str],
) -> DisaronClassification:
    """Classify a Disaron nom before PublicationPage lookup.

    Priority: curated upload exceptions → non-public → post-cutoff → other.
    """
    if disaron_nom in will_not_upload:
        return DisaronClassification(
            kind="will_not_upload",
            reason=format_upload_exception_skip(
                SKIP_LABEL_WILL_NOT_UPLOAD,
                will_not_upload[disaron_nom],
            ),
        )
    if disaron_nom in not_uploaded_yet:
        return DisaronClassification(
            kind="not_uploaded_yet",
            reason=format_upload_exception_skip(
                SKIP_LABEL_NOT_UPLOADED_YET,
                not_uploaded_yet[disaron_nom],
            ),
        )

    meta = meta_by_nom.get(disaron_nom)
    if meta is not None and is_non_public(meta):
        return DisaronClassification(
            kind="non_public",
            reason=SKIP_REASON_NON_PUBLIC,
        )

    cutoff_kind = classify_missing_by_cutoff(disaron_nom, meta_by_nom)
    if cutoff_kind == "skip_publication":
        return DisaronClassification(
            kind="after_publication_cutoff",
            reason=SKIP_REASON_PUBLICATION_CUTOFF,
        )
    if cutoff_kind == "skip_data":
        return DisaronClassification(
            kind="after_data_cutoff",
            reason=SKIP_REASON_DATA_CUTOFF,
        )

    return DisaronClassification(kind="other", reason="")
