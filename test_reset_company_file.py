"""reset_company_file — the operator lane for clearing ONE test artefact.

Deliberately not an MCP tool. H3 is open: nothing binds a session to a BIA folder, watched live
2026-08-20 09:37:58 when a run attempted a write into another BIA's `output/slaughter/`. Today
that hole is additive and the worst case is a stray file; an agent-callable delete would make it
destructive. So the capability exists for the operator and the agent never sees it.
"""
import pytest

import reset_company_file as rc


@pytest.mark.parametrize("path", [
    "01_Organisation/company-profile.md",       # source material, never a test artefact
    "02_BCM-Method/method.json",
    "03_Dependencies/dependency-register.json",
    "../../etc/passwd",
    "output/../01_Organisation/org-and-roles.md",
])
def test_only_output_paths_can_be_reset(path):
    """The blast radius is the run folder. Company source material is what every BIA is built
    from and is not reproducible from this tree — refuse it by shape, before any network call."""
    with pytest.raises(SystemExit):
        rc.check("marschkamp", path)


def test_an_output_path_is_accepted():
    assert rc.check("marschkamp", "output/logistics/stage1-scope-and-guide.md") == \
        "output/logistics/stage1-scope-and-guide.md"


def test_the_secret_is_found_without_the_service_environment(monkeypatch):
    """Run by hand as svc-bia it died on `missing graph secret file:
    /opt/apps/bia-workflow/graph-secret`. graph_files derives the secret from
    BIA_WORKFLOW_TOKEN_FILE, which the systemd unit sets and a bare shell does not, so the
    script inherited a path that only exists inside the service. An operator lane that needs
    four environment variables spelled correctly is not an operator lane."""
    monkeypatch.delenv("BIA_WORKFLOW_TOKEN_FILE", raising=False)
    assert str(rc.secret_path()) == "/srv/addendum/graph-secret"


def test_an_explicit_token_file_still_wins(monkeypatch):
    monkeypatch.setenv("BIA_WORKFLOW_TOKEN_FILE", "/tmp/elsewhere/secret")
    assert str(rc.secret_path()) == "/tmp/elsewhere/graph-secret"


def test_a_room_slug_is_refused_with_a_filesystem_pointer():
    """T6 (2026-08-24): without the guard a room slug raw-DELETEs against Graph, gets a
    404, prints 'already absent' — and the room file survives on disk. A lying no-op.
    Refused by shape instead, pointing at the room's real data plane."""
    import graph_files
    (graph_files.ROOMS_DIR / "kranich-x7k2mp" / "output").mkdir(parents=True)
    with pytest.raises(SystemExit) as e:
        rc.check("kranich-x7k2mp", "output/slaughter/stage1-scope-and-guide.md")
    msg = str(e.value)
    assert "demo room" in msg and "re-mint" in msg, msg
