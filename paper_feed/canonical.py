import re
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from paper_feed.identity import build_paper_id, normalize_url, sha256_hex
from paper_feed.models import CanonicalPaperRecord


def normalize_journal_title(journal):
    clean = (journal or "").strip()
    clean = re.sub(r"^sciencedirect(?:\s+publication)?\s*:\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def normalize_paper_title(title, journal=None):
    clean = (title or "").strip()
    match = re.match(r"^\[(.*?)\]\s*(.+)$", clean)
    if not match:
        return clean
    bracket, remainder = match.group(1).strip(), match.group(2).strip()
    if bracket.lower().startswith("sciencedirect publication"):
        return remainder
    if journal and bracket == journal:
        return remainder
    return clean


def _strip_tags(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _extract_source_name(item):
    link = item.get("link", "") or item.get("id", "")
    if not link:
        return ""
    hostname = urlsplit(link).netloc.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _split_authors(raw_authors):
    value = (raw_authors or "").strip()
    if not value:
        return []
    if ";" in value:
        parts = [part.strip() for part in value.split(";") if part.strip()]
    elif " and " in value:
        parts = [part.strip() for part in value.split(" and ") if part.strip()]
    else:
        parts = [value]

    creators = []
    for part in parts:
        creator = {"full_name": part}
        tokens = [token for token in part.split() if token]
        if len(tokens) >= 2:
            creator["given_name"] = " ".join(tokens[:-1])
            creator["family_name"] = tokens[-1]
        creators.append(creator)
    return creators


def _extract_authors(item):
    summary = _strip_tags(item.get("summary", ""))
    match = re.search(r"Authors?(?:\(s\))?\s*:\s*(.+?)(?=(Publication date|Source)\s*:|$)", summary, flags=re.IGNORECASE)
    if not match:
        return []
    return _split_authors(match.group(1).strip())


def _normalize_raw_abstract_source(raw_abstract, source):
    if not raw_abstract:
        return ""
    normalized = (source or "").strip().lower()
    if normalized in {"crossref", "semantic_scholar", "user"}:
        return normalized
    return "unknown"


def _compute_upstream_fingerprint(record):
    subset = {
        "authors": record.authors,
        "canonical_url": record.canonical_url,
        "doi": record.doi,
        "journal": record.journal,
        "method": record.method,
        "published_at": record.published_at,
        "raw_abstract": record.raw_abstract,
        "raw_abstract_source": record.raw_abstract_source,
        "source": record.source,
        "title": record.title,
        "title_zh": record.title_zh,
        "topics": record.topics,
    }
    payload = json.dumps(subset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_hex(payload)


def build_canonical_record(item, analysis, abstract_info):
    raw_journal = item.get("journal", "")
    title = normalize_paper_title(item.get("title", ""), raw_journal)
    journal = normalize_journal_title(raw_journal)
    methods = list(analysis.get("methods") or [])
    topics = list(analysis.get("topics") or [])
    pub_date = item.get("pub_date")
    published_at = pub_date.isoformat() if hasattr(pub_date, "isoformat") else str(pub_date or "")
    raw_abstract = abstract_info.get("raw_abstract", "")
    abstract_source = abstract_info.get("source", "")
    canonical_url = normalize_url(item.get("link", ""))
    ingested_at = item.get("ingested_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    record = CanonicalPaperRecord(
        paper_id=build_paper_id(
            {
                "doi": item.get("doi", ""),
                "canonical_url": canonical_url,
                "title": title,
                "journal": journal,
                "published_at": published_at,
            }
        ),
        source_id=item.get("id", ""),
        title=title,
        title_zh=analysis.get("zh", ""),
        method=(methods[0]["name"] if methods else "Unclassified"),
        topic=(topics[0]["name"] if topics else "Other Marketing"),
        methods=methods,
        topics=topics,
        authors=_extract_authors(item),
        link=item.get("link", ""),
        canonical_url=canonical_url,
        summary=item.get("summary", ""),
        journal=journal,
        source=_extract_source_name(item),
        published_at=published_at,
        doi=item.get("doi", ""),
        raw_abstract=raw_abstract,
        raw_abstract_source=_normalize_raw_abstract_source(raw_abstract, abstract_source),
        abstract=abstract_info.get("abstract", ""),
        abstract_source=abstract_source,
        ingested_at=ingested_at,
        classification_version=analysis.get("classification_version", ""),
    )
    record.upstream_fingerprint = _compute_upstream_fingerprint(record)
    return record
