"""Conservative, deterministic identities for papers."""
import hashlib
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\"')\]]+", re.I)
ARXIV_EXPLICIT_RE = re.compile(
    r"arxiv:\s*([0-9]{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)", re.I
)
ARXIV_URL_RE = re.compile(
    r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/"
    r"([0-9]{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)(?:\.pdf)?", re.I
)
PMID_RE = re.compile(r"(?:pmid[:/ ]|pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)", re.I)
PII_RE = re.compile(r"(?:/pii/|pii:)([A-Z0-9-]{6,})", re.I)
TRACKING_PARAMETERS = {
    "dgcid", "fbclid", "gclid", "rss", "rss_source", "rss_campaign", "feed", "feed_name",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
}


def norm_text(value):
    return " ".join(re.sub(r"[^\w]+", " ", (value or "").casefold()).split())


def canonical_url(value):
    if not value:
        return None
    try:
        bits = urlsplit(unquote(value.strip()))
        if not bits.scheme or not bits.netloc:
            return None
        query = [
            (key, value)
            for key, value in parse_qsl(bits.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_PARAMETERS and not key.casefold().startswith("utm_")
        ]
        path = re.sub(r"/+", "/", bits.path).rstrip("/") or "/"
        return urlunsplit((bits.scheme.casefold(), bits.netloc.casefold(), path, urlencode(sorted(query)), ""))
    except ValueError:
        return None


def normalize_doi(value):
    """Return a stable DOI without URL wrappers, tracking query, or fragment."""
    text = unquote(str(value or "")).strip()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I)
    match = DOI_RE.search(text)
    if not match:
        return None
    # Queries/fragments are URL syntax, not DOI syntax.  Keep DOI-internal punctuation intact.
    doi = match.group(0).split("?", 1)[0].split("#", 1)[0]
    return doi.rstrip(".,;:").casefold() or None


def identifiers(record):
    """Return identities in merge priority order, strongest first."""
    link = record.get("link", "")
    guid = record.get("guid") or record.get("id", "")
    haystack = " ".join(str(value or "") for value in (record.get("doi"), link, guid))
    found = []
    doi = normalize_doi(haystack)
    if doi:
        found.append(("doi", doi))
    for kind, regex in (("pii", PII_RE), ("pmid", PMID_RE)):
        match = regex.search(haystack)
        if match:
            found.append((kind, match.group(1).casefold()))
    arxiv_match = ARXIV_EXPLICIT_RE.search(haystack) or ARXIV_URL_RE.search(haystack)
    if arxiv_match:
        found.append(("arxiv", arxiv_match.group(1).casefold()))
    url = canonical_url(link)
    if url:
        found.append(("url", url))
    source = str(record.get("source") or "").strip()
    if source and guid:
        found.append(("source_guid", f"{source}\x1f{str(guid).strip()}"))
    return found


def fingerprint(record):
    journal = norm_text(record.get("journal"))
    title = norm_text(record.get("title"))
    date = str(record.get("pub_date") or "")[:10]
    if not (journal and title and date):
        return None
    digest = hashlib.sha256(f"{journal}\x1f{title}\x1f{date}".encode("utf-8")).hexdigest()
    return "fingerprint", digest
