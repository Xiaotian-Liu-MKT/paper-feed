import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from paper_feed.publish_guard import PublishGuardError, validate_exports


def write_xml(path: Path, identifiers) -> None:
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    for number, identifier in enumerate(identifiers):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"Paper {number}"
        if identifier is not None:
            ET.SubElement(item, "guid").text = identifier
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_json(path: Path, identifiers, paper_ids=None) -> None:
    items = []
    for index, identifier in enumerate(identifiers):
        item = {} if identifier is None else {"id": identifier}
        item["paper_id"] = paper_ids[index] if paper_ids is not None else f"paper-{index}"
        items.append(item)
    path.write_text(json.dumps({"items": items}), encoding="utf-8")


class PublishGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.xml = self.root / "filtered_feed.xml"
        self.feed = self.root / "feed.json"
        self.baseline = self.root / "baseline.xml"

    def tearDown(self):
        self.temp.cleanup()

    def test_first_publish_accepts_nonempty_consistent_exports(self):
        write_xml(self.xml, ["one", "two"]); write_json(self.feed, ["one", "two"], ["p1", "p2"])
        self.assertEqual(validate_exports(self.xml, self.feed), 2)

    def test_existing_baseline_accepts_pure_additions(self):
        write_xml(self.baseline, ["one", "two"])
        write_xml(self.xml, ["three", "one", "two"]); write_json(self.feed, ["three", "one", "two"])
        self.assertEqual(validate_exports(self.xml, self.feed, self.baseline), 3)

    def test_empty_output_or_missing_file_is_rejected(self):
        write_xml(self.xml, []); write_json(self.feed, [])
        with self.assertRaisesRegex(PublishGuardError, "without a stable identity"):
            validate_exports(self.xml, self.feed)
        write_xml(self.xml, ["one"])
        with self.assertRaisesRegex(PublishGuardError, "not parseable"):
            validate_exports(self.xml, self.root / "missing.json")

    def test_workflow_fails_explicitly_when_clean_checkout_has_no_json_export(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "rss_action.yaml").read_text(encoding="utf-8")
        self.assertIn('[[ ! -f filtered_feed.xml || ! -f web/feed.json ]]', workflow)
        self.assertIn("expected both filtered_feed.xml and web/feed.json", workflow)

    def test_same_count_with_different_identity_sets_is_rejected(self):
        write_xml(self.xml, ["one", "two"]); write_json(self.feed, ["one", "three"])
        with self.assertRaisesRegex(PublishGuardError, "do not match"):
            validate_exports(self.xml, self.feed)

    def test_duplicate_or_missing_ids_are_rejected(self):
        write_xml(self.xml, ["one", "one"]); write_json(self.feed, ["one", "one"])
        with self.assertRaisesRegex(PublishGuardError, "duplicate"):
            validate_exports(self.xml, self.feed)
        write_xml(self.xml, ["one"]); write_json(self.feed, [None])
        with self.assertRaisesRegex(PublishGuardError, "stable identity"):
            validate_exports(self.xml, self.feed)

    def test_any_baseline_identity_loss_is_rejected_before_projection_cap(self):
        write_xml(self.baseline, ["one", "two"])
        write_xml(self.xml, ["one"]); write_json(self.feed, ["one"])
        with self.assertRaisesRegex(PublishGuardError, "existing feed identities"):
            validate_exports(self.xml, self.feed, self.baseline)

    def test_full_projection_allows_new_item_to_replace_oldest_tail(self):
        write_xml(self.baseline, ["a", "b", "c"])
        write_xml(self.xml, ["new", "a", "b"]); write_json(self.feed, ["new", "a", "b"])
        self.assertEqual(validate_exports(self.xml, self.feed, self.baseline, projection_limit=3), 3)

    def test_full_projection_rejects_middle_removal_or_no_addition_shrink(self):
        write_xml(self.baseline, ["a", "b", "c"])
        write_xml(self.xml, ["new", "a", "c"]); write_json(self.feed, ["new", "a", "c"])
        with self.assertRaisesRegex(PublishGuardError, "non-oldest"):
            validate_exports(self.xml, self.feed, self.baseline, projection_limit=3)
        write_xml(self.xml, ["a", "b"]); write_json(self.feed, ["a", "b"])
        with self.assertRaisesRegex(PublishGuardError, "changed size"):
            validate_exports(self.xml, self.feed, self.baseline, projection_limit=3)

    def test_full_projection_rejects_non_tail_removal_and_retained_reordering(self):
        write_xml(self.baseline, ["a", "b", "c"])
        write_xml(self.xml, ["new", "b", "c"]); write_json(self.feed, ["new", "b", "c"])
        with self.assertRaisesRegex(PublishGuardError, "non-oldest"):
            validate_exports(self.xml, self.feed, self.baseline, projection_limit=3)
        write_xml(self.xml, ["b", "new", "a"]); write_json(self.feed, ["b", "new", "a"])
        with self.assertRaisesRegex(PublishGuardError, "changed order"):
            validate_exports(self.xml, self.feed, self.baseline, projection_limit=3)

    def test_malformed_existing_baseline_is_rejected(self):
        self.baseline.write_text("not XML", encoding="utf-8")
        write_xml(self.xml, ["one"]); write_json(self.feed, ["one"])
        with self.assertRaisesRegex(PublishGuardError, "not parseable"):
            validate_exports(self.xml, self.feed, self.baseline)

    def test_missing_or_duplicate_paper_id_is_rejected(self):
        write_xml(self.xml, ["one"]); write_json(self.feed, ["one"], [""])
        with self.assertRaisesRegex(PublishGuardError, "without a paper_id"):
            validate_exports(self.xml, self.feed)
        write_xml(self.xml, ["one", "two"]); write_json(self.feed, ["one", "two"], ["same", "same"])
        with self.assertRaisesRegex(PublishGuardError, "duplicate"):
            validate_exports(self.xml, self.feed)
        self.feed.write_text(json.dumps({"items": [{"id": "one"}]}), encoding="utf-8")
        write_xml(self.xml, ["one"])
        with self.assertRaisesRegex(PublishGuardError, "without a paper_id"):
            validate_exports(self.xml, self.feed)


if __name__ == "__main__":
    unittest.main()
