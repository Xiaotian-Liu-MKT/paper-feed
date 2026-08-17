"""Validate generated RSS exports before an automated publication."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


class PublishGuardError(ValueError):
    """Raised when generated feed exports are unsafe to publish."""


def _identity(*values: object) -> str:
    """Return the first non-empty stable identity, with whitespace normalized."""
    for value in values:
        normalized = " ".join(str(value or "").split())
        if normalized:
            return normalized
    return ""


def _require_unique(identifiers: list[str], label: str, path: Path) -> None:
    if not identifiers or any(not identifier for identifier in identifiers):
        raise PublishGuardError(f"{label} contains an item without a stable identity: {path}")
    if len(identifiers) != len(set(identifiers)):
        raise PublishGuardError(f"{label} contains duplicate item identities: {path}")


def xml_item_ids(path: Path) -> list[str]:
    """Parse RSS and return GUID identities, falling back to links for legacy RSS."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise PublishGuardError(f"RSS XML is not parseable: {path}") from error
    if root.tag != "rss" or root.find("channel") is None:
        raise PublishGuardError(f"RSS XML has no rss/channel structure: {path}")
    identifiers = [_identity(item.findtext("guid"), item.findtext("link"))
                   for item in root.findall("./channel/item")]
    _require_unique(identifiers, "RSS XML", path)
    return identifiers


def json_item_ids(path: Path) -> list[str]:
    """Parse feed JSON and return legacy IDs, falling back to links.

    ``paper_id`` is the canonical client identity and is required to be unique.
    The RSS compatibility export stores legacy ``id`` values in XML GUIDs, so
    those legacy IDs remain the cross-export key.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise PublishGuardError(f"Feed JSON is not parseable: {path}") from error
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise PublishGuardError(f"Feed JSON must contain an items list: {path}")
    identifiers, paper_ids = [], []
    for item in items:
        if not isinstance(item, dict):
            raise PublishGuardError(f"Feed JSON items must be objects: {path}")
        paper_id = _identity(item.get("paper_id"))
        if not paper_id:
            raise PublishGuardError(f"Feed JSON contains an item without a paper_id: {path}")
        paper_ids.append(paper_id)
        identifiers.append(_identity(item.get("id"), item.get("link")))
    _require_unique(identifiers, "Feed JSON", path)
    _require_unique(paper_ids, "Feed JSON paper_id", path)
    return identifiers


def _validate_rolling_projection(baseline_ids: list[str], candidate_ids: list[str], limit: int) -> None:
    """Permit only newest-item replacement of the oldest tail at a full cap."""
    baseline_set, candidate_set = set(baseline_ids), set(candidate_ids)
    missing = baseline_set - candidate_set
    added = candidate_set - baseline_set
    if len(baseline_ids) < limit:
        if missing:
            raise PublishGuardError(
                f"Refusing to publish because {len(missing)} existing feed identities are missing."
            )
        return
    if len(candidate_ids) != limit:
        raise PublishGuardError(
            f"Refusing to publish because a full {limit}-item projection changed size to {len(candidate_ids)}."
        )
    if len(missing) != len(added):
        raise PublishGuardError("Refusing to publish because history loss is not balanced by new identities.")
    if missing:
        expected_tail = baseline_ids[-len(missing):]
        if set(expected_tail) != missing:
            raise PublishGuardError("Refusing to publish because a non-oldest baseline identity was removed.")
    retained_baseline = [identifier for identifier in baseline_ids if identifier in candidate_set]
    retained_candidate = [identifier for identifier in candidate_ids if identifier in baseline_set]
    if retained_candidate != retained_baseline:
        raise PublishGuardError("Refusing to publish because retained baseline identities changed order.")


def validate_exports(xml_path: Path, json_path: Path, baseline_xml: Path | None = None,
                     projection_limit: int = 1000) -> int:
    """Reject malformed, inconsistent, empty, or unsafe rolling publication exports."""
    if projection_limit < 1:
        raise PublishGuardError("Projection limit must be at least 1.")
    xml_ids = xml_item_ids(xml_path)
    json_ids = json_item_ids(json_path)
    if set(xml_ids) != set(json_ids):
        raise PublishGuardError("RSS XML identities do not match feed JSON identities.")
    if baseline_xml is not None:
        _validate_rolling_projection(xml_item_ids(baseline_xml), xml_ids, projection_limit)
    return len(xml_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--baseline-xml", type=Path)
    parser.add_argument("--projection-limit", type=int, default=1000)
    args = parser.parse_args(argv)
    try:
        count = validate_exports(args.xml, args.json, args.baseline_xml, args.projection_limit)
    except PublishGuardError as error:
        print(f"Publication guard failed: {error}", file=sys.stderr)
        return 1
    print(f"Publication guard passed: {count} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
