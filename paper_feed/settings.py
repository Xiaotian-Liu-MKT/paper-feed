from dataclasses import dataclass
import os


@dataclass
class IntegrationSettings:
    notion_api_version: str = os.getenv("NOTION_API_VERSION", "2022-06-28")
    notion_timeout_seconds: int = int(os.getenv("NOTION_TIMEOUT_SECONDS", "120"))
    notion_parent_page_id: str = os.getenv("NOTION_PARENT_PAGE_ID", "")
    notion_papers_database_id: str = os.getenv("NOTION_PAPERS_DATABASE_ID", "")
    notion_token: str = os.getenv("NOTION_TOKEN", "")
    zotero_api_version: str = os.getenv("ZOTERO_API_VERSION", "3")
    zotero_library_type: str = os.getenv("ZOTERO_LIBRARY_TYPE", "users")
    zotero_library_id: str = os.getenv("ZOTERO_LIBRARY_ID", "")
    zotero_api_key: str = os.getenv("ZOTERO_API_KEY", "")
