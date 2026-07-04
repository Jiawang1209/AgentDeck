from __future__ import annotations

from pathlib import Path

from agentdeck.contracts import project_view_contract_payload, project_view_example
from agentdeck.models import PROJECT_VIEW_SCHEMA_VERSION


def test_project_view_contract_payload_is_reusable_without_cli(tmp_path: Path) -> None:
    contract_path = tmp_path / "project-view-schema.md"
    contract_path.write_text("# ProjectView Contract\n", encoding="utf-8")

    payload = project_view_contract_payload(contract_path)

    assert payload["schema_version"] == PROJECT_VIEW_SCHEMA_VERSION
    assert payload["status_command"] == "agentdeck status"
    assert payload["contract_path"] == str(contract_path)
    assert payload["contract_exists"] is True
    assert "recovery" in payload["top_level_fields"]
    assert "recommended_action" in payload["recovery_fields"]
    assert payload["recommended_action_fields"] == [
        "label",
        "command",
        "safety",
        "requires_explicit_user",
        "source",
        "target_id",
    ]


def test_project_view_example_matches_contract_field_lists(tmp_path: Path) -> None:
    contract_path = tmp_path / "missing.md"
    payload = project_view_contract_payload(contract_path)
    example = project_view_example()

    assert payload["schema_version"] == example["schema_version"]
    assert set(payload["top_level_fields"]) == set(example)
    assert set(payload["recovery_fields"]) == set(example["recovery"])
    assert set(payload["recommended_action_fields"]) == set(example["recovery"]["recommended_action"])
    assert example["recovery"]["recommended_action"]["target_id"] == "act_example"
