"""Operator re-arm script — import safety + constant shape (no network).

The script's body used to run at module level, so `import rearm_register` fired a live
SharePoint write. These tests lock the __main__ guard in place.
"""
import importlib
import json

import graph_files
import pytest


def test_import_does_not_touch_sharepoint(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("import must not call SharePoint")

    monkeypatch.setattr(graph_files, "read_file", boom)
    monkeypatch.setattr(graph_files, "write_file", boom)
    module = importlib.reload(importlib.import_module("rearm_register"))
    assert callable(module.main)


def test_armed_state_covers_every_run_mutated_field():
    # The 2026-07-21 acceptance run proved a run mutates more than owner/deputy —
    # the reset must cover all 8 fields, with the two seeded stall flags intact.
    import rearm_register

    armed = rearm_register.ARMED
    assert set(armed) == {"owner_role", "owner_name", "stellvertreter", "second_source",
                          "second_source_owner", "quality_flags", "current_capability",
                          "contract_status", "notes"}
    assert armed["owner_name"] is None and armed["stellvertreter"] is None
    assert armed["second_source_owner"] == "TBD (open)"
    assert len(armed["quality_flags"]) == 2
    assert armed["quality_flags"][0].startswith("Missing owner:")
    assert rearm_register.PATH == "03_Dependencies/dependency-register.json"


def test_company_argument_reaches_sharepoint(monkeypatch):
    # The demo room (marschkamp-demo) is re-armed with the same script — a company argument
    # that is accepted but ignored would silently re-arm the private room instead.
    import rearm_register

    seen = []

    def fake_read(company, path):
        seen.append(company)
        return {"content": json.dumps({"LF-ABP-01": dict(rearm_register.ARMED)})}

    monkeypatch.setattr(graph_files, "read_file", fake_read)
    monkeypatch.setattr(graph_files, "write_file", lambda *a, **k: pytest.fail("armed: no write"))

    assert rearm_register.main("marschkamp-demo") == 0  # already armed
    assert seen == ["marschkamp-demo"]
    assert rearm_register.main.__defaults__ == ("marschkamp",)
