"""Suite-wide fixtures: pytest must never touch the live data source or the live usage dir."""
import pytest


@pytest.fixture(autouse=True)
def _fresh_save_tokens():
    """The three process-global stores — isolate tests. The read store joined this fixture
    2026-08-20: reads leaked between tests, so a stage-1 gate test could inherit another test's
    credit and pass without exercising the gate. The pinned risk task joined the same day and
    for the same reason: start_journey_fn sets it, and a test that starts a journey was handing
    its governance classification to every test after it."""
    import graph_files, addendum_tools
    graph_files._validated_records.clear()
    graph_files.forget_reads()
    addendum_tools.forget_risk_task()
    yield


@pytest.fixture(autouse=True)
def _graph_pages_to_tmp(monkeypatch, tmp_path):
    """The write-triggered graph regen writes public/graph/<company>/ pages — point the
    output at tmp so no test ever mutates the repo tree as a side effect."""
    import dep_graph
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path / "graph-pages")


@pytest.fixture(autouse=True)
def _advance_gate_world(monkeypatch):
    """The journey advance gate reads company artifacts from Graph — pytest must never
    touch the live data source (the real graph-secret is reachable on this box). Default
    world: every canonical stage artifact exists and meets its contract, so next_step
    tests stay navigation tests. Gate tests install their own worlds over this stub."""
    import addendum_tools
    import graph_files

    def saved_and_conforming(company, path):
        c = graph_files._contract_for(path) or {"markers": [], "min_bytes": 0}
        content = "\n".join(c["markers"]) + "\n" + "x" * c["min_bytes"]
        return {"path": f"{company}/{path}", "content": content,
                "size": len(content.encode("utf-8"))}

    monkeypatch.setattr(addendum_tools, "_fetch_artifact", saved_and_conforming)


@pytest.fixture(autouse=True)
def _usage_log_to_tmp(monkeypatch, tmp_path):
    """call_log writes one JSON line per decorated tool call — under pytest that must land in
    tmp, never in the live usage dir (141 test rows polluted it on 2026-08-16 before this)."""
    import call_log
    monkeypatch.setattr(call_log, "DIR", tmp_path / "bia-usage")


@pytest.fixture(autouse=True)
def _rooms_to_tmp(monkeypatch, tmp_path):
    """Room routing is directory existence under ROOMS_DIR — point it at tmp so no test
    ever sees a real room and every test starts roomless (mirrors _graph_pages_to_tmp)."""
    import graph_files
    monkeypatch.setattr(graph_files, "ROOMS_DIR", tmp_path / "demo-rooms")
