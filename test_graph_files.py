"""Unit tests for graph_files — jail + gate logic, Graph mocked."""
import hashlib
import json
import re

import httpx
import pytest

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


def test_read_follows_download_redirect(monkeypatch):
    """Graph serves file content via a 302 to a download URL — reads must follow it."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        if ":/content" in url:
            return httpx.Response(302, headers={"Location": "https://dl.example/x"})
        if url == "https://dl.example/x":
            return httpx.Response(200, text="# via redirect")
        return httpx.Response(404)
    _mock(monkeypatch, handler)
    out = gf.read_file("marschkamp", "company-profile.md")
    assert out["content"] == "# via redirect"


def token_site_drive(request):
    url = str(request.url)
    if "oauth2/v2.0/token" in url:
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    if url.endswith(":/sites/AIBCM"):
        return httpx.Response(200, json={"id": "site-1"})
    if url.endswith("/sites/site-1/drive"):
        return httpx.Response(200, json={"id": "drive-1"})
    return None


def test_list_files_happy(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert "/drives/drive-1/root:/marschkamp:/children" in str(request.url)
        return httpx.Response(200, json={"value": [
            {"name": "company-profile.md", "size": 14309, "file": {}},
            {"name": "output", "size": 0, "folder": {}},
        ]})
    _mock(monkeypatch, handler)
    out = gf.list_files("marschkamp")
    assert out["files"][0]["name"] == "company-profile.md"
    assert out["files"][1]["is_folder"] is True


def test_search_files_walks_the_room_and_never_fetches_a_fixture(monkeypatch):
    """Tool #15: Graph's own drive search() is app-only-unsupported here (500 generalException,
    probed live), so this walks. The load-bearing property is that 09_Evaluation and
    pack-backup-* are skipped BEFORE the read, not filtered out of the results: rt1-poison.md
    is a live prompt-injection fixture, so 'we dropped it afterwards' would already have pulled
    its bytes in. Assert on the URLs actually requested, not just on the returned paths."""
    TREE = {"": [("output", 0, True), ("approval-log.jsonl", 40, False),
                 ("09_Evaluation", 0, True), ("README.md", 20, False)],
            "output": [("bia-record.json", 30, False), ("pack-backup-1", 0, True)]}
    BODY = {"approval-log.jsonl": "Torsten Ahlgrim approved the record",
            "README.md": "nothing to see",
            "output/bia-record.json": '{"owner": "OLGA MILEVSKA"}'}
    seen = []

    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        seen.append(url)
        tail = url.split("root:/marschkamp", 1)[1]
        if tail.endswith(":/children"):
            rel = tail.removesuffix(":/children").strip("/")
            return httpx.Response(200, json={"value": [
                {"name": n, "size": s, **({"folder": {}} if f else {"file": {}})}
                for n, s, f in TREE[rel]]})
        return httpx.Response(200, text=BODY[tail.removesuffix(":/content").strip("/")])

    _mock(monkeypatch, handler)
    out = gf.search_files("marschkamp", "  olga   MILEVSKA ")
    assert [m["path"] for m in out["matches"]] == ["output/bia-record.json"]  # case-insensitive
    assert out["query"] == "olga MILEVSKA"         # whitespace collapsed, case left alone
    assert out["excluded_fixture_paths"] == 2      # 09_Evaluation and pack-backup-1
    assert out["truncated"] is False
    assert all("content" not in m for m in out["matches"])
    # the point of the whole exclusion: those bytes were never requested
    assert not [u for u in seen if "09_Evaluation" in u or "pack-backup" in u], seen


def test_search_files_rejects_empty_query_and_unknown_company():
    assert "error" in gf.search_files("marschkamp", "   ")
    assert "unknown company" in gf.search_files("nope", "x")["error"]


def test_read_file_happy(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert "root:/marschkamp/company-profile.md:/content" in str(request.url)
        return httpx.Response(200, text="# Marschkamp Fleisch GmbH")
    _mock(monkeypatch, handler)
    out = gf.read_file("marschkamp", "company-profile.md")
    assert out["content"].startswith("# Marschkamp")


def test_bad_company_refused():
    assert "error" in gf.list_files("evilco")
    assert "error" in gf.read_file("evilco", "x.md")


def test_traversal_refused():
    for bad in ("../otherco/x.md", "a/../../x", "/abs.md", "a\\b.md", ""):
        assert "error" in gf.read_file("marschkamp", bad)


def test_write_needs_confirmation():
    out = gf.write_file("marschkamp", "output/x.md", "hi", user_confirmed=False)
    assert "error" in out and "approval" in out["error"].lower()


def test_write_jailed_to_output():
    out = gf.write_file("marschkamp", "company-profile.md", "hi", user_confirmed=True)
    assert "error" in out  # not output/, not a sanctioned exception


def _fake_register(n_assets=15):
    """A register-shaped payload big enough to clear the full-register floor."""
    reg = {"synthetic": True}
    for i in range(n_assets):
        reg[f"AS-{i:02d}"] = {"asset_id": f"AS-{i:02d}", "owner_name": f"Owner {i}",
                              "notes": "x" * 700}
    reg["LF-ABP-01"] = {"asset_id": "LF-ABP-01", "owner_name": None, "stellvertreter": None,
                        "second_source_owner": "TBD (open)",
                        "quality_flags": ["Missing owner: backup renderer (open / TBD)"],
                        "notes": "seeded"}
    return reg


def test_write_exceptions_allowed(monkeypatch):
    """The register is a write target outside output/. Uses the stateful handler because
    the register lane is byte-read-back verified since the §RUN finding-3 fix."""
    _register_handler(monkeypatch, _fake_register())
    out = gf.write_file("marschkamp", "03_Dependencies/dependency-register.json",
                        json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n",
                        user_confirmed=True, mode="overwrite")
    assert out.get("written") is True


def test_register_write_rejects_prose():
    """The P4 incident payload (lessons #16): approval-summary prose written AS the register."""
    out = gf.write_file("marschkamp", "03_Dependencies/dependency-register.json",
                        "REGISTER UPDATE APPROVED FOR LF-ABP-01 ONLY:\n"
                        "owner_name=Dr. Katrin Sauer\nstellvertreter=Nadine Pohl\n",
                        user_confirmed=True, mode="overwrite")
    assert "error" in out and "update_register_entry" in out["error"]


def test_register_write_rejects_shrunken_register():
    tiny = json.dumps({"synthetic": True, "LF-ABP-01": {"owner_name": "X"}})
    out = gf.write_file("marschkamp", "03_Dependencies/dependency-register.json", tiny,
                        user_confirmed=True, mode="overwrite")
    assert "error" in out and "update_register_entry" in out["error"]


def test_approval_log_write_still_unguarded(monkeypatch):
    """The guard is register-scoped: the jsonl ledger keeps its existing contract."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        if request.method == "GET":
            return httpx.Response(200, json={"id": "f2"})
        return httpx.Response(200, json={"id": "f2"})
    _mock(monkeypatch, handler)
    out = gf.write_file("marschkamp", "approval-log.jsonl", '{"event": "x"}\n',
                        user_confirmed=True, mode="overwrite")
    assert out.get("written") is True


def _register_handler(monkeypatch, reg):
    """Stateful mock: serves the register, records PUTs, serves updated content after."""
    state = {"content": json.dumps(reg, ensure_ascii=False, indent=2) + "\n", "puts": 0}

    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        if request.method == "GET" and url.endswith(":/content"):
            return httpx.Response(200, text=state["content"])
        if request.method == "GET":
            return httpx.Response(200, json={"id": "f1"})  # metadata: exists
        if request.method == "PUT":
            state["content"] = request.content.decode("utf-8")
            state["puts"] += 1
            return httpx.Response(200, json={"id": "f1"})
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    return state


def test_update_register_entry_needs_confirmation():
    out = gf.update_register_entry("marschkamp", "LF-ABP-01", {"owner_name": "X"})
    assert "error" in out and "approval" in out["error"].lower()


def test_update_register_entry_patches_one_entry_only(monkeypatch):
    reg = _fake_register()
    state = _register_handler(monkeypatch, reg)
    out = gf.update_register_entry(
        "marschkamp", "LF-ABP-01",
        {"owner_name": "Dr. Katrin Sauer", "stellvertreter": "Nadine Pohl"},
        user_confirmed=True)
    assert out.get("updated") is True
    assert out["changed_fields"] == ["owner_name", "stellvertreter"]
    new = json.loads(state["content"])
    assert new["LF-ABP-01"]["owner_name"] == "Dr. Katrin Sauer"
    assert new["LF-ABP-01"]["second_source_owner"] == "TBD (open)"  # untouched field survives
    assert new["AS-03"] == reg["AS-03"]  # other assets byte-faithful
    assert state["puts"] == 1


def test_update_register_entry_refuses_a_no_op_change(monkeypatch):
    """Same guard as the record lane: a field already at the requested value must not
    rewrite the whole 31KB register and fire a graph regen for nothing."""
    reg = _fake_register()
    state = _register_handler(monkeypatch, reg)
    out = gf.update_register_entry("marschkamp", "LF-ABP-01",
                                   {"owner_name": reg["LF-ABP-01"]["owner_name"]},
                                   user_confirmed=True)
    assert "error" in out and "already" in out["error"]
    assert state["puts"] == 0


def test_update_register_entry_applies_only_the_fields_that_change(monkeypatch):
    reg = _fake_register()
    state = _register_handler(monkeypatch, reg)
    out = gf.update_register_entry(
        "marschkamp", "LF-ABP-01",
        {"owner_name": reg["LF-ABP-01"]["owner_name"], "stellvertreter": "Nadine Pohl"},
        user_confirmed=True)
    assert out.get("updated") is True, out
    assert out["changed_fields"] == ["stellvertreter"]
    assert state["puts"] == 1


def test_update_register_entry_accepts_json_string_changes(monkeypatch):
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "LF-ABP-01",
                                   '{"owner_name": "Dr. Katrin Sauer"}', user_confirmed=True)
    assert out.get("updated") is True
    assert out["entry"]["owner_name"] == "Dr. Katrin Sauer"


def test_update_register_entry_unknown_asset(monkeypatch):
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "NOPE-99", {"owner_name": "X"},
                                   user_confirmed=True)
    assert "error" in out and "LF-ABP-01" in out["error"]


def test_update_register_entry_rejects_asset_id_change(monkeypatch):
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "LF-ABP-01", {"asset_id": "HACK"},
                                   user_confirmed=True)
    assert "error" in out


def test_update_register_entry_refuses_corrupt_register(monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        if request.method == "GET" and str(request.url).endswith(":/content"):
            return httpx.Response(200, text="REGISTER UPDATE APPROVED — not json")
        return httpx.Response(200, json={"id": "f1"})
    _mock(monkeypatch, handler)
    out = gf.update_register_entry("marschkamp", "LF-ABP-01", {"owner_name": "X"},
                                   user_confirmed=True)
    assert "error" in out and "restore" in out["error"].lower()


def test_write_exceptions_are_path_qualified(monkeypatch):
    """The register moved into 03_Dependencies/ — the bare root-level filename is no
    longer a valid write target (it isn't where the file lives anymore)."""
    out = gf.write_file("marschkamp", "dependency-register.json", "{}",
                        user_confirmed=True, mode="overwrite")
    assert "error" in out


# `test_create_refuses_existing` stood here until 2026-08-20. It pinned a refusal that has been
# reversed on purpose: the server now picks create-vs-overwrite from the DriveItem it fetches
# anyway. The replacement, with the measured reason, is
# `test_create_of_a_file_that_is_there_overwrites_it_and_the_receipt_says_so`.


def test_write_size_cap():
    out = gf.write_file("marschkamp", "output/x.md", "x" * (1_000_001), user_confirmed=True)
    assert "error" in out and "large" in out["error"].lower()


# ─────────────────────────────── allowlist write binding (widened 2026-08-03)
# Both rooms are writable again: the Teams agent needs marschkamp, the public agent
# needs marschkamp-demo, and one shared service+token cannot tell them apart. The
# server binds writes to the allowlist; which room a given agent aims at is Part D's
# job. Supersedes the P-16 first-company-only binding (I-12, lesson #35).

def test_unallowlisted_company_write_refused_before_any_network(monkeypatch):
    """A company outside BIA_WORKFLOW_COMPANIES is refused by _jail() before any Graph
    call — the allowlist is the whole write binding now."""
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp", "marschkamp-demo"))
    calls = []
    monkeypatch.setattr(gf, "_client", lambda: calls.append("net"))  # tripwire
    out = gf.write_file("acme-corp", "output/pp4-handoff.md", "# exact copy",
                        user_confirmed=True,
                        expect={"markers": ["# exact copy"], "min_bytes": 1})
    assert "error" in out and "invalid path" in out["error"]
    assert calls == []


def test_both_allowlisted_companies_pass_the_write_gate(monkeypatch):
    """Either allowlisted room clears the company gate. Empty content stops the call
    immediately after it, so the gate is proven without touching the network."""
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp", "marschkamp-demo"))
    calls = []
    monkeypatch.setattr(gf, "_client", lambda: calls.append("net"))  # tripwire
    for company in ("marschkamp", "marschkamp-demo"):
        out = gf.write_file(company, "output/x.md", "", user_confirmed=True)
        assert "error" in out and "content is empty" in out["error"]
        assert "read-only" not in out["error"]
    assert calls == []


def test_unallowlisted_company_register_write_refused(monkeypatch):
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp", "marschkamp-demo"))
    out = gf.write_file("acme-corp", gf.REGISTER_PATH,
                        json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n",
                        user_confirmed=True, mode="overwrite")
    assert "error" in out and "invalid path" in out["error"]


def test_demo_company_read_still_allowed(monkeypatch):
    """The binding is write-side only — the public agent keeps its read grounding."""
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp", "marschkamp-demo"))

    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert "root:/marschkamp-demo/company-profile.md:/content" in str(request.url)
        return httpx.Response(200, text="# Demo GmbH")
    _mock(monkeypatch, handler)
    out = gf.read_file("marschkamp-demo", "company-profile.md")
    assert out["content"] == "# Demo GmbH"


# ------------------------------------------------------- in-loop verifier: expect contract (§12)

STUB_290 = (
    "# BIA 2026 – Slaughter Process\n"
    "## Stage 1 – Scope and Risk\n\n"
    "Company: Marschkamp Fleisch GmbH & Co. KG\n"
    "Process in Scope: Slaughter (stunning → bleeding → scalding → dehairing → evisceration)\n"
    "Owner Role: Slaughter Production Manager\n"
    "Status: Approved Stage 1\n"
)  # the P5 D1 payload shape: a regenerated summary written instead of the approved artifact

FULL_EXPECT = {"markers": ["## Scope Note", "## Interview Guide"], "min_bytes": 400}
FULL_CONTENT = ("# BIA Stage 1\n## Scope Note\n" + "scope line\n" * 30 +
                "## Interview Guide\n" + "question line\n" * 30)


def _artifact_handler(state, web_url=None):
    """Graph double for one output/ artifact: meta 404 (new file), PUT stored, read-back echoes.
    `web_url` mirrors the DriveItem's webUrl that a real PUT returns (the openable link)."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        if request.method == "PUT":
            state["put"] = request.content.decode("utf-8")
            item = {"id": "f1"}
            if web_url:
                item["webUrl"] = web_url
            return httpx.Response(201, json=item)
        if url.endswith(":/content"):
            return httpx.Response(200, text=state.get("readback", state.get("put", "")))
        if request.method == "GET":
            return httpx.Response(404)  # meta: file does not exist yet
        return httpx.Response(404)
    return handler


def test_output_write_without_expect_is_refused(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT, user_confirmed=True)
    assert "error" in out and "expect" in out["error"]
    assert "put" not in state


def test_expect_rejects_stub_before_any_write(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", STUB_290, user_confirmed=True,
                        expect={"markers": ["## Scope Note", "## Interview Guide"],
                                "min_bytes": 5000})
    assert "error" in out and "COMPLETE approved content" in out["error"]
    assert "put" not in state  # refused before anything reached SharePoint


def test_expect_pass_writes_verifies_and_returns_human_line(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT, user_confirmed=True,
                        expect=FULL_EXPECT)
    assert out.get("written") is True
    line = out["verification"]["human_line"]
    assert "Saved" in line and "2 sections" in line
    assert "sha" not in line.lower() and "marschkamp/" not in line  # no jail internals


def test_receipt_names_the_file(monkeypatch):
    """Run (a), 2026-08-18, Hans §5: two identical receipts in a row read like a bug — name
    the file. REVERSED to the full company-relative path 2026-08-24 by the same judge: 'add
    the folder, i asked twice in my own run where things were saved' — and sales/ and
    packing/ both hold a stage1-scope-and-guide.md, so the basename is ambiguous the moment
    two BIAs exist. The jailed company prefix still never appears (§A.7's one-path-form
    rule: the path he approved is the path the receipt repeats)."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/slaughter/scope-notes.md", FULL_CONTENT,
                        user_confirmed=True, expect=FULL_EXPECT)
    line = out["verification"]["human_line"]
    assert "output/slaughter/scope-notes.md" in line
    assert "marschkamp/" not in line


def test_receipt_states_the_byte_count_it_wrote(monkeypatch):
    """run (b) 2026-08-18, Hans 5: "'Saved and checked: stage1-scope-and-guide.md matches what you
    approved (all 4 required sections present).' -> 'saved, 3,955 bytes, 4 sections.' Marking your
    own homework is not a receipt; the byte count is." This reverses part of d1a0128's wording, from
    the same judge — the file name stays, the self-certification goes, and the number the user can
    check against the folder listing takes its place."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/slaughter/scope-notes.md", FULL_CONTENT,
                        user_confirmed=True, expect=FULL_EXPECT)
    line = out["verification"]["human_line"]
    assert f"{len(FULL_CONTENT.encode('utf-8')):,} bytes" in line
    assert "matches what you approved" not in line
    assert "required sections present" not in line


def test_overwrite_receipt_says_sharepoint_keeps_the_previous_version(monkeypatch):
    """Run (a): Bruno had to say 'the write API doesn't expose version history, so I can't honestly
    promise an overwritten copy survives' — the receipt says it for him. Only on overwrite."""
    def exists(request):
        base = token_site_drive(request)
        if base:
            return base
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "f1"})
        if str(request.url).endswith(":/content"):
            return httpx.Response(200, text=FULL_CONTENT)
        return httpx.Response(200, json={"id": "f1"})  # meta: file EXISTS
    _mock(monkeypatch, exists)
    over = gf.write_file("marschkamp", "output/slaughter/scope-notes.md", FULL_CONTENT,
                         user_confirmed=True, mode="overwrite", expect=FULL_EXPECT)
    assert over.get("written") is True
    assert "previous version" in over["verification"]["human_line"]
    assert "version history" in over["verification"]["human_line"]
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    fresh = gf.write_file("marschkamp", "output/slaughter/scope-notes.md", FULL_CONTENT,
                          user_confirmed=True, expect=FULL_EXPECT)
    assert "previous version" not in fresh["verification"]["human_line"]


def test_expect_readback_mismatch_is_reported(monkeypatch):
    state = {"readback": "truncated"}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT, user_confirmed=True,
                        expect=FULL_EXPECT)
    assert "error" in out and "read-back" in out["error"]


def test_expect_malformed_is_refused(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT, user_confirmed=True,
                        expect={"markers": [], "min_bytes": 0})
    assert "error" in out and "expect" in out["error"]
    assert "put" not in state


# ─────────────────────────────── P7 I-1 part 1b: journey-owned artifact contracts
# The live halfB payload (PUT 2026-07-25 19:40:02, the only bad bytes of all P7): a 367 B
# "Headline Summary" persisted AS the stage-2 record. Verbatim fixture; sha pinned below.

I1_STUB = (
    "# Slaughter BIA Interview Capture — Headline Summary\n"
    "\n"
    "Process: Slaughter (AN-SCHLACHT-01)\n"
    "\n"
    "Key Findings\n"
    "- MTPD: 24 hours\n"
    "- Required RTO: 8 hours\n"
    "- Separate animal-welfare clock begins at ~2 hours\n"
    "- Critical dependencies: official veterinarian, potable water, CO₂, steam, "
    "refrigeration, ABP collection\n"
    "- Major unresolved gap: backup Category 3 disposal capability\n"
)
I1_STUB_SHA = "c39b5fb8ed4401e122f7bacb4e3ee1928ebfd48ba29ab710b7fe9fe4aca9707d"

STAGE2_PATH = "output/slaughter/stage2-interview-capture.md"  # per-BIA folder (owner ruling 2026-08-18)


def test_i1_stub_fixture_is_verbatim():
    data = I1_STUB.encode("utf-8")
    assert len(data) == 367
    assert hashlib.sha256(data).hexdigest() == I1_STUB_SHA


def _stage2_full():
    return ("# Stage 2 — Structured Interview Capture\n\n"
            "## Impacts\n" + "impact line\n" * 30 +
            "## Dependencies\n" + "dependency line\n" * 30 +
            "### Resource requirements by category\n"          # §A.15: all four, always
            "- People — carried from prior transcripts.\n"
            "- IT/applications — partial, via ERP and weighing.\n"
            "- Buildings/seats — not asked in this interview.\n"
            "- Suppliers — not asked in this interview.\n" +
            "## Assumptions\n" + "assumption line\n" * 10 +
            "## Unresolved points\n" + "open point\n" * 10 +
            "## Gaps\n" + "gap line\n" * 10)


def test_contract_refuses_i1_stub_at_canonical_stage2_path(monkeypatch):
    """The live I-1 route: expect declared FROM the stub holds vacuously — the
    journey-owned contract must refuse anyway, teaching stage + missing sections."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", STAGE2_PATH, I1_STUB, user_confirmed=True,
                        expect={"markers": ["# Slaughter BIA Interview Capture",
                                            "Key Findings"],
                                "min_bytes": 300})
    assert "error" in out and "put" not in state
    msg = out["error"]
    assert "Stage-2" in msg
    for missing in ("## Impacts", "## Dependencies", "## Assumptions",
                    "## Unresolved points", "## Gaps"):
        assert missing in msg
    assert "summary" in msg.lower()
    assert "different filename" in msg  # a real summary is licensed to live elsewhere


def test_contract_composes_with_expect(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", STAGE2_PATH, _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts", "## Gaps"], "min_bytes": 800})
    assert out.get("written") is True
    assert "human_line" in out["verification"]
    # 2026-08-16 smart next steps: a verified save on a canonical path names the stage's gate
    # and the literal advance call, so the model never has to guess what comes after a save.
    assert out["next_move"].startswith("Ask for Stage 2 · Structured interview")
    # the write knows the folder from the path, so the literal advance call carries it
    assert "next_step('run-bia', 'capture-transcript', bia='slaughter')" in out["next_move"]


def test_contract_floor_and_markers_come_from_yaml_not_agent(monkeypatch):
    """Marker-complete but under the YAML floor, agent floor lowballed → still refused."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    thin = ("## Impacts\nx\n## Dependencies\nx\n## Assumptions\nx\n"
            "## Unresolved points\nx\n## Gaps\nx\n")
    out = gf.write_file("marschkamp", STAGE2_PATH, thin, user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and "1200" in out["error"]
    assert "put" not in state


def test_contract_matches_case_insensitively_like_sharepoint(monkeypatch):
    """SharePoint resolves paths case-insensitively — a Case-variant canonical path is
    the same file slot and must meet the same contract (review finding, 2026-07-26)."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/slaughter/Stage2-Interview-Capture.md", I1_STUB,
                        user_confirmed=True,
                        expect={"markers": ["Key Findings"], "min_bytes": 300})
    assert "error" in out and "Stage-2" in out["error"]
    assert "put" not in state


def test_save_returns_the_openable_link(monkeypatch):
    """Run (a) 2026-08-18: Hans asked "where is that file actually saved, can i open it somewhere?"
    and got a path, then "so no link, i have to go and look in the folder myself. na gut". The Graph
    PUT response IS the DriveItem and already carries webUrl — it was being discarded."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state, web_url="https://sp.example/sites/x/out.md"))
    out = gf.write_file("marschkamp", STAGE2_PATH, _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 800})
    assert out.get("written") is True
    assert out.get("url") == "https://sp.example/sites/x/out.md"


def test_save_without_a_link_still_succeeds(monkeypatch):
    """A response without webUrl must not break the save — the link is a courtesy, not a contract."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", STAGE2_PATH, _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 800})
    assert out.get("written") is True and "url" not in out


# ─────────────────────────────── per-BIA folders (owner ruling 2026-08-18: add, never overwrite)
# Run (a) 2026-08-18 morning: the Slaughter run overwrote Cutting/Deboning's still-open bia-draft.md +
# bia-signoff.json (BIA-2026-002) because every BIA document sat at one fixed name. Now the six
# documents live in output/<bia>/ (process slug); the shared machine record stays flat and merges.

LEGACY_NAMES = ("stage1-scope-and-guide.md", "stage2-interview-capture.md",
                "stage3-dependency-analysis.md", "bia-draft.md", "bia-signoff.json", "pp4-handoff.md")


@pytest.mark.parametrize("name", LEGACY_NAMES)
def test_flat_legacy_bia_document_names_are_refused(monkeypatch, name):
    """The old singleton path is refused loudly and points at the folder — drift becomes a
    message, never a clobber of another BIA's file."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", f"output/{name}", _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and f"output/<bia>/{name}" in out["error"]
    assert "put" not in state


def test_flat_legacy_name_refused_case_insensitively(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/BIA-Draft.md", _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and "output/<bia>/" in out["error"]
    assert "put" not in state


def test_literal_bia_placeholder_is_refused(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/<bia>/stage2-interview-capture.md", _stage2_full(),
                        user_confirmed=True, expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and "slug" in out["error"]
    assert "put" not in state


def test_non_slug_bia_folder_is_refused(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/Slaughter Process/stage2-interview-capture.md",
                        _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and "lowercase" in out["error"] and "slaughter-process" in out["error"]
    assert "put" not in state


SHARED_FOLDERS = ("owner-interviews", "proposals")


@pytest.mark.parametrize("folder", SHARED_FOLDERS)
def test_shared_folder_is_refused_as_a_bia_slug(monkeypatch, folder):
    """run (b) 2026-08-18 turn 1: the kickoff said the interview is saved to
    output/owner-interviews/; Bruno's Stage 1 card read that as "the BIA folder is
    output/owner-interviews/" and the server took the stage-1 write (20:40:15Z, saved).
    'owner-interviews' is a well-formed slug and the shape check was the only check.
    The yaml declares both folders as shared across BIAs (output/owner-interviews/*.md,
    output/proposals/*-owner-capture.md) — a BIA's stage documents landing in one is the
    cross-BIA collision the per-BIA ruling exists to prevent."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", f"output/{folder}/stage2-interview-capture.md",
                        _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and folder in out["error"]
    assert "put" not in state


def test_shared_folder_still_accepts_its_own_document(monkeypatch):
    """The refusal is about the BIA's stage documents, not the folder — the owner interview
    transcript the folder exists for must still save."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/owner-interviews/vertrieb-interview-transcript.md",
                        _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert out.get("written") is True and "put" in state


def test_undeclared_filename_cannot_enter_a_shared_folder(monkeypatch):
    """D-19, run (c) 2026-08-19 08:01:08Z: both folder jaws hang off `_contract_for`, so a
    filename no stage declares (`pre-interview-request.md`) skipped every folder rule and landed
    where the jail allowed. `output/proposals/` is declared for `*-owner-capture.md` only, so an
    undeclared document there is the same cross-BIA collision run (b) hit — with a filename the
    contract lookup does not recognise. The folder rule is a property of the path, not of the
    contract that happens to match it."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/proposals/pre-interview-request.md",
                        _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and "proposals" in out["error"], out
    assert "put" not in state


def test_undeclared_filename_still_needs_a_slug_shaped_folder(monkeypatch):
    """Same hole, other jaw: 'Slaughter Process/' and 'slaughter/' are two folders for one
    process, and SharePoint keeps whatever it is given."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/Slaughter Process/pre-interview-request.md",
                        _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert "error" in out and "slug" in out["error"], out
    assert "put" not in state


def test_an_undeclared_document_saves_into_its_own_bia_folder(monkeypatch):
    """The rule is the folder, never the filename: W33 thread 5 is a user saying "save it to the
    output, so I can print it for later", and a document nobody declared still belongs to the BIA
    whose folder it is written to."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/slaughter/pre-interview-request.md",
                        _stage2_full(), user_confirmed=True,
                        expect={"markers": ["## Impacts"], "min_bytes": 10})
    assert out.get("written") is True and "put" in state, out


def test_shared_record_path_stays_flat():
    """output/bia-record.json is the one flat artifact — the graph renders from it and it merges."""
    assert gf.RECORD_SAVE_PATH == "output/bia-record.json"
    assert gf._contract_for("output/bia-record.json") is not None
    assert gf._contract_for("output/slaughter/bia-record.json") is None


def test_omitted_content_without_token_refused(monkeypatch):
    """content became optional for the token lane ONLY — an omitted-content overwrite
    must never truncate a ledger to zero bytes."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "approval-log.jsonl", user_confirmed=True,
                        mode="overwrite")
    assert "error" in out and "empty" in out["error"]
    assert "put" not in state


def test_free_novel_path_stays_expect_only(monkeypatch):
    """A manager may genuinely want a summary file — it just can't BE the stage artifact."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/slaughter-bia-capture-summary.md", I1_STUB,
                        user_confirmed=True,
                        expect={"markers": ["Key Findings"], "min_bytes": 300})
    assert out.get("written") is True


# ─────────────────────────────── P-15 (lesson #26): in-loop pp4_issue enumeration
# PE-ZERLEG-01 was dropped in 6 consecutive runs; every catch was a human eyeball.
# The write jaw now runs the same enumeration the offline grader always had.

PP4_PATH = "output/slaughter/pp4-handoff.md"  # per-BIA folder; the jaw keys on the basename


def _pp4_register():
    reg = _fake_register()
    reg["PE-ZERLEG-01"] = {"asset_id": "PE-ZERLEG-01", "owner_name": "Petra Louven",
                           "pp4_issue": True, "notes": "x" * 700}
    reg["AB-KAELTE-02"] = {"asset_id": "AB-KAELTE-02", "owner_name": "R. Boll",
                           "pp4_issue": True, "notes": "x" * 700}
    return reg


def _pp4_handoff(*ids):
    return ("# PP4 requirements handoff\n"
            "## Continuity requirements\n" + "requirement line\n" * 60 +
            "## Risk-assessment referrals\n" + "\n".join(ids) + "\n" +
            "## Missing evidence\n" + "evidence line\n" * 40)


def _pp4_world(monkeypatch, register_text):
    """Graph double: register content served, handoff meta 404 (new), PUT recorded."""
    state = {}

    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        if "dependency-register.json" in url and url.endswith(":/content"):
            if register_text is None:
                return httpx.Response(404)
            return httpx.Response(200, text=register_text)
        if request.method == "PUT":
            state["put"] = request.content.decode("utf-8")
            return httpx.Response(201, json={"id": "f1"})
        if url.endswith(":/content"):
            return httpx.Response(200, text=state.get("put", ""))
        if request.method == "GET":
            return httpx.Response(404)  # handoff meta: new file
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    return state


def test_pp4_handoff_dropping_register_item_is_refused(monkeypatch):
    reg = _pp4_register()
    state = _pp4_world(monkeypatch, json.dumps(reg, ensure_ascii=False, indent=2))
    out = gf.write_file("marschkamp", PP4_PATH, _pp4_handoff("AB-KAELTE-02"),
                        user_confirmed=True,
                        expect={"markers": ["## Continuity requirements"], "min_bytes": 800})
    assert "error" in out and "PE-ZERLEG-01" in out["error"]
    assert "AB-KAELTE-02" not in out["error"]  # only the dropped item is named
    assert "put" not in state  # refused before anything reached SharePoint


def test_pp4_handoff_enumerating_every_item_is_written(monkeypatch):
    reg = _pp4_register()
    state = _pp4_world(monkeypatch, json.dumps(reg, ensure_ascii=False, indent=2))
    out = gf.write_file("marschkamp", PP4_PATH,
                        _pp4_handoff("PE-ZERLEG-01", "AB-KAELTE-02"),
                        user_confirmed=True,
                        expect={"markers": ["## Continuity requirements"], "min_bytes": 800})
    assert out.get("written") is True
    assert "PE-ZERLEG-01" in state["put"]


def test_pp4_handoff_with_unreadable_register_fails_closed(monkeypatch):
    state = _pp4_world(monkeypatch, None)  # register 404s
    out = gf.write_file("marschkamp", PP4_PATH, _pp4_handoff("PE-ZERLEG-01"),
                        user_confirmed=True,
                        expect={"markers": ["## Continuity requirements"], "min_bytes": 800})
    assert "error" in out and "register" in out["error"].lower()
    assert "put" not in state


# ─────────────────────────────── P7 I-1 part 3: token validate-and-save (record lane)
# HalfA proved the agent can neither count bytes nor re-emit byte-identically: any fix
# relying on agent-declared numbers or re-emission is structurally weak. The token binds
# the referee-validated bytes server-side; the write is by reference, never re-typed.

RECORD = "output/bia-record.json"


def _fat_record():
    """Referee-shaped activities record, big enough for the bia-record.json contract."""
    return {"dept": "slaughter", "version": 1, "activities": [{
        "id": "act-1", "owner_name": "Torsten Ahlgrim", "priority": 1,
        "impact_grid": {"financial": {"0-4 h": 1, "8 h": 4, "24 h": 5}},
        "mtpd": "24 h", "rpo": "4 h (ERP order data)", "recovery_target": "8 h",
        "notes": "x" * 1600,
        "evidence": [{"type": "transcript_quote", "quote": "the line stops fast",
                      "source_path": "07_Interviews/x.md"}],
    }]}


def _canonical(rec):
    return json.dumps(rec, ensure_ascii=False, indent=2) + "\n"


def test_issue_save_token_returns_hex_and_stores_canonical_bytes():
    rec = _fat_record()
    tok = gf.issue_save_token("marschkamp", rec)
    assert isinstance(tok, str) and len(tok) == 32
    slot = gf._validated_records["marschkamp"]
    assert slot["token"] == tok
    assert slot["data"] == _canonical(rec).encode("utf-8")


def test_save_token_write_by_reference_and_single_use(monkeypatch):
    """2026-08-19 (Task 7): the receipt says what moved, not that the server applauded
    itself — 'byte-identical to the referee-validated record' is gone, so this no longer
    asserts 'validated record' and instead asserts the sizes-only sentence it became."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    rec = _fat_record()
    tok = gf.issue_save_token("marschkamp", rec)
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert out.get("written") is True
    assert state["put"] == _canonical(rec)  # exact stored bytes, no re-emission
    line = out["verification"]["human_line"]
    new_size = len(state["put"].encode("utf-8"))
    assert line == f"✓ Saved: output/bia-record.json — {new_size:,} bytes."
    assert "sha" not in line.lower() and "marschkamp/" not in line  # no jail internals
    # consumed on success — replay teaches revalidation, nothing reaches SharePoint
    state2 = {}
    _mock(monkeypatch, _artifact_handler(state2))
    again = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert "error" in again and "validate_bia_record" in again["error"]
    assert "put" not in state2


def test_the_record_cannot_be_written_without_a_save_token(monkeypatch):
    """Backlog §A.1, the top defect, PROVEN BY PROBE 2026-08-20: the referee returns a
    save_token, but nothing bound the record PATH to a token — so a hand-typed record with
    expect={markers,min_bytes} took the ordinary document lane and saved, referee never
    consulted. It happened live: last PASS 15:56:34 at 13,889 bytes, write 15:58:32 at
    18,966, no validate call anywhere reporting 18966, 44,064 bytes on disk beforehand.
    A 44 KB record was replaced by 19 KB nothing ever validated.

    Enforcement, not instruction: the tool description already said 'never re-type the
    record' and instruction has failed six times on this codebase (§B.1–B.6)."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    # Fat enough to clear the stage contract's floor — the point is that NOTHING but the
    # missing token stands between hand-typed bytes and the saved record.
    hand_typed = _canonical(_fat_record())
    out = gf.write_file("marschkamp", RECORD, hand_typed, user_confirmed=True,
                        expect={"markers": ["activities"], "min_bytes": 1500})
    assert "error" in out, out
    assert "save_token" in out["error"], out["error"]
    assert "validate_bia_record" in out["error"], out["error"]
    assert "put" not in state, "the unvalidated record must never reach SharePoint"


def test_the_record_refusal_tells_the_agent_to_validate_first(monkeypatch):
    """A refusal the agent can resolve alone carries next_move (the _err classification) —
    here the whole recovery is one call it already has."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", RECORD, _canonical(_fat_record()), user_confirmed=True,
                        expect={"markers": ["activities"], "min_bytes": 10})
    assert "error" in out, out
    assert "validate_bia_record" in (out.get("next_move") or ""), out


def test_a_shrinking_record_says_what_came_out_in_the_receipt(monkeypatch):
    """Hans, §A.7, on the live 44,064 → 18,966 save: *"i approved that write as a referee
    fix, not as a delete."* The loss WAS detected — _graph_regen's activity-count check
    raised 'BIA activity count would drop from 3 to 1' — and the detection went to a log,
    which is the estate charter's "alarms are read, not filed" broken inside the product.
    The number has a user-visible home now: the receipt line the model prints verbatim.

    The regen boundary still never blocks the write (its documented contract) — it warns."""
    import dep_graph
    state = {}
    _mock(monkeypatch, _existing_artifact_handler(state, size=44064))

    def refuse(company, rel, data, result, fetch):
        raise RuntimeError("refusing to publish marschkamp: BIA activity count would drop "
                           "from 3 to 1. Check the record is really gone")

    monkeypatch.setattr(dep_graph, "bank_and_regen", refuse)
    tok = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert out.get("written") is True, out          # the boundary never blocks
    line = out["verification"]["human_line"]
    assert "3" in line and "1" in line, line        # what came out, by the numbers
    assert "activit" in line.lower(), line
    assert "version history" in line.lower(), line  # and how to get it back


def test_save_token_still_requires_user_confirmed():
    tok = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", RECORD, "", save_token=tok)
    assert "error" in out and "approval" in out["error"].lower()


def test_save_token_unknown_or_expired_teaches_revalidate(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    gf._validated_records.clear()
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True,
                        save_token="feed" * 8)
    assert "error" in out and "validate_bia_record" in out["error"]
    tok = gf.issue_save_token("marschkamp", _fat_record())
    gf._validated_records["marschkamp"]["issued"] -= gf.SAVE_TOKEN_TTL_S + 1
    out2 = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert "error" in out2 and "validate_bia_record" in out2["error"]
    assert "put" not in state


def test_save_token_bound_to_record_path(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    tok = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", "output/other.json", "", user_confirmed=True,
                        save_token=tok)
    assert "error" in out and RECORD in out["error"]
    assert "put" not in state


def test_save_token_content_if_passed_must_match_stored_bytes(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    tok = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", RECORD, '{"activities": []}', user_confirmed=True,
                        save_token=tok)
    assert "error" in out and "put" not in state
    ok = gf.write_file("marschkamp", RECORD, _canonical(_fat_record()),
                       user_confirmed=True, save_token=tok)
    assert ok.get("written") is True


def test_save_token_lane_trusts_referee_over_stage_contract(monkeypatch):
    """A token write persists referee-validated bytes — byte-identity is strictly
    stronger than the stage contract, which must not dead-end a legacy questions-form
    record the referee passed (its serialisation lacks the '"activities"' marker)."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    legacy = {"dept": "slaughter", "questions": [{"id": "q1", "notes": "x" * 1600}]}
    tok = gf.issue_save_token("marschkamp", legacy)
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert out.get("written") is True


def test_newer_pass_supersedes_save_token(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    old = gf.issue_save_token("marschkamp", _fat_record())
    new = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=old)
    assert "error" in out and "put" not in state
    ok = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=new)
    assert ok.get("written") is True


def test_save_token_survives_failed_write(monkeypatch):
    """Consumed only on verified success, so a failed write leaves it usable.

    The failure used to be a create-collision. Since 2026-08-20 the server picks the mode
    itself, so that is no longer a failure at all — this uses the one that still is: a read-back
    whose bytes do not match the referee-validated record."""
    puts = {}
    corrupt = {"on": True}

    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        if request.method == "PUT":
            puts["put"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"id": "f1"})
        if url.endswith(":/content"):
            return httpx.Response(200, text="" if corrupt["on"] else puts.get("put", ""))
        return httpx.Response(200, json={"id": "f1"})  # meta: file EXISTS

    _mock(monkeypatch, handler)
    tok = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert "error" in out and "read-back" in out["error"], out
    corrupt["on"] = False
    ok = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert ok.get("written") is True, ok


# ─────────────────────────────── Open item 9: graph regen hook

def test_verified_register_write_triggers_graph_regen(monkeypatch):
    import dep_graph
    calls = []
    monkeypatch.setattr(dep_graph, "bank_and_regen",
                        lambda company, rel, data, result, fetch: calls.append((company, rel)))
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "LF-ABP-01", {"owner_name": "X"},
                                   user_confirmed=True)
    assert out.get("updated") is True
    assert ("marschkamp", gf.REGISTER_PATH) in calls


def test_register_write_banks_a_deterministic_verification(monkeypatch):
    """§RUN finding 3 (2026-07-30): the register lane took write_file's early return, so
    evidence.json banked human_line as null on every register write while the record lane
    banked a real string. The write is now byte-read-back verified and banks its line."""
    import dep_graph
    banked = []
    monkeypatch.setattr(dep_graph, "bank_and_regen",
                        lambda company, rel, data, result, fetch: banked.append(result))
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "LF-ABP-01", {"owner_name": "X"},
                                   user_confirmed=True)
    assert out.get("updated") is True
    assert banked, "the regen hook must still fire"
    verification = banked[0].get("verification") or {}
    assert verification.get("human_line"), "register write must bank a human_line, not null"
    assert verification.get("byte_identical") is True


def test_record_token_write_triggers_graph_regen(monkeypatch):
    import dep_graph
    calls = []
    monkeypatch.setattr(dep_graph, "bank_and_regen",
                        lambda company, rel, data, result, fetch: calls.append(rel))
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    tok = gf.issue_save_token("marschkamp", _fat_record())
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert out.get("written") is True and calls == [RECORD]


def test_regen_failure_never_fails_the_write(monkeypatch):
    import dep_graph
    def boom(*a, **k):
        raise RuntimeError("render exploded")
    monkeypatch.setattr(dep_graph, "bank_and_regen", boom)
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "LF-ABP-01", {"owner_name": "X"},
                                   user_confirmed=True)
    assert out.get("updated") is True  # the write stands; failure only logs


def test_plain_artifact_write_does_not_regen(monkeypatch):
    import dep_graph
    calls = []
    monkeypatch.setattr(dep_graph, "bank_and_regen",
                        lambda *a, **k: calls.append(1))
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT,
                        user_confirmed=True, expect=FULL_EXPECT)
    assert out.get("written") is True and calls == []


def test_bank_and_regen_banks_evidence_and_regenerates(tmp_path, monkeypatch):
    """The real hook body: evidence banked per source, page regenerated from it."""
    import dep_graph
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path / "pages")
    reg_text = json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n"

    def fetch(company, path):
        if path.endswith("dependency-register.json"):
            return {"content": reg_text, "size": len(reg_text)}
        return {"error": "file not found: " + path}

    result = {"verification": {"human_line": "✓ Saved and checked: patch applied."}}
    dep_graph.bank_and_regen("marschkamp", gf.REGISTER_PATH, reg_text.encode("utf-8"),
                             result, fetch)
    ev = json.loads((tmp_path / "pages" / "marschkamp" / "evidence.json")
                    .read_text(encoding="utf-8"))
    assert ev["register"]["sha"] == hashlib.sha256(reg_text.encode("utf-8")).hexdigest()[:8]
    assert ev["register"]["human_line"] == "✓ Saved and checked: patch applied."
    page = (tmp_path / "pages" / "marschkamp" / "index.html").read_text(encoding="utf-8")
    assert "no run overlay" in page  # record absent → register base view


def test_register_lane_needs_no_expect(monkeypatch):
    """update_register_entry's internal full-register overwrite stays expect-free — since
    the §RUN finding-3 fix it is verified by byte read-back instead, so the fake must serve
    the written content back (the previous stub 404'd every :/content GET)."""
    _register_handler(monkeypatch, _fake_register())
    out = gf.write_file("marschkamp", gf.REGISTER_PATH,
                        json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n",
                        user_confirmed=True, mode="overwrite")
    assert out.get("written") is True
    assert out["verification"]["byte_identical"] is True


def test_register_write_refused_when_read_back_differs(monkeypatch):
    """The byte read-back must actually bite: a store that returns different content
    fails the write rather than banking a false 'byte-identical' claim."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        if request.method == "GET" and str(request.url).endswith(":/content"):
            return httpx.Response(200, text='{"tampered": true}\n')
        if request.method == "GET":
            return httpx.Response(200, json={"id": "reg"})
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "reg"})
        return httpx.Response(404)
    _mock(monkeypatch, handler)
    out = gf.write_file("marschkamp", gf.REGISTER_PATH,
                        json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n",
                        user_confirmed=True, mode="overwrite")
    assert "error" in out and "read-back" in out["error"]


# ─────────────────────────────── 2026-07-30 bundle: update_bia_activity (tool #14)
# Administrative-metadata corrections to the saved record go read → patch → referee →
# token-lane save; analytical fields stay journey work (correcting them re-opens the
# stage). The approval (approved_by + reason) and the original values are banked as a
# durable amendments entry in the graph evidence.

LIVE_SHAPE_RECORD = {"activities": [{
    "name": "Slaughter Process", "owner": "Torsten Ahlgrim", "priority": "1",
    "impact_grid": {"financial": {"0–4 h": 1, "8 h": 4}},
    "mtpd": "8 h", "rpo": "4 h (ERP order data)", "recovery_target": "4 h",
    "recovery_gap_flagged": True,
    "evidence": [{"type": "transcript_quote", "quote": "the line stops fast",
                  "source_path": "07_Interviews/int.md"}],
}]}


def _record_world(monkeypatch, record, register=None, extra=None):
    """Stateful Graph double for the record-correction lane: serves the saved record
    (+ register, for the referee and the regen hook), records PUTs, serves the updated
    record content afterwards. `extra` = {relpath: content} for method/interview files."""
    state = {"record": json.dumps(record, ensure_ascii=False, indent=2) + "\n",
             "register": json.dumps(register if register is not None else _fake_register(),
                                    ensure_ascii=False, indent=2) + "\n",
             "puts": 0}
    files = dict(extra or {})

    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url, method = str(request.url), request.method
        if method == "PUT":
            state["record"] = request.content.decode("utf-8")
            state["puts"] += 1
            return httpx.Response(200, json={"id": "f1"})
        if url.endswith(":/content"):
            if "output/bia-record.json" in url:
                return httpx.Response(200, text=state["record"])
            if "dependency-register.json" in url:
                return httpx.Response(200, text=state["register"])
            for rel, body in files.items():
                if url.endswith(f"root:/marschkamp/{rel}:/content"):
                    return httpx.Response(200, text=body)
            return httpx.Response(404)
        if url.endswith(":/children"):
            folder = url.split("root:/marschkamp/", 1)[1].split(":/children")[0]
            names = sorted({p[len(folder) + 1:] for p in files
                            if p.startswith(folder + "/")
                            and "/" not in p[len(folder) + 1:]})
            return httpx.Response(200, json={"value": [
                {"name": n, "size": 1, "file": {}} for n in names]})
        if method == "GET":
            return httpx.Response(200, json={"id": "f1"})  # metadata: exists
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    return state


def _stub_referee(monkeypatch, result=None):
    """Referee double: records the record it judged; the default mints a REAL token so
    the save runs the genuine write-by-reference lane."""
    import bia_referee
    seen = {}

    def fake(company, record):
        seen["company"], seen["record"] = company, record
        if result is not None:
            return result
        return {"pass": True, "save_token": gf.issue_save_token(company, record)}

    monkeypatch.setattr(bia_referee, "validate_bia_record", fake)
    return seen


def test_update_bia_activity_needs_confirmation():
    out = gf.update_bia_activity("marschkamp", "Slaughter Process", {"owner": "X"},
                                 approved_by="KG", reason="handover")
    assert "error" in out and "approval" in out["error"].lower()


def test_update_bia_activity_requires_approved_by_and_reason():
    out = gf.update_bia_activity("marschkamp", "Slaughter Process", {"owner": "X"},
                                 user_confirmed=True)
    assert "error" in out and "approved_by" in out["error"] and "reason" in out["error"]


def test_update_bia_activity_refuses_analytical_fields():
    """The allowlist IS the contract: analysis corrections re-open the stage, and the
    activity name is the graph node id — refused with the teaching message, before
    any network or referee work."""
    for field in ("mtpd", "impact_grid", "evidence", "name", "recovery_target"):
        out = gf.update_bia_activity("marschkamp", "Slaughter Process", {field: "x"},
                                     approved_by="KG", reason="r", user_confirmed=True)
        assert "error" in out and field in out["error"], field
        assert "owner" in out["error"]  # the message names what IS allowed
        assert "re-open" in out["error"]


def test_update_bia_activity_values_must_be_text():
    out = gf.update_bia_activity("marschkamp", "Slaughter Process", {"owner": 42},
                                 approved_by="KG", reason="r", user_confirmed=True)
    assert "error" in out and "text" in out["error"]


def test_update_bia_activity_unknown_activity_lists_known(monkeypatch):
    _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    out = gf.update_bia_activity("marschkamp", "Packing", {"owner": "X"},
                                 approved_by="KG", reason="r", user_confirmed=True)
    assert "error" in out and "Slaughter Process" in out["error"]


def test_update_bia_activity_patches_via_referee_and_token_lane(monkeypatch):
    state = _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    seen = _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover 2026-07",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    judged = seen["record"]["activities"][0]  # referee judged the PATCHED record
    assert judged["owner"] == "Olga Milevska" and judged["mtpd"] == "8 h"
    assert state["puts"] == 1  # token lane: PUT bytes are the server's serialisation
    assert state["record"] == _canonical(seen["record"])
    assert out["changed_fields"] == ["owner"]
    assert out["previous"] == {"owner": "Torsten Ahlgrim"}
    assert "human_line" in out["verification"]


def test_update_bia_activity_refuses_a_no_op_change(monkeypatch):
    """A from == to correction must not manufacture an audit entry. Live 2026-08-03:
    the same owner reassignment submitted twice banked an amendment reading
    'Konstantin Gerner -> Konstantin Gerner' and regenerated the graph for nothing,
    which read as "the first change did not apply" when it had."""
    state = _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    seen = _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Torsten Ahlgrim"},
                                 approved_by="KG", reason="resubmitted by mistake",
                                 user_confirmed=True)
    assert "error" in out and "already" in out["error"]
    assert "Torsten Ahlgrim" in out["error"]  # names the value it already holds
    assert state["puts"] == 0                 # nothing saved
    assert "record" not in seen               # referee never ran


def test_update_bia_activity_applies_only_the_fields_that_change(monkeypatch):
    """A mixed correction keeps the real change and drops the no-op, so the amendment
    never records a field as changed when it wasn't."""
    state = _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Torsten Ahlgrim", "contact": "t.ahlgrim@mk.de"},
                                 approved_by="KG", reason="contact added",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    assert out["changed_fields"] == ["contact"]
    assert "owner" not in out["amendment"]["fields"]
    assert state["puts"] == 1


def test_update_bia_activity_flags_prose_still_naming_the_old_value(monkeypatch):
    """Live 2026-08-03: owner moved to Konstantin Gerner while `status` still read
    'pending Petra Louven in-person read-back'. The allowlist correctly refuses to touch
    analytical prose, so the correction must at least SAY which fields now contradict it."""
    import copy
    rec = copy.deepcopy(LIVE_SHAPE_RECORD)
    rec["activities"][0]["status"] = ("PROVISIONAL — pending Torsten Ahlgrim read-back "
                                      "scheduled 10 Aug 2026")
    rec["activities"][0]["rpo_status"] = "Torsten Ahlgrim has not seen a restore test."
    _record_world(monkeypatch, rec)
    _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    assert out["stale_mentions"] == {"owner": ["rpo_status", "status"]}


def test_update_bia_activity_reports_no_stale_mentions_when_prose_is_clean(monkeypatch):
    _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    assert out["stale_mentions"] == {}


def test_update_bia_activity_referee_reject_saves_nothing(monkeypatch):
    state = _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    _stub_referee(monkeypatch, result={"pass": False,
                                       "rejections": ["mtpd does not match the grid"]})
    out = gf.update_bia_activity("marschkamp", "Slaughter Process", {"owner": "X"},
                                 approved_by="KG", reason="r", user_confirmed=True)
    assert "error" in out and "mtpd does not match the grid" in out["error"]
    assert "nothing was saved" in out["error"]
    assert state["puts"] == 0


def test_update_bia_activity_accepts_json_string_changes(monkeypatch):
    _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 '{"owner": "Olga Milevska"}',
                                 approved_by="KG", reason="r", user_confirmed=True)
    assert out.get("updated") is True


def test_update_bia_activity_banks_a_durable_amendment(monkeypatch):
    import dep_graph
    _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    ev = json.loads((dep_graph.PUBLIC / "marschkamp" / "evidence.json").read_text())
    (entry,) = ev["amendments"]
    assert entry["activity"] == "Slaughter Process"
    assert entry["fields"]["owner"] == {"from": "Torsten Ahlgrim", "to": "Olga Milevska"}
    assert entry["approved_by"] == "KG" and entry["reason"] == "owner handover"
    assert entry["at"]  # stamped by the banking clock
    # a later plain token save must NOT wipe the audit trail
    tok = gf.issue_save_token("marschkamp", LIVE_SHAPE_RECORD)
    ok = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, mode="overwrite",
                       save_token=tok)
    assert ok.get("written") is True
    ev2 = json.loads((dep_graph.PUBLIC / "marschkamp" / "evidence.json").read_text())
    assert len(ev2["amendments"]) == 1  # survived, un-duplicated


def test_update_bia_activity_full_chain_with_real_referee(monkeypatch):
    """Offline twin of the 2026-07-30 live-fire (commit 7f56dfb): REAL referee over the
    Graph double — including the new dependencies gate — real token mint, real
    write-by-reference, amendment banked."""
    import dep_graph
    method = {"time_horizons": ["0–4 h", "8 h", "24 h", "1 week"],
              "intolerability_threshold": 4,
              "scenarios": [{"id": "financial"}],
              "rpo_vocabulary": ["4 h (ERP order data)"]}
    record = {"activities": [{
        "name": "Slaughter Process", "owner": "Torsten Ahlgrim",
        "dept": "schlachtung",
        "impact_grid": {"financial": {"0–4 h": 1, "8 h": 4, "24 h": 5, "1 week": 5}},
        "mtpd": "8 h", "rpo": "4 h (ERP order data)", "recovery_target": "4 h",
        "dependencies": ["KA-01"],
        "evidence": [{"type": "transcript_quote", "lens": "financial", "quote": "the line stops fast",
                      "source_path": "07_Interviews/int.md"}],
    }]}
    register = {"synthetic": True,
                "KA-01": {"asset_id": "KA-01", "owner_name": "R. Boll"}}
    _record_world(monkeypatch, record, register=register, extra={
        "02_BCM-Method/method.json": json.dumps(method),
        "07_Interviews/int.md": "Ahlgrim: the line stops fast.",
    })
    gf._validated_records.clear()
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    ev = json.loads((dep_graph.PUBLIC / "marschkamp" / "evidence.json").read_text())
    assert ev["amendments"][0]["approved_by"] == "KG"
    assert ev["record"]["human_line"]  # the token-lane save banked its own receipt


def test_update_bia_activity_grandfathers_untagged_lenses_on_the_saved_record(monkeypatch):
    """Kickoff 2 thread 7: the live record was PASSed before the lens rule, so its evidence
    carries no `lens` while financial is scored 4+. An owner correction re-validates the whole
    saved record through the real referee — it must not re-litigate an activity nobody
    re-drafted, and it must not rewrite the untagged evidence to get there."""
    import dep_graph
    method = {"time_horizons": ["0–4 h", "8 h", "24 h", "1 week"],
              "intolerability_threshold": 4,
              "scenarios": [{"id": "financial"}],
              "rpo_vocabulary": ["4 h (ERP order data)"]}
    record = {"activities": [{
        "name": "Slaughter Process", "owner": "Torsten Ahlgrim",
        "dept": "schlachtung",
        "impact_grid": {"financial": {"0–4 h": 1, "8 h": 4, "24 h": 5, "1 week": 5}},
        "mtpd": "8 h", "rpo": "4 h (ERP order data)", "recovery_target": "4 h",
        "dependencies": ["KA-01"],
        "evidence": [{"type": "transcript_quote", "quote": "the line stops fast",
                      "source_path": "07_Interviews/int.md"}],
    }]}
    register = {"synthetic": True,
                "KA-01": {"asset_id": "KA-01", "owner_name": "R. Boll"}}
    state = _record_world(monkeypatch, record, register=register, extra={
        "02_BCM-Method/method.json": json.dumps(method),
        "07_Interviews/int.md": "Ahlgrim: the line stops fast.",
    })
    gf._validated_records.clear()
    out = gf.update_bia_activity("marschkamp", "Slaughter Process",
                                 {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    saved = json.loads(state["record"])
    assert saved["activities"][0]["owner"] == "Olga Milevska"
    assert "lens" not in saved["activities"][0]["evidence"][0]
    assert (dep_graph.PUBLIC / "marschkamp" / "evidence.json").exists()


# ─────────────────────────────── Task 7 (2026-08-19): the receipt says what moved
# A consultant judged the old wording — "byte-identical to the referee-validated record" /
# "byte-identical to the approved field update" — self-certification noise: the server
# telling the human it checked its own homework. The receipt now says what changed:
# previous size -> new size when Graph reports one, and — on the record/register lanes —
# which field moved from what to what, reusing update_bia_activity's own amendment shape.

DRAFT_MARKERS = ["## Scope", "## Impacts", "## Dependencies", "## Assumptions",
                 "## Risks", "## Next steps"]


def _draft_content(total_bytes):
    """A 6-section artifact padded to an exact byte length so the receipt's size math is
    checkable by eye against the numbers this file's assertions expect."""
    body = "# BIA Draft\n\n" + "".join(f"{m}\nnotes\n" for m in DRAFT_MARKERS)
    pad = total_bytes - len(body.encode("utf-8"))
    assert pad >= 0, "target too small for the fixed header"
    return body + "x" * pad


def _sized_record(notes_len):
    """Like _fat_record, with `notes` padded to an exact length — same reason."""
    return {"dept": "slaughter", "version": 1, "activities": [{
        "id": "act-1", "owner_name": "Torsten Ahlgrim", "priority": 1,
        "impact_grid": {"financial": {"0-4 h": 1, "8 h": 4, "24 h": 5}},
        "mtpd": "24 h", "rpo": "4 h (ERP order data)", "recovery_target": "8 h",
        "notes": "x" * notes_len,
        "evidence": [{"type": "transcript_quote", "quote": "the line stops fast",
                      "source_path": "07_Interviews/x.md"}],
    }]}


def _metadata_handler(state, meta_json):
    """Like _artifact_handler, but the existence GET answers with a real DriveItem body
    (carrying `size`) instead of 404 — the shape of an overwrite Graph actually returns."""
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        url = str(request.url)
        if request.method == "PUT":
            state["put"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"id": "f1"})
        if url.endswith(":/content"):
            return httpx.Response(200, text=state.get("readback", state.get("put", "")))
        if request.method == "GET":
            return httpx.Response(200, json=meta_json)
        return httpx.Response(404)
    return handler


def test_expect_lane_receipt_shows_the_previous_size_when_graph_reports_one(monkeypatch):
    """The doubles used everywhere else in this file 404 the existence GET or answer with
    no `size` key — this is the one test where Graph's metadata carries a real size, so it
    is the only place the 'previous -> new' half of the sentence is proven."""
    state = {}
    _mock(monkeypatch, _metadata_handler(state, {"id": "f1", "size": 3955}))
    content = _draft_content(4210)
    out = gf.write_file("marschkamp", "output/draft.md", content, user_confirmed=True,
                        mode="overwrite", expect={"markers": DRAFT_MARKERS, "min_bytes": 3000})
    assert out.get("written") is True
    line = out["verification"]["human_line"]
    assert "✓ Saved: output/draft.md — 3,955 → 4,210 bytes, 6 sections." in line
    assert "matches what you approved" not in line


def test_expect_lane_receipt_degrades_to_one_number_without_a_previous_size(monkeypatch):
    """A create (or any double whose metadata carries no `size`, which is every other
    fixture in this file) has nothing to diff against — no bogus '->', no crash."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    content = _draft_content(4210)
    out = gf.write_file("marschkamp", "output/draft.md", content, user_confirmed=True,
                        expect={"markers": DRAFT_MARKERS, "min_bytes": 3000})
    assert out.get("written") is True
    assert out["verification"]["human_line"] == "✓ Saved: output/draft.md — 4,210 bytes, 6 sections."


def test_record_lane_receipt_states_the_amendment_that_moved(monkeypatch):
    """The record-correction receipt names the field, the activity and the values that
    changed — 'what moved' — instead of repeating that the write matched the referee.
    Reuses update_bia_activity's own amendment shape end to end."""
    state = _record_world(monkeypatch, LIVE_SHAPE_RECORD)
    _stub_referee(monkeypatch)
    out = gf.update_bia_activity("marschkamp", "Slaughter Process", {"owner": "Olga Milevska"},
                                 approved_by="KG", reason="owner handover",
                                 user_confirmed=True)
    assert out.get("updated") is True, out
    assert out["verification"]["byte_identical"] is True
    new_size = len(state["record"].encode("utf-8"))
    line = out["verification"]["human_line"]
    assert line == (
        f'✓ Saved: output/bia-record.json — {new_size:,} bytes; owner on "Slaughter Process" '
        'changed from "Torsten Ahlgrim" to "Olga Milevska". The previous version stays '
        "in SharePoint's version history."
    )


def test_record_lane_receipt_with_previous_size_and_amendment(monkeypatch):
    """The brief's own worked example, reproduced exactly: a previous size AND a named
    amendment on one line. update_bia_activity's own Graph double never returns a `size`
    (proven by the test above), so this drives write_file directly to prove the combination
    the two dimensions make together."""
    state = {}
    _mock(monkeypatch, _metadata_handler(state, {"id": "f1", "size": 12880}))
    rec = _sized_record(12335)  # notes padded so the canonical JSON is exactly 12,904 bytes
    new_size = len(_canonical(rec).encode("utf-8"))
    tok = gf.issue_save_token("marschkamp", rec)
    amendment = {"activity": "Slaughter line",
                "fields": {"owner": {"from": "unassigned", "to": "J. Vermeer"}}}
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, mode="overwrite",
                        save_token=tok, amendment=amendment)
    assert out.get("written") is True
    line = out["verification"]["human_line"]
    assert line == (
        f'✓ Saved: output/bia-record.json — 12,880 → {new_size:,} bytes; owner on "Slaughter '
        'line" changed from "unassigned" to "J. Vermeer". The previous version stays in '
        "SharePoint's version history."
    )


def test_record_lane_receipt_without_amendment_is_just_the_sizes(monkeypatch):
    """A plain token-lane save (no correction in progress, amendment=None) keeps the
    sentence to sizes only — this is the case
    test_save_token_write_by_reference_and_single_use used to assert the old 'validated
    record' wording for; see that test's docstring for the rewrite."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    rec = _fat_record()
    tok = gf.issue_save_token("marschkamp", rec)
    out = gf.write_file("marschkamp", RECORD, "", user_confirmed=True, save_token=tok)
    assert out.get("written") is True
    new_size = len(_canonical(rec).encode("utf-8"))
    assert out["verification"]["human_line"] == f"✓ Saved: output/bia-record.json — {new_size:,} bytes."


def test_update_register_entry_receipt_states_the_amendment_that_moved(monkeypatch):
    """update_register_entry captures the previous value BEFORE entry.update(changes) and
    passes it through as an amendment, so the banked register receipt says what changed —
    not just that the write matched the approved field update. The receipt is banked into
    evidence.json (§RUN finding 3), never returned by update_register_entry itself, so this
    reads it the same way test_register_write_banks_a_deterministic_verification does."""
    import dep_graph
    banked = []
    monkeypatch.setattr(dep_graph, "bank_and_regen",
                        lambda company, rel, data, result, fetch: banked.append(result))
    _register_handler(monkeypatch, _fake_register())
    out = gf.update_register_entry("marschkamp", "LF-ABP-01",
                                   {"second_source_owner": "Nadine Pohl"},
                                   user_confirmed=True)
    assert out.get("updated") is True
    new_size = banked[0]["size"]
    line = banked[0]["verification"]["human_line"]
    assert banked[0]["verification"]["byte_identical"] is True
    assert line == (
        f'✓ Saved: the register on file — {new_size:,} bytes; second_source_owner on '
        '"LF-ABP-01" changed from "TBD (open)" to "Nadine Pohl". The previous version '
        "stays in SharePoint's version history."
    )


def test_register_lane_receipt_without_amendment_is_just_the_sizes(monkeypatch):
    """write_file called directly on the register path (not through update_register_entry)
    never receives an amendment — the receipt stays sizes-only, matching the record lane's
    equivalent case (test_record_lane_receipt_without_amendment_is_just_the_sizes)."""
    _register_handler(monkeypatch, _fake_register())
    payload = json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n"
    out = gf.write_file("marschkamp", gf.REGISTER_PATH, payload,
                        user_confirmed=True, mode="overwrite")
    assert out.get("written") is True
    new_size = len(payload.encode("utf-8"))
    assert out["verification"]["human_line"] == (
        f"✓ Saved: the register on file — {new_size:,} bytes. The previous version stays "
        "in SharePoint's version history."
    )


def test_only_the_agents_own_read_is_recorded(monkeypatch):
    """The trap this feature dies on: bia_referee reads the method and the register
    internally on every validate call, and the advance gate fetches artifacts itself.
    If those counted, the read gate would always pass while appearing to work."""
    import graph_files
    graph_files.forget_reads()
    graph_files.note_read("marschkamp", "03_Dependencies/dependency-register.json")
    assert graph_files.reads_seen("marschkamp") == {"03_Dependencies/dependency-register.json"}
    graph_files.forget_reads()
    assert graph_files.reads_seen("marschkamp") == set()


def test_a_read_is_found_whatever_the_casing():
    """`_validated_records` two functions up normalizes its company key and this store did
    not. read_company_file records whatever string the agent passed; the gate looks it up
    with whatever string next_step received, and next_step only strips. Same company, two
    spellings, and the gate would demand a read that already happened."""
    import graph_files
    graph_files.forget_reads()
    graph_files.note_read(" Marschkamp ", "02_BCM-Method/method.json")
    assert graph_files.reads_seen("marschkamp") == {"02_BCM-Method/method.json"}


def test_read_credit_expires_after_the_idle_window(monkeypatch):
    """Backlog §A.2, second half: a genuinely new run must not inherit stale credit. Credit
    now ages out on a quiet gap instead of being wiped by a start_journey call."""
    import graph_files
    graph_files.forget_reads()
    now = [1000.0]
    monkeypatch.setattr(graph_files.time, "monotonic", lambda: now[0])
    graph_files.note_read("marschkamp", "02_BCM-Method/method.json")
    now[0] += graph_files.READ_CREDIT_IDLE_S + 1
    assert graph_files.reads_seen("marschkamp") == set()


def test_read_credit_survives_a_recall_within_the_idle_window(monkeypatch):
    """Backlog §A.2, the proven defect: reads four minutes apart lost credit. A quiet gap
    well inside the idle window must not touch either read."""
    import graph_files
    graph_files.forget_reads()
    now = [1000.0]
    monkeypatch.setattr(graph_files.time, "monotonic", lambda: now[0])
    graph_files.note_read("marschkamp", "02_BCM-Method/method.json")
    now[0] += 4 * 60
    graph_files.note_read("marschkamp", "03_Dependencies/dependency-register.json")
    assert graph_files.reads_seen("marschkamp") == {
        "02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"}


def test_a_fresh_read_does_not_revive_a_stale_sibling_reads_credit(monkeypatch):
    """Adversarial-review finding: a single shared per-company clock lets any fresh read (of
    an unrelated path, read well inside the idle window) reset the clock for EVERY
    previously-credited path — so checking after the first path alone would be stale, but
    before the shared clock's own window closes, wrongly still returns it. Each path must
    age out on its own timestamp, not a per-company one."""
    import graph_files
    graph_files.forget_reads()
    now = [1000.0]
    monkeypatch.setattr(graph_files.time, "monotonic", lambda: now[0])
    graph_files.note_read("marschkamp", "02_BCM-Method/method.json")
    now[0] += graph_files.READ_CREDIT_IDLE_S - 30 * 60   # still within the window: refreshes
                                                          # a SHARED clock were there one
    graph_files.note_read("marschkamp", "03_Dependencies/dependency-register.json")
    now[0] += 60 * 60   # now: >IDLE_S since the first read, <IDLE_S since the second
    assert graph_files.reads_seen("marschkamp") == {"03_Dependencies/dependency-register.json"}


# ── the read gate, moved to the write jaw (1a) ────────────────────────────────────────────
# Testers complained 2026-08-20: reaching Stage 2 took seven approvals. The log showed why —
# next_step blocked on an unread register AFTER the scope note had been drafted, approved and
# saved, so the document had to be rewritten. The gate was auditing the draft instead of
# informing it. Same check, one jaw earlier: refuse the SAVE, and the agent reads, then drafts
# once. No new machinery and no payload — the advance gate keeps its own copy as the backstop.
STAGE1_PATH = "output/packing/stage1-scope-and-guide.md"
# The guide section conforms to the interview_guide universal checks (questions, short set,
# bring list) — with _stage1_handler's '{"scenarios": []}' sources the register/method checks
# have nothing to demand, so these fixtures exercise the read gate, not the guide jaw.
STAGE1_CONTENT = ("## Scope\n" + "the packing department scope line\n" * 20 +
                  "## Risk and environment\n" + "the risk and environment line\n" * 20 +
                  "## Method parameters\n" + "the method parameter line\n" * 20 +
                  "## Interview guide\n" + "what breaks when this stops?\n" * 20 +
                  "### Short version\n1. what first? 2. how bad? 3. depends on what?\n" +
                  "### Bring to the interview\n- last year's BIA\n")
STAGE1_EXPECT = {"markers": ["## Scope", "## Risk and environment",
                             "## Method parameters", "## Interview guide"], "min_bytes": 1200}


def _stage1_handler(state):
    """As _artifact_handler, but the two documents stage 1 must read actually exist — a company
    that never supplied one must never be blocked on it, so 'missing' and 'unread' differ."""
    inner = _artifact_handler(state)
    def handler(request):
        url = str(request.url)
        if ("method.json" in url or "dependency-register.json" in url) and request.method == "GET":
            return httpx.Response(200, text='{"scenarios": []}')
        return inner(request)
    return handler


def test_a_stage_document_cannot_be_saved_before_its_required_reads(monkeypatch):
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    out = gf.write_file("marschkamp", STAGE1_PATH, STAGE1_CONTENT,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    assert "method.json" in out["error"], out["error"]
    assert "put" not in state, "refused before anything reached SharePoint"


def test_the_stage_document_saves_once_its_sources_have_been_read(monkeypatch):
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read("marschkamp", path)
    out = gf.write_file("marschkamp", STAGE1_PATH, STAGE1_CONTENT,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert out.get("written") is True, out
    assert "put" in state


def test_a_source_the_company_never_supplied_does_not_block_the_save(monkeypatch):
    """'Missing' and 'unread' are different things. The method's own rule for a document that
    does not exist is to ask the user, never to invent — so a company without a dependency
    register must still be able to run stage 1."""
    state = {}
    inner = _artifact_handler(state)

    def no_sources(request):
        url = str(request.url)
        if "method.json" in url or "dependency-register.json" in url:
            return httpx.Response(404)      # this company supplied neither
        return inner(request)

    _mock(monkeypatch, no_sources)
    out = gf.write_file("marschkamp", STAGE1_PATH, STAGE1_CONTENT,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert out.get("written") is True, out


GUIDE_METHOD_JSON = ('{"time_horizons": ["0–4 h", "8 h", "24 h", "48 h", "72 h", '
                     '"1 week"], "scenarios": [{"id": "financial", "name": "Financial"}]}')
GUIDE_REGISTER_JSON = ('{"IT-ERP-01": {"name": "SAP", "criticality": 1, "consumers": '
                       '[{"dept": "vertrieb", "activity": "order/EDI with grocery-retail '
                       'customers"}]}}')


def _stage1_guide_world(monkeypatch, state):
    """The _pp4_world pattern for the guide jaw: both stage-1 sources serve REAL-shaped
    JSON (en-dash horizons, a register activity with a consuming asset id), reads are
    credited so the read gate stays out of the way, and the guide jaw is what decides."""
    inner = _artifact_handler(state)

    def handler(request):
        url = str(request.url)
        if "method.json" in url and request.method == "GET":
            return httpx.Response(200, text=GUIDE_METHOD_JSON)
        if "dependency-register.json" in url and request.method == "GET":
            return httpx.Response(200, text=GUIDE_REGISTER_JSON)
        return inner(request)

    _mock(monkeypatch, handler)
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read("marschkamp", path)


GUIDE_CONFORMING = (
    "## Scope\n" + "the sales department scope line\n" * 30 +
    "## Risk and environment\n" + "the risk line\n" * 10 +
    "## Method parameters\nHorizons: 0–4 h, 8 h, 24 h, 48 h, 72 h, 1 week.\n" +
    "## Interview guide\n"
    "### order/EDI with grocery-retail customers\n"
    "1. If this stops, what breaks at 0–4 h, 8 h, 24 h, 48 h, 72 h, 1 week?\n"
    "2. When is it intolerable, and in which categories?\n"
    "3. Nights, weekends, peak weeks — what changes?\n"
    "4. People per shift, rooms, kit — what must keep running?\n"
    "5. This depends on IT-ERP-01 — what happens while it is away?\n"
    "6. Single points of failure? (Recorded for the risk assessment, not scored here.)\n"
    "### Short version (20 minutes)\n"
    "1. What breaks first? 2. What can you not tolerate? 3. What do you depend on?\n"
    "### Bring to the interview\n- last year's BIA for sales\n- the delivery SLA\n")


def test_stage1_save_with_a_hollow_interview_guide_is_refused(monkeypatch):
    """The 2026-08-23 live defect (backlog §A.8): a real scope with a hollow guide saved at
    1,842 bytes. With the guide jaw it is refused BEFORE the PUT, naming what is hollow."""
    state = {}
    _stage1_guide_world(monkeypatch, state)
    hollow = GUIDE_CONFORMING.split("## Interview guide\n")[0] + \
        "## Interview guide\n" + "Impact and dependency questions.\n" * 40
    out = gf.write_file("marschkamp", "output/sales/stage1-scope-and-guide.md", hollow,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    assert "question" in out["error"], out["error"]
    assert "put" not in state, "refused before anything reached SharePoint"


def test_guide_problems_batch_with_contract_problems_in_one_refusal(monkeypatch):
    """The one-refusal rule holds for the new jaw: a draft that is both under the contract
    floor AND hollow learns both in ONE message, with one next_move."""
    state = {}
    _stage1_guide_world(monkeypatch, state)
    thin_and_hollow = ("## Scope\nx\n## Risk and environment\nx\n## Method parameters\nx\n"
                       "## Interview guide\nA guide will be prepared.\n")
    out = gf.write_file("marschkamp", "output/sales/stage1-scope-and-guide.md",
                        thin_and_hollow, user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    assert "problems with this draft" in out["error"], out["error"]
    assert "1200" in out["error"] or "1,200" in out["error"]   # the contract floor
    assert "question" in out["error"]                          # the guide jaw
    assert out.get("next_move"), "a recoverable refusal carries a move"


def test_the_redraft_next_move_survives_an_outline_save(monkeypatch):
    """Live 2026-08-24 06:43:19: the model saved its outline preview, so the then-current
    move — 'Send the complete document you previewed' — pointed at a document that never
    existed, and the agent turned the refusal into an approval menu (one wasted press).
    The move must script the recovery from what IS at hand: fix the numbered problems,
    retry the same write now, the approval already given covers the corrected version."""
    state = {}
    _stage1_guide_world(monkeypatch, state)
    thin = ("## Scope\nx\n## Risk and environment\nx\n## Method parameters\nx\n"
            "## Interview guide\nA guide will be prepared.\n")
    out = gf.write_file("marschkamp", "output/sales/stage1-scope-and-guide.md",
                        thin, user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    nm = out.get("next_move") or ""
    assert "previewed" not in nm, nm       # may point at a document that never existed
    assert "retry this write" in nm, nm
    assert "approval" in nm and "covers" in nm, nm


def test_stage1_save_with_a_conforming_guide_links_the_print_view(monkeypatch, tmp_path):
    """The deliverable: one click from the receipt to a printable guide. The page lands
    under the (test-redirected) public tree and the receipt line carries the URL — which
    rides human_line because the model already prints that verbatim; no tool-description
    change, no republish."""
    import dep_graph
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path / "graph-pages")
    state = {}
    _stage1_guide_world(monkeypatch, state)
    out = gf.write_file("marschkamp", "output/sales/stage1-scope-and-guide.md",
                        GUIDE_CONFORMING, user_confirmed=True, expect=STAGE1_EXPECT)
    assert out.get("written") is True, out
    assert out.get("print_url") == \
        "https://agent.ai4bcm.org/demo/graph/marschkamp/sales/guide.html"
    assert out["verification"]["human_line"].endswith(
        " [Print view](https://agent.ai4bcm.org/demo/graph/marschkamp/sales/guide.html)")
    page = tmp_path / "graph-pages" / "marschkamp" / "sales" / "guide.html"
    assert page.exists() and "order/EDI" in page.read_text(encoding="utf-8")


def test_guide_render_failure_never_fails_the_save(monkeypatch, tmp_path):
    """The hook is a courtesy, not a contract — mirror of the graph-regen boundary."""
    import interview_guide
    monkeypatch.setattr(interview_guide, "publish",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("render broke")))
    state = {}
    _stage1_guide_world(monkeypatch, state)
    out = gf.write_file("marschkamp", "output/sales/stage1-scope-and-guide.md",
                        GUIDE_CONFORMING, user_confirmed=True, expect=STAGE1_EXPECT)
    assert out.get("written") is True, out
    assert "print_url" not in out
    assert "Print view" not in out["verification"]["human_line"]


def test_non_stage1_writes_never_touch_the_guide_renderer(monkeypatch):
    """Mirror of test_plain_artifact_write_does_not_regen for the new hook."""
    import interview_guide
    calls = []
    monkeypatch.setattr(interview_guide, "publish",
                        lambda *a, **k: calls.append(a) or "https://x/guide.html")
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/sales/scratch-notes.md", FULL_CONTENT,
                        user_confirmed=True, expect=FULL_EXPECT)
    assert out.get("written") is True, out
    assert calls == []


def test_the_refusal_names_every_unread_source_at_once(monkeypatch):
    """Live 2026-08-20 12:39-12:42, the Logistics run: the write jaw refused, named
    `02_BCM-Method/method.json`, the agent read exactly that, retried, and was refused AGAIN for
    `03_Dependencies/dependency-register.json`. Two refusals, two narrated approval turns, two
    save previews — four of the seven presses it took to reach Stage 2.

    Naming one missing file at a time makes the round trips scale with the number of required
    reads. One refusal must name them all."""
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    out = gf.write_file("marschkamp", STAGE1_PATH, STAGE1_CONTENT,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    assert "method.json" in out["error"], out["error"]
    assert "dependency-register.json" in out["error"], "the second read must be named too"


# --- mode is a fact the server already holds -------------------------------------------------
# Live 2026-08-20, the Logistics run: 13:18:46Z `write_company_file` refused with "cannot
# overwrite: ... does not exist — use mode='create'", the agent re-sent the same bytes under the
# other word 35 seconds later and it saved. Three such refusals are in the call log, in both
# directions, and not one of them stopped a mistake: the agent's path and content were right
# every time and only the word was wrong. The 09:37:58Z instance ended its run outright — the
# next event in the log is a fresh start_journey 32 minutes later.
#
# The server GETs the DriveItem four lines further down to build the receipt's size delta, so it
# knows the answer at the moment it asks the agent for it. It picks, and the receipt says which
# way it went.

def _existing_artifact_handler(state, size=7931):
    """As _artifact_handler, but the target is already in the folder: the meta GET answers 200
    with a size, which is what a real DriveItem does. _artifact_handler answers that GET 404,
    so every test built on it silently means 'new file'."""
    inner = _artifact_handler(state)

    def handler(request):
        url = str(request.url)
        if (request.method == "GET" and "root:/marschkamp/" in url
                and not url.endswith(":/content")):
            return httpx.Response(200, json={"id": "f1", "size": size})
        return inner(request)
    return handler


def test_overwrite_of_a_file_that_is_not_there_creates_it(monkeypatch):
    """13:18:46Z. Nothing is at the path, the user approved these bytes for it: write them.
    There is nothing to destroy and nothing to ask."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT, user_confirmed=True,
                        mode="overwrite", expect=FULL_EXPECT)
    assert out.get("written") is True, out
    assert out["mode"] == "create", "the server reports what it actually did"
    assert "version history" not in out["verification"]["human_line"], \
        "nothing was replaced, so the receipt must not claim a previous version"


def test_create_of_a_file_that_is_there_overwrites_it_and_the_receipt_says_so(monkeypatch):
    """10:43:04Z and 09:37:58Z. The file is there and the user approved replacing it; SharePoint
    keeps the old version either way. The guard only ever made the agent re-say the word — and
    it reached the manager as one more approval request, which is where a real clobber would
    have hidden. The size delta and the version-history clause put it in front of them instead."""
    state = {}
    _mock(monkeypatch, _existing_artifact_handler(state, size=7931))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT, user_confirmed=True,
                        mode="create", expect=FULL_EXPECT)
    assert out.get("written") is True, out
    assert out["mode"] == "overwrite", "the server reports what it actually did"
    line = out["verification"]["human_line"]
    assert "7,931" in line, f"the previous size is the visible half of the guard: {line}"
    assert "version history" in line, line


# --- one refusal names every problem with the draft ------------------------------------------

def test_a_short_unsourced_draft_is_refused_for_both_reasons_at_once(monkeypatch):
    """Live 2026-08-20 13:17:38Z: an 782-byte draft written 65 seconds after start_journey, with
    zero reads behind it. Two things were wrong with it and the server said one — size — because
    `_expect_error` returns before the read check is reached. The agent fixed the half it was
    told about, called next_step at 13:17:59Z, and only THEN learned about the reads, from
    `stage_incomplete`. Two of the seven presses bought one draft's worth of correction.

    Same lesson as `test_the_refusal_names_every_unread_source_at_once`, one level up: it holds
    within a check and not yet across them."""
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    short = "## Scope\nthe packing department scope line\n"      # far under the 1200 it declares
    out = gf.write_file("marschkamp", STAGE1_PATH, short,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    err = out["error"]
    assert "1200" in err, f"the size problem must survive the batching: {err}"
    assert "method.json" in err, f"and the reads must be named in the same refusal: {err}"
    assert "dependency-register.json" in err, err
    assert "put" not in state, "refused before anything reached SharePoint"


def test_a_single_problem_still_reads_as_one_sentence(monkeypatch):
    """The batching must not turn every refusal into a numbered list of one."""
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read("marschkamp", path)
    out = gf.write_file("marschkamp", STAGE1_PATH, "## Scope\ntoo short\n",
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    assert out["error"].startswith("write refused: "), out["error"]
    assert "\n" not in out["error"], f"one problem, one sentence: {out['error']!r}"


# --- a refusal the agent can fix itself says how -----------------------------------------------
# Live control case, 2026-08-20, one run, same missing reads, two tools:
#   13:17:59  next_step  refused WITH next_move  -> the agent read both files 7 seconds later,
#                                                   no user turn
#   14:06:18  write      refused WITHOUT it      -> the agent rendered an approval menu and the
#                                                   manager had to press 1 to authorise reads that
#                                                   need no authorisation
# Both are delivered with isError=True (`tool_result(payload, is_error="error" in payload)`), so
# the flag is not the difference — the payload is. graph_files was the only refusal path in the
# system returning a bare {"error": ...}: the referee (bia_referee.py) and every advance-gate
# refusal (addendum_tools.py) already carry next_move.
#
# The presence of next_move IS the classification. A refusal the agent can resolve alone carries
# one; a refusal that genuinely needs the human — missing approval — deliberately does not.

def test_the_unread_source_refusal_tells_the_agent_its_next_move(monkeypatch):
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    out = gf.write_file("marschkamp", STAGE1_PATH, STAGE1_CONTENT,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    nm = out.get("next_move")
    assert nm, f"an agent-recoverable refusal must say what to do: {out}"
    assert "read_company_file" in nm, nm
    assert "02_BCM-Method/method.json" in nm and "dependency-register.json" in nm, nm
    assert "retry" in nm.casefold(), "and that the write is then retried, not re-approved"


def test_a_short_draft_refusal_tells_the_agent_to_resend_the_whole_document(monkeypatch):
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read("marschkamp", path)
    out = gf.write_file("marschkamp", STAGE1_PATH, "## Scope\ntoo short\n",
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out, out
    nm = out.get("next_move")
    assert nm, f"a size refusal is mechanical — the agent fixes it alone: {out}"
    assert "retry" in nm.casefold(), nm


def test_the_batched_refusal_carries_one_next_move_covering_every_problem(monkeypatch):
    """Two problems, one refusal, one move — not a move per problem."""
    state = {}
    _mock(monkeypatch, _stage1_handler(state))
    out = gf.write_file("marschkamp", STAGE1_PATH, "## Scope\nshort and unsourced\n",
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert "error" in out and "problems" in out["error"], out
    nm = out.get("next_move")
    assert nm, out
    assert "read_company_file" in nm, f"the reads must survive the batching: {nm}"
    assert "retry" in nm.casefold(), nm


def test_a_refusal_that_genuinely_needs_the_user_carries_no_next_move(monkeypatch):
    """The classification, made real. Missing approval is the one refusal the agent must NOT
    resolve on its own — so it gets no move, and the absence is the signal."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", "output/stage1.md", FULL_CONTENT,
                        user_confirmed=False, expect=FULL_EXPECT)
    assert "error" in out and "approval missing" in out["error"], out
    assert "next_move" not in out, \
        "an approval gate is not agent-recoverable; a move here would teach it to self-approve"


# --- one legal company is not a question ------------------------------------------------------
# 2026-08-20T14:37:49Z, Logistics run: the agent rendered two cards, then Copilot Studio stopped
# and asked the manager "Please provide the name of the company you want to fetch a document for"
# — a required-parameter slot-fill for a question with exactly one possible answer. NO tool call
# reached the server that turn, so no server-side gate was involved; the schema alone did it.
#
# Same shape as the `mode` trap removed the same morning: a slot the agent must fill with a fact
# the server already holds. `next_step` had already been given company="marschkamp" as a literal
# default, which is the precedent AND a latent bug — it names a company the allowlist might not
# contain. Deriving it fixes both.

def test_a_single_allowlisted_company_answers_for_an_omitted_one(monkeypatch):
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp",))
    assert gf.resolve_company("") == "marschkamp"
    assert gf.resolve_company(None) == "marschkamp"
    assert gf.resolve_company("   ") == "marschkamp"


def test_an_explicit_company_always_wins(monkeypatch):
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp", "second-co"))
    assert gf.resolve_company("second-co") == "second-co"
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp",))
    assert gf.resolve_company("second-co") == "second-co", \
        "resolution must not silently redirect a named company to the sole one"


def test_two_companies_make_it_a_real_question_again(monkeypatch):
    """The default exists only while the answer is unambiguous. Add a second company and an
    omitted one stays empty, so the existing unknown-company refusal asks — which is correct,
    because then it IS a question."""
    monkeypatch.setattr(gf, "COMPANIES", ("marschkamp", "second-co"))
    assert gf.resolve_company("") == ""


def test_an_omitted_company_reads_and_writes_against_the_sole_one(monkeypatch):
    """End to end: the resolved company must reach the jail, not just the helper."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file(gf.resolve_company(""), "output/stage1.md", FULL_CONTENT,
                        user_confirmed=True, expect=FULL_EXPECT)
    assert out.get("written") is True, out
    assert out["path"].startswith("marschkamp/"), out["path"]


# ── Demo rooms: the room code decides storage (2026-08-24) ───────────────────────────
# A room is a directory under ROOMS_DIR whose name matches BIA_SLUG and is NOT an
# allowlisted company. Admission is directory existence — COMPANIES stays
# ("marschkamp",), so DEFAULT_COMPANY and resolve_company keep their single-company
# shape and the 2026-08-20 "which company?" regression cannot re-trigger.

ROOM = "kranich-x7k2mp"


@pytest.fixture()
def room():
    """One seeded room under the tmp ROOMS_DIR (conftest reroutes it); returns its code."""
    d = gf.ROOMS_DIR / ROOM
    (d / "output").mkdir(parents=True)
    (d / "01_Organisation").mkdir()
    (d / "02_BCM-Method").mkdir()
    (d / "03_Dependencies").mkdir()
    (d / ".versions").mkdir()
    (d / "company-profile.md").write_text("# Kranich Logistik (fiktiv)", encoding="utf-8")
    (d / "01_Organisation" / "org.md").write_text("org chart", encoding="utf-8")
    (d / "02_BCM-Method" / "method.json").write_text('{"scale": "1-4"}', encoding="utf-8")
    (d / "03_Dependencies" / "dependency-register.json").write_text(
        '{"a1": {"name": "ERP"}}', encoding="utf-8")
    (d / ".hidden.md").write_text("x", encoding="utf-8")
    return ROOM


@pytest.fixture()
def room2():
    """A second room — for pins that two codes never share state."""
    (gf.ROOMS_DIR / "adler-m3p7q2").mkdir(parents=True)
    return "adler-m3p7q2"


@pytest.fixture()
def no_network(monkeypatch):
    """Tripwire: every Graph touch goes through _client(); a room operation must never."""
    def _no_client():
        raise AssertionError("room operation reached the network")
    monkeypatch.setattr(gf, "_client", _no_client)


def test_a_room_code_lists_its_directory_without_graph(room, no_network):
    out = gf.list_files(room)
    assert out["company"] == room
    assert {f["name"] for f in out["files"]} == {
        "company-profile.md", "01_Organisation", "02_BCM-Method", "03_Dependencies",
        "output"}  # dot-names (.versions, .hidden.md) never listed
    by_name = {f["name"]: f for f in out["files"]}
    assert by_name["01_Organisation"]["is_folder"] is True
    assert by_name["company-profile.md"]["is_folder"] is False
    assert by_name["company-profile.md"]["path"] == "company-profile.md"


def test_a_room_subfolder_lists_like_graph(room, no_network):
    out = gf.list_files(room, "01_Organisation")
    assert [f["name"] for f in out["files"]] == ["org.md"]


def test_a_room_listing_miss_says_not_found(room, no_network):
    assert "not found" in gf.list_files(room, "09_Nichts")["error"]


def test_a_room_code_reads_a_file_without_graph(room, no_network):
    out = gf.read_file(room, "company-profile.md")
    assert out["content"] == "# Kranich Logistik (fiktiv)"
    assert out["path"] == f"{room}/company-profile.md"
    assert out["size"] == len(out["content"].encode("utf-8"))


def test_a_room_read_miss_keeps_the_graph_404_sentence(room, no_network):
    assert gf.read_file(room, "nope.md")["error"] == \
        f"file not found: {room}/nope.md — call list_company_files to see what exists"


def test_a_room_read_respects_max_read(room, no_network, monkeypatch):
    monkeypatch.setattr(gf, "MAX_READ", 10)
    (gf.ROOMS_DIR / room / "big.md").write_text("x" * 11, encoding="utf-8")
    assert "file too large to read (11 bytes)" in gf.read_file(room, "big.md")["error"]


def test_a_room_read_refuses_non_utf8(room, no_network):
    (gf.ROOMS_DIR / room / "blob.bin").write_bytes(b"\xff\xfe\x00nope")
    out = gf.read_file(room, "blob.bin")
    assert "error" in out and "UTF-8" in out["error"]


def test_an_unknown_code_gets_the_exact_unknown_company_error(room):
    """Codes are never enumerated: the refusal names the allowlist and nothing else."""
    err = gf.list_files("no-such-room")["error"]
    assert err == "unknown company 'no-such-room' — allowed: marschkamp"
    assert room not in err


def test_a_room_dir_never_shadows_an_allowlisted_company(room):
    """A directory named like an allowlisted company is dead weight, not a reroute:
    marschkamp stays SharePoint even if someone drops a folder with its name."""
    (gf.ROOMS_DIR / "marschkamp").mkdir()
    assert gf._room_dir("marschkamp") is None
    assert gf._room_dir("MARSCHKAMP") is None
    assert gf._room_dir(f"  {ROOM.upper()}  ") is not None  # lower/strip, like _jail


def test_room_codes_are_slug_jailed(room):
    """The code becomes a path segment, so BIA_SLUG is the traversal guard."""
    (gf.ROOMS_DIR / ".." / "escape").mkdir(exist_ok=True)
    assert gf._room_dir("../escape") is None
    assert gf._room_dir("a/b") is None
    assert gf._room_dir(".versions") is None
    assert gf._jail(f"{ROOM}", "../x") is None          # path rules unchanged inside a room
    assert gf._jail(f"{ROOM}", "a\\b") is None
    assert gf._jail(f"{ROOM}", "/abs") is None


def test_marschkamp_still_hits_graph_with_rooms_present(room, monkeypatch):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        assert "/drives/drive-1/root:/marschkamp:/children" in str(request.url)
        return httpx.Response(200, json={"value": [
            {"name": "company-profile.md", "size": 5, "file": {}}]})
    _mock(monkeypatch, handler)
    out = gf.list_files("marschkamp")
    assert [f["name"] for f in out["files"]] == ["company-profile.md"]


def test_default_company_and_empty_resolution_survive_rooms(room):
    """COMPANIES stays ("marschkamp",): rooms must not reopen the which-company question."""
    assert gf.DEFAULT_COMPANY == "marschkamp"
    assert gf.resolve_company("") == "marschkamp"
    assert gf.resolve_company(None) == "marschkamp"


STAGE1_ROOM_PATH = "output/cutting/stage1-scope-and-guide.md"
STAGE1_THIN = "# thin\n## Scope\n"
STAGE1_THIN_EXPECT = {"markers": ["## Scope"], "min_bytes": 10}


def test_a_room_gated_write_returns_the_full_receipt(room, no_network):
    out = gf.write_file(room, "output/slaughter/scope-notes.md", FULL_CONTENT,
                        user_confirmed=True, expect=FULL_EXPECT)
    assert out.get("written") is True, out
    assert out["mode"] == "create"
    assert out["path"] == f"{room}/output/slaughter/scope-notes.md"
    assert out["url"] == f"{gf.ROOMS_URL}/{room}/output/slaughter/scope-notes.md"
    line = out["verification"]["human_line"]
    assert "✓ Saved: output/slaughter/scope-notes.md" in line
    assert "bytes" in line and "2 sections" in line
    # No jail internals in the receipt PROSE, exactly like the Graph lane. The openable link
    # appended after it (F6) is a URL and does carry the room code — that is the tester's own
    # address, not a leak — so the rule is checked on the prose, not on the link.
    prose = line.split(" [Open document](")[0]
    assert f"{room}/" not in prose
    assert f"[Open document]({gf.ROOMS_URL}/{room}/output/slaughter/scope-notes.md)" in line
    assert out["next_move"] == "Tell the user the file is saved and continue the current stage."
    disk = gf.ROOMS_DIR / room / "output" / "slaughter" / "scope-notes.md"
    assert disk.read_text(encoding="utf-8") == FULL_CONTENT


def test_a_room_overwrite_banks_a_version_and_names_the_room_history(room, no_network):
    kw = dict(user_confirmed=True, expect=FULL_EXPECT)
    gf.write_file(room, "output/slaughter/scope-notes.md", FULL_CONTENT, **kw)
    out = gf.write_file(room, "output/slaughter/scope-notes.md", FULL_CONTENT + "more\n", **kw)
    assert out.get("written") is True, out
    assert out["mode"] == "overwrite"
    line = out["verification"]["human_line"]
    first = len(FULL_CONTENT.encode("utf-8"))
    second = len((FULL_CONTENT + "more\n").encode("utf-8"))
    assert f"{first:,} → {second:,} bytes" in line
    assert "The previous version stays in the room's version history." in line
    assert "SharePoint" not in line
    versions = list((gf.ROOMS_DIR / room / ".versions").iterdir())
    assert len(versions) == 1 and versions[0].name.endswith("-scope-notes.md")
    assert versions[0].read_text(encoding="utf-8") == FULL_CONTENT  # the OLD bytes


@pytest.mark.parametrize("kw", [
    dict(path="output/x/y.md", content="hi"),                                  # approval missing
    dict(path="notes.md", content="hi", user_confirmed=True),                  # outside output/
    dict(path=".versions/x.md", content="hi", user_confirmed=True),            # version store unwritable
    dict(path="output/x/y.md", content="", user_confirmed=True),               # empty content
    dict(path="output/x/y.md", content="hi", user_confirmed=True),             # expect missing
    dict(path=gf.RECORD_SAVE_PATH, content='{"a": 1}', user_confirmed=True),   # record by reference only
    dict(path=gf.REGISTER_PATH, content='{"a": {"x": 1}}',
         user_confirmed=True),                                                 # register floor
    dict(path="output/.versions/x.md", content=FULL_CONTENT, user_confirmed=True,
         expect=FULL_EXPECT),                                                  # dot-folder fails the slug rule
])
def test_room_refusals_match_the_graph_lane_word_for_word(room, no_network, kw):
    """Every gate runs identically on both backends by construction — and pre-network:
    the tripwire is live for BOTH lanes here, so a refusal that reached Graph would show."""
    kw = dict(kw)
    path, content = kw.pop("path"), kw.pop("content")
    graph = gf.write_file("marschkamp", path, content, **kw)
    local = gf.write_file(room, path, content, **kw)
    assert "error" in graph and "error" in local
    assert graph["error"] == local["error"]


def test_a_room_save_token_writes_by_reference_and_consumes(room, no_network):
    tok = gf.issue_save_token(room, {"activities": ["slaughter"]})
    expected = gf._validated_records[room]["data"]
    out = gf.write_file(room, gf.RECORD_SAVE_PATH, "", user_confirmed=True, save_token=tok)
    assert out.get("written") is True, out
    assert (gf.ROOMS_DIR / room / "output" / "bia-record.json").read_bytes() == expected
    assert out["url"] == f"{gf.ROOMS_URL}/{room}/output/bia-record.json"
    again = gf.write_file(room, gf.RECORD_SAVE_PATH, "", user_confirmed=True, save_token=tok)
    assert "save_token unknown, superseded or expired" in again["error"]


def test_a_room_approval_log_append_lands_locally(room, no_network):
    out = gf.write_file(room, "approval-log.jsonl", '{"approved": "scope"}\n',
                        user_confirmed=True)
    assert out.get("written") is True, out
    assert out["url"] == f"{gf.ROOMS_URL}/{room}/approval-log.jsonl"
    assert (gf.ROOMS_DIR / room / "approval-log.jsonl").read_text(encoding="utf-8") == \
        '{"approved": "scope"}\n'


def test_a_room_stage1_write_without_reads_names_the_room_in_the_read_calls(room, no_network):
    """§B.9 self-heals per room: the refusal's read calls carry the ROOM as company, so the
    agent's very next reads land credit where the write needs it."""
    out = gf.write_file(room, STAGE1_ROOM_PATH, STAGE1_THIN, user_confirmed=True,
                        expect=STAGE1_THIN_EXPECT)
    assert "error" in out
    calls = out["next_move"]
    assert f"read_company_file(company='{room}', path='02_BCM-Method/method.json')" in calls
    assert (f"read_company_file(company='{room}', "
            f"path='03_Dependencies/dependency-register.json')") in calls


def test_read_credit_never_leaks_between_rooms(room, room2, no_network):
    """The 2026-08-20 identity-plan regression shape: credit noted for one identity must
    never satisfy another's gate."""
    reads = ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json")
    for p in reads:
        gf.note_read(room2, p)
    out = gf.write_file(room, STAGE1_ROOM_PATH, STAGE1_THIN, user_confirmed=True,
                        expect=STAGE1_THIN_EXPECT)
    assert f"company='{room}'" in out["next_move"], "room2's credit must not leak into room"
    for p in reads:
        gf.note_read(room, p)
    out2 = gf.write_file(room, STAGE1_ROOM_PATH, STAGE1_THIN, user_confirmed=True,
                        expect=STAGE1_THIN_EXPECT)
    assert "error" in out2  # still refused — thin draft — but the reads are settled now
    assert "read_company_file" not in (out2.get("next_move") or "")


# T3 pins: the generated URLs come out per-room with ~zero changes, because dep_graph and
# interview_guide are already per-company. Expected to pass off T1/T2 — a failure is a finding.

def test_a_room_register_write_regens_the_public_graph_page(room, no_network, tmp_path):
    payload = json.dumps(_fake_register(), ensure_ascii=False, indent=2) + "\n"
    out = gf.write_file(room, gf.REGISTER_PATH, payload, user_confirmed=True)
    assert out.get("written") is True, out
    pages = tmp_path / "graph-pages" / room
    assert (pages / "index.html").exists(), "register write must regen the room's graph page"
    evidence = json.loads((pages / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["register"]["human_line"], "the room lane banks a real human_line"


def test_a_room_stage1_write_links_the_room_print_view(room, no_network, tmp_path):
    d = gf.ROOMS_DIR / room
    (d / "02_BCM-Method" / "method.json").write_text(GUIDE_METHOD_JSON, encoding="utf-8")
    (d / "03_Dependencies" / "dependency-register.json").write_text(
        GUIDE_REGISTER_JSON, encoding="utf-8")
    for p in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read(room, p)
    out = gf.write_file(room, "output/sales/stage1-scope-and-guide.md", GUIDE_CONFORMING,
                        user_confirmed=True, expect=STAGE1_EXPECT)
    assert out.get("written") is True, out
    assert out.get("print_url") == \
        f"https://agent.ai4bcm.org/demo/graph/{room}/sales/guide.html"
    page = tmp_path / "graph-pages" / room / "sales" / "guide.html"
    assert page.exists() and "order/EDI" in page.read_text(encoding="utf-8")


def test_room_search_walks_locally_and_skips_fixtures(room, no_network):
    d = gf.ROOMS_DIR / room
    (d / "09_Evaluation").mkdir()
    (d / "09_Evaluation" / "rt1-poison.md").write_text("OLGA MILEVSKA", encoding="utf-8")
    (d / "output" / "bia-record.json").write_text('{"owner": "OLGA MILEVSKA"}',
                                                 encoding="utf-8")
    out = gf.search_files(room, "olga milevska")
    assert [m["path"] for m in out["matches"]] == ["output/bia-record.json"]
    assert out["excluded_fixture_paths"] == 1  # 09_Evaluation skipped before any read


# ── F1 (2026-08-25): the Graph URLs quote the jailed path ────────────────────────────────
# The jail rightly allows ':' '?' '#' '%' — real filenames carry them ("BIA 2025: final",
# "coverage 100%.md") — but all four Graph URLs interpolated the path raw, so a '?' turned
# the path's tail into a query string, a '#' dropped it as a fragment, and ':'/'%' were
# mis-addressed. Everything but '/' is quoted; Graph's own ':' addressing stays raw.

ODD_NAME = "08_Prior-Cycle/BIA 2025: v2? #final 100%.md"


def _capture(seen):
    def handler(request):
        base = token_site_drive(request)
        if base:
            return base
        seen["url"] = str(request.url)
        seen["query"] = str(request.url.query, "ascii")
        path = str(request.url.path)
        if request.method == "PUT":
            seen["stored"] = request.content
            return httpx.Response(201, json={"id": "f1"})
        if path.endswith(":/children"):
            return httpx.Response(200, json={"value": []})
        if path.endswith(":/content"):   # read-back echoes the PUT, like _artifact_handler
            return httpx.Response(200, content=seen.get("stored", b"body"))
        return httpx.Response(404)       # meta GET: new file
    return handler


def test_read_file_quotes_the_odd_path(monkeypatch):
    seen = {}
    _mock(monkeypatch, _capture(seen))
    out = gf.read_file("marschkamp", ODD_NAME)
    assert "error" not in out, out
    assert seen["query"] == ""                        # '?' never became a query string
    assert seen["url"].endswith(":/content")          # '#' never truncated the tail
    for enc in ("%3A", "%3F", "%23", "%25"):
        assert enc in seen["url"], seen["url"]


def test_list_files_quotes_the_odd_subpath(monkeypatch):
    seen = {}
    _mock(monkeypatch, _capture(seen))
    out = gf.list_files("marschkamp", "08_Prior: 2025? #x 100%")
    assert "error" not in out, out
    assert seen["query"] == ""
    assert seen["url"].endswith(":/children")
    for enc in ("%3A", "%3F", "%23", "%25"):
        assert enc in seen["url"], seen["url"]


def test_write_file_quotes_the_odd_path_on_meta_and_put(monkeypatch):
    seen = {}
    _mock(monkeypatch, _capture(seen))
    out = gf.write_file("marschkamp", "output/packing/notes 100% #v2?.md", "x" * 40,
                        user_confirmed=True, expect={"markers": ["xxxx"], "min_bytes": 10})
    assert out.get("written") is True, out
    assert seen["query"] == ""                        # the PUT is captured last
    assert seen["url"].endswith(":/content")
    for enc in ("%3F", "%23", "%25"):
        assert enc in seen["url"], seen["url"]


# ── F6 (2026-08-25): receipt URLs are markdown links, never bare ──────────────────────────
# Live in bia3: human_line ended "Print view: <url>" and the model's next line began "Open
# document:", so the web chat's autolinker swallowed the following word into the href and
# served https://…/guide.htmlOpen — a 404. Markdown-wrapped links cannot be extended that
# way. Runtime strings only: no payload cost, no schema change, no republish.

def test_the_receipt_links_are_markdown_and_carry_no_bare_url(monkeypatch, tmp_path):
    import dep_graph
    monkeypatch.setattr(dep_graph, "PUBLIC", tmp_path / "graph-pages")
    state = {}
    _stage1_guide_world(monkeypatch, state)
    out = gf.write_file("marschkamp", "output/sales/stage1-scope-and-guide.md",
                        GUIDE_CONFORMING, user_confirmed=True, expect=STAGE1_EXPECT)
    assert out.get("written") is True, out
    line = out["verification"]["human_line"]
    guide = "https://agent.ai4bcm.org/demo/graph/marschkamp/sales/guide.html"
    assert f"[Print view]({guide})" in line, line
    # no URL may sit outside markdown link syntax — that is the whole defect
    for bare in re.finditer(r"(?<!\()https?://\S+", line):
        assert False, f"bare URL in receipt: {bare.group(0)}"


def test_the_openable_document_link_rides_the_receipt_as_markdown(monkeypatch):
    """So the model has a verbatim link to print instead of composing its own bare
    'Open document: <url>' from result['url'] — which is what produced '…-guide.mdStage'."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state, web_url="https://sharepoint.example/x.md"))
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read("marschkamp", path)
    out = gf.write_file("marschkamp", "output/packing/notes.md", "x" * 40,
                        user_confirmed=True, expect={"markers": ["xxxx"], "min_bytes": 10})
    assert out.get("written") is True, out
    line = out["verification"]["human_line"]
    assert "[Open document](https://sharepoint.example/x.md)" in line, line
    for bare in re.finditer(r"(?<!\()https?://\S+", line):
        assert False, f"bare URL in receipt: {bare.group(0)}"


def test_a_save_without_an_openable_url_still_gets_a_clean_receipt(monkeypatch):
    """The room lane and any Graph reply without a webUrl: absent link, absent clause —
    never an empty '[Open document]()' the renderer turns into a dead anchor."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))          # PUT returns no webUrl
    for path in ("02_BCM-Method/method.json", "03_Dependencies/dependency-register.json"):
        gf.note_read("marschkamp", path)
    out = gf.write_file("marschkamp", "output/packing/notes.md", "x" * 40,
                        user_confirmed=True, expect={"markers": ["xxxx"], "min_bytes": 10})
    assert out.get("written") is True, out
    assert "[Open document]" not in out["verification"]["human_line"]


# ── §A.15 (2026-08-25): the four resource categories are contract markers ────────────────
# Found 2026-08-24 by Bruno re-reading his own output, not by the check built to catch it:
# a Stage 2 capture carrying two of the four required resource categories PASSED — the
# absent ones were absent, not recorded as "not asked", and Stage 3 fills its template from
# exactly those fields, so an absent category invites filling by inference. The prompt has
# taught "capture it or record it as not asked" all along; instruction without enforcement
# is the §B pattern. The markers now assert presence; the recorded STATE stays the prompt's
# job (D-36: "not asked", never "none", never "n/a").

A15_PATH = "output/cutting/stage2-interview-capture.md"
A15_BASE = ("## Impacts\n" + "impact line\n" * 30 +
               "## Dependencies\n" + "dependency line\n" * 30 +
               "### Resource requirements by category\n"
               "- People — carried from prior transcripts.\n"
               "- IT/applications — partial, via ERP and weighing.\n"
               "## Assumptions\nassumption line\n" * 3 +
               "## Unresolved points\npoint\n" +
               "## Gaps\ngap\n")
A15_EXPECT = {"markers": ["## Impacts", "## Dependencies"], "min_bytes": 1200}


def test_a_capture_missing_resource_categories_is_refused_naming_them(monkeypatch):
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    out = gf.write_file("marschkamp", A15_PATH, A15_BASE,
                        user_confirmed=True, expect=A15_EXPECT)
    assert "error" in out, out
    assert "Buildings/seats" in out["error"] and "Suppliers" in out["error"], out["error"]
    assert "put" not in state, "refused before anything reached SharePoint"


def test_a_capture_recording_all_four_categories_saves_even_as_not_asked(monkeypatch):
    """'Not asked' IS a recorded state — the enforcement must accept exactly what D-36's
    owner-ruled amendment wrote, or the fix would refuse the ruling it encodes."""
    state = {}
    _mock(monkeypatch, _artifact_handler(state))
    full = A15_BASE.replace(
        "- IT/applications — partial, via ERP and weighing.\n",
        "- IT/applications — partial, via ERP and weighing.\n"
        "- Buildings/seats — not asked in this interview.\n"
        "- Suppliers — not asked in this interview.\n")
    out = gf.write_file("marschkamp", A15_PATH, full,
                        user_confirmed=True, expect=A15_EXPECT)
    assert out.get("written") is True, out
    assert "put" in state
