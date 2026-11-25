#!/usr/bin/env python3
"""Resolve duplicate TMDB person names by querying person details.

The script handles names that appear multiple times in adult_person_ids.json
with different ids. It chooses the most detailed TMDB person record among the
candidates. By default it requires both a non-empty biography and profile
image; with --allow-profile-only it can fall back to records with a profile
image but no biography.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from format_full_mapping import build_normalized_xml, get_actor_node


TMDB_API_BASE = "https://api.themoviedb.org/3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append duplicate-name TMDB people after online detail analysis."
    )
    parser.add_argument("--xml", default="actor-mapping.xml")
    parser.add_argument("--person-ids", default="adult_person_ids.json")
    parser.add_argument(
        "--cache",
        default=".cache/tmdb_person_details.json",
        help="JSON cache for TMDB person detail responses.",
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Resolve only from cached TMDB detail records without network requests.",
    )
    parser.add_argument(
        "--allow-profile-only",
        action="store_true",
        help="Allow candidates with profile image but no biography when no candidate has both.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Resolve at most N names.")
    parser.add_argument("--sleep", type=float, default=0.02, help="Delay between requests.")
    return parser.parse_args()


def load_person_id_groups(path: Path) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            name = str(record.get("name", "")).strip()
            person_id = record.get("id")
            if name and person_id is not None:
                groups[name].add(str(person_id))
    return groups


def collect_xml_names(path: Path) -> set[str]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for child in get_actor_node(root):
        if child.tag != "a":
            continue
        for attr in ("zh_cn", "zh_tw", "jp"):
            value = child.attrib.get(attr, "").strip()
            if value:
                names.add(value)
        for keyword in child.attrib.get("keyword", "").split(","):
            keyword = keyword.strip()
            if keyword:
                names.add(keyword)
    return names


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def tmdb_headers() -> dict[str, str]:
    bearer = os.environ.get("TMDB_BEARER_TOKEN") or os.environ.get("TMDB_ACCESS_TOKEN")
    if bearer:
        return {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    return {"Accept": "application/json"}


def tmdb_query_params() -> dict[str, str]:
    api_key = os.environ.get("TMDB_API_KEY")
    return {"api_key": api_key} if api_key else {}


def require_credentials() -> None:
    if not (
        os.environ.get("TMDB_BEARER_TOKEN")
        or os.environ.get("TMDB_ACCESS_TOKEN")
        or os.environ.get("TMDB_API_KEY")
    ):
        raise RuntimeError(
            "Missing TMDB credentials. Set TMDB_BEARER_TOKEN or TMDB_API_KEY."
        )


def fetch_person_detail(
    person_id: str,
    cache: dict[str, Any],
    sleep_seconds: float,
    cache_only: bool = False,
) -> dict[str, Any] | None:
    cache_key = f"person:{person_id}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    if cache_only:
        return None

    params = {
        "append_to_response": "external_ids,combined_credits",
        **tmdb_query_params(),
    }
    url = f"{TMDB_API_BASE}/person/{urllib.parse.quote(person_id)}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=tmdb_headers())
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        data = {"_error": f"HTTP {exc.code}", "id": person_id}
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error while fetching TMDB person {person_id}: {exc}") from exc

    cache[cache_key] = data
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    return data


def detail_score(
    detail: dict[str, Any],
    expected_name: str,
    allow_profile_only: bool = False,
) -> int:
    biography = str(detail.get("biography") or "").strip()
    profile_path = str(detail.get("profile_path") or "").strip()
    if not profile_path:
        return -1
    if not biography and not allow_profile_only:
        return -1

    also_known_as = detail.get("also_known_as") or []
    combined_credits = detail.get("combined_credits") or {}
    cast_count = len(combined_credits.get("cast") or [])
    crew_count = len(combined_credits.get("crew") or [])
    external_ids = detail.get("external_ids") or {}
    exact_name_bonus = 500 if detail.get("name") == expected_name else 0
    alias_bonus = 250 if expected_name in also_known_as else 0

    populated_fields = sum(
        1
        for key in ("birthday", "deathday", "place_of_birth", "known_for_department")
        if detail.get(key)
    )
    external_id_count = sum(1 for value in external_ids.values() if value)

    detail_tier = 1_000_000 if biography else 500_000

    return (
        detail_tier
        + exact_name_bonus
        + alias_bonus
        + min(len(biography), 4000)
        + len(also_known_as) * 20
        + populated_fields * 100
        + external_id_count * 75
        + min(cast_count + crew_count, 300)
        + int(float(detail.get("popularity") or 0) * 10)
    )


def choose_best(
    name: str,
    ids: set[str],
    cache: dict[str, Any],
    sleep_seconds: float,
    cache_only: bool = False,
    allow_profile_only: bool = False,
) -> tuple[str, dict[str, Any], int] | None:
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for person_id in sorted(ids, key=lambda value: int(value) if value.isdigit() else value):
        detail = fetch_person_detail(person_id, cache, sleep_seconds, cache_only)
        if detail is None:
            continue
        score = detail_score(detail, name, allow_profile_only)
        if score >= 0:
            scored.append((score, person_id, detail))
    if not scored:
        return None
    score, person_id, detail = max(scored, key=lambda item: (item[0], -int(item[1]) if item[1].isdigit() else 0))
    return person_id, detail, score


def render_entry(name: str, person_id: str) -> str:
    escaped_name = xml_escape(name.strip(), {'"': "&quot;"})
    escaped_id = xml_escape(person_id.strip(), {'"': "&quot;"})
    return (
        f'  <a zh_cn="{escaped_name}" zh_tw="{escaped_name}" jp="{escaped_name}" '
        f'keyword="{escaped_name}" tmdb_id="{escaped_id}" />\n'
    )


def append_entries(xml_path: Path, entries: list[tuple[str, str]]) -> None:
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    actor = get_actor_node(root)
    for name, person_id in entries:
        ET.SubElement(actor, "a", zh_cn=name, zh_tw=name, jp=name,
                      keyword=name, tmdb_id=person_id)
    # Validate IDs and normalize everything before touching the mapping.
    normalized = build_normalized_xml(ET.tostring(root, encoding="unicode"))
    xml_path.write_text(normalized, encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.cache_only:
        require_credentials()

    xml_path = Path(args.xml)
    groups = load_person_id_groups(Path(args.person_ids))
    existing_names = collect_xml_names(xml_path)
    duplicate_missing = [
        (name, ids)
        for name, ids in groups.items()
        if len(ids) > 1 and name not in existing_names
    ]
    if args.limit > 0:
        duplicate_missing = duplicate_missing[: args.limit]

    cache_path = Path(args.cache)
    cache = load_cache(cache_path)
    selected: list[tuple[str, str]] = []
    unresolved: list[tuple[str, list[str]]] = []

    try:
        for index, (name, ids) in enumerate(duplicate_missing, 1):
            best = choose_best(
                name,
                ids,
                cache,
                args.sleep,
                cache_only=args.cache_only,
                allow_profile_only=args.allow_profile_only,
            )
            if best is None:
                unresolved.append((name, sorted(ids)))
            else:
                person_id, detail, score = best
                selected.append((name, person_id))
                print(
                    f"{index}/{len(duplicate_missing)} selected {name}: {person_id} "
                    f"score={score} bio={len(str(detail.get('biography') or '').strip())} "
                    f"profile={detail.get('profile_path')}"
                )
            if index % 100 == 0:
                save_cache(cache_path, cache)
    finally:
        save_cache(cache_path, cache)

    print(f"duplicate_missing_names: {len(duplicate_missing)}")
    if args.allow_profile_only:
        print(f"selected_with_profile: {len(selected)}")
    else:
        print(f"selected_with_biography_and_profile: {len(selected)}")
    print(f"unresolved_without_required_details: {len(unresolved)}")

    if args.write and selected:
        append_entries(xml_path, selected)
        print(f"Appended {len(selected)} entries to {xml_path}")
    elif not args.write:
        print("Dry run only. Re-run with --write to append selected entries.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
