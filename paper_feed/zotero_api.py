import requests


class ZoteroApiError(RuntimeError):
    """Raised when the Zotero API returns an error response."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ZoteroClient:
    def __init__(self, api_key, library_type, library_id, api_version="3", base_url="https://api.zotero.org"):
        self.api_version = api_version
        self.library_type = library_type
        self.library_id = library_id
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Zotero-API-Key": api_key,
                "Zotero-API-Version": api_version,
                "Content-Type": "application/json",
            }
        )

    def create_item(self, payload):
        response = self.session.post(
            f"{self.base_url}/{self.library_type}/{self.library_id}/items",
            json=[payload],
            timeout=30,
        )
        if response.status_code >= 400:
            raise ZoteroApiError(f"Zotero API request failed: {response.status_code} {response.text}", status_code=response.status_code)
        return response.json()

    def retrieve_item(self, item_key):
        response = self.session.get(
            f"{self.base_url}/{self.library_type}/{self.library_id}/items/{item_key}",
            timeout=30,
        )
        if response.status_code >= 400:
            raise ZoteroApiError(f"Zotero API request failed: {response.status_code} {response.text}", status_code=response.status_code)
        return response.json()

    def update_item(self, item_key, payload, expected_version=None):
        headers = {}
        if expected_version is not None:
            headers["If-Unmodified-Since-Version"] = str(expected_version)
        response = self.session.patch(
            f"{self.base_url}/{self.library_type}/{self.library_id}/items/{item_key}",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if response.status_code >= 400:
            raise ZoteroApiError(f"Zotero API request failed: {response.status_code} {response.text}", status_code=response.status_code)
        if not response.content:
            return {"successful": {"0": {"key": item_key}}}
        return response.json()

    def validate_access(self):
        response = self.session.get(
            f"{self.base_url}/{self.library_type}/{self.library_id}/items",
            params={"limit": 1},
            timeout=30,
        )
        if response.status_code >= 400:
            raise ZoteroApiError(f"Zotero API request failed: {response.status_code} {response.text}", status_code=response.status_code)
        return True

    def find_item_by_tag(self, tag):
        response = self.session.get(
            f"{self.base_url}/{self.library_type}/{self.library_id}/items",
            params={"tag": tag, "limit": 1},
            timeout=30,
        )
        if response.status_code >= 400:
            raise ZoteroApiError(f"Zotero API request failed: {response.status_code} {response.text}", status_code=response.status_code)
        items = response.json()
        if not items:
            return None
        return items[0]

    def iter_items(self, *, limit=100):
        start = 0
        while True:
            response = self.session.get(
                f"{self.base_url}/{self.library_type}/{self.library_id}/items",
                params={"limit": limit, "start": start},
                timeout=30,
            )
            if response.status_code >= 400:
                raise ZoteroApiError(
                    f"Zotero API request failed: {response.status_code} {response.text}",
                    status_code=response.status_code,
                )
            items = response.json()
            if not items:
                break
            for item in items:
                yield item
            if len(items) < limit:
                break
            start += limit


def build_item_payload(
    *,
    title,
    creators,
    publication_title,
    published_at="",
    url,
    doi,
    paper_id,
    notion_page_id="",
    source="",
    method="",
    topics=None,
):
    tags = [{"tag": f"pf:id:{paper_id}"}]
    if method:
        tags.append({"tag": f"pf:method:{method}"})
    for topic in topics or []:
        if topic:
            tags.append({"tag": f"pf:topic:{topic}"})

    extra_lines = [f"Paper Feed ID: {paper_id}"]
    if notion_page_id:
        extra_lines.append(f"Notion Page ID: {notion_page_id}")
    if source:
        extra_lines.append(f"Canonical Source: {source}")
    if url:
        extra_lines.append(f"Canonical URL: {url}")

    normalized_creators = []
    for creator in creators or []:
        if not isinstance(creator, dict):
            continue
        if creator.get("creatorType"):
            normalized_creators.append(creator)
            continue
        first_name = creator.get("given_name", "") or creator.get("firstName", "")
        last_name = creator.get("family_name", "") or creator.get("lastName", "")
        full_name = creator.get("full_name", "") or creator.get("name", "")
        if last_name:
            payload_creator = {"creatorType": "author", "lastName": last_name}
            if first_name:
                payload_creator["firstName"] = first_name
            normalized_creators.append(payload_creator)
            continue
        if full_name:
            normalized_creators.append({"creatorType": "author", "name": full_name})

    return {
        "itemType": "journalArticle",
        "title": title,
        "creators": normalized_creators,
        "publicationTitle": publication_title,
        "date": published_at,
        "url": url,
        "DOI": doi,
        "tags": tags,
        "extra": "\n".join(extra_lines),
    }
