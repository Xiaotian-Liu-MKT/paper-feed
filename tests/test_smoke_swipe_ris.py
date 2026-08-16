"""Browser smoke coverage for the local-only swipe and RIS workflow."""
import contextlib
import http.server
import socketserver
import threading
import unittest
from pathlib import Path

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    PlaywrightError = None
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def static_server():
    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(*args, directory=str(ROOT), **kwargs)
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_swipe_undo_and_favorites_ris_smoke():
    paper = {"paper_id": "paper-1", "title": "Swipe paper", "link": "https://example.invalid/paper", "journal": "Journal", "pub_date": "2026-08-01"}
    reviews = []
    with static_server() as base_url, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def api_route(route):
            url = route.request.url
            if "/api/papers?" in url:
                route.fulfill(json={"items": [paper], "keywords": []})
            elif "/api/interactions" in url:
                route.fulfill(json={"favorites": [], "archived": [], "hidden": []})
            elif "/api/categories" in url:
                route.fulfill(json={"methods": [], "topics": [], "theories": [], "contexts": [], "subjects": []})
            elif "/api/papers/paper-1/review" in url:
                action = route.request.post_data_json["action"]
                reviews.append(action)
                favorites = ["paper-1"] if action == "like" else []
                route.fulfill(json={"interactions": {"favorites": favorites, "archived": [], "hidden": []}})
            elif "/api/export_favorites_ris" in url:
                route.fulfill(status=200, content_type="application/x-research-info-systems", body="TY  - JOUR\nTI  - Swipe paper\nER  - \n")
            else:
                route.fallback()

        page.route("**/api/**", api_route)
        page.goto(f"{base_url}/web/", wait_until="domcontentloaded")
        page.locator(".swipe-action--right").wait_for(timeout=5_000)
        page.locator(".swipe-action--right").click()
        page.wait_for_timeout(250)
        assert reviews == ["like"]
        page.locator(".undo-btn").click()
        page.wait_for_timeout(100)
        assert reviews == ["like", "unlike"]

        # Like again, then the favorites-only export control triggers a download.
        page.locator(".swipe-action--right").click()
        page.wait_for_timeout(250)
        page.locator("button[data-filter='favorites']").click()
        assert page.locator("#btnExportFavorites").is_visible()
        with page.expect_download() as download_info:
            page.locator("#btnExportFavorites").click()
        assert download_info.value.suggested_filename == "paper-feed-favorites.ris"
        browser.close()


@unittest.skipUnless(sync_playwright is not None, "Playwright is not installed in this Python environment")
class TestSwipeRisBrowserSmoke(unittest.TestCase):
    def test_swipe_undo_and_favorites_ris_button(self):
        try:
            run_swipe_undo_and_favorites_ris_smoke()
        except PlaywrightError as error:
            # A venv may contain the Python package but not the Chromium binary.
            if "Executable doesn't exist" in str(error) or "browserType.launch" in str(error):
                self.skipTest(f"Playwright Chromium unavailable: {error}")
            raise
