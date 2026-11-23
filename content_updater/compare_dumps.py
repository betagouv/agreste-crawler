"""
Compare two Disaron JSON dumps and write appeared / disappeared / renames /
changed CSVs, split into normal vs ``*_ignored`` via ``classify_disaron``.

Only ``DISAR-PUBLIC`` docs are considered. After-cutoff classifications stay in
the normal (non-ignored) files.

Renames are detected by exact title match only (after strip + casefold); there
is no fuzzy / near-title matching.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ignored_disarons.classify import (
    DEFAULT_NOT_UPLOADED_YET_FILE,
    DEFAULT_WILL_NOT_UPLOAD_FILE,
    DisaronClassification,
    DisaronMeta,
    classify_disaron,
    is_non_public,
    load_upload_exception_reasons,
)
from json_parser import loads_disaron_object


def _parse_communaute_restit(raw: object) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(item) for item in raw if item is not None and str(item))
    if isinstance(raw, str) and raw.strip():
        return (raw.strip(),)
    return ()


def _parse_premiere_publication(raw: object) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

DEFAULT_OLD = "2026-05-18_Disarons.json"
DEFAULT_NEW = "2026-11-13_Disarons.json"
DEFAULT_OUTPUT_DIR = "content_updater/output"
DEFAULT_FIELD_LIST = "content_updater/data/Disaron_field_list.json"

NORMAL_KINDS = frozenset(
    {"other", "after_publication_cutoff", "after_data_cutoff"}
)
IGNORED_KINDS = frozenset({"will_not_upload", "not_uploaded_yet"})


@dataclass(frozen=True)
class FieldListConfig:
    fields: list[str]
    donnees_item_fields: list[str]
    donnees_fichier_fields: list[str]
    normalize_newlines: bool


@dataclass
class DisaronRecord:
    nom: str
    title: str
    type_disaron: str
    date_premiere_publication: str
    doc: dict[str, Any]
    meta: DisaronMeta


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def load_field_list(path: Path) -> FieldListConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = list(raw["fields"])
    donnees = raw.get("donnees") or {}
    return FieldListConfig(
        fields=fields,
        donnees_item_fields=list(donnees.get("item_fields") or []),
        donnees_fichier_fields=list(donnees.get("fichier_fields") or []),
        normalize_newlines=bool(raw.get("normalize_newlines", True)),
    )


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalize_value(value: Any, *, normalize_nl: bool) -> Any:
    if isinstance(value, str):
        return normalize_newlines(value) if normalize_nl else value
    if isinstance(value, list):
        return [normalize_value(x, normalize_nl=normalize_nl) for x in value]
    if isinstance(value, dict):
        return {
            k: normalize_value(v, normalize_nl=normalize_nl) for k, v in value.items()
        }
    return value


def scrub_donnees(
    value: Any,
    *,
    item_fields: list[str],
    fichier_fields: list[str],
    normalize_nl: bool,
) -> Any:
    if not isinstance(value, list):
        return normalize_value(value, normalize_nl=normalize_nl)
    out: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            out.append(normalize_value(item, normalize_nl=normalize_nl))
            continue
        scrubbed: dict[str, Any] = {
            k: normalize_value(item.get(k), normalize_nl=normalize_nl)
            for k in item_fields
        }
        fic = item.get("Fichier")
        if isinstance(fic, dict):
            scrubbed["Fichier"] = {
                k: normalize_value(fic.get(k), normalize_nl=normalize_nl)
                for k in fichier_fields
            }
        else:
            scrubbed["Fichier"] = normalize_value(fic, normalize_nl=normalize_nl)
        out.append(scrubbed)
    return out


def iter_top_level_objects(path: Path):
    in_string = False
    escape = False
    depth = 0
    started = False
    buffer: list[str] = []
    with path.open("r", encoding="utf-8") as f:
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
                        yield "".join(buffer)
                        started = False
                        buffer = []


def iter_docs(path: Path):
    for raw in iter_top_level_objects(path):
        try:
            doc = loads_disaron_object(raw)
        except json.JSONDecodeError:
            continue
        entries = doc.get("entries")
        if isinstance(entries, list) and doc.get("entity-type") == "documents":
            for entry in entries:
                if isinstance(entry, dict):
                    yield entry
            continue
        yield doc


def checked_title(doc: dict[str, Any], *, side: str, nom: str) -> str | None:
    """Return title if doc.title == dc:title; print error and return None if not."""
    props = doc.get("properties") if isinstance(doc.get("properties"), dict) else {}
    doc_title = doc.get("title")
    dc_title = props.get("dc:title")

    def as_text(value: Any) -> str | None:
        if value is None:
            return None
        text = normalize_newlines(str(value)).strip()
        return text

    left = as_text(doc_title)
    right = as_text(dc_title)
    if left is None and right is None:
        return ""
    if left is None or right is None or left != right:
        eprint(
            f"ERROR title mismatch ({side}) disaron:nom={nom!r}: "
            f"doc.title={doc_title!r} dc:title={dc_title!r}"
        )
        return None
    return left


def props(doc: dict[str, Any]) -> dict[str, Any]:
    p = doc.get("properties")
    return p if isinstance(p, dict) else {}


def load_public_records(path: Path, *, side: str) -> dict[str, DisaronRecord]:
    out: dict[str, DisaronRecord] = {}
    for doc in iter_docs(path):
        p = props(doc)
        nom = (p.get("disaron:nom") or "").strip()
        if not nom:
            continue
        restit = _parse_communaute_restit(p.get("disaron:communaute_restit"))
        meta = DisaronMeta(
            type_disaron=(
                ""
                if p.get("disaron:type_disaron") is None
                else str(p.get("disaron:type_disaron"))
            ),
            date_premiere_publication=_parse_premiere_publication(
                p.get("disaron:Date_premiere_publication")
            ),
            communaute_restit=restit,
        )
        if is_non_public(meta):
            continue

        title = checked_title(doc, side=side, nom=nom)
        if title is None:
            # Title inconsistency: still skip from outputs.
            continue

        date_raw = p.get("disaron:Date_premiere_publication")
        date_s = "" if date_raw is None else str(date_raw)
        type_s = meta.type_disaron
        if nom in out:
            eprint(f"ERROR duplicate disaron:nom in {side} dump: {nom!r} (keeping first)")
            continue
        out[nom] = DisaronRecord(
            nom=nom,
            title=title,
            type_disaron=type_s,
            date_premiere_publication=date_s,
            doc=doc,
            meta=meta,
        )
    return out


def get_field_value(
    record: DisaronRecord,
    field: str,
    config: FieldListConfig,
) -> Any:
    normalize_nl = config.normalize_newlines
    if field == "title":
        return normalize_value(record.title, normalize_nl=normalize_nl)
    p = props(record.doc)
    if field == "disaron:donnees":
        return scrub_donnees(
            p.get("disaron:donnees"),
            item_fields=config.donnees_item_fields,
            fichier_fields=config.donnees_fichier_fields,
            normalize_nl=normalize_nl,
        )
    return normalize_value(p.get(field), normalize_nl=normalize_nl)


def is_ignored_kind(kind: str) -> bool:
    return kind in IGNORED_KINDS


def is_normal_kind(kind: str) -> bool:
    return kind in NORMAL_KINDS


def classify_records(
    noms: set[str],
    records_by_nom: dict[str, DisaronRecord],
    *,
    will_not_upload: dict[str, str],
    not_uploaded_yet: dict[str, str],
) -> dict[str, DisaronClassification]:
    meta_by_nom = {nom: records_by_nom[nom].meta for nom in noms if nom in records_by_nom}
    out: dict[str, DisaronClassification] = {}
    for nom in sorted(noms):
        classification = classify_disaron(
            nom,
            meta_by_nom=meta_by_nom,
            will_not_upload=will_not_upload,
            not_uploaded_yet=not_uploaded_yet,
        )
        if classification.kind == "non_public":
            eprint(
                f"ERROR non-public slipped through after public filter: "
                f"disaron:nom={nom!r} reason={classification.reason!r}"
            )
            continue
        out[nom] = classification
    return out


def norm_title(title: str) -> str:
    return " ".join(title.strip().casefold().split())


def find_rename_pairs(
    old_only: set[str],
    new_only: set[str],
    old: dict[str, DisaronRecord],
    new: dict[str, DisaronRecord],
) -> list[tuple[str, str]]:
    old_by_title: dict[str, list[str]] = defaultdict(list)
    new_by_title: dict[str, list[str]] = defaultdict(list)
    for nom in sorted(old_only):
        key = norm_title(old[nom].title)
        if key:
            old_by_title[key].append(nom)
    for nom in sorted(new_only):
        key = norm_title(new[nom].title)
        if key:
            new_by_title[key].append(nom)

    pairs: list[tuple[str, str]] = []
    for key in sorted(set(old_by_title) & set(new_by_title)):
        olds = old_by_title[key]
        news = new_by_title[key]
        for before, after in zip(olds, news):
            pairs.append((before, after))
        # Extra same-title noms without a partner stay in appeared/disappeared.
    pairs.sort(key=lambda p: p[0])
    return pairs


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sort_key_type_nom(row: dict[str, Any], nom_key: str = "disaron:nom") -> tuple:
    return (row.get("disaron:type_disaron") or "", row.get(nom_key) or "")


def build_simple_row(
    record: DisaronRecord,
    classification: DisaronClassification | None,
    *,
    ignored: bool,
) -> dict[str, Any]:
    row = {
        "disaron:nom": record.nom,
        "disaron:Date_premiere_publication": record.date_premiere_publication,
        "disaron:type_disaron": record.type_disaron,
        "title": record.title,
    }
    if ignored and classification is not None:
        row["ignore_kind"] = classification.kind
        row["ignore_reason"] = classification.reason
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", default=DEFAULT_OLD, help="Older Disarons JSON dump")
    parser.add_argument("--new", default=DEFAULT_NEW, help="Newer Disarons JSON dump")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated CSV files",
    )
    parser.add_argument(
        "--field-list",
        default=DEFAULT_FIELD_LIST,
        help="JSON allowlist for changed_content comparison",
    )
    parser.add_argument(
        "--will-not-upload",
        default=DEFAULT_WILL_NOT_UPLOAD_FILE,
        help="Curated will-not-upload CSV",
    )
    parser.add_argument(
        "--not-uploaded-yet",
        default=DEFAULT_NOT_UPLOADED_YET_FILE,
        help="Curated not-uploaded-yet CSV",
    )
    args = parser.parse_args(argv)

    old_path = Path(args.old)
    new_path = Path(args.new)
    output_dir = Path(args.output_dir)
    field_list_path = Path(args.field_list)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = load_field_list(field_list_path)
    will_not_upload = load_upload_exception_reasons(args.will_not_upload)
    not_uploaded_yet = load_upload_exception_reasons(args.not_uploaded_yet)

    eprint(f"Loading old dump (public only): {old_path}")
    old = load_public_records(old_path, side="old")
    eprint(f"  {len(old)} public records")
    eprint(f"Loading new dump (public only): {new_path}")
    new = load_public_records(new_path, side="new")
    eprint(f"  {len(new)} public records")

    old_noms = set(old)
    new_noms = set(new)
    common = old_noms & new_noms
    old_only = old_noms - new_noms
    new_only = new_noms - old_noms

    # Meta/classify: new dump for shared+appeared; old for disappeared-only.
    records_for_classify = dict(new)
    for nom in old_only:
        records_for_classify[nom] = old[nom]

    classifications = classify_records(
        old_noms | new_noms,
        records_for_classify,
        will_not_upload=will_not_upload,
        not_uploaded_yet=not_uploaded_yet,
    )
    # Noms that slipped as non_public are absent from classifications.

    rename_pairs = find_rename_pairs(old_only, new_only, old, new)
    rename_noms: set[str] = set()
    renames_normal: list[dict[str, Any]] = []
    renames_ignored: list[dict[str, Any]] = []

    for before, after in rename_pairs:
        c_before = classifications.get(before)
        c_after = classifications.get(after)
        if c_before is None or c_after is None:
            # Excluded earlier (e.g. non-public slip / title error already handled).
            rename_noms.add(before)
            rename_noms.add(after)
            continue

        before_ignored = is_ignored_kind(c_before.kind)
        after_ignored = is_ignored_kind(c_after.kind)
        rename_noms.add(before)
        rename_noms.add(after)

        if before_ignored != after_ignored:
            eprint(
                f"ERROR rename pair mixed ignore status: "
                f"{before!r} ({c_before.kind}) -> {after!r} ({c_after.kind})"
            )
            continue

        row = {
            "disaron:nom_before": before,
            "disaron:nom_after": after,
            "disaron:Date_premiere_publication_before": old[
                before
            ].date_premiere_publication,
            "disaron:Date_premiere_publication_after": new[
                after
            ].date_premiere_publication,
            "disaron:type_disaron": new[after].type_disaron or old[before].type_disaron,
            "title": new[after].title or old[before].title,
        }
        if before_ignored:
            row["ignore_kind"] = (
                f"{c_before.kind}|{c_after.kind}"
                if c_before.kind != c_after.kind
                else c_before.kind
            )
            row["ignore_reason"] = (
                f"{before}: {c_before.reason}; {after}: {c_after.reason}"
            )
            renames_ignored.append(row)
        else:
            renames_normal.append(row)

    appeared_normal: list[dict[str, Any]] = []
    appeared_ignored: list[dict[str, Any]] = []
    for nom in sorted(new_only - rename_noms):
        c = classifications.get(nom)
        if c is None:
            continue
        record = new[nom]
        if is_ignored_kind(c.kind):
            appeared_ignored.append(build_simple_row(record, c, ignored=True))
        elif is_normal_kind(c.kind):
            appeared_normal.append(build_simple_row(record, c, ignored=False))

    disappeared_normal: list[dict[str, Any]] = []
    disappeared_ignored: list[dict[str, Any]] = []
    for nom in sorted(old_only - rename_noms):
        c = classifications.get(nom)
        if c is None:
            continue
        record = old[nom]
        if is_ignored_kind(c.kind):
            disappeared_ignored.append(build_simple_row(record, c, ignored=True))
        elif is_normal_kind(c.kind):
            disappeared_normal.append(build_simple_row(record, c, ignored=False))

    changed_normal: list[dict[str, Any]] = []
    changed_ignored: list[dict[str, Any]] = []
    for nom in sorted(common):
        c = classifications.get(nom)
        if c is None:
            continue
        old_rec = old[nom]
        new_rec = new[nom]
        changed_fields = [
            field
            for field in config.fields
            if get_field_value(old_rec, field, config)
            != get_field_value(new_rec, field, config)
        ]
        if not changed_fields:
            continue
        row = {
            "disaron:nom": nom,
            "disaron:type_disaron": new_rec.type_disaron or old_rec.type_disaron,
            "title": new_rec.title or old_rec.title,
            "n_fields_changed": len(changed_fields),
            "fields_changed": "|".join(changed_fields),
        }
        if is_ignored_kind(c.kind):
            row["ignore_kind"] = c.kind
            row["ignore_reason"] = c.reason
            changed_ignored.append(row)
        elif is_normal_kind(c.kind):
            changed_normal.append(row)

    appeared_normal.sort(key=sort_key_type_nom)
    appeared_ignored.sort(key=sort_key_type_nom)
    disappeared_normal.sort(key=sort_key_type_nom)
    disappeared_ignored.sort(key=sort_key_type_nom)
    renames_normal.sort(key=lambda r: sort_key_type_nom(r, "disaron:nom_before"))
    renames_ignored.sort(key=lambda r: sort_key_type_nom(r, "disaron:nom_before"))
    changed_normal.sort(key=sort_key_type_nom)
    changed_ignored.sort(key=sort_key_type_nom)

    simple_fields = [
        "disaron:nom",
        "disaron:Date_premiere_publication",
        "disaron:type_disaron",
        "title",
    ]
    simple_ignored_fields = simple_fields + ["ignore_kind", "ignore_reason"]
    rename_fields = [
        "disaron:nom_before",
        "disaron:nom_after",
        "disaron:Date_premiere_publication_before",
        "disaron:Date_premiere_publication_after",
        "disaron:type_disaron",
        "title",
    ]
    rename_ignored_fields = rename_fields + ["ignore_kind", "ignore_reason"]
    changed_fields_cols = [
        "disaron:nom",
        "disaron:type_disaron",
        "title",
        "n_fields_changed",
        "fields_changed",
    ]
    changed_ignored_fields = changed_fields_cols + ["ignore_kind", "ignore_reason"]

    write_csv(output_dir / "appeared_content.csv", simple_fields, appeared_normal)
    write_csv(
        output_dir / "appeared_content_ignored.csv",
        simple_ignored_fields,
        appeared_ignored,
    )
    write_csv(output_dir / "disappeared_content.csv", simple_fields, disappeared_normal)
    write_csv(
        output_dir / "disappeared_content_ignored.csv",
        simple_ignored_fields,
        disappeared_ignored,
    )
    write_csv(output_dir / "renames.csv", rename_fields, renames_normal)
    write_csv(
        output_dir / "renames_ignored.csv", rename_ignored_fields, renames_ignored
    )
    write_csv(output_dir / "changed_content.csv", changed_fields_cols, changed_normal)
    write_csv(
        output_dir / "changed_content_ignored.csv",
        changed_ignored_fields,
        changed_ignored,
    )

    eprint(
        "Wrote CSVs:",
        f"appeared={len(appeared_normal)}/{len(appeared_ignored)} ignored,",
        f"disappeared={len(disappeared_normal)}/{len(disappeared_ignored)} ignored,",
        f"renames={len(renames_normal)}/{len(renames_ignored)} ignored,",
        f"changed={len(changed_normal)}/{len(changed_ignored)} ignored",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
