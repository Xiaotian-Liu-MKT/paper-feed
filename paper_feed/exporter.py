"""Compatibility exports derived from durable Paper Feed SQLite records."""
import json
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .db import connect


def _payload(value):
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _date_key(value):
    try:
        text = str(value or "")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try: parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError): return float("-inf")
    if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _rss_date(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try: stamp = parsedate_to_datetime(str(value))
        except (TypeError, ValueError): stamp = datetime.now(timezone.utc)
    if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
    return format_datetime(stamp.astimezone(timezone.utc), usegmt=True)


def database_items(database, predicate=None):
    """Return all durable papers (the caller may impose a display predicate)."""
    conn = connect(database)
    try:
        rows = conn.execute("""SELECT p.paper_id,p.title,p.journal,p.published_at,p.canonical_url,
          o.source_guid,o.link,o.title observed_title,o.journal observed_journal,o.published_at observed_published_at,o.summary,o.payload_json
          FROM papers p LEFT JOIN paper_observations o ON o.observation_id=(SELECT MAX(observation_id) FROM paper_observations WHERE paper_id=p.paper_id)
          ORDER BY COALESCE(o.published_at,p.published_at) DESC,p.paper_id""").fetchall()
        items = []
        for row in rows:
            item = _payload(row["payload_json"])
            item.update({"paper_id": row["paper_id"], "id": item.get("id") or row["source_guid"] or row["paper_id"],
                         "link": row["link"] or item.get("link") or row["canonical_url"] or "",
                         "title": row["observed_title"] or item.get("title") or row["title"],
                         "journal": row["observed_journal"] or item.get("journal") or row["journal"] or "",
                         "pub_date": row["observed_published_at"] or item.get("pub_date") or row["published_at"] or "",
                         "summary": row["summary"] or item.get("summary") or ""})
            item["legacy_ids"] = [alias[0] for alias in conn.execute(
                "SELECT identifier_value FROM paper_identifiers WHERE paper_id=? AND identifier_type='legacy_id'", (row["paper_id"],)
            )]
            for table, column, kind in (("paper_analyses", "analysis_kind", "translation"), ("paper_analyses", "analysis_kind", "abstract"), ("paper_user_overrides", "override_kind", "user_correction")):
                extra = conn.execute(f"SELECT payload_json FROM {table} WHERE paper_id=? AND {column}=?", (row["paper_id"], kind)).fetchone()
                if extra:
                    item[kind] = _payload(extra[0])
            if predicate is None or predicate(item):
                items.append(item)
        # Stable newest-first ordering before any export limit is applied.
        return sorted(sorted(items, key=lambda item: item["paper_id"]), key=lambda item: _date_key(item.get("pub_date")), reverse=True)
    finally:
        conn.close()


def _labels(value):
    if isinstance(value, list): return value
    return [value] if value else []


def export_items(items, xml_path, json_path, queries=(), limit=1000, atomic_write=None):
    """Atomically write legacy XML/JSON; limits only the presentation, never DB."""
    items = sorted(sorted(items, key=lambda item: item["paper_id"]), key=lambda item: _date_key(item.get("pub_date")), reverse=True)[:limit]
    data = []
    for item in items:
        translation, abstract, correction = (item.get("translation") or {}), (item.get("abstract") or {}), (item.get("user_correction") or {})
        methods = _labels(translation.get("methods", translation.get("method", [])))
        topics = _labels(translation.get("topics", translation.get("topic", [])))
        for key in ("methods", "topics", "theories", "context", "subjects", "novelty_score"):
            if key in correction and correction[key] not in (None, [], ""):
                if key == "methods": methods = _labels(correction[key])
                elif key == "topics": topics = _labels(correction[key])
                else: translation[key] = correction[key]
        data.append({"paper_id": item["paper_id"], "id": item["id"], "link": item["link"], "title": item["title"],
          "title_zh": translation.get("zh", ""), "method": (methods[0].get("name") if methods and isinstance(methods[0], dict) else (methods[0] if methods else "Qualitative")),
          "topic": (topics[0].get("name") if topics and isinstance(topics[0], dict) else (topics[0] if topics else "Other Marketing")),
          "methods": methods, "topics": topics, "theories": translation.get("theories", []), "context": translation.get("context", []), "subjects": translation.get("subjects", []),
          "novelty_score": translation.get("novelty_score"), "classification_source": "user" if correction else "gpt", "classification_version": translation.get("classification_version", ""), "user_corrected": bool(correction),
          "summary": item.get("summary", ""), "abstract": abstract.get("abstract", ""), "raw_abstract": abstract.get("raw_abstract", ""), "abstract_source": abstract.get("source", ""), "journal": item.get("journal", ""), "pub_date": str(item.get("pub_date") or "")})
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "keywords": sorted({p.strip() for q in queries for p in q.split("AND") if p.strip()}, key=str.lower), "items": data}
    root = ET.Element("rss", version="2.0"); channel = ET.SubElement(root, "channel")
    for tag, value in (("title", "My Customized Papers"), ("link", "https://github.com/your_username/your_repo"), ("description", "Aggregated research papers")):
        ET.SubElement(channel, tag).text = value
    for item in data:
        node = ET.SubElement(channel, "item")
        for tag, value in (("title", item["title"]), ("link", item["link"]), ("description", item["summary"]), ("author", item["journal"]), ("guid", item["id"]), ("pubDate", _rss_date(item["pub_date"]))):
            ET.SubElement(node, tag).text = str(value or "")
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if atomic_write:
        atomic_write(json_path, json.dumps(payload, ensure_ascii=True, indent=2)); atomic_write(xml_path, xml)
    else:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True); Path(json_path).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"); Path(xml_path).write_bytes(xml)
    return payload
