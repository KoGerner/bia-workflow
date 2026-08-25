"""call_log — one JSON line per MCP tool call, never file content (visibility layer A,
2026-08-16). The MCP server sees tool calls, not the chat; this is the part it can see."""
from __future__ import annotations

import json
import os

import call_log


def test_logged_writes_names_and_sizes_never_content(tmp_path, monkeypatch):
    monkeypatch.setattr(call_log, "DIR", tmp_path)

    @call_log.logged
    def write_company_file(company, path, content="", user_confirmed=False):
        return {"path": path, "verification": {"human_line": "ok"}}

    write_company_file(company="marschkamp", path="output/x.md", content="SECRET " * 50,
                       user_confirmed=True)
    files = list(tmp_path.glob("calls-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "write_company_file" and row["company"] == "marschkamp"
    assert row["path"] == "output/x.md" and row["content_bytes"] == 350
    assert row["verdict"] == "saved"
    assert "content" not in row and "SECRET" not in json.dumps(row)
    # 0640 / dir 0750, not 0600 / 0700: chmod rewrites the POSIX-ACL mask (= the group bits), and
    # the read grant for `brain` on /srv/addendum/usage (setfacl u:brain:r, T6 2026-08-18) died on
    # the first logged call under 0600 — measured. Group is svc-bia with no members, so 0640 gives
    # nobody new anything without an ACL. Secrets (dataverse.token) stay 0600 on purpose.
    assert oct(os.stat(files[0]).st_mode)[-3:] == "640"
    assert oct(os.stat(tmp_path).st_mode)[-3:] == "750"


def test_record_lane_logs_the_bytes_written_not_the_omitted_argument(tmp_path, monkeypatch):
    """run (b) 2026-08-18, 21:00:12Z: output/bia-record.json logged content_bytes 0 — the token
    lane binds the referee-validated bytes server-side, so the tool is called with no content and
    the argument's length is 0. Three runs logged a zero-byte save of the one artifact the graph
    renders from (11:03:25, 17:50:32, 21:00:12). A reviewer checking 'was anything saved' against
    the log — Leo's audit brief, and the W33 'its not in teh putput folder' class — reads that as
    a truncation. When the tool reports what it wrote, the log records that."""
    monkeypatch.setattr(call_log, "DIR", tmp_path)

    @call_log.logged
    def write_company_file(company, path, content="", save_token=None):
        return {"written": True, "path": path, "size": 31337,
                "verification": {"human_line": "ok"}}

    write_company_file(company="marschkamp", path="output/bia-record.json",
                       save_token="tok")
    row = json.loads(next(tmp_path.glob("calls-*.jsonl")).read_text().splitlines()[0])
    assert row["content_bytes"] == 31337
    assert row["verdict"] == "saved"


def test_verdicts_cover_error_pass_fail_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(call_log, "DIR", tmp_path)

    @call_log.logged
    def t(v):
        return v

    t(v={"error": "stage_incomplete"}); t(v={"pass": True}); t(v={"pass": False}); t(v={"files": []})
    rows = [json.loads(line) for line in next(tmp_path.glob("calls-*.jsonl")).read_text().splitlines()]
    assert [r["verdict"] for r in rows] == ["error:stage_incomplete", "PASS", "FAIL", "ok"]


def test_logged_never_breaks_the_tool(monkeypatch):
    monkeypatch.setattr(call_log, "_write", lambda row: (_ for _ in ()).throw(OSError("disk")))

    @call_log.logged
    def search(query):
        return {"hits": 1}

    assert search(query="rto") == {"hits": 1}


def test_logged_keeps_the_signature_for_fastmcp():
    import inspect

    @call_log.logged
    def next_step(journey_id: str = "run-bia", stage_id: str = "", company: str = "marschkamp"):
        return {}

    assert list(inspect.signature(next_step).parameters) == ["journey_id", "stage_id", "company"]


def test_sized_arguments_are_counted_in_bytes_not_characters():
    """Leo's audit 2026-08-19, objection 6: `_summary` sized every body with `len(str)` — a
    CHARACTER count. Six of the seven byte numbers in run (b)'s report disagreed with the log
    because the marschkamp files are German; Hans's numbers were right and the log was wrong, and
    the run (b) verification declared that slice clean. `9660dc9` moved `content_bytes` onto the
    written bytes; `record_bytes` and `changes_bytes` are still on this path, so nothing in the log
    binds a record save to its size. A byte count that is not bytes is worse than no byte count:
    it reads as corroboration."""
    umlauts = "Schlüsselprozess Kühlung Übergabe"           # 33 characters, 36 UTF-8 bytes
    out = call_log._summary({"record": umlauts, "changes": {"owner": "Petra Löwen"}})
    assert out["record_bytes"] == len(umlauts.encode("utf-8")) == 36, out
    assert out["changes_bytes"] == len(json.dumps({"owner": "Petra Löwen"}, default=str).encode("utf-8")), out


def test_rows_carry_a_stable_conversation_id_without_logging_the_raw_one(tmp_path, monkeypatch):
    """The log records what happened and never who, so a Copilot run could only be attributed by
    wall-clock window — measured all through 2026-08-20, and it is why two surfaces sharing one
    server cannot be told apart. `ctx` carries `client_id`, so every row can name its
    conversation.

    Hashed, not raw: the id comes from the client and this file is read by people debugging, so
    it identifies a conversation without publishing whatever the client chose to call itself."""
    monkeypatch.setattr(call_log, "DIR", tmp_path)

    class Ctx:
        client_id = "copilot-studio-conversation-42"

    @call_log.logged
    def read_company_file(company, path, ctx=None):
        return {"content": "x"}

    ctx = Ctx()
    read_company_file(company="marschkamp", path="a.md", ctx=ctx)
    read_company_file(company="marschkamp", path="b.md", ctx=ctx)
    rows = [json.loads(l) for l in list(tmp_path.glob("calls-*.jsonl"))[0].read_text().splitlines()]
    assert rows[0]["conv"] == rows[1]["conv"], "one conversation, one id"
    assert "copilot-studio-conversation-42" not in json.dumps(rows), "hashed, never raw"


def test_a_call_with_no_context_carries_no_conversation_id(tmp_path, monkeypatch):
    """Absent, not null or 'unknown' — the estate clock and the tests call these functions
    directly, and a placeholder id would read as a conversation that never happened."""
    monkeypatch.setattr(call_log, "DIR", tmp_path)

    @call_log.logged
    def list_company_files(company):
        return {"files": []}

    list_company_files(company="marschkamp")
    row = json.loads(list(tmp_path.glob("calls-*.jsonl"))[0].read_text().splitlines()[0])
    assert "conv" not in row


def test_every_logged_tool_is_sync():
    """`logged` carried an async branch until 2026-08-24 that had never wrapped anything —
    all 14 registered tools are plain defs. Removing it is only safe while that stays true:
    an `async def` tool would silently lose its call-log row, and the call log is the only
    evidence layer this product has for what the agent actually did. Regression pin, not a
    red-green cycle — it passed on arrival, which is the point."""
    import ast
    import inspect as _inspect
    import pathlib

    src = pathlib.Path(__file__).with_name("server.py").read_text(encoding="utf-8")
    tools = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and any("logged" in ast.dump(d) for d in n.decorator_list)]
    assert tools, "no @call_log.logged tools found — the scan broke, not the server"
    offenders = [n.name for n in tools if isinstance(n, ast.AsyncFunctionDef)]
    assert not offenders, (
        f"async tool(s) {offenders} would not be logged — call_log.logged is sync-only since "
        "2026-08-24; restore the async branch in the same commit that adds an async tool")
    assert not _inspect.iscoroutinefunction(call_log.logged(lambda **kw: None))
