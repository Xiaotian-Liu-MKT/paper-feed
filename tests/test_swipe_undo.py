import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"
INTERACTIONS_PATH = REPO_ROOT / "web" / "interactions.json"


def wait_for_server(timeout_seconds=20):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(BASE_URL, timeout=2) as response:
                if response.status == 200:
                    return
        except URLError:
            pass
        time.sleep(0.5)
    raise RuntimeError("Timed out waiting for local server")


def progress_total(page):
    text = page.locator(".swipe-progress").inner_text()
    return int(text.split("/", 1)[1].strip())


def wait_for_progress_total(page, expected):
    page.wait_for_function(
        """expected => {
            const progress = document.querySelector('.swipe-progress');
            if (!progress) return false;
            const total = Number(progress.textContent.split('/')[1]?.trim());
            return total === expected;
        }""",
        arg=expected,
    )


def test_swipe_deck_allows_multiple_consecutive_undos():
    previous_interactions = INTERACTIONS_PATH.read_text(encoding="utf-8") if INTERACTIONS_PATH.exists() else None
    INTERACTIONS_PATH.write_text(
        json.dumps({"favorites": [], "archived": [], "hidden": []}),
        encoding="utf-8",
    )

    server = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1200})
            page.goto(BASE_URL, wait_until="networkidle")
            page.locator(".swipe-card--current").wait_for()

            total_before = progress_total(page)
            assert total_before >= 3

            page.locator(".swipe-action--right").click()
            wait_for_progress_total(page, total_before - 1)

            page.locator(".swipe-action--left").click()
            wait_for_progress_total(page, total_before - 2)

            page.locator("#undoContainer .undo-btn").click()
            wait_for_progress_total(page, total_before - 1)

            page.locator("#undoContainer .undo-btn").click()
            wait_for_progress_total(page, total_before)

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

        if previous_interactions is None:
            INTERACTIONS_PATH.unlink(missing_ok=True)
        else:
            INTERACTIONS_PATH.write_text(previous_interactions, encoding="utf-8")
