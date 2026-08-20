"""Company-file tools backed by Microsoft Graph (Sites.Selected, app-only).

Stateless pass-through: the server stores NO company data. All access jailed to
allowlisted company folders in the AIBCM site's Documents library. Spec:
docs/superpowers/specs/2026-07-19-graph-file-tools-mcp-design.md
"""
from __future__ import annotations

import concurrent.futures
import fnmatch
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

import httpx

import journeys as journey_engine

# File-relative default (same pattern as journeys.py): the checkout this module lives in
# IS the app root, so a worktree/copy run without BIA_WORKFLOW_ROOT stays inside itself.
APP_ROOT = Path(os.environ.get("BIA_WORKFLOW_ROOT", Path(__file__).resolve().parent))
# The Graph client secret sits beside the bearer (C13, 2026-08-18): /srv/addendum/{secret,graph-secret}
# on brain, <checkout>/{secret,graph-secret} locally — one knob (TOKEN_FILE) places both.
SECRET_FILE = Path(os.environ.get("BIA_WORKFLOW_TOKEN_FILE", APP_ROOT / "secret")).parent / "graph-secret"
SITE_HOST = "kgerner.sharepoint.com"
SITE_PATH = "/sites/AIBCM"
COMPANIES = tuple(
    c.strip().lower()
    for c in os.environ.get("BIA_WORKFLOW_COMPANIES", "marschkamp").split(",")
    if c.strip()
)
REGISTER_PATH = "03_Dependencies/dependency-register.json"
WRITE_EXCEPTIONS = (REGISTER_PATH, "approval-log.jsonl")
MAX_WRITE = 1_000_000  # bytes of UTF-8 text
# Full-register floor (P4 incident 2026-07-22, lessons #16: the server accepted 370 bytes of
# approval-summary prose over the 31KB register). The live register has 15 entries / ~31KB.
REGISTER_MIN_ENTRIES = 10
REGISTER_MIN_BYTES = 10_000
MAX_READ = 2_000_000
GRAPH = "https://graph.microsoft.com/v1.0"

_cache: dict[str, object] = {}


def _client() -> httpx.Client:
    # follow_redirects: Graph answers GET …:/content with a 302 to a short-lived
    # SharePoint download URL — without this every read fails on the redirect.
    return httpx.Client(timeout=30, follow_redirects=True)


def _creds() -> dict[str, str]:
    if not SECRET_FILE.exists():
        raise RuntimeError(f"missing graph secret file: {SECRET_FILE}")
    creds: dict[str, str] = {}
    for line in SECRET_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    for key in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"):
        if not creds.get(key):
            raise RuntimeError(f"graph-secret missing {key}")
    return creds


def _token() -> str:
    now = time.monotonic()
    tok = _cache.get("token")
    if tok and now < _cache.get("token_exp", 0):  # type: ignore[operator]
        return tok  # type: ignore[return-value]
    c = _creds()
    with _client() as http:
        r = http.post(
            f"https://login.microsoftonline.com/{c['TENANT_ID']}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": c["CLIENT_ID"],
                "client_secret": c["CLIENT_SECRET"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
    r.raise_for_status()
    body = r.json()
    _cache["token"] = body["access_token"]
    _cache["token_exp"] = now + int(body.get("expires_in", 3600)) - 300
    return body["access_token"]


def _get(url: str, **kw) -> httpx.Response:
    with _client() as http:
        return http.get(url, headers={"Authorization": f"Bearer {_token()}"}, **kw)


def _drive() -> str:
    if "drive" not in _cache:
        r = _get(f"{GRAPH}/sites/{SITE_HOST}:{SITE_PATH}")
        r.raise_for_status()
        site_id = r.json()["id"]
        r2 = _get(f"{GRAPH}/sites/{site_id}/drive")
        r2.raise_for_status()
        _cache["drive"] = r2.json()["id"]
    return _cache["drive"]  # type: ignore[return-value]


def _jail(company: str, path: str = "") -> str | None:
    """Return the jailed drive path, or None if refused."""
    company = (company or "").strip().lower()
    if company not in COMPANIES:
        return None
    if path in ("", None):
        return company
    if "\\" in path or path.startswith("/") or ".." in path.split("/") or ".." in path:
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or any(".." in p for p in parts):
        return None
    return company + "/" + "/".join(parts)


def _err(msg: str) -> dict:
    return {"error": msg}


def _drop_no_ops(entry: dict, changes: dict) -> tuple[dict, str | None]:
    """Strip fields already holding the requested value; the second item is a refusal
    reason when nothing is left to change.

    ponytail: a from == to patch otherwise rewrites the whole file, re-runs the referee
    and fires a graph regen for no change — and on the record lane it banks an amendment
    reading 'X -> X', which reads back as "the first change did not apply" when it had
    (live 2026-08-03, "Konstantin Gerner -> Konstantin Gerner" on Cutting / deboning).
    """
    same = sorted(f for f, v in changes.items() if v == entry.get(f))
    changes = {f: v for f, v in changes.items() if f not in same}
    if changes:
        return changes, None
    return changes, (", ".join(f"{f} is already '{entry.get(f)}'" for f in same)
                     + " — nothing to correct.")


def _mentions(value, needle: str) -> bool:
    """Does a record field still contain this text? Strings and lists of strings only —
    the analytical prose that names owners (`status`, `rpo_status`, `open_decisions`)."""
    if isinstance(value, str):
        return needle in value
    if isinstance(value, list):
        return any(isinstance(x, str) and needle in x for x in value)
    return False


# ── journey-owned artifact contracts (P7 I-1 part 1) ─────────────────────────────────
# Write-side jaw of the stage binding: on a canonical stage-artifact path the YAML's
# markers + floor are enforced IN ADDITION to the agent's self-declared expect —
# server-owned anchors hold where agent-owned anchors fold (halfB 19:40:02 stub PUT).
_contracts_cache: list[dict] | None = None

# Per-BIA folders (owner ruling 2026-08-18: runs ADD to output/, they never overwrite another
# BIA). A contract path may carry `<bia>` — one folder segment, the process slug — e.g.
# output/<bia>/bia-draft.md. Run (a) 2026-08-18 morning showed why: the Slaughter run overwrote
# Cutting/Deboning's still-open bia-draft.md + bia-signoff.json (BIA-2026-002) at the fixed names.
BIA_PLACEHOLDER = "<bia>"
BIA_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def slugify(text: str) -> str:
    """'Slaughter Process' -> 'slaughter-process' — the folder name we suggest back."""
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").casefold()).strip("-")


def _bia_regex(path: str) -> re.Pattern:
    seg = re.escape(BIA_PLACEHOLDER)
    return re.compile("^" + re.escape(path.casefold()).replace(seg, r"(?P<bia>[^/]+)") + "$")


def _stage_contracts() -> list[dict]:
    global _contracts_cache
    if _contracts_cache is None:
        _contracts_cache = [
            {**c, "stage_num": num, "stage_id": s.id, "stage_name": s.name,
             "requires_reads": list(getattr(s, "requires_reads", [])),
             "_re": _bia_regex(c["path"]) if BIA_PLACEHOLDER in c["path"] else None}
            for j in journey_engine.load_journeys(journey_engine.JOURNEYS_DIR).values()
            for num, s in enumerate(j.stages, start=1)
            for c in s.document_contracts
        ]
    return _contracts_cache


def _legacy_singletons() -> set[str]:
    """Basenames of the per-BIA documents — at the top of output/ they are the old clobbering
    slots and are refused (derived from the yaml, never listed by hand)."""
    return {c["path"].rsplit("/", 1)[-1].casefold() for c in _stage_contracts() if c["_re"]}


def _contract_for(rel: str) -> dict | None:
    # SharePoint resolves paths case-insensitively — match the same way, or a
    # Case-variant canonical path would occupy the slot yet dodge the write-time jaw.
    low = rel.casefold()
    contracts = _stage_contracts()
    exact = next((c for c in contracts if not c["_re"] and c["path"].casefold() == low), None)
    if exact:
        return exact
    per_bia = next((c for c in contracts if c["_re"] and c["_re"].match(low)), None)
    if per_bia:
        return per_bia
    return next((c for c in contracts
                 if "*" in c["path"] and fnmatch.fnmatch(low, c["path"].casefold())), None)


def _shared_folders() -> set[str]:
    """Folder names the yaml declares as shared across BIAs (output/owner-interviews/*.md,
    output/proposals/*-owner-capture.md) — derived, never listed by hand, like
    _legacy_singletons. A shared folder is nobody's BIA folder."""
    return {c["path"].split("/")[1].casefold() for c in _stage_contracts()
            if not c["_re"] and c["path"].casefold().startswith("output/")
            and c["path"].count("/") >= 2}


def _bia_folder_error(rel: str, contract: dict) -> str | None:
    """The folder segment of a per-BIA path must be a lowercase-hyphen slug (SharePoint keeps
    what it is given; 'Slaughter Process/' and 'slaughter/' would be two BIAs of one process),
    and it must not be one of the folders every BIA shares."""
    seg = contract["_re"].match(rel.casefold()).group("bia")
    raw = rel.split("/")[1]
    # Run (b) 2026-08-18: 'the interview is saved to output/owner-interviews/' was read as
    # 'the BIA folder is output/owner-interviews/' and the stage-1 write was taken — a shared
    # folder is a well-formed slug, so shape was the only thing standing in the way.
    if raw.casefold() in _shared_folders():
        return (f"write refused: output/{raw}/ is shared by every BIA and is not this BIA's "
                f"folder — save the stage documents as output/<bia>/{rel.rsplit('/', 1)[-1]} "
                f"(<bia> = the process slug named in the Stage 1 card), and keep "
                f"output/{raw}/ for the documents it is declared for.")
    if BIA_SLUG.fullmatch(raw):
        return None
    return (f"write refused: the BIA folder must be a lowercase-hyphen slug of the process "
            f"(e.g. output/slaughter/, output/cutting-deboning/) — got 'output/{raw}/'; "
            f"use output/{slugify(raw) or seg}/ and retry.")


# ── token validate-and-save (P7 I-1 part 3, record lane only) ────────────────────────
# HalfA proved the agent can neither count bytes nor re-emit byte-identically; the token
# binds the referee-validated bytes server-side and the write happens by reference.
RECORD_SAVE_PATH = "output/bia-record.json"
SAVE_TOKEN_TTL_S = 2 * 60 * 60
_validated_records: dict[str, dict] = {}  # company -> {"token", "data", "issued"}

# Which company documents the AGENT read since the current journey started. Deliberately
# NOT recorded inside read_file(): the referee reads the method and the register on every
# validate call, and the advance gate fetches artifacts itself — counting those would make
# the read gate pass for work the agent never did. Recorded at the tool boundary only.
# ponytail: process-wide and cleared wholesale by start_journey, because start_journey_fn
# takes no company. With two journeys in flight the second start clears the first's reads,
# which makes the gate stricter rather than looser — it fails safe. Key it per journey if
# concurrent runs ever become real.
_reads_seen: dict[str, set[str]] = {}


def note_read(company: str, path: str) -> None:
    _reads_seen.setdefault((company or "").strip().lower(), set()).add(path)


def reads_seen(company: str) -> set[str]:
    return set(_reads_seen.get((company or "").strip().lower(), ()))


def forget_reads() -> None:
    _reads_seen.clear()



def issue_save_token(company: str, record: dict) -> str:
    """Referee-PASS hook: bind the validated record's canonical bytes to a one-time
    token. One slot per company — a newer PASS supersedes; a service restart drops the
    slot and the agent simply revalidates. If a newer PASS lands mid-write, the older
    token still writes its own referee-validated bytes and the newer slot stays live —
    _consume_save_token only clears the slot whose token matches."""
    data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    slot = {"token": secrets.token_hex(16), "data": data, "issued": time.monotonic()}
    _validated_records[(company or "").strip().lower()] = slot
    return slot["token"]


def _save_token_error(company_key: str, rel: str, save_token: str,
                      content: str) -> tuple[str | None, bytes | None]:
    slot = _validated_records.get(company_key)
    if (slot is None or slot["token"] != save_token
            or time.monotonic() - slot["issued"] > SAVE_TOKEN_TTL_S):
        return ("write refused: save_token unknown, superseded or expired — re-run "
                "validate_bia_record and use the save_token from its fresh PASS "
                "result.", None)
    if rel != RECORD_SAVE_PATH:
        return (f"write refused: a save_token write is bound to {RECORD_SAVE_PATH} — "
                "save the validated record there; other files use expect.", None)
    if content and content.encode("utf-8") != slot["data"]:
        return ("write refused: content does not match the validated record this "
                "save_token is bound to — omit content (the server writes the "
                "validated bytes itself) or re-run validate_bia_record for the "
                "changed record.", None)
    return None, slot["data"]


def _consume_save_token(company_key: str, save_token: str) -> None:
    slot = _validated_records.get(company_key)
    if slot is not None and slot["token"] == save_token:
        del _validated_records[company_key]


def _one_refusal(problems: list[str]) -> str:
    """Several problems with one draft, named in one refusal.

    Live 2026-08-20 13:17:38Z: a 782-byte draft with zero reads behind it was refused for its
    size alone, because _expect_error returns before the read check is reached. The agent fixed
    the half it had been told about, called next_step 21 seconds later, and learned about the
    reads from `stage_incomplete` instead. One bad draft, two narrated approval turns. The same
    lesson already holds INSIDE _unread_source_error, which names every unread source at once;
    this is it one level up, across the checks.

    A single problem keeps its own sentence, unchanged — the numbered form is only for the case
    that used to cost a round trip each.
    """
    if len(problems) == 1:
        return problems[0]
    body = "\n".join(f"{i}. {p.removeprefix('write refused: ')}"
                     for i, p in enumerate(problems, 1))
    return f"write refused — {len(problems)} problems with this draft:\n{body}"


def _stage_contract_error(contract: dict, content: str, size: int) -> str | None:
    problems = []
    if size < contract["min_bytes"]:
        problems.append(f"the file is {size} bytes but this stage document requires "
                        f"at least {contract['min_bytes']}")
    missing = [m for m in contract["markers"] if m not in content]
    if missing:
        shown = ", ".join(repr(m) for m in missing[:6])
        more = f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""
        problems.append(f"it is missing the required sections: {shown}{more}")
    if not problems:
        return None
    label = contract.get("name") or contract["stage_id"]
    return (f"write refused: this is the Stage-{contract['stage_num']} {label} "
            f"({contract['path']}) — " + " and ".join(problems) + ". A headline "
            "summary is not the stage document. Save the complete version you "
            "presented for approval; a short extra summary may go to a different "
            "filename.")


def _unread_source_error(company: str, contract: dict) -> str | None:
    """The stage's required reads, checked at the SAVE rather than only at the advance.

    Testers, 2026-08-20: seven approvals to reach Stage 2. next_step blocked on an unread
    dependency register AFTER the scope note was drafted, approved and saved, so the document
    had to be rewritten — the gate audited the draft instead of informing it. Refusing here
    means the agent reads first and drafts once. The advance gate keeps its own copy: this is
    the earlier jaw, not a replacement, and a document written outside a journey still meets it.

    Every unread source is named in ONE refusal. Naming them one at a time makes the round
    trips scale with the number of required reads: live 2026-08-20 12:39-12:42, the agent was
    refused for the method, read exactly it, retried, and was refused again for the register —
    two refusals, two narrated approval turns and two save previews for one missing pair.

    A document the company never supplied is never demanded — same rule as the advance gate, so
    'missing' and 'unread' stay different things. The probe reads through this module, which
    does NOT grant credit (note_read is called one layer up, in the tool), so probing can never
    satisfy the very gate it is testing.
    """
    missing = [p for p in (contract.get("requires_reads") or ())
               if p not in reads_seen(company) and "error" not in read_file(company, p)]
    if not missing:
        return None
    label = contract.get("name") or contract["stage_id"]
    calls = "; ".join(f"read_company_file(company='{company}', path='{p}')" for p in missing)
    return (f"write refused: this is the Stage-{contract['stage_num']} {label} "
            f"({contract['path']}) and these have not been read in this journey: "
            f"{', '.join(missing)}. This document's content must come from the company's own "
            f"material, not from memory. Call {calls} — all of them — then write the document "
            "from what they say.")


def _graph_regen(company_key: str, rel: str, data: bytes, result: dict) -> None:
    """Open item 9: regenerate the dependency-graph page after a verified gated write
    of the register or the BIA record. Contract: NEVER
    blocks or fails the write — this is a logged boundary, not flow control."""
    if rel not in (REGISTER_PATH, RECORD_SAVE_PATH):
        return
    try:
        import dep_graph  # lazy: dep_graph's CLI imports this module
        dep_graph.bank_and_regen(company_key, rel, data, result, read_file)
    except Exception as exc:  # noqa: BLE001 — hook boundary, logged and swallowed
        logging.getLogger(__name__).warning("graph regen failed: %s", exc)


def list_files(company: str, subpath: str = "") -> dict:
    # subpath="" lists the company root; a relative subpath (e.g. "07_Interviews") lists that
    # subfolder's children — the referee needs it to discover date-prefixed interview filenames.
    jailed = _jail(company, subpath)
    if jailed is None:
        return _err(f"unknown company '{company}' — allowed: {', '.join(COMPANIES)}")
    r = _get(f"{GRAPH}/drives/{_drive()}/root:/{jailed}:/children")
    if r.status_code == 404:
        return _err(f"folder '{jailed}' not found in SharePoint")
    r.raise_for_status()
    files = [
        {"name": it["name"], "size": it.get("size", 0),
         "is_folder": "folder" in it, "path": f"{it['name']}"}
        for it in r.json().get("value", [])
    ]
    return {"company": company, "files": files}


# ── search_files (tool #15) ──────────────────────────────────────────────────────────
# Graph's own drive search() would be the lazy answer, but it is app-only-unsupported on
# this library: every form of /search(q=) returns 500 generalException under the client-
# credentials token (probed live 2026-07-31). So: walk and match in Python. The room is
# ~260 KB once fixtures are excluded, which is why the naive version is the right one.
#
# Two names are skipped BEFORE they are read, not filtered out of results afterwards, so
# their bytes never enter the process: 09_Evaluation carries rt1-poison.md, a live
# prompt-injection fixture, plus golden-run transcripts holding the answers to the BIA
# the agent is meant to work out; pack-backup-* carries a stale second register that
# re-arming never reaches. read_file on a known path still reaches all three, deliberately.
SEARCH_EXCLUDE = ("09_evaluation", "pack-backup")
# ponytail: whole-room scan, no index. Fine at demo-room size; if a room ever outgrows this
# budget the upgrade is a SharePoint search *connector*, not paging this loop.
MAX_SEARCH_BYTES = 4_000_000


def search_files(company: str, query: str) -> dict:
    """Which company artifacts mention this text. Returns paths, never content: 'which
    files' is the question, read_file answers 'what do they say', and keeping content out
    means one poisoned file cannot ride a search hit into the model's context."""
    q = " ".join(str(query or "").split())
    if not q:
        return _err("search refused: query must be non-empty text")
    if _jail(company) is None:
        return _err(f"unknown company '{company}' — allowed: {', '.join(COMPANIES)}")
    needle, skipped, truncated = q.lower(), 0, False
    todo, pending, budget = [], [""], MAX_SEARCH_BYTES
    while pending:
        base = pending.pop()
        listing = list_files(company, base)
        if "error" in listing:
            return listing
        for it in listing["files"]:
            rel = f"{base}/{it['name']}" if base else it["name"]
            if any(x in rel.lower() for x in SEARCH_EXCLUDE):
                skipped += 1
            elif it["is_folder"]:
                pending.append(rel)
            elif it["size"] > budget:
                truncated = True
            else:
                budget -= it["size"]
                todo.append((rel, it["size"]))

    def _hit(item):
        rel, size = item
        got = read_file(company, rel)
        # no "content" = too large or vanished mid-walk; not a match, not an error worth failing on
        return (rel, size) if "content" in got and needle in got["content"].lower() else None

    # ponytail: 8 threads, because this is latency-bound and not CPU- or bandwidth-bound —
    # one round trip per file, ~40 files, 37 s sequential on marschkamp (measured), which is
    # past a chat tool's patience. Raise it only if a room's file COUNT grows, not its size.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        matches = [{"path": r, "size": s} for r, s in filter(None, pool.map(_hit, todo))]
    return {"company": company, "query": q,
            "matches": sorted(matches, key=lambda m: m["path"]),
            # Say what was withheld rather than silently shrinking the answer — a search that
            # under-reports without saying so reads as "nothing there".
            "excluded_fixture_paths": skipped, "truncated": truncated,
            "note": "paths only — read_company_file for content. Evaluation fixtures and "
                    "backup packs are never read. Exact substring, case-insensitive."}


def read_file(company: str, path: str) -> dict:
    jailed = _jail(company, path)
    if jailed is None or jailed == company:
        return _err("invalid path — use a relative path inside the company folder, e.g. 'company-profile.md'")
    r = _get(f"{GRAPH}/drives/{_drive()}/root:/{jailed}:/content")
    if r.status_code == 404:
        return _err(f"file not found: {jailed} — call list_company_files to see what exists")
    r.raise_for_status()
    if len(r.content) > MAX_READ:
        return _err(f"file too large to read ({len(r.content)} bytes)")
    return {"path": jailed, "content": r.text, "size": len(r.content)}


# Task 7 (2026-08-19): the receipt says what moved. A consultant judged the old wording —
# "byte-identical to the referee-validated record" / "byte-identical to the approved field
# update" — self-certification noise, so the sentence now names the sizes (previous, when
# Graph reports one) and, on the record/register lanes, the fields that changed.
def _size_clause(prev_size: int | None, size: int) -> str:
    """'12,880 → 12,904 bytes' when a previous size is known, else just '12,904 bytes'."""
    tail = f"{size:,} bytes"
    return f"{prev_size:,} → {tail}" if prev_size is not None else tail


def _amendment_clause(amendment: dict | None) -> str:
    """'; <field> on "<name>" changed from "<old>" to "<new>"' per changed field, joined by
    '; ' — empty string when there is no amendment. Reuses update_bia_activity's amendment
    shape ({"activity": <name>, "fields": {field: {"from", "to"}}}) for both the record and
    the register lane, so there is exactly one amendment shape in the codebase."""
    if not amendment or not amendment.get("fields"):
        return ""
    who = amendment.get("activity")
    parts = "; ".join(f'{field} on "{who}" changed from "{vals["from"]}" to "{vals["to"]}"'
                      for field, vals in amendment["fields"].items())
    return f"; {parts}"


def write_file(company: str, path: str, content: str = "",
               user_confirmed: bool = False, mode: str = "create",
               expect: dict | None = None, save_token: str | None = None,
               amendment: dict | None = None) -> dict:
    # `amendment` is internal (update_bia_activity only, never exposed on the MCP tool):
    # a correction's audit entry, folded into the result so the regen hook banks it.
    if user_confirmed is not True:
        return _err("write refused: user approval missing. Ask the user to approve this "
                    "exact write in chat first, then retry with user_confirmed=true.")
    jailed = _jail(company, path)
    if jailed is None or jailed == company:
        return _err("invalid path — use a relative path inside the company folder")
    rel = jailed[len(company) + 1:]
    if not (rel.startswith("output/") or rel in WRITE_EXCEPTIONS):
        return _err("write refused: writes are limited to the output/ area "
                    f"(or exactly {', '.join(WRITE_EXCEPTIONS)})")
    company_key = (company or "").strip().lower()
    # ponytail: writes are bound to the allowlist only — _jail() above already refuses any
    # company outside BIA_WORKFLOW_COMPANIES, so both rooms accept writes (2026-08-03, KG).
    # This drops the P-16 first-company-only narrowing (I-12, lesson #35): one shared service
    # and one shared bearer token cannot distinguish the Teams agent from the public web
    # agent, so "which room this agent works" is held by Part D instructions, NOT by this
    # server. Since 2026-08-10 that is moot: one room, both agents. Ceiling: a visitor who
    # steers the public agent can write marschkamp's output/ area and the register (reset
    # with rearm_register.py). Upgrade path if that bites —
    # give each agent its own endpoint and token so the server can tell callers apart, then
    # bind writes to the caller instead of the allowlist.
    stored: bytes | None = None
    if save_token is not None:
        guard, stored = _save_token_error(company_key, rel, save_token, content)
        if guard:
            return _err(guard)
        content = stored.decode("utf-8")  # write by reference: server-owned bytes
    if not content:
        # content is optional for the token lane ONLY — an omitted-content overwrite
        # must never truncate a ledger/artifact to zero bytes.
        return _err("write refused: content is empty — pass the complete file content "
                    "(the validated BIA record saves via save_token instead).")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE:
        return _err(f"content too large ({len(data)} bytes > {MAX_WRITE})")
    if rel.startswith("output/") and expect is None and save_token is None:
        return _err("write refused: output/ document writes must declare "
                    "expect={'markers': [...], 'min_bytes': N} taken from the approved preview "
                    "— the server verifies the saved file against it. Declare the exact section "
                    "strings the approved preview contains and its minimum size, then retry.")
    # Size, unread sources and the stage contract are independent facts about the same bytes:
    # they are collected and reported together (see _one_refusal). Path-shape problems below
    # stay immediate returns — a wrong path makes every later complaint meaningless, since the
    # contract that would raise them is looked up BY that path, and the two have never
    # co-occurred in the call log.
    expect_problem = _expect_error(expect, content, len(data)) if expect is not None else None
    problems = []
    if rel.startswith("output/") and stored is None:
        if "<" in rel or ">" in rel:
            return _err("write refused: replace <bia> in the path with this BIA's folder slug "
                        "(lowercase-hyphen process name, e.g. output/slaughter/) and retry.")
        if rel.count("/") == 1 and rel.rsplit("/", 1)[-1].casefold() in _legacy_singletons():
            name = rel.rsplit("/", 1)[-1]
            return _err(f"write refused: {name} is a per-BIA document and lives in this BIA's own "
                        f"folder — save it as output/<bia>/{name} (<bia> = the process slug named in "
                        "the Stage 1 card). Runs add to output/, they never overwrite another BIA "
                        "(owner ruling 2026-08-18).")
        # D-19, run (c) 2026-08-19 08:01:08Z: both folder jaws hung off `_contract_for`, so a
        # filename no stage declares slipped past them entirely and landed in whatever folder the
        # jail allowed. The folder rule belongs to the path, not to the contract that happens to
        # match it: a shared folder holds only what it is declared for, and a BIA folder is a slug.
        if rel.count("/") >= 2:
            folder = rel.split("/")[1]
            if folder.casefold() in _shared_folders():
                if not _contract_for(rel):
                    return _err(
                        f"write refused: output/{folder}/ is shared by every BIA and holds only "
                        f"the documents it is declared for — save this one as "
                        f"output/<bia>/{rel.rsplit('/', 1)[-1]} (<bia> = the process slug named in "
                        f"the Stage 1 card).")
            elif not BIA_SLUG.fullmatch(folder):
                return _err(
                    f"write refused: the BIA folder must be a lowercase-hyphen slug of the process "
                    f"(e.g. output/slaughter/, output/cutting-deboning/) — got 'output/{folder}/'; "
                    f"use output/{slugify(folder) or '<bia>'}/ and retry.")
        # Token-lane bytes are referee-validated — byte-identity is strictly stronger
        # than the stage contract, so the contract must not dead-end a passed record.
        contract = _contract_for(rel)
        if contract:
            if contract["_re"]:
                guard = _bia_folder_error(rel, contract)
                if guard:
                    return _err(guard)
            contract_problem = _stage_contract_error(contract, content, len(data))
            if contract_problem:
                # The same yardstick, said twice: an agent that behaves takes min_bytes and the
                # markers from the stage card, so its own `expect` and this contract complain
                # about the same bytes in almost the same words. The contract's line supersedes
                # it — that one names the stage and the canonical path. A stricter `expect` the
                # contract is happy with still stands on its own below.
                expect_problem = None
            problems += [_unread_source_error(company, contract), contract_problem]
    problems = [p for p in [expect_problem, *problems] if p]
    if problems:
        return _err(_one_refusal(problems))
    if rel.casefold().rsplit("/", 1)[-1] == "pp4-handoff.md":
        # Lesson #26: enumeration lives in the write jaw — the yaml wording alone
        # failed 6 consecutive runs.
        import bia_referee  # lazy: bia_referee -> this module
        reg = read_file(company, REGISTER_PATH)
        if "error" in reg:
            return _err("write refused: cannot verify the pp4_issue enumeration — the "
                        "dependency register is unreadable (" + reg["error"] + ")")
        try:
            register = json.loads(reg["content"])
        except ValueError:
            register = None
        if not isinstance(register, dict):
            return _err("write refused: cannot verify the pp4_issue enumeration — the "
                        "dependency register is not a valid JSON object")
        missing = bia_referee.pp4_missing(register, content)
        if missing:
            return _err("write refused: the handoff drops register pp4_issue items: "
                        + ", ".join(sorted(missing)) + " — enumerate every register "
                        "pp4_issue item so none is dropped, then retry.")
    if rel == REGISTER_PATH:
        guard = _register_payload_error(data)
        if guard:
            return _err(guard)
    meta = _get(f"{GRAPH}/drives/{_drive()}/root:/{jailed}")
    exists = meta.status_code == 200
    # The receipt says what moved, so an overwrite's previous size — when the DriveItem
    # metadata carries one — feeds the sentence below. A double may answer with no body or
    # no `size` key (every fixture in test_graph_files.py does that), so this must never
    # raise: guarded to None rather than trusted.
    prev_size = None
    if exists:
        try:
            meta_json = meta.json()
        except ValueError:
            meta_json = None
        raw_size = meta_json.get("size") if isinstance(meta_json, dict) else None
        if isinstance(raw_size, int) and not isinstance(raw_size, bool):
            prev_size = raw_size
    # ponytail: mode is a fact the GET above just established, so asking the agent to declare
    # it too was a symmetric trap — "cannot overwrite: does not exist" one way, "file already
    # exists: use mode='overwrite'" the other. Three live refusals, both directions, and not one
    # of them stopped a mistake: the path and the approved bytes were right every time and only
    # the word was wrong. 2026-08-20 09:37:58Z ended its run on it. What guards a clobber now is
    # the receipt, not a refusal — the size delta and the version-history clause reach the
    # manager, where the refusal only ever reached them as one more thing to approve. The
    # parameter stays accepted and ignored: two testers are live and dropping it from the tool
    # schema would fail their next call at the transport.
    mode = "overwrite" if exists else "create"
    with _client() as http:
        r = http.put(
            f"{GRAPH}/drives/{_drive()}/root:/{jailed}:/content",
            headers={"Authorization": f"Bearer {_token()}",
                     "Content-Type": "text/plain; charset=utf-8"},
            content=data,
        )
    r.raise_for_status()
    # The PUT response IS the DriveItem: keep its webUrl so a save can answer "where is it, can I
    # open it?" with a link instead of a path. Run (a) 2026-08-18: Hans asked four times and got
    # "go and look in the folder yourself"; the same question is all over the W33 digest.
    try:
        web_url = (r.json() or {}).get("webUrl")
    except ValueError:
        web_url = None
    # The register lane carries neither expect nor save_token, so it used to take the
    # early return below: no read-back, and evidence.json banking
    # human_line as null on every register write while the record lane
    # banked real strings (§RUN finding 3, measured 2026-07-30). It is a gated write and
    # is verified like one; the remaining exception (approval-log.jsonl) still returns early.
    register_lane = rel == REGISTER_PATH and expect is None and stored is None
    if expect is None and stored is None and not register_lane:
        result = {"written": True, "path": jailed, "size": len(data), "mode": mode}
        if web_url:
            result["url"] = web_url
        _graph_regen(company_key, rel, data, result)
        return result
    back = read_file(company, rel)
    back_ok = "error" not in back
    if back_ok and register_lane:
        back_ok = back["content"].encode("utf-8") == data
    if back_ok and stored is not None:
        back_ok = back["content"].encode("utf-8") == stored
    if back_ok and expect is not None:
        back_ok = (len(back["content"].encode("utf-8")) >= expect["min_bytes"]
                   and all(m in back["content"] for m in expect["markers"]))
    if not back_ok:
        return _err("written but read-back verification failed — the saved file does not "
                    "match the approved preview. Retry with the complete approved content.")
    # Run (a) 2026-08-18, Hans §5: the receipt names the file (basename — the path stays out) so
    # two saves in a row never read as one repeated line, and an overwrite says where the old
    # version went instead of leaving the agent to hedge about it.
    name = rel.rsplit("/", 1)[-1]
    kept = (" The previous version stays in SharePoint's version history."
            if mode == "overwrite" else "")
    # Run (b) 2026-08-18, Hans §5: "'Saved and checked: … matches what you approved (all 4 required
    # sections present).' → 'saved, 3,955 bytes, 4 sections.' Marking your own homework is not a
    # receipt; the byte count is." It reverses d1a0128's phrasing from the same judge — the file
    # name stays (that fix held: "good that you named the file without me asking"), the
    # self-certification goes, and the number the user can check against the folder listing
    # replaces it. The server still verifies exactly as before; it just stops applauding itself.
    # Task 7 (2026-08-19): that fix reversed one self-certification phrase; this one drops
    # the rest of it ("byte-identical to the referee-validated record" / "... to the
    # approved field update") in favour of what moved — the size delta, and on the
    # record/register lanes, the field(s) an amendment names as changed.
    if stored is not None:
        # Consumed only here, on verified success — a failed write keeps the token usable.
        _consume_save_token(company_key, save_token)
        verification = {"checked": True, "byte_identical": True,
                        "human_line": (f"✓ Saved: {name} — {_size_clause(prev_size, len(data))}"
                                       f"{_amendment_clause(amendment)}.{kept}")}
    elif register_lane:
        verification = {"checked": True, "byte_identical": True,
                        "human_line": (f"✓ Saved: the register on file — "
                                       f"{_size_clause(prev_size, len(data))}"
                                       f"{_amendment_clause(amendment)}.{kept}")}
    else:
        n = len(expect["markers"])
        verification = {"checked": True, "markers_present": n, "min_bytes": expect["min_bytes"],
                        "human_line": (f"✓ Saved: {name} — {_size_clause(prev_size, len(data))}, "
                                       f"{n} sections.{kept}")}
    result = {"written": True, "path": jailed, "size": len(data), "mode": mode,
              "verification": verification}
    if web_url:
        result["url"] = web_url
    # 2026-08-16 smart next steps: a verified save on a canonical stage path names the gate
    # and the literal advance call; anything else just carries on with the current stage.
    stage = _contract_for(rel)
    if stage and stage.get("stage_name"):
        bia_arg = (f", bia='{stage['_re'].match(rel.casefold()).group('bia')}'"
                   if stage["_re"] else "")
        result["next_move"] = (f"Ask for {stage['stage_name']} approval, then call "
                               f"next_step('run-bia', '{stage['stage_id']}'{bia_arg})")
    else:
        result["next_move"] = "Tell the user the file is saved and continue the current stage."
    if amendment is not None and stored is not None:
        result["amendment"] = amendment
    _graph_regen(company_key, rel, data, result)
    return result


def _expect_error(expect, content: str, size: int) -> str | None:
    """Pre-write §12 expect check. The stub of P5 D1 fails it two ways (bytes and markers)."""
    if not isinstance(expect, dict):
        return "expect must be an object: {'markers': [exact strings], 'min_bytes': N}"
    markers = expect.get("markers")
    min_bytes = expect.get("min_bytes")
    if (not isinstance(markers, list) or not markers or len(markers) > 32
            or not all(isinstance(m, str) and m.strip() for m in markers)):
        return ("invalid expect: markers must be 1-32 non-empty exact strings taken from the "
                "approved preview")
    if not isinstance(min_bytes, int) or isinstance(min_bytes, bool) \
            or not 1 <= min_bytes <= MAX_WRITE:
        return f"invalid expect: min_bytes must be an integer between 1 and {MAX_WRITE}"
    problems = []
    if size < min_bytes:
        problems.append(f"content is {size} bytes but the approved preview requires at least "
                        f"{min_bytes}")
    missing = [m for m in markers if m not in content]
    if missing:
        shown = ", ".join(repr(m) for m in missing[:3])
        more = f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""
        problems.append(f"missing required sections: {shown}{more}")
    if problems:
        return ("write refused: " + " and ".join(problems) + " — this looks like a summary, "
                "not the approved document. Send the COMPLETE approved content exactly as "
                "previewed.")
    return None


def _register_payload_error(data: bytes) -> str | None:
    """Content sanity for full-register writes. Returns an error string or None."""
    try:
        reg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ("register write refused: content is not valid JSON. For field changes use "
                "update_register_entry(asset_id, changes) — never rewrite the register file "
                "from chat output.")
    entries = [k for k, v in reg.items() if isinstance(v, dict)] if isinstance(reg, dict) else []
    if len(entries) < REGISTER_MIN_ENTRIES or len(data) < REGISTER_MIN_BYTES:
        return (f"register write refused: payload has {len(entries)} entries / {len(data)} "
                f"bytes, a full register has >= {REGISTER_MIN_ENTRIES} entries and >= "
                f"{REGISTER_MIN_BYTES} bytes. For field changes use "
                "update_register_entry(asset_id, changes) — never rewrite the register file "
                "from chat output.")
    return None


def update_register_entry(company: str, asset_id: str, changes: dict | str,
                          user_confirmed: bool = False) -> dict:
    """Field-level patch of ONE register entry — server-side read-modify-write.

    P4 lessons #16: a chat model round-tripping the full 31KB register through its own
    output corrupts it; here it sends only {field: new_value} and the server rebuilds the
    file, so every other record stays byte-faithful."""
    if user_confirmed is not True:
        return _err("register update refused: user approval missing. Ask the user to approve "
                    "the exact field changes in chat first, then retry with user_confirmed=true.")
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            return _err("changes must be a JSON object of {field: new_value}")
    if not isinstance(changes, dict) or not changes:
        return _err("changes must be a non-empty object of {field: new_value}")
    if "asset_id" in changes:
        return _err("asset_id cannot be changed — it identifies the entry")
    cur = read_file(company, REGISTER_PATH)
    if "error" in cur:
        return cur
    try:
        reg = json.loads(cur["content"])
    except ValueError:
        return _err("register is not valid JSON — restore it (version history or snapshot) "
                    "before any further update")
    entry = reg.get(asset_id)
    if not isinstance(entry, dict):
        known = ", ".join(sorted(k for k, v in reg.items() if isinstance(v, dict)))
        return _err(f"unknown asset_id '{asset_id}' — known entries: {known}")
    changes, nothing_to_do = _drop_no_ops(entry, changes)
    if nothing_to_do:
        return _err("register update refused: " + nothing_to_do)
    previous = {f: entry.get(f) for f in changes}
    entry.update(changes)
    # Task 7 (2026-08-19): same amendment shape as update_bia_activity's (below), minus
    # approved_by/reason — this tool collects no approval metadata to put there — so the
    # register receipt can say which field moved and what it moved from/to.
    amendment = {"activity": entry.get("name") or asset_id,
                 "fields": {f: {"from": previous[f], "to": changes[f]} for f in changes}}
    new_content = json.dumps(reg, ensure_ascii=False, indent=2) + "\n"
    res = write_file(company, REGISTER_PATH, new_content, user_confirmed=True, mode="overwrite",
                     amendment=amendment)
    if "error" in res:
        return res
    back = read_file(company, REGISTER_PATH)
    if "error" in back:
        return _err("update written but read-back failed: " + back["error"])
    try:
        got = json.loads(back["content"]).get(asset_id, {})
    except ValueError:
        return _err("update written but read-back is not valid JSON — verify the register")
    bad = [k for k, v in changes.items() if got.get(k) != v]
    if bad:
        return _err(f"read-back mismatch on fields: {', '.join(bad)} — verify the register")
    return {"updated": True, "path": res["path"], "asset_id": asset_id,
            "changed_fields": sorted(changes), "entry": got}


# ── update_bia_activity (2026-07-30 contract bundle, tool #14) ───────────────────────
# Post-save corrections to the machine record are administrative metadata ONLY — the
# analytical fields (grid, MTPD, RPO, targets, evidence) are analysis, and correcting
# them means re-opening the BIA stage. `name` stays out: it is the graph node id and
# the de-facto join key (78af195).
BIA_ACTIVITY_ADMIN_FIELDS = ("owner", "owner_role", "contact")


def update_bia_activity(company: str, activity: str, changes: dict | str,
                        approved_by: str = "", reason: str = "",
                        user_confirmed: bool = False) -> dict:
    """Field-level correction of ONE activity's administrative metadata in the saved
    BIA record: read → patch allowlisted fields → referee revalidation (fresh
    save_token) → token-lane save, which read-back-verifies, runs the advisory second
    opinion, banks evidence and regenerates the graph page itself. The approval
    (approved_by + reason) and the original values ride the write as an amendments
    audit entry; banking sits behind the regen hook's never-blocks boundary, so on a
    regen failure the audit line survives in the tool result and the service journal."""
    if user_confirmed is not True:
        return _err("record update refused: user approval missing. Ask the user to "
                    "approve the exact field changes in chat first, then retry with "
                    "user_confirmed=true.")
    approved_by = str(approved_by or "").strip()
    reason = str(reason or "").strip()
    if not approved_by or not reason:
        return _err("record update refused: approved_by and reason are required — record "
                    "WHO gave the named sign-off in chat and WHY the correction is made "
                    "(one line, e.g. 'owner changed after handover').")
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            return _err("changes must be a JSON object of {field: new_value}")
    if not isinstance(changes, dict) or not changes:
        return _err("changes must be a non-empty object of {field: new_value}")
    blocked = sorted(set(changes) - set(BIA_ACTIVITY_ADMIN_FIELDS))
    if blocked:
        return _err("record update refused: " + ", ".join(blocked) + " is not "
                    "administrative metadata — this tool changes only "
                    + ", ".join(BIA_ACTIVITY_ADMIN_FIELDS) + ". MTPD, impact scores, "
                    "evidence, recovery targets and the activity name are analysis: "
                    "correcting them means re-opening the BIA stage (re-run the "
                    "journey), never editing the saved record.")
    bad = sorted(f for f, v in changes.items() if not isinstance(v, str) or not v.strip())
    if bad:
        return _err("record update refused: value for " + ", ".join(bad) + " must be "
                    "non-empty text (the corrected name/role/contact).")
    cur = read_file(company, RECORD_SAVE_PATH)
    if "error" in cur:
        return cur
    try:
        record = json.loads(cur["content"])
    except ValueError:
        return _err("the saved BIA record is not valid JSON — restore it (version "
                    "history) before any correction")
    acts = record.get("activities") if isinstance(record, dict) else None
    if not isinstance(acts, list) or not acts:
        return _err("the saved BIA record has no activities list — nothing to correct")
    wanted = str(activity or "").strip()
    entry = next((a for a in acts if isinstance(a, dict)
                  and wanted and wanted in (a.get("id"), a.get("name"))), None)
    if entry is None:
        known = ", ".join(sorted(str(a.get("id") or a.get("name") or "?")
                                 for a in acts if isinstance(a, dict)))
        return _err(f"unknown activity '{activity}' — known activities: {known}")
    changes, nothing_to_do = _drop_no_ops(entry, changes)
    if nothing_to_do:
        return _err("record update refused: " + nothing_to_do)
    previous = {f: entry.get(f) for f in changes}
    entry.update(changes)
    # The allowlist deliberately cannot touch analytical prose, so a corrected owner can
    # leave `status`/`rpo_status`/`open_decisions` still naming the previous one (live
    # 2026-08-03). Nothing here can fix that — only re-running the stage can — but the
    # correction must at least name the fields that now contradict it.
    stale_mentions = {}
    for f, old in previous.items():
        if not isinstance(old, str) or not old.strip():
            continue
        hits = sorted(k for k, v in entry.items()
                      if k not in changes and _mentions(v, old))
        if hits:
            stale_mentions[f] = hits
    import bia_referee  # lazy: bia_referee imports this module
    verdict = bia_referee.validate_bia_record(company, record)
    if "error" in verdict:
        return verdict
    if not verdict.get("pass"):
        return _err("record update refused: the corrected record no longer passes the "
                    "referee — nothing was saved. Rejections: "
                    + "; ".join(verdict.get("rejections", ["FAIL"])))
    amendment = {"activity": entry.get("name") or entry.get("id"),
                 "fields": {f: {"from": previous[f], "to": changes[f]} for f in changes},
                 "approved_by": approved_by, "reason": reason}
    res = write_file(company, RECORD_SAVE_PATH, user_confirmed=True, mode="overwrite",
                     save_token=verdict["save_token"], amendment=amendment)
    if "error" in res:
        return res
    return {"updated": True, "path": res["path"], "activity": amendment["activity"],
            "changed_fields": sorted(changes), "previous": previous,
            "amendment": amendment, "stale_mentions": stale_mentions,
            "verification": res.get("verification")}


if __name__ == "__main__":  # live smoke: python3 graph_files.py marschkamp
    import json as _json
    import sys as _sys
    comp = _sys.argv[1] if len(_sys.argv) > 1 else "marschkamp"
    print(_json.dumps(list_files(comp), indent=2)[:800])
    print(_json.dumps(read_file(comp, "company-profile.md"), indent=2)[:400])
