import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"


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


def find_first_populated_mode(page):
    modes = [
        ("favorites", "button[data-filter='favorites']"),
        ("archived", "button[data-filter='archived']"),
        ("all", "button[data-filter='all']"),
    ]
    for name, selector in modes:
        page.locator(selector).click()
        page.wait_for_timeout(300)
        if page.locator("#list .card .article-actions").count() > 0:
            return name
    raise AssertionError("No populated mode found for smoke test")


def main():
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
            page = browser.new_page(viewport={"width": 1440, "height": 1600})
            page.goto(BASE_URL, wait_until="networkidle")

            hero_subhead = page.locator(".subhead").inner_text()
            assert "有摘要时直接展开" in hero_subhead

            page.locator("button[data-filter='favorites']").click()
            page.wait_for_timeout(300)

            favorites_mode_title = page.locator("#modeTitle").inner_text()
            favorites_count_label = page.locator("#countLabel").inner_text()
            favorites_chips = page.locator("#insightChips .insight-chip").all_inner_texts()

            assert favorites_mode_title == "把值得追踪的论文留在这里"
            assert "收藏夹中显示" in favorites_count_label
            assert any("已收藏" in chip for chip in favorites_chips)

            populated_mode = find_first_populated_mode(page)
            labels = page.locator("#list .card .article-actions .action-btn .action-btn__label").evaluate_all(
                "nodes => nodes.slice(0, 5).map(node => node.textContent && node.textContent.trim()).filter(Boolean)"
            )

            assert labels, "Expected visible text labels on action buttons"
            assert any(label in {"分类", "摘要", "收藏", "取消收藏", "归档", "回到收件箱", "恢复收藏", "跳过"} for label in labels)

            report = {
                "hero_subhead": hero_subhead,
                "favorites_mode_title": favorites_mode_title,
                "favorites_count_label": favorites_count_label,
                "favorites_chips": favorites_chips,
                "populated_mode": populated_mode,
                "action_labels": labels,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    main()
