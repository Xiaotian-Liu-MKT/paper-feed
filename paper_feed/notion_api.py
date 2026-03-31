import json
from datetime import datetime, timezone

import requests


class NotionApiError(RuntimeError):
    def __init__(self, message, status_code=None, response_body=None, code="", notion_message=""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.code = code
        self.notion_message = notion_message


class NotionClient:
    def __init__(self, token, api_version, base_url="https://api.notion.com/v1", timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": api_version,
                "Content-Type": "application/json",
            }
        )

    def _request(self, method, path, *, payload=None, params=None):
        response = self.session.request(
            method=method,
            url=f"{self.base_url}{path}",
            json=payload,
            params=params,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            body = response.text
            code = ""
            notion_message = ""
            try:
                parsed = response.json()
                body = json.dumps(parsed, ensure_ascii=False)
                code = parsed.get("code", "")
                notion_message = parsed.get("message", "")
            except Exception:
                pass
            raise NotionApiError(
                f"Notion API request failed: {method} {path}",
                status_code=response.status_code,
                response_body=body,
                code=code,
                notion_message=notion_message,
            )
        if not response.content:
            return {}
        return response.json()

    def retrieve_page(self, page_id):
        return self._request("GET", f"/pages/{page_id}")

    def create_page(self, payload):
        return self._request("POST", "/pages", payload=payload)

    def archive_page(self, page_id):
        return self._request("PATCH", f"/pages/{page_id}", payload={"archived": True})

    def retrieve_database(self, database_id):
        return self._request("GET", f"/databases/{database_id}")

    def query_database(self, database_id, payload):
        return self._request("POST", f"/databases/{database_id}/query", payload=payload)

    def create_database(self, payload):
        return self._request("POST", "/databases", payload=payload)

    def update_database(self, database_id, payload):
        return self._request("PATCH", f"/databases/{database_id}", payload=payload)

    def update_page(self, page_id, payload):
        return self._request("PATCH", f"/pages/{page_id}", payload=payload)

    def append_block_children(self, block_id, children, *, after=None):
        payload = {"children": children}
        if after:
            payload["after"] = after
        return self._request("PATCH", f"/blocks/{block_id}/children", payload=payload)

    def delete_block(self, block_id):
        return self._request("DELETE", f"/blocks/{block_id}")

    def list_block_children(self, block_id, *, start_cursor=None, page_size=100):
        params = {"page_size": page_size}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return self._request("GET", f"/blocks/{block_id}/children", params=params)

    def iter_block_children(self, block_id, *, page_size=100):
        cursor = None
        while True:
            payload = self.list_block_children(block_id, start_cursor=cursor, page_size=page_size)
            for result in payload.get("results", []):
                yield result
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")

    def capability_probe(self, parent_page_id):
        self.retrieve_page(parent_page_id)
        scratch = self.create_page(
            {
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "properties": {
                    "title": [
                        {
                            "type": "text",
                            "text": {
                                "content": (
                                    "Paper Feed bootstrap probe "
                                    + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                                )
                            },
                        }
                    ]
                },
            }
        )
        self.archive_page(scratch["id"])
        return {"parent_page_id": parent_page_id, "scratch_page_id": scratch["id"]}
