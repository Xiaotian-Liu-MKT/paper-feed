import json
from pathlib import Path

import yaml

from paper_feed.automation_state import bootstrap_state_tree, merge_registry_namespace, write_duplicate_audit_report


def test_bootstrap_state_tree_creates_required_layout(tmp_path):
    result = bootstrap_state_tree(tmp_path, now_iso="2026-03-30T10:20:00+00:00")

    assert (tmp_path / "state" / "registry.json").exists()
    assert (tmp_path / "state" / "heartbeat.json").exists()
    assert (tmp_path / "state" / "duplicate_audit").is_dir()
    assert (tmp_path / "state" / "compatibility").is_dir()
    heartbeat = json.loads((tmp_path / "state" / "heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["last_keepalive"] == "2026-03-30T10:20:00+00:00"
    assert heartbeat["workflow"] == ""
    assert result["state_branch"] == "automation-state"


def test_bootstrap_state_tree_does_not_rewrite_existing_heartbeat_without_touch_flag(tmp_path):
    bootstrap_state_tree(tmp_path, now_iso="2026-03-30T10:20:00+00:00")
    bootstrap_state_tree(tmp_path, now_iso="2026-03-30T12:20:00+00:00")

    heartbeat = json.loads((tmp_path / "state" / "heartbeat.json").read_text(encoding="utf-8"))

    assert heartbeat["last_keepalive"] == "2026-03-30T10:20:00+00:00"


def test_bootstrap_state_tree_rewrites_heartbeat_when_touch_flag_is_true(tmp_path):
    bootstrap_state_tree(tmp_path, now_iso="2026-03-30T10:20:00+00:00")
    bootstrap_state_tree(
        tmp_path,
        now_iso="2026-03-30T12:20:00+00:00",
        touch_heartbeat=True,
        workflow="keepalive-state",
    )

    heartbeat = json.loads((tmp_path / "state" / "heartbeat.json").read_text(encoding="utf-8"))

    assert heartbeat["last_keepalive"] == "2026-03-30T12:20:00+00:00"
    assert heartbeat["workflow"] == "keepalive-state"


def test_merge_registry_namespace_preserves_remote_other_namespace():
    remote = {
        "metadata": {"schema_version": 1, "last_compaction_week": ""},
        "papers": {
            "paper-1": {
                "paper_id": "paper-1",
                "ingest": {"notion_page_id": "page-old"},
                "export": {"zotero_item_key": "ITEM1"},
            }
        },
    }
    local = {
        "metadata": {"schema_version": 1, "last_compaction_week": ""},
        "papers": {
            "paper-1": {
                "paper_id": "paper-1",
                "ingest": {"notion_page_id": "page-new"},
            },
            "paper-2": {
                "paper_id": "paper-2",
                "ingest": {"notion_page_id": "page-2"},
            },
        },
    }

    merged = merge_registry_namespace(remote, local, "ingest")

    assert merged["papers"]["paper-1"]["ingest"]["notion_page_id"] == "page-new"
    assert merged["papers"]["paper-1"]["export"]["zotero_item_key"] == "ITEM1"
    assert merged["papers"]["paper-2"]["ingest"]["notion_page_id"] == "page-2"


def test_keepalive_workflow_checks_out_code_and_automation_state_workspace():
    workflow = yaml.safe_load(Path(".github/workflows/keepalive_state.yaml").read_text(encoding="utf-8"))

    steps = workflow["jobs"]["keepalive"]["steps"]
    checkout_steps = [step for step in steps if step.get("uses") == "actions/checkout@v4"]

    assert len(checkout_steps) == 2
    assert checkout_steps[0]["name"] == "Checkout code"
    assert "with" not in checkout_steps[0]
    assert checkout_steps[1]["name"] == "Checkout automation-state workspace"
    assert checkout_steps[1]["with"]["path"] == "automation-state"

    update_step = next(step for step in steps if step["name"] == "Update heartbeat")
    assert "python scripts/touch_heartbeat.py" in update_step["run"]
    assert "--root automation-state" in update_step["run"]


def test_write_duplicate_audit_report_creates_machine_readable_file(tmp_path):
    registry_path = tmp_path / "state" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps({"metadata": {}, "papers": {}}, ensure_ascii=False), encoding="utf-8")

    output_path = write_duplicate_audit_report(
        registry_path,
        occurred_at="2026-03-31T02:03:04+00:00",
        workflow="ingest-to-notion",
        entries=[{"paper_id": "doi:10.1/example", "reason": "duplicate_exact_match"}],
        degraded_reason="",
    )

    payload = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert payload["workflow"] == "ingest-to-notion"
    assert payload["entry_count"] == 1
    assert payload["entries"][0]["paper_id"] == "doi:10.1/example"
