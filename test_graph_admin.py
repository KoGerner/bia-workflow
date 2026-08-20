"""Unit tests for graph_admin — unjailed operator CRUD, Graph mocked.

This module is deliberately NOT registered as an @mcp.tool anywhere (see server.py — it never
imports graph_admin). That is the real security boundary: the BIA-Workflow Copilot agent is
wired to https://agent.ai4bcm.org/mcp and can only ever reach what server.py registers.
These tests exercise graph_admin's own logic (reused jail/traversal guard, dropped write
restrictions) — they do not and cannot prove the MCP-exposure boundary; that's proven by
server.py simply never importing this module (see test_admin_never_imported_by_server below).
"""
import pathlib

import httpx
import pytest

import graph_admin as ga
import graph_files as gf


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    secret = tmp_path / "graph-secret"
    secret.write_text("TENANT_ID=t-id\nCLIENT_ID=c-id\nCLIENT_SECRET=s3cr3t\n")
    monkeypatch.setattr(gf, "SECRET_FILE", secret)
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp",))
    gf._cache.clear()


def _mock(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(gf, "_client",
                        lambda: httpx.Client(transport=transport, follow_redirects=True))


def token_site_drive(request):
    url = str(request.url)
    if "oauth2/v2.0/token" in url:
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if url.endswith(":/sites/AIBCM"):
        return httpx.Response(200, json={"id": "site-1"})
    if url.endswith("/sites/site-1/drive"):
        return httpx.Response(200, json={"id": "drive-1"})
    return None


def test_move_rename_in_place(monkeypatch):
    """Same-folder rename: PATCH body carries only `name`, no parentReference."""
    seen = {}
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert request.method == "PATCH"
        seen["url"] = str(request.url)
        seen["json"] = httpx.Request(request.method, request.url, content=request.content).content
        return httpx.Response(200, json={"id": "f1", "name": "new.md"})
    _mock(monkeypatch, handler)
    out = ga.move_file("marschkamp", "07_Interviews/old.md", "07_Interviews/new.md")
    assert out["moved"] is True
    assert "root:/marschkamp/07_Interviews/old.md" in seen["url"]
    assert b'"name": "new.md"' in seen["json"] or b'"name":"new.md"' in seen["json"]
    assert b"parentReference" not in seen["json"]


def test_move_to_different_folder(monkeypatch):
    """Cross-folder move: PATCH body carries parentReference.path + name."""
    seen = {}
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert request.method == "PATCH"
        seen["json"] = request.content
        return httpx.Response(200, json={"id": "f1"})
    _mock(monkeypatch, handler)
    out = ga.move_file("marschkamp", "08_Prior-Cycle/prior-bia.md",
                        "99_Sandbox/prior-bia.md")
    assert out["moved"] is True
    assert b"99_Sandbox" in seen["json"]


def test_move_source_not_found(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        return httpx.Response(404)
    _mock(monkeypatch, handler)
    out = ga.move_file("marschkamp", "01_Organisation/ghost.md", "01_Organisation/x.md")
    assert "error" in out


def test_move_refuses_bad_company():
    out = ga.move_file("evilco", "a.md", "b.md")
    assert "error" in out


def test_move_refuses_path_traversal():
    out = ga.move_file("marschkamp", "../../etc/passwd", "x.md")
    assert "error" in out


def test_move_not_jailed_to_output(monkeypatch):
    """The whole point of graph_admin: a non-output/ destination is allowed —
    proves this module is genuinely unjailed, unlike graph_files.write_file."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        return httpx.Response(200, json={"id": "f1"})
    _mock(monkeypatch, handler)
    out = ga.move_file("marschkamp", "01_Organisation/company-profile.md",
                        "01_Organisation/company-profile-v2.md")
    assert out.get("moved") is True
    # the equivalent write through graph_files.write_file would be refused:
    denied = gf.write_file("marschkamp", "01_Organisation/company-profile-v2.md",
                           "x", user_confirmed=True)
    assert "error" in denied


def test_delete_file_happy(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert request.method == "DELETE"
        assert "root:/marschkamp/output/stale.md" in str(request.url)
        return httpx.Response(204)
    _mock(monkeypatch, handler)
    out = ga.delete_file("marschkamp", "output/stale.md")
    assert out["deleted"] is True


def test_delete_not_found(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        return httpx.Response(404)
    _mock(monkeypatch, handler)
    out = ga.delete_file("marschkamp", "output/ghost.md")
    assert "error" in out


def test_delete_refuses_path_traversal():
    out = ga.delete_file("marschkamp", "../otherco/x.md")
    assert "error" in out


def test_delete_refuses_bad_company():
    out = ga.delete_file("evilco", "x.md")
    assert "error" in out


def test_create_folder_happy(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert request.method == "POST"
        assert "root:/marschkamp:/children" in str(request.url)
        return httpx.Response(201, json={"id": "f1", "name": "09_Scratch", "folder": {}})
    _mock(monkeypatch, handler)
    out = ga.create_folder("marschkamp", "09_Scratch")
    assert out["created"] is True


def test_create_folder_nested(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert "root:/marschkamp/07_Interviews:/children" in str(request.url)
        return httpx.Response(201, json={"id": "f1"})
    _mock(monkeypatch, handler)
    out = ga.create_folder("marschkamp", "07_Interviews/drafts")
    assert out["created"] is True


def test_create_folder_conflict(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        return httpx.Response(409)
    _mock(monkeypatch, handler)
    out = ga.create_folder("marschkamp", "01_Organisation")
    assert "error" in out


def test_create_folder_refuses_bad_company():
    out = ga.create_folder("evilco", "x")
    assert "error" in out


def test_create_file_happy(monkeypatch):
    """New file: existence GET returns 404, then PUT :/content carries the body."""
    seen = {}
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        if request.method == "GET":
            return httpx.Response(404)  # does not exist yet
        assert request.method == "PUT"
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(201, json={"id": "f1"})
    _mock(monkeypatch, handler)
    out = ga.create_file("marschkamp", "07_Interviews/zz-poison-test.md", "hello")
    assert out["created"] is True
    assert "root:/marschkamp/07_Interviews/zz-poison-test.md:/content" in seen["url"]
    assert seen["body"] == b"hello"


def test_create_file_refuses_existing_without_overwrite(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        return httpx.Response(200, json={"id": "exists"})  # existence GET: already there
    _mock(monkeypatch, handler)
    out = ga.create_file("marschkamp", "07_Interviews/x.md", "hi")
    assert "error" in out and "already exists" in out["error"]


def test_create_file_not_jailed_to_output(monkeypatch):
    """Operator create can target 07_Interviews/; the agent's own write tool cannot."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(201, json={"id": "f1"})
    _mock(monkeypatch, handler)
    out = ga.create_file("marschkamp", "07_Interviews/new.md", "x")
    assert out.get("created") is True
    denied = gf.write_file("marschkamp", "07_Interviews/new.md", "x", user_confirmed=True)
    assert "error" in denied


def test_create_file_refuses_path_traversal():
    out = ga.create_file("marschkamp", "../otherco/x.md", "x")
    assert "error" in out


def test_admin_never_imported_by_server():
    """The security boundary: server.py (what Copilot Studio's MCP wiring reaches)
    must never import graph_admin. If this ever fails, admin CRUD just became reachable
    from the demo agent's endpoint."""
    server_src = (pathlib.Path(__file__).parent / "server.py").read_text(encoding="utf-8")
    assert "graph_admin" not in server_src
