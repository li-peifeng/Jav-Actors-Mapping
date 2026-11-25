#!/usr/bin/env python3
"""Normalize and validate actor-mapping.xml."""

from __future__ import annotations
import argparse
from functools import lru_cache
import html
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple, TypeAlias
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from pypinyin import Style, lazy_pinyin

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'
NAME_FIELDS: Sequence[str] = ("zh_cn", "zh_tw", "jp")
PREFERRED_ATTR_ORDER: Sequence[str] = (
    "zh_cn",
    "zh_tw",
    "jp",
    "keyword",
    "page_id",
    "tmdb_id",
    "dackdrop",
    "bio_graphy",
)
SUSPICIOUS_ESCAPE_RE = re.compile(
    r"(\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|\\[nrtfv]|&amp;(?:amp|lt|gt|quot|apos);|&#x[0-9a-fA-F]+;|&#\d+;)"
)
DIGIT_SPLIT_RE = re.compile(r"(\d+)")
ACTOR_TEXT_TRANSLATION = str.maketrans({
    **dict.fromkeys("\u200b\u200c\u200d\u200e\u200f\ufeff\u2060\u00ad"),
    "\u00a0": " ",
})

EncodedTextPart: TypeAlias = Tuple[int, ...]
TextCharKey: TypeAlias = Tuple[int, EncodedTextPart, int]
TextKey: TypeAlias = Tuple[TextCharKey, ...]
NaturalChunk: TypeAlias = Tuple[int, int | TextKey]
NaturalKey: TypeAlias = Tuple[NaturalChunk, ...]
FieldSortKey: TypeAlias = Tuple[int, NaturalKey]
SortKey: TypeAlias = Tuple[
    FieldSortKey,
    FieldSortKey,
    FieldSortKey,
    FieldSortKey,
    FieldSortKey,
    NaturalKey,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sort entries inside <actor> with natural order and normalize escaped strings."
        )
    )
    parser.add_argument(
        "file",
        nargs="?",
        default="actor-mapping.xml",
        help="Path to actor mapping XML file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if formatting or escape checks fail.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write normalized XML.",
    )
    parser.add_argument(
        "--adult-person-ids",
        default=None,
        help="Path to adult_person_ids.json for tmdb_id synchronization.",
    )
    return parser.parse_args()


def normalize_newlines(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def natural_key(value: str) -> NaturalKey:
    parts = DIGIT_SPLIT_RE.split(value)
    key: List[NaturalChunk] = []
    for part in parts:
        if part == "":
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, text_key(part)))
    return tuple(key)


def is_cjk_han(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0x2CEB0 <= code <= 0x2EBEF
        or 0x30000 <= code <= 0x3134F
    )


def is_japanese_kana(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9F
    )


def script_bucket_for_char(char: str) -> int:
    if is_cjk_han(char):
        return 0
    if ("a" <= char <= "z") or ("A" <= char <= "Z"):
        return 1
    if is_japanese_kana(char):
        return 2
    return 3


def script_bucket_for_text(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 3

    # Ignore leading punctuation/symbols so entries like "【あいちゃん" follow Japanese order.
    for char in stripped:
        if char.isspace() or not char.isalnum():
            continue
        return script_bucket_for_char(char)

    for char in stripped:
        if not char.isspace():
            return script_bucket_for_char(char)

    return 3


def gbk_han_fallback_key(char: str) -> EncodedTextPart:
    try:
        encoded = char.encode("gbk")
        return (1, *encoded)
    except UnicodeEncodeError:
        return (2, ord(char))


@lru_cache(maxsize=None)
def han_order_key(char: str) -> EncodedTextPart:
    # Use pinyin for Han ordering so Chinese block starts from a/阿-like order.
    py = "".join(lazy_pinyin(char, style=Style.NORMAL, strict=False, errors="default"))
    if py and py != char:
        ascii_bytes = py.casefold().encode("ascii", errors="ignore")
        if ascii_bytes:
            # Add a separator so "a" sorts before "ai".
            return (0, *ascii_bytes, -1, ord(char))

    # Fallback for unknown/ext chars.
    return gbk_han_fallback_key(char)


def text_key(value: str) -> TextKey:
    key: List[TextCharKey] = []
    for char in value:
        if ("a" <= char <= "z") or ("A" <= char <= "Z"):
            # Enforce aA-bB-...-zZ order for ASCII letters.
            key.append((0, (ord(char.lower()),), 0 if char.islower() else 1))
        elif is_cjk_han(char):
            key.append((1, han_order_key(char), 0))
        else:
            key.append((2, (ord(char),), 0))
    return tuple(key)


def ordered_attributes(attributes: Dict[str, str]) -> List[Tuple[str, str]]:
    ordered: List[Tuple[str, str]] = []
    consumed: Set[str] = set()

    for attr_name in PREFERRED_ATTR_ORDER:
        if attr_name in attributes:
            ordered.append((attr_name, attributes[attr_name]))
            consumed.add(attr_name)

    for attr_name in sorted(attributes.keys()):
        if attr_name not in consumed:
            ordered.append((attr_name, attributes[attr_name]))

    return ordered


def normalize_attribute_value(value: str) -> str:
    # Decode existing entities first so re-escaping becomes deterministic.
    decoded = html.unescape(value)
    # Keep each XML record single-line by stripping hard line/control chars.
    decoded = decoded.replace("\r", "").replace("\n", "").replace("\t", "")
    decoded = re.sub(r"[\x00-\x1F\x7F]", "", decoded)
    return decoded.translate(ACTOR_TEXT_TRANSLATION).strip()


def render_entry(attributes: Dict[str, str]) -> str:
    parts: List[str] = []
    for key, value in ordered_attributes(attributes):
        normalized = normalize_attribute_value(value)
        escaped = xml_escape(normalized, {'"': "&quot;"})
        parts.append(f'{key}="{escaped}"')

    return f"    <a {' '.join(parts)} />"


def split_blacklist_values(value: str) -> List[str]:
    return [item.strip() for item in re.split(r"[\n,;|]+", value or "") if item.strip()]


def collect_blacklist_values(root: ET.Element) -> List[str]:
    values: List[str] = []
    seen: Set[str] = set()
    for blacklist_node in root.findall(".//actor-blacklist"):
        raw_values = split_blacklist_values(blacklist_node.text or "")
        for child in list(blacklist_node):
            raw_values.extend(
                split_blacklist_values(
                    child.text or child.attrib.get("name") or child.attrib.get("value") or ""
                )
            )
        for value in raw_values:
            normalized = normalize_attribute_value(value)
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(normalized)
    return values


def render_blacklist(values: List[str]) -> List[str]:
    lines = ["  <actor-blacklist>"]
    for value in sorted(values, key=natural_key):
        lines.append(f"    {xml_escape(value)}")
    lines.append("  </actor-blacklist>")
    return lines


def get_actor_node(root: ET.Element) -> ET.Element:
    if root.tag == "actor":
        return root
    if root.tag != "actor-mapping":
        raise ValueError(f"Root element must be <actor> or <actor-mapping>, got <{root.tag}>.")
    actor_nodes = [child for child in list(root) if child.tag == "actor"]
    if len(actor_nodes) != 1:
        raise ValueError(f"<actor-mapping> must contain exactly one <actor> child, got {len(actor_nodes)}.")
    return actor_nodes[0]


def load_person_ids(file_path: Path) -> List[Dict[str, str]]:
    people: List[Dict[str, str]] = []
    with file_path.open(encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON line in {file_path}: {exc}") from exc
            name = record.get("name")
            person_id = record.get("id")
            if name is None or person_id is None:
                continue
            people.append({"name": str(name).strip(), "id": str(person_id)})
    return people


def split_keyword_values(keyword_value: str) -> List[str]:
    return [name for value in keyword_value.split(",") if (name := normalize_attribute_value(value))]


def merge_person_ids(root: ET.Element, person_ids: List[Dict[str, str]]) -> List[Tuple[str, str, str, str]]:
    exact_index: Dict[str, ET.Element] = {}
    keyword_index: Dict[str, ET.Element] = {}

    for child in list(root):
        if child.tag != "a":
            continue
        for field in ("zh_cn", "zh_tw", "jp"):
            field_value = normalize_attribute_value(child.attrib.get(field, ""))
            if field_value:
                exact_index.setdefault(field_value, child)
        keywords = split_keyword_values(child.attrib.get("keyword", ""))
        for keyword in keywords:
            keyword_index.setdefault(keyword, child)

    changes: List[Tuple[str, str, str, str]] = []
    for person in person_ids:
        name = normalize_attribute_value(person["name"])
        person_id = person["id"]
        entry = exact_index.get(name)
        match_kind = "exact"
        if entry is None:
            entry = keyword_index.get(name)
            match_kind = "keyword" if entry is not None else "none"

        if entry is None:
            continue

        current_tmdb = entry.attrib.get("tmdb_id", "").strip()
        if current_tmdb != person_id:
            entry.attrib["tmdb_id"] = person_id
            changes.append((name, current_tmdb, person_id, match_kind))

    return changes


def entry_display_name(attributes: Dict[str, str]) -> str:
    for field in ("zh_cn", "zh_tw", "jp", "keyword"):
        value = attributes.get(field, "").strip()
        if value:
            return value
    return "<unnamed>"


def find_duplicate_tmdb_ids(root: ET.Element) -> List[Tuple[str, List[str]]]:
    entries_by_tmdb_id: Dict[str, List[str]] = {}

    for child in list(root):
        if child.tag != "a":
            continue
        attrs = dict(child.attrib)
        tmdb_id = normalize_attribute_value(attrs.get("tmdb_id", ""))
        if not tmdb_id:
            continue
        entries_by_tmdb_id.setdefault(tmdb_id, []).append(entry_display_name(attrs))

    return [
        (tmdb_id, names)
        for tmdb_id, names in entries_by_tmdb_id.items()
        if len(names) > 1
    ]


def validate_unique_tmdb_ids(root: ET.Element) -> None:
    duplicates = find_duplicate_tmdb_ids(root)
    if not duplicates:
        return

    preview_lines: List[str] = []
    max_preview = 20
    for tmdb_id, names in duplicates[:max_preview]:
        preview_lines.append(f"  tmdb_id={tmdb_id}: {', '.join(names)}")

    remaining = len(duplicates) - max_preview
    if remaining > 0:
        preview_lines.append(f"  ... and {remaining} more")

    details = "\n".join(preview_lines)
    raise ValueError(f"Duplicate tmdb_id values found; tmdb_id must be unique:\n{details}")


def validate_page_ids(root: ET.Element) -> None:
    """JavDB page identities are case-sensitive short IDs, never URLs or DB IDs."""
    owners: Dict[str, str] = {}
    for entry in root:
        if entry.tag != "a" or "page_id" not in entry.attrib:
            continue
        page_id = entry.attrib["page_id"]
        name = entry_display_name(entry.attrib)
        if not re.fullmatch(r"[A-Za-z0-9]{1,120}", page_id):
            raise ValueError(f"Invalid page_id={page_id!r} for {name}; use a 1-120 character alphanumeric short ID.")
        if page_id in owners:
            raise ValueError(f"Duplicate page_id={page_id}: {owners[page_id]}, {name}; page_id must be unique.")
        owners[page_id] = name


def validate_unique_actor_names(root: ET.Element) -> None:
    """Each primary name belongs to one row, including cross-field matches.

    Repeating a name in multiple language fields of the same row is valid.
    Match AVDB's case-insensitive name lookup after output normalization.
    Keywords remain aliases and are not unique identifiers.
    """
    owners: Dict[str, Tuple[int, str]] = {}
    duplicates: List[str] = []
    for index, entry in enumerate(root, start=1):
        if entry.tag != "a":
            continue
        for field in NAME_FIELDS:
            value = normalize_attribute_value(entry.get(field, ""))
            if not value:
                continue
            key = value.casefold()
            previous = owners.get(key)
            if previous is not None and previous[0] != index:
                duplicates.append(f"  {value!r}: entry {previous[0]} ({previous[1]}) and entry {index} ({field})")
            else:
                owners.setdefault(key, (index, field))
    if duplicates:
        preview = "\n".join(duplicates[:20])
        if len(duplicates) > 20:
            preview += f"\n  ... and {len(duplicates) - 20} more"
        raise ValueError(f"Duplicate actor names found; zh_cn, zh_tw and jp must be unique across entries:\n{preview}")


def sort_key_for_entry(attributes: Dict[str, str], rendered_line: str) -> SortKey:
    return (
        (script_bucket_for_text(attributes.get("zh_cn", "")), natural_key(attributes.get("zh_cn", ""))),
        (script_bucket_for_text(attributes.get("zh_tw", "")), natural_key(attributes.get("zh_tw", ""))),
        (script_bucket_for_text(attributes.get("jp", "")), natural_key(attributes.get("jp", ""))),
        (script_bucket_for_text(attributes.get("keyword", "")), natural_key(attributes.get("keyword", ""))),
        (script_bucket_for_text(attributes.get("tmdb_id", "")), natural_key(attributes.get("tmdb_id", ""))),
        natural_key(rendered_line),
    )


def build_normalized_xml(raw_xml: str, person_ids: List[Dict[str, str]] | None = None) -> str:
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    actor_node = get_actor_node(root)
    blacklist_values = collect_blacklist_values(root)

    if person_ids is not None:
        merge_person_ids(actor_node, person_ids)

    validate_unique_tmdb_ids(actor_node)
    validate_page_ids(actor_node)
    validate_unique_actor_names(actor_node)

    rendered_entries: List[Tuple[SortKey, str]] = []

    for child in list(actor_node):
        if child.tag != "a":
            raise ValueError(f"Only <a> children are supported, got <{child.tag}>.")

        attrs = dict(child.attrib)
        rendered_line = render_entry(attrs)
        sort_attrs = {key: value.translate(ACTOR_TEXT_TRANSLATION) for key, value in attrs.items()}
        rendered_entries.append((sort_key_for_entry(sort_attrs, rendered_line), rendered_line))

    rendered_entries.sort(key=lambda item: item[0])

    lines = [XML_DECLARATION, "<actor-mapping>", "  <actor>"]
    lines.extend(line for _, line in rendered_entries)
    lines.append("  </actor>")
    lines.extend(render_blacklist(blacklist_values))
    lines.append("</actor-mapping>")
    return "\n".join(lines) + "\n"


def find_suspicious_escapes(text: str) -> List[Tuple[int, str]]:
    issues: List[Tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in SUSPICIOUS_ESCAPE_RE.finditer(line):
            issues.append((line_number, match.group(0)))
    return issues


def print_suspicious_issues(issues: Iterable[Tuple[int, str]]) -> None:
    issues_list = list(issues)
    if not issues_list:
        return

    print("Suspicious escaped strings found:")
    max_preview = 30
    for line_number, fragment in issues_list[:max_preview]:
        print(f"  line {line_number}: {fragment}")

    remaining = len(issues_list) - max_preview
    if remaining > 0:
        print(f"  ... and {remaining} more")


def main() -> int:
    args = parse_args()
    xml_path = Path(args.file)

    if not xml_path.exists():
        print(f"File not found: {xml_path}")
        return 1

    original_text = xml_path.read_text(encoding="utf-8")
    normalized_original = normalize_newlines(original_text)

    person_ids: List[Dict[str, str]] | None = None
    if args.adult_person_ids:
        person_ids_path = Path(args.adult_person_ids)
        if not person_ids_path.exists():
            print(f"Person ID file not found: {person_ids_path}")
            return 1
        try:
            person_ids = load_person_ids(person_ids_path)
        except ValueError as exc:
            print(str(exc))
            return 1

    try:
        normalized_xml = build_normalized_xml(normalized_original, person_ids)
    except ValueError as exc:
        print(str(exc))
        return 1

    has_format_diff = normalized_xml != normalized_original
    if args.write and has_format_diff:
        xml_path.write_text(normalized_xml, encoding="utf-8", newline="\n")
        print(f"Updated {xml_path}")
    elif has_format_diff:
        print(f"Formatting required: {xml_path}")

    # Check escaped strings against normalized output to avoid false positives from formatting.
    suspicious_issues = find_suspicious_escapes(normalized_xml)
    print_suspicious_issues(suspicious_issues)

    if args.check:
        format_failed = has_format_diff and not args.write
        if format_failed or suspicious_issues:
            return 1

    if not args.check and not args.write:
        sys.stdout.write(normalized_xml)

    return 0


if __name__ == "__main__":
    sys.exit(main())
