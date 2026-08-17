import json
import os
import tempfile
import unittest
from pathlib import Path

from paper_feed.db import SCHEMA_VERSION, PaperRepository, connect
from paper_feed.exporter import database_items
from paper_feed.importer import LegacyImporter


def write_json(root, name, value):
    path = os.path.join(root, "web", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle)


class PaperFeedStorageTests(unittest.TestCase):
    def test_doi_merges_sources_but_title_alone_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = PaperRepository(connect(os.path.join(directory, "p.sqlite3")))
            with repo.transaction():
                first = repo.resolve({"source": "a", "id": "a1", "link": "https://doi.org/10.1000/ABC", "title": "A"})
                same_doi = repo.resolve({"source": "b", "id": "b1", "link": "https://publisher.test/doi/10.1000/abc", "title": "Changed"})
                title_one = repo.resolve({"source": "c", "id": "c1", "title": "A"})
                title_two = repo.resolve({"source": "d", "id": "d1", "title": "A"})
            self.assertEqual(first, same_doi)
            self.assertNotEqual(title_one, title_two)
            repo.conn.close()

    def test_normalized_doi_merges_query_case_and_doi_prefix_forms(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = PaperRepository(connect(os.path.join(directory, "p.sqlite3")))
            with repo.transaction():
                plain = repo.resolve({"source": "one", "id": "one", "link": "https://doi.org/10.1000/ABC"})
                tracked = repo.resolve({"source": "two", "id": "two", "link": "https://doi.org/10.1000/abc?utm_source=rss#section"})
                prefixed = repo.resolve({"source": "three", "id": "three", "doi": "doi:10.1000/AbC"})
            self.assertEqual(plain, tracked)
            self.assertEqual(plain, prefixed)
            repo.conn.close()

    def test_guid_and_tracking_url_aliases_resolve_to_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = PaperRepository(connect(os.path.join(directory, "p.sqlite3")))
            with repo.transaction():
                first = repo.resolve({"source": "journal", "id": "guid", "link": "https://x.test/article/?utm_source=rss"})
                by_guid = repo.resolve({"source": "journal", "id": "guid", "title": "new"})
                by_url = repo.resolve({"source": "other", "id": "different", "link": "https://x.test/article"})
                science_direct = repo.resolve({"source": "sd", "id": "sd1", "link": "https://sciencedirect.test/article/S1?dgcid=rss_sd_all"})
                clean_science_direct = repo.resolve({"source": "other", "id": "sd2", "link": "https://sciencedirect.test/article/S1"})
            self.assertEqual(first, by_guid)
            self.assertEqual(first, by_url)
            self.assertEqual(science_direct, clean_science_direct)
            repo.conn.close()

    def test_distinct_publisher_records_with_shared_front_matter_do_not_merge(self):
        """Real RSS front matter can share title, journal, and publication date."""
        with tempfile.TemporaryDirectory() as directory:
            repo = PaperRepository(connect(os.path.join(directory, "p.sqlite3")))
            with repo.transaction():
                first = repo.resolve({
                    "source": "legacy_xml", "id": "https://www.sciencedirect.com/science/article/pii/S0167811626000388",
                    "title": "Editorial Board", "journal": "International Journal of Research in Marketing",
                    "pub_date": "Sun, 16 Aug 2026 06:20:51 GMT",
                    "link": "https://www.sciencedirect.com/science/article/pii/S0167811626000388?dgcid=rss_sd_all",
                })
                second = repo.resolve({
                    "source": "legacy_xml", "id": "https://www.sciencedirect.com/science/article/pii/S0167811626000479",
                    "title": "Editorial Board", "journal": "International Journal of Research in Marketing",
                    "pub_date": "Sun, 16 Aug 2026 06:20:51 GMT",
                    "link": "https://www.sciencedirect.com/science/article/pii/S0167811626000479?dgcid=rss_sd_all",
                })
            self.assertNotEqual(first, second)
            self.assertEqual(repo.conn.execute("SELECT count(*) FROM papers").fetchone()[0], 2)
            repo.conn.close()

    def test_importer_merges_matching_xml_and_json_legacy_guids_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xml = b'''<?xml version="1.0"?><rss><channel><item><title>Canonical title</title>
                <link>https://publisher.test/article/original</link><guid>shared-legacy-guid</guid>
                <pubDate>2026-08-16</pubDate><author>Journal</author></item></channel></rss>'''
            (root / "filtered_feed.xml").write_bytes(xml)
            # The JSON projection may contain normalized display text and a
            # rewritten link; its legacy GUID remains the import bridge.
            write_json(directory, "feed.json", {"items": [{
                "id": "shared-legacy-guid", "title": "Normalized title",
                "link": "https://publisher.test/article/normalized", "journal": "Journal",
                "pub_date": "2026-08-16",
            }, {
                "id": "stale-local-only", "title": "Must not expand XML history",
                "link": "https://publisher.test/article/stale", "journal": "Journal",
                "pub_date": "2026-08-16",
            }]})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            first = LegacyImporter(directory, database).run()
            second = LegacyImporter(directory, database).run()
            conn = connect(database)
            self.assertEqual((first["database"]["papers"], second["database"]["papers"]), (1, 1))
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_observations").fetchone()[0], 2)
            conn.close()

    def test_clean_xml_bootstrap_preserves_distinct_same_front_matter_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = "https://www.sciencedirect.com/science/article/pii/S0167811626000388"
            second = "https://www.sciencedirect.com/science/article/pii/S0167811626000479"
            xml = f'''<?xml version="1.0"?><rss><channel>
                <item><title>Editorial Board</title><link>{first}?dgcid=rss_sd_all</link><guid>{first}</guid><pubDate>Sun, 16 Aug 2026 06:20:51 GMT</pubDate><author>International Journal of Research in Marketing</author></item>
                <item><title>Editorial Board</title><link>{second}?dgcid=rss_sd_all</link><guid>{second}</guid><pubDate>Sun, 16 Aug 2026 06:20:51 GMT</pubDate><author>International Journal of Research in Marketing</author></item>
                </channel></rss>'''
            (root / "filtered_feed.xml").write_text(xml, encoding="utf-8")
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            outcome = LegacyImporter(directory, database).run(_backup_enabled=False)
            self.assertEqual(outcome["database"]["papers"], 2)
            self.assertEqual([item["id"] for item in database_items(database)], [first, second])

    def test_doi_abs_pages_are_not_treated_as_arxiv(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = PaperRepository(connect(os.path.join(directory, "p.sqlite3")))
            sage_url = "https://journals.sagepub.com/doi/abs/10.1177/0000000000001"
            first_doi = "https://journals.example.test/doi/abs/10.1287/mksc.2024.1"
            second_doi = "https://journals.example.test/doi/abs/10.1287/mksc.2024.2"
            with repo.transaction():
                sage = repo.resolve({"source": "sage", "id": "sage", "link": sage_url})
                first = repo.resolve({"source": "one", "id": "one", "link": first_doi})
                second = repo.resolve({"source": "two", "id": "two", "link": second_doi})
                arxiv = repo.resolve({"source": "arxiv", "id": "a", "link": "https://arxiv.org/abs/2401.01234v2"})
            self.assertNotEqual(first, second)
            self.assertIsNone(repo.conn.execute("SELECT 1 FROM paper_identifiers WHERE paper_id=? AND identifier_type='arxiv'", (sage,)).fetchone())
            self.assertEqual(repo.conn.execute("SELECT identifier_value FROM paper_identifiers WHERE paper_id=? AND identifier_type='arxiv'", (arxiv,)).fetchone()[0], "2401.01234v2")
            repo.conn.close()

    def test_import_is_idempotent_and_no_history_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            items = [
                {"id": f"id-{index}", "title": f"Paper {index}", "link": f"https://x.test/{index}", "journal": "J", "pub_date": "2024-01-01"}
                for index in range(1001)
            ]
            write_json(directory, "feed.json", {"items": items})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            importer = LegacyImporter(directory, database)
            first = importer.run()
            second = importer.run()
            conn = connect(database)
            self.assertEqual(first["database"]["papers"], 1001)
            self.assertEqual(second["database"]["papers"], 1001)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_observations").fetchone()[0], 1001)
            conn.close()

    def test_rollback_and_unresolved_interaction_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(directory, "feed.json", {"items": [{"id": "old", "title": "Paper", "link": "https://x.test/p"}]})
            write_json(directory, "interactions.json", {"favorites": ["old", "missing"]})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            with self.assertRaises(RuntimeError):
                LegacyImporter(directory, database).run(fail_after=1)
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT count(*) FROM papers").fetchone()[0], 0)
            conn.close()
            LegacyImporter(directory, database).run()
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT state FROM paper_review_state").fetchone()[0], "favorite")
            self.assertEqual(conn.execute("SELECT count(*) FROM migration_unresolved").fetchone()[0], 1)
            conn.close()

    def test_conflicting_interaction_states_are_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(directory, "feed.json", {"items": [{"id": "old", "title": "Paper"}]})
            write_json(directory, "interactions.json", {"favorites": ["old"], "hidden": ["old"]})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            LegacyImporter(directory, database).run()
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT state FROM paper_review_state").fetchone()[0], "inbox")
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_review_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT reason FROM migration_unresolved").fetchone()[0], "conflicting legacy interaction states")
            conn.close()

    def test_translation_title_key_maps_only_when_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(directory, "feed.json", {"items": [{"id": "one", "title": "The Paper: An Example"}]})
            write_json(directory, "translations.json", {"the paper an example": {"zh": "论文"}})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            LegacyImporter(directory, database).run()
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_analyses WHERE analysis_kind='translation'").fetchone()[0], 1)
            conn.close()

    def test_ambiguous_translation_title_is_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(directory, "feed.json", {"items": [{"id": "one", "title": "Same Title"}, {"id": "two", "title": "Same Title"}]})
            write_json(directory, "translations.json", {"Same Title": {"zh": "同名"}})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            LegacyImporter(directory, database).run()
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_analyses").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT reason FROM migration_unresolved").fetchone()[0], "ambiguous normalized title")
            conn.close()

    def test_dry_run_has_counts_and_no_shadow_path(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(directory, "feed.json", {"items": [{"id": "one", "title": "P"}]})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            outcome = LegacyImporter(directory, database).run(dry_run=True)
            self.assertTrue(outcome["dry_run"])
            self.assertEqual(outcome["database"]["papers"], 1)
            self.assertNotIn("shadow_database", outcome)
            self.assertFalse(os.path.exists(database))

    def test_formal_import_backs_up_all_legacy_files_once_without_mutating_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xml = b'<?xml version="1.0"?><rss><channel><item><title>P</title><link>https://x.test/p</link><guid>one</guid></item></channel></rss>'
            (root / "filtered_feed.xml").write_bytes(xml)
            for name, payload in {
                "feed.json": {"items": [{"id": "one", "title": "P", "link": "https://x.test/p"}]},
                "interactions.json": {"favorites": ["one"]},
                "translations.json": {"P": {"zh": "论文"}},
                "abstracts.json": {"one": {"abstract": "A"}},
                "user_corrections.json": {"one": {"topics": ["T"]}},
            }.items():
                write_json(directory, name, payload)
            originals = {name: (root / name).read_bytes() for name in ("filtered_feed.xml", "web/feed.json", "web/interactions.json", "web/translations.json", "web/abstracts.json", "web/user_corrections.json")}
            importer = LegacyImporter(directory, str(root / "data" / "paper_feed.sqlite3"))
            first = importer.run()
            backups = list((root / "data").glob("paper_feed-legacy-backup-*"))
            self.assertIsNotNone(first["backup"])
            self.assertEqual(len(backups), 1)
            for name, content in originals.items():
                self.assertEqual((root / name).read_bytes(), content)
                self.assertEqual((backups[0] / name).read_bytes(), content)
            second = importer.run()
            self.assertIsNone(second["backup"])
            self.assertEqual(len(list((root / "data").glob("paper_feed-legacy-backup-*"))), 1)

    def test_abstract_and_correction_id_caches_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(directory, "feed.json", {"items": [{"id": "paper-id", "title": "P"}]})
            write_json(directory, "abstracts.json", {"paper-id": {"abstract": "Abstract"}})
            write_json(directory, "user_corrections.json", {"paper-id": {"topics": ["Topic"]}})
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            importer = LegacyImporter(directory, database)
            importer.run()
            importer.run()
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_analyses WHERE analysis_kind='abstract'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_user_overrides WHERE override_kind='user_correction'").fetchone()[0], 1)
            conn.close()

    def test_connection_pragmas_and_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(os.path.join(directory, "p.sqlite3"))
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertGreaterEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertIsNotNone(conn.execute("SELECT applied_at FROM schema_migrations WHERE version=?", (SCHEMA_VERSION,)).fetchone())
            conn.close()


if __name__ == "__main__":
    unittest.main()
