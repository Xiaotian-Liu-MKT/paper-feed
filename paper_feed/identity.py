import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}
ALLOWED_PAPER_ID_CHARS = re.compile(r"^[a-z0-9:._/-]+$")


def sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_doi(value):
    doi = (value or "").strip().lower()
    doi = re.sub(r"^https?://doi\.org/", "", doi)
    doi = doi.rstrip(".,;:!?")
    return doi


def normalize_url(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    split = urlsplit(raw)
    filtered_pairs = []
    for key, item in parse_qsl(split.query, keep_blank_values=True):
        if key in TRACKING_QUERY_KEYS or any(key.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        filtered_pairs.append((key, item))
    filtered_pairs.sort()
    query = urlencode(filtered_pairs, doseq=True)
    normalized = urlunsplit(
        (
            split.scheme.lower(),
            split.netloc.lower(),
            split.path.rstrip("/"),
            query,
            "",
        )
    )
    return normalized


def normalize_title(value):
    title = re.sub(r"<[^>]+>", " ", value or "")
    title = title.lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def build_paper_id(record):
    doi = normalize_doi(record.get("doi"))
    if doi:
        candidate = f"doi:{doi}"
        if len(candidate) <= 191 and ALLOWED_PAPER_ID_CHARS.match(candidate):
            return candidate
        return f"doi-hash:{sha256_hex(doi)}"

    canonical_url = normalize_url(record.get("canonical_url"))
    if canonical_url:
        return f"url:{sha256_hex(canonical_url)}"

    title = normalize_title(record.get("title"))
    journal = normalize_title(record.get("journal"))
    published_at = (record.get("published_at") or "").strip()
    return f"hash:{sha256_hex('|'.join([title, journal, published_at]))}"
