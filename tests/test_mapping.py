import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from format_full_mapping import build_normalized_xml
from merge_avdb_mapping import audit_coverage, convert_record, load_source, merge_records, names
from resolve_duplicate_person_ids import append_entries, collect_xml_names


def row(name, **kwargs):
    return dict(zh_cn=name, zh_tw=name, jp=name, keyword=name, **kwargs)


class MappingTests(unittest.TestCase):
    def test_primary_names_are_unique_across_rows_and_language_fields(self):
        for first in ("zh_cn", "zh_tw", "jp"):
            for second in ("zh_cn", "zh_tw", "jp"):
                with self.subTest(first=first, second=second):
                    with self.assertRaisesRegex(ValueError, "Duplicate actor names"):
                        build_normalized_xml(f'<actor><a {first}="重名" page_id="One"/><a {second}="重名" page_id="Two"/></actor>')

    def test_same_row_names_and_shared_keywords_are_allowed(self):
        build_normalized_xml('<actor><a zh_cn="甲" zh_tw="甲" jp="甲" keyword="共用别名"/><a zh_cn="乙" keyword="甲,共用别名"/></actor>')

    def test_name_uniqueness_uses_rendered_values_and_casefold(self):
        for first, second in ((" RINA ", "rina"), ("A&amp;amp;B", "A&amp;B"), ("&#x200C;甲", "甲"), ("A&#160;B", "A B")):
            with self.subTest(first=first, second=second):
                with self.assertRaisesRegex(ValueError, "Duplicate actor names"):
                    build_normalized_xml(f'<actor><a zh_cn="{first}"/><a jp="{second}"/></actor>')

    def test_name_conflict_never_overwrites_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actor-mapping.xml"
            simple = path.with_name("mapping_actor.xml")
            path.write_text('<actor><a zh_cn="甲" page_id="One"/><a jp="甲" page_id="Two"/></actor>')
            before = path.read_bytes()
            result = subprocess.run([sys.executable, "scripts/format_full_mapping.py", "--write", str(path)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"Duplicate actor names", result.stdout)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(simple.exists())

    def test_merge_rejects_same_name_distinct_ids_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actor-mapping.xml"
            simple = path.with_name("mapping_actor.xml")
            source = path.with_name("source.xml")
            path.write_text('<actor><a zh_cn="同名" page_id="One" tmdb_id="1"/></actor>')
            source.write_text('<actor><a jp="同名" page_id="Two" tmdb_id="2"/></actor>')
            before = path.read_bytes()
            result = subprocess.run([
                sys.executable, "scripts/merge_avdb_mapping.py", str(source), "--xml", str(path),
                "--report", str(path.with_name("report.json")), "--write",
            ], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"Duplicate actor names", result.stderr)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(simple.exists())

    def test_page_ids_preserved_in_full_mapping(self):
        full = build_normalized_xml('<actor><a zh_cn="演员" keyword="A&amp;B" page_id="k2Ne" tmdb_id="12"/></actor>')
        self.assertEqual(ET.fromstring(full).find(".//a").get("page_id"), "k2Ne")
        self.assertLess(full.index('page_id='), full.index('tmdb_id='))
        self.assertEqual(ET.fromstring(full).find(".//a").get("keyword"), "A&B")

    def test_invisible_characters_are_cleaned_in_names_aliases_and_biography(self):
        raw = '<actor><a zh_cn="甲&#x200B;&#x200C;&#x200D;&#x200E;&#x200F;" jp="&#xFEFF;甲" keyword="甲,&#x2060;Jane&#xA0;Doe" bio_graphy="word&#xAD;break&#xA0;here"/></actor>'
        entry = ET.fromstring(build_normalized_xml(raw)).find(".//a")
        self.assertEqual(entry.attrib, dict(zh_cn="甲", jp="甲", keyword="甲,Jane Doe", bio_graphy="wordbreak here"))
        self.assertEqual(names(dict(zh_cn="\u200c甲", keyword="Jane\u00a0Doe")), {"甲", "Jane Doe"})

    def test_invalid_page_ids_and_duplicate_ids_fail(self):
        for value in ("", "../bad", "https://javdb.com/actors/A", "A B", "汉字", "x" * 121):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_normalized_xml(f'<actor><a zh_cn="甲" page_id="{value}"/></actor>')
        with self.assertRaisesRegex(ValueError, "Duplicate page_id"):
            build_normalized_xml('<actor><a page_id="Ab"/><a page_id="Ab"/></actor>')
        build_normalized_xml('<actor><a page_id="Ab"/><a page_id="ab"/></actor>')

    def test_coverage_checks_aliases_on_the_correct_identity(self):
        source = [dict(zh_cn="甲", keyword="丢失别名", page_id="Ab")]
        entries = [row("甲", page_id="Ab"), dict(zh_cn="乙", keyword="丢失别名", page_id="Cd")]
        self.assertEqual(audit_coverage(entries, source)["missing_count"], 1)
        entries[0]["keyword"] += ",丢失别名"
        self.assertEqual(audit_coverage(entries, source)["missing_count"], 0)

    def test_legacy_multi_name_field_preserves_each_alias(self):
        converted = convert_record(dict(zh_cn="八束美琴", jp="八束みこと,八束美琴,", keyword=",八束美琴,"))
        self.assertEqual(converted["jp"], "八束みこと")
        self.assertEqual(names(converted), {"八束美琴", "八束みこと"})

    def test_legacy_and_collector_conversion(self):
        converted = convert_record(dict(name="甲", aliases=["别名"], id="Ab1", href="https://javdb.com/actors/Ab1"))
        self.assertEqual(converted["page_id"], "Ab1")
        self.assertTrue({"甲", "别名"} <= names(converted))
        self.assertNotIn("id", converted)
        self.assertNotIn("href", converted)
        self.assertNotIn("page_id", convert_record(dict(zh_cn="甲", id=123)))
        with self.assertRaises(ValueError):
            convert_record(dict(name="甲", id="Other", href="https://javdb.com/actors/Ab1"))
        with self.assertRaises(ValueError):
            convert_record(dict(zh_cn="甲", href="https://evil.test/actors/Ab1"))

    def test_merge_preserves_existing_data_and_is_idempotent(self):
        entries = [row("甲", tmdb_id="1", bio_graphy="原简介", custom="保留")]
        source = [dict(zh_cn="新显示名", jp="甲", keyword="新别名", page_id="Ab", tmdb_id="1", bio_graphy="其他简介")]
        report = merge_records(entries, source)
        self.assertEqual(report["added"], 0)
        self.assertEqual(entries[0]["bio_graphy"], "原简介")
        self.assertEqual(entries[0]["custom"], "保留")
        self.assertEqual(entries[0]["zh_cn"], "甲")
        self.assertTrue({"新别名", "新显示名"} <= names(entries[0]))
        before = json.dumps(entries)
        self.assertEqual(merge_records(entries, source)["updated"], 0)
        self.assertEqual(json.dumps(entries), before)

    def test_page_identity_wins_over_shared_aliases(self):
        entries = [row("甲", page_id="A"), row("乙", page_id="B")]
        merge_records(entries, [dict(zh_cn="不同显示名", keyword="甲,乙,新别名", page_id="B")])
        self.assertNotIn("新别名", names(entries[0]))
        self.assertIn("新别名", names(entries[1]))

    def test_distinct_pages_are_not_collapsed_and_order_is_stable(self):
        source = [row("甲", page_id="One"), dict(zh_cn="别名甲", keyword="甲", page_id="Two")]
        first, second = [row("甲")], [row("甲")]
        merge_records(first, source)
        merge_records(second, source[::-1])
        self.assertEqual(first, second)
        self.assertEqual({r.get("page_id") for r in first}, {"One", "Two"})
        count = len(first)
        merge_records(first, source)
        self.assertEqual(len(first), count)

    def test_ambiguous_names_preserved_separately(self):
        entries = [row("甲"), row("乙")]
        source = [dict(zh_cn="丙", keyword="甲,乙", page_id="C")]
        report = merge_records(entries, source)
        self.assertEqual(report["issues"][0]["reason"], "ambiguous_names_kept_separate")
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["page_id"], "C")
        self.assertEqual(merge_records(entries, source)["added"], 0)

    def test_conflicting_stable_ids_cannot_silently_merge(self):
        entries = [row("甲", page_id="A", tmdb_id="1"), row("乙", page_id="B", tmdb_id="2")]
        with self.assertRaises(ValueError):
            merge_records(entries, [row("甲", page_id="A", tmdb_id="2")])

    def test_both_xml_roots_and_bad_legacy_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.xml"
            for xml in ('<actor><a zh_cn="甲" href="https://cnmdb.net/tag/test"/></actor>',
                        '<actor-mapping><actor><a zh_cn="甲" href="https://cnmdb.net/tag/test"/></actor></actor-mapping>'):
                path.write_text(xml)
                records, _, issues = load_source(path)
                self.assertEqual(len(records), 1)
                self.assertNotIn("page_id", records[0])
                self.assertEqual(len(issues), 1)
                self.assertEqual(issues[0]["reason"], "legacy_href_ignored")

    def test_repeat_after_another_source_adds_a_shared_name(self):
        entries = [row("甲")]
        original = [dict(zh_cn="甲", keyword="甲,别名", bio_graphy="简介")]
        merge_records(entries, original)
        merge_records(entries, [dict(zh_cn="乙", keyword="甲,别名", page_id="Another")])
        before = json.dumps(entries)
        self.assertEqual(merge_records(entries, original)["added"], 0)
        self.assertEqual(json.dumps(entries), before)

    def test_sqlite_readonly_and_export_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "avdb.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE actor (id INTEGER, zh_cn TEXT, tmdb_source TEXT, page_id TEXT)")
            connection.executemany("INSERT INTO actor VALUES (?, ?, ?, ?)",
                                   [(1, "甲", "online", "Ab"), (2, "乙", "emby", "Cd"), (3, "丙", "external", "Ef")])
            connection.commit()
            connection.close()
            before = path.read_bytes()
            records, _, _ = load_source(path)
            self.assertEqual([r["page_id"] for r in records], ["Ab"])
            self.assertEqual(path.read_bytes(), before)

    def test_tmdb_tool_updates_only_the_full_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actor-mapping.xml"
            path.write_text('<actor-mapping><actor><a zh_cn="甲" keyword="别名" page_id="Ab"/></actor><actor-blacklist>保护</actor-blacklist></actor-mapping>')
            self.assertEqual(collect_xml_names(path), {"甲", "别名"})
            append_entries(path, [("乙", "12")])
            full = path.read_text()
            self.assertIn('page_id="Ab"', full)
            self.assertIn("保护", full)
            self.assertIn('tmdb_id="12"', full)
            self.assertFalse(path.with_name("mapping_actor.xml").exists())
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                append_entries(path, [("丙", "12")])
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_source_never_overwrites_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actor-mapping.xml"
            simple = path.with_name("mapping_actor.xml")
            source = path.with_name("source.xml")
            path.write_text('<actor><a zh_cn="甲" page_id="Keep"/></actor>')
            source.write_text('<actor><a zh_cn="乙" page_id="../bad"/></actor>')
            before = path.read_bytes()
            command = [sys.executable, "scripts/merge_avdb_mapping.py", str(source), "--xml", str(path), "--write"]
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(simple.exists())


if __name__ == "__main__":
    unittest.main()
