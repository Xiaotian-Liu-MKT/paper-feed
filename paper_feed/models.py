from dataclasses import dataclass, field


@dataclass
class CanonicalPaperRecord:
    paper_id: str
    source_id: str
    title: str
    title_zh: str
    method: str
    topic: str
    methods: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    authors: list = field(default_factory=list)
    link: str = ""
    canonical_url: str = ""
    summary: str = ""
    journal: str = ""
    source: str = ""
    published_at: str = ""
    doi: str = ""
    raw_abstract: str = ""
    raw_abstract_source: str = ""
    abstract: str = ""
    abstract_source: str = ""
    upstream_fingerprint: str = ""
    ingested_at: str = ""
    classification_version: str = ""
