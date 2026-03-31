import json
from pathlib import Path


CATEGORIES_FILE = Path(__file__).resolve().parents[1] / "web" / "categories.json"


def load_categories():
    with CATEGORIES_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def topic_names():
    return [item["name"] for item in load_categories().get("topics", []) if item.get("name")]


def method_names():
    return [item["name"] for item in load_categories().get("methods", []) if item.get("name")]
