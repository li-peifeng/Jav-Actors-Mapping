#!/usr/bin/env python3
"""Merge AVDB SQLite/exports and legacy mapping sources without losing identities.

Sources are read-only. Dry-run by default; --write updates the full mapping XML.
Legacy href/id fields are converted only here, never accepted by the formatter.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from format_full_mapping import (
    ACTOR_TEXT_TRANSLATION, NAME_FIELDS, build_normalized_xml, collect_blacklist_values,
    find_suspicious_escapes, get_actor_node,
    validate_page_ids, validate_unique_tmdb_ids, validate_unique_actor_names,
)

DATA_FIELDS = (*NAME_FIELDS, "keyword", "page_id", "tmdb_id", "bio_graphy", "dackdrop", "verified")
PAGE_RE = re.compile(r"[A-Za-z0-9]{1,120}")


def names(record: dict) -> set[str]:
    values = [*(record.get(k, "") for k in NAME_FIELDS), *record.get("keyword", "").split(",")]
    normalized = (value.translate(ACTOR_TEXT_TRANSLATION).strip() for value in values)
    return {value for value in normalized if value}


def convert_record(record: dict) -> dict[str, str]:
    attrs = {key: str(record[key]).strip() for key in DATA_FIELDS
             if record.get(key) is not None and str(record[key]).strip()}
    if not attrs.get("keyword") and record.get("keyword_raw"):
        attrs["keyword"] = str(record["keyword_raw"]).strip()
    if record.get("name"):
        for key in NAME_FIELDS:
            attrs.setdefault(key, str(record["name"]).strip())
    aliases = record.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(v, str) for v in aliases):
        raise ValueError("aliases must be a list of strings")
    aliases = list(aliases)
    for key in NAME_FIELDS:
        # Legacy mappings occasionally put an alias list in a name field.
        # Keep its first name in that language and move all names to aliases.
        if "," in attrs.get(key, ""):
            values = [v.strip() for v in attrs[key].split(",") if v.strip()]
            if values:
                attrs[key] = values[0]
                aliases.extend(values)
    if record.get("href"):
        url = urlsplit(str(record["href"]).strip())
        match = re.fullmatch(r"/actors/([A-Za-z0-9]{1,120})/?", url.path)
        if (url.scheme != "https" or url.netloc not in {"javdb.com", "www.javdb.com"}
                or not match or url.query or url.fragment):
            raise ValueError(f"Invalid legacy JavDB actor URL: {record['href']!r}")
        page_id = match.group(1)
        # actor-all.json uses id for the upstream ID; AVDB exports use a local
        # numeric id. Only the href-backed collector format may interpret id.
        if record.get("name") and record.get("id") is not None and str(record["id"]) != page_id:
            raise ValueError(f"Collector id disagrees with href: {record['id']!r}")
        if attrs.get("page_id") and attrs["page_id"] != page_id:
            raise ValueError("page_id disagrees with href")
        attrs["page_id"] = page_id
    if "page_id" in attrs and not PAGE_RE.fullmatch(attrs["page_id"]):
        raise ValueError(f"Invalid page_id: {attrs['page_id']!r}")
    if not any(attrs.get(k) for k in NAME_FIELDS):
        raise ValueError("Mapping entry has no actor name")
    keyword = list(dict.fromkeys(v.strip() for v in [
        *attrs.get("keyword", "").split(","), *aliases,
    ] if v.strip()))
    attrs["keyword"] = ",".join(keyword)
    if record.get("tmdb_verified"):
        attrs["verified"] = "1"
    return attrs


def load_source(path: Path) -> tuple[list[dict[str, str]], list[str], list[dict]]:
    blacklist: list[str] = []
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        # mode=ro respects the live WAL while preventing writes to AVDB.
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            records = [dict(row) for row in connection.execute(
                "SELECT * FROM actor WHERE lower(trim(coalesce(tmdb_source, ''))) "
                "NOT IN ('emby', 'external') ORDER BY id"
            )]
        finally:
            connection.close()
    elif path.suffix.lower() == ".json":
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError(f"{path}: expected an array of actor records")
    else:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        records = [dict(entry.attrib) for entry in get_actor_node(root)]
        blacklist = collect_blacklist_values(root)
    converted, issues = [], []
    for index, row in enumerate(records, 1):
        if str(row.get("tmdb_source") or "").strip().lower() in {"emby", "external"}:
            continue
        try:
            converted.append(convert_record(row))
        except ValueError as exc:
            if not str(exc).startswith("Invalid legacy JavDB actor URL:"):
                raise
            # Retain actor data from other sites or malformed JavDB paths,
            # but never derive a JavDB ID from an unrecognized URL.
            issues.append(dict(reason="legacy_href_ignored", index=index, source=row))
            converted.append(convert_record({k: v for k, v in row.items() if k != "href"}))
    return converted, blacklist, issues


def supplement(target: dict[str, str], source: dict[str, str]) -> None:
    """Retain repository values; add missing data and all incoming name forms."""
    aliases = list(dict.fromkeys(v.strip() for v in [
        *target.get("keyword", "").split(","),
        *(source.get(k, "") for k in NAME_FIELDS),
        *source.get("keyword", "").split(","),
    ] if v.strip()))
    # Avoid introducing a redundant keyword field/change on an identical row.
    incoming_names = names(source) - names(target)
    if incoming_names:
        target["keyword"] = ",".join(aliases)
    for key, value in source.items():
        if value and not target.get(key):
            target[key] = value


def merge_records(entries: list[dict[str, str]], records: list[dict[str, str]]) -> dict:
    """Stage against immutable indexes so shared aliases cannot win by order."""
    by_name = defaultdict(set)
    by_primary = defaultdict(set)
    by_page = {}
    by_tmdb = {}
    existing_names = [names(entry) for entry in entries]
    for index, entry in enumerate(entries):
        for name in names(entry):
            by_name[name].add(index)
        for key in NAME_FIELDS:
            if entry.get(key):
                by_primary[entry[key]].add(index)
        if entry.get("page_id"):
            by_page[entry["page_id"]] = index
        if entry.get("tmdb_id"):
            by_tmdb[entry["tmdb_id"]] = index

    # Repeated source identities are one actor, even if names differ.
    combined: dict[tuple, dict[str, str]] = {}
    for row in sorted(records, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)):
        identity = (("page", row["page_id"]) if row.get("page_id") else
                    ("tmdb", row["tmdb_id"]) if row.get("tmdb_id") else
                    ("names", *(row.get(k, "") for k in NAME_FIELDS)))
        if identity in combined:
            previous = combined[identity]
            if previous.get("tmdb_id") and row.get("tmdb_id") and previous["tmdb_id"] != row["tmdb_id"]:
                raise ValueError(f"Source identity {identity} has conflicting tmdb_id values")
            supplement(previous, row)
        else:
            combined[identity] = dict(row)

    proposals = defaultdict(list)
    separate = []
    issues = []
    represented = 0
    for row in combined.values():
        page, tmdb = row.get("page_id"), row.get("tmdb_id")
        row_names = names(row)
        possible = ({by_page[page]} if page in by_page else
                    {by_tmdb[tmdb]} if tmdb in by_tmdb else
                    {i for name in row_names for i in by_name[name]})
        if any(row_names <= existing_names[i]
               and all(not row.get(key) or entries[i].get(key) == row[key]
                       for key in ("page_id", "tmdb_id"))
               and all(entries[i].get(key) for key in row
                       if key not in (*NAME_FIELDS, "keyword", "page_id", "tmdb_id"))
               for i in possible):
            represented += 1
            continue
        primary_names = {row[k] for k in NAME_FIELDS if row.get(k)}
        exact = {i for n in primary_names for i in by_primary[n]}
        trio = {i for i in exact if all(entries[i].get(k, "") == row.get(k, "") for k in NAME_FIELDS)}
        if page in by_page:
            candidates, rank = {by_page[page]}, 5
        elif tmdb in by_tmdb:
            candidates, rank = {by_tmdb[tmdb]}, 4
        elif trio:
            candidates, rank = trio, 3
        elif exact:
            candidates, rank = exact, 2
        else:
            candidates, rank = {i for n in names(row) for i in by_name[n]}, 1
        # Names alone cannot collapse distinct known page or TMDB identities.
        candidates = {i for i in candidates if not (
            (page and entries[i].get("page_id") and page != entries[i]["page_id"])
            or (tmdb and entries[i].get("tmdb_id") and tmdb != entries[i]["tmdb_id"])
        )}
        if len(candidates) == 1:
            index = next(iter(candidates))
            overlap = len(primary_names & {entries[index].get(k, "") for k in NAME_FIELDS})
            proposals[index].append((rank, overlap, row))
        elif not candidates:
            separate.append(row)
        else:
            # Preserve uncertain rows independently rather than guessing an owner.
            separate.append(row)
            issues.append(dict(reason="ambiguous_names_kept_separate", source=row,
                               candidates=[entries[i] for i in sorted(candidates)]))

    updated = 0
    for index, group in proposals.items():
        pages = {row.get("page_id") for _, _, row in group if row.get("page_id")}
        tmdbs = {row.get("tmdb_id") for _, _, row in group if row.get("tmdb_id")}
        ranked = sorted(group, key=lambda x: (-x[0], -x[1], json.dumps(x[2], sort_keys=True)))
        best = ranked[0]
        tied = [row for rank, overlap, row in group if (rank, overlap) == best[:2]]
        tied_pages = {row.get("page_id") for row in tied if row.get("page_id")}
        tied_tmdbs = {row.get("tmdb_id") for row in tied if row.get("tmdb_id")}
        chosen_page = (next(iter(tied_pages)) if len(tied_pages) == 1 else None)
        chosen_tmdb = (next(iter(tied_tmdbs)) if len(tied_tmdbs) == 1 else None)
        before = dict(entries[index])
        for _, _, row in ranked:
            if ((len(pages) > 1 and row.get("page_id") and row["page_id"] != chosen_page)
                    or (len(tmdbs) > 1 and row.get("tmdb_id") and row["tmdb_id"] != chosen_tmdb)):
                separate.append(row)
                issues.append(dict(reason="distinct_identity_kept_separate", source=row,
                                   target=dict(before)))
                continue
            supplement(entries[index], row)
        updated += entries[index] != before

    # Validate the whole result before writing, including identities added by
    # separate/ambiguous rows and collisions between existing and incoming IDs.
    entries.extend(dict(row) for row in separate)
    node = ET.Element("actor")
    for entry in entries:
        ET.SubElement(node, "a", entry)
    validate_page_ids(node)
    validate_unique_tmdb_ids(node)
    return dict(source_count=len(records), merged_count=len(combined) - len(separate),
                already_represented=represented,
                updated=updated, added=len(separate), issues=issues)


def audit_coverage(entries: list[dict[str, str]], records: list[dict[str, str]]) -> dict:
    """Require every source name/alias to remain on its own saved identity."""
    by_name = defaultdict(set)
    by_page = {}
    by_tmdb = {}
    entry_names = [names(entry) for entry in entries]
    for index, entry in enumerate(entries):
        for name in entry_names[index]:
            by_name[name].add(index)
        if entry.get("page_id"):
            by_page[entry["page_id"]] = index
        if entry.get("tmdb_id"):
            by_tmdb[entry["tmdb_id"]] = index
    missing = []
    for row in records:
        source_names = names(row)
        if row.get("page_id"):
            candidates = {by_page[row["page_id"]]} if row["page_id"] in by_page else set()
        elif row.get("tmdb_id"):
            candidates = {by_tmdb[row["tmdb_id"]]} if row["tmdb_id"] in by_tmdb else set()
        else:
            candidates = {i for name in source_names for i in by_name[name]}
        if not any(source_names <= entry_names[i]
                   and all(not row.get(key) or entries[i].get(key) == row[key]
                           for key in ("page_id", "tmdb_id")) for i in candidates):
            missing.append(row)
    return dict(checked=len(records), missing_count=len(missing), missing=missing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, nargs="+", help="AVDB DB, XML export, legacy XML or collector JSON")
    parser.add_argument("--xml", type=Path, default=Path("actor-mapping.xml"))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report", type=Path, default=Path(".cache/avdb-merge-report.json"))
    args = parser.parse_args()
    try:
        inputs = [args.xml, *args.sources]
        if args.report.resolve() in {p.resolve() for p in inputs}:
            raise ValueError("Report must not overwrite a source or mapping file")
        if any(p.resolve() == args.xml.resolve() for p in args.sources):
            raise ValueError("Input sources must be separate from the output mapping file")
        root = ET.fromstring(args.xml.read_text(encoding="utf-8"))
        actor = get_actor_node(root)
        validate_page_ids(actor)
        validate_unique_tmdb_ids(actor)
        validate_unique_actor_names(actor)
        entries = [dict(entry.attrib) for entry in actor]
        originals = [dict(entry) for entry in entries]
        reports = []
        source_records = []
        blacklist = collect_blacklist_values(root)
        for source in args.sources:
            records, incoming_blacklist, conversion_issues = load_source(source)
            source_records.append(records)
            report = merge_records(entries, records)
            report["issues"].extend(conversion_issues)
            reports.append(dict(source=str(source), **report))
            blacklist.extend(incoming_blacklist)
        merged_root = ET.Element("actor-mapping")
        node = ET.SubElement(merged_root, "actor")
        for entry in entries:
            ET.SubElement(node, "a", entry)
        ET.SubElement(merged_root, "actor-blacklist").text = "\n".join(blacklist)
        normalized = build_normalized_xml(ET.tostring(merged_root, encoding="unicode"))
        if find_suspicious_escapes(normalized):
            raise ValueError("Merged data contains suspicious escaped strings; no mappings written")
        final_entries = [dict(entry.attrib) for entry in get_actor_node(ET.fromstring(normalized))]
        original_coverage = audit_coverage(final_entries, originals)
        for report, records in zip(reports, source_records):
            report["coverage"] = audit_coverage(final_entries, records)
        result = dict(mode="written" if args.write else "dry_run", actors=len(entries),
                      page_ids=sum(bool(row.get("page_id")) for row in entries),
                      original_coverage=original_coverage, sources=reports)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if original_coverage["missing_count"] or any(r["coverage"]["missing_count"] for r in reports):
            raise ValueError(f"Coverage audit failed; no mappings written. See {args.report}")
        if args.write:
            args.xml.write_text(normalized, encoding="utf-8")
        print(json.dumps({**result, "sources": [{**r, "issues": len(r["issues"])} for r in reports]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, ET.ParseError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
