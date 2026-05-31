#!/usr/bin/env python3
"""Normalize actor-mapping.xml by sorting <a> entries and checking escaped strings. AVdb 1.0.0"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple, TypeAlias
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'
PREFERRED_ATTR_ORDER: Sequence[str] = ("zh_cn", "zh_tw", "jp", "keyword", "tmdb_id")
SUSPICIOUS_ESCAPE_RE = re.compile(
    r"(\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|\\[nrtfv]|&amp;(?:amp|lt|gt|quot|apos);|&#x[0-9a-fA-F]+;|&#\d+;)"
)
DIGIT_SPLIT_RE = re.compile(r"(\d+)")

NaturalChunk: TypeAlias = Tuple[int, int | str]
NaturalKey: TypeAlias = Tuple[NaturalChunk, ...]
SortKey: TypeAlias = Tuple[NaturalKey, NaturalKey, NaturalKey, NaturalKey, NaturalKey, NaturalKey]


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
        help="Fail if formatting or escape checks are not satisfied.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write normalized XML content back to file.",
    )
    return parser.parse_args()


def normalize_newlines(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def natural_key(value: str) -> NaturalKey:
    parts = DIGIT_SPLIT_RE.split(value.casefold())
    key: List[NaturalChunk] = []
    for part in parts:
        if part == "":
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
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
    return decoded.strip()


def render_entry(attributes: Dict[str, str]) -> str:
    parts: List[str] = []
    for key, value in ordered_attributes(attributes):
        normalized = normalize_attribute_value(value)
        escaped = xml_escape(normalized, {'"': "&quot;"})
        parts.append(f'{key}="{escaped}"')

    return f"  <a {' '.join(parts)} />"


def sort_key_for_entry(attributes: Dict[str, str], rendered_line: str) -> SortKey:
    return (
        natural_key(attributes.get("zh_cn", "")),
        natural_key(attributes.get("zh_tw", "")),
        natural_key(attributes.get("jp", "")),
        natural_key(attributes.get("keyword", "")),
        natural_key(attributes.get("tmdb_id", "")),
        natural_key(rendered_line),
    )


def build_normalized_xml(raw_xml: str) -> str:
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    if root.tag != "actor":
        raise ValueError(f"Root element must be <actor>, got <{root.tag}>.")

    rendered_entries: List[Tuple[SortKey, str]] = []

    for child in list(root):
        if child.tag != "a":
            raise ValueError(f"Only <a> children are supported, got <{child.tag}>.")

        attrs = dict(child.attrib)
        rendered_line = render_entry(attrs)
        rendered_entries.append((sort_key_for_entry(attrs, rendered_line), rendered_line))

    rendered_entries.sort(key=lambda item: item[0])

    lines = [XML_DECLARATION, "<actor>"]
    lines.extend(line for _, line in rendered_entries)
    lines.append("</actor>")
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

    try:
        normalized_xml = build_normalized_xml(normalized_original)
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
