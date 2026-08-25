# BIA-Workflow — decision record (why the architecture is shaped this way)

The durable **why** behind the product. The **how** is [`ms-agent-install.md`](ms-agent-install.md);
**validating** it was the red-team + acceptance runbook (retired 2026-08-18, git history);
live **status** is the vault board `02-Projects/BCI/bia-workflow/bia-teams-progress.md`. Migrated
into the repo 2026-07-20 from the vault research note; the full 2026-07-19 research (decision
matrix, Langdock/n8n/Slack comparison, MS fact-check, practitioner evidence) is archived at
`99-Archive/04-Resources/enterprise-agents-teams-research{,-v1}.md`.

## The decision

One **Copilot Studio agent** driven by a **method/data split**:

| Piece | Runs where | Role | Trust posture |
|---|---|---|---|
| BIA-Workflow MCP (`https://agent.ai4bcm.org/mcp`, bearer) | brain | **100% method** — BCM knowledge, prompts, risk checks, the `run-bia` journey, and the `validate_bia_record` referee — **0% company data** | read-only, bearer-gated, watchdog-probed |
| SharePoint (kgerner.at tenant) | M365 | **100% company data** — the marschkamp folder; the agent READS artifacts and WRITES its own outputs | Entra-secured; reachable ONLY via the jailed MCP file tools |
| Company-file MCP tools (`list`/`read`/`write_company_file`) | addendum server → Microsoft Graph (app `AIBCM-graph`, `Sites.Selected` = the one AIBCM site) | one wiring, tested Python, no clicked glue | company allowlist + path jail; writes only to `output/` + register/ledger; `user_confirmed` server gate; SharePoint version history = undo |

Why this shape: the VPS never grows a write endpoint (the write surface lives inside M365 behind
Entra); the demo shows real files created in a real enterprise data plane; and the method is
company-agnostic — the journey references artifacts **by role only**, never by path or company, so
the same method runs for any company folder. The old demo engine (`/opt/aibcm-demo`,
`bia_runner.py`) is **retired** — its deterministic referee was copied into `bia_referee.py` (tool
#11 `validate_bia_record`), so the Teams lane produces gated records, not prose.

**Two write surfaces, one boundary.** The agent writes only through the jailed MCP
`write_company_file` (`output/` + register/ledger, `user_confirmed`). All other company-data
maintenance — create/move/delete/reorganise — is a human-directed operator module `graph_admin.py`
(unjailed) that is **never an MCP tool** (`server.py` never imports it, guard-tested), so it is
structurally unreachable from the agent's endpoint.

## The company-data contract (artifacts by role)

Single company (**marschkamp**; the swap-pack lane was dropped 2026-07-20 — the one-wire
architecture + guard-tested generic journey already prove method/data separation). The pack lives
in SharePoint under 8 role folders: `01_Organisation, 02_BCM-Method, 03_Dependencies, 04_Suppliers,
05_Regulatory, 06_Risk-and-Incidents, 07_Interviews, 08_Prior-Cycle` (+ `approval-log.jsonl`,
`README.md`, `output/` at root). Roles the journey reads/writes: company-profile, impact-criteria,
dependency-register (`03_Dependencies/dependency-register.json`, read + gated write), prior-bia,
supplier-sla, regulatory-obligations, method calibration (`method.json`), approval ledger (gated
append), interview transcripts (`07_Interviews/*.md`, read), and the BIA output plus the structured
`output/bia-record.json` the referee checks (gated write). A missing artifact is legal — the agent
asks instead of inventing. Since 2026-08-24 this contract instantiates per demo room: a room is a
complete copy of the pack under `/srv/addendum/demo-rooms/<code>/`, the room code stands in for
the company name on every tool, and the same roles, gates and ledgers apply inside it (§ Room
code decides storage).

## Verified MS feature state / the licensing reality (checked 2026-07-19)

SharePoint create/update via the **Standard** connector (no premium Power Automate licence); a
remote MCP tool and SharePoint actions coexist on one agent; Business Basic carries the data plane.
**The one gate:** Copilot Studio is not in Business Basic and the free "Copilot Studio for Teams"
tier can't run MCP (no generative orchestration) — **PAYG is mandatory** (Azure sub → billing
policy; ≈$0.01/credit, a full run ≈$0.10–0.25, cap ~$20/mo). **S3 = FAIL:** publishing the agent
into Teams needs a per-user Copilot licence, so the **free Copilot test panel is the demo surface**
(screen-share); the Langdock lane (€25/mo) is the parked alternative for a real Teams face.

## Shipped vs roadmap

- **Shipped:** the method/data split is live; **13** MCP tools (7 method/journey + 4 file — incl.
  `update_register_entry`, the only sanctioned register change since 2026-07-22 — +
  `validate_bia_record` + `resource_dependencies`, a deterministic register-derived dependency
  answer); the genericised `run-bia` journey (guard-tested, 0% company data); the
  referee (the machine floor — a draft must pass it before the agent shows it); in-loop write
  verification on every `output/` save (`expect` markers/byte-floor contract 2026-07-23; phase-3
  **second opinion** 2026-07-25 — an independent litellm model re-reads the saved bytes,
  advisory-only, never the worker model, deterministic checks decide; **stage binding
  2026-07-26** — journey-owned `artifact_contracts` (renamed `document_contracts`
  2026-08-20, W11a) on canonical stage paths, the `next_step`
  advance gate, and the referee's one-time `save_token` write-by-reference for
  `output/bia-record.json`, closing the halfB referent-swap hole); a register-derived
  **dependency-graph page** per company (bidirectional focus, owner/criticality lenses,
  pan/zoom), shipped 2026-07-28 and overhauled to v2 2026-07-29; the **2026-07-30 contract
  bundle** — per-activity `dependencies` (exact register asset ids, referee-gated, elicited by
  the journey, drawn as real activity edges) and `update_bia_activity` (tool #14: post-save
  corrections limited to owner/owner_role/contact through the same referee + token lane, with
  approved_by/reason and a durable amendments audit in the graph evidence — analytical values
  re-open the stage instead); the product consolidated
  into the app repo (today `KoGerner/bia-workflow`) as the git-backed single source of truth.
- **Pilot proof COMPLETE (2026-07-20):** red-team 6/6 + the Stage 6→7 acceptance run — four
  referee-validated activity cards through owner gates, gated writes, PP4 stop machine-verified
  (verdicts on the vault board). An unassisted clean-run repeat (session-prompts P4) scores
  autonomy.
- **Acceptance ACCEPTED (2026-07-21):** the clean single-session 6-stage run on the deployed
  lean upgrade — all 4 formal gates held, LF-ABP-01 reality loop closed by a human-signed
  register write, requirements-only PP4 stop, offline grader `bia_verify.py` GREEN on the saved
  artifacts (scorecard on the vault board; golden full-journey fixture at SharePoint
  `marschkamp/09_Evaluation/golden-run-2026-07-21/` — moved out 2026-08-10 when the room went
  public, then restored the same evening on KG's instruction; one room, no separation —
  §Isolation). The ~19h/run manual verification is
  retired: referee live + grader offline; residual human load = structural supervision
  (dual-clock and pp4-enumeration eyeballs).
- **Roadmap (not demo-blocking):** Agent 365 / Entra Agent ID as the named-colleague landing zone;
  BYO-MCP tenant registration; Universal Actions stage-cards; the render bridge (SharePoint output →
  engine surfaces). Deliberately **not** built: orchestrator agents (run-bia is sequential +
  human-gated), an HR-builder agent, a memory DB, per-agent cost-routing — vanity at one seat.

## The graph page is a cache, and that is the decision (2026-08-04)

The dependency-graph page is **not** synchronised with the agent. The agent reads SharePoint
live on every call; the page is a snapshot written to VPS disk, rebuilt from scratch — both
sources re-read — only when a write passes through the MCP (`graph_files._graph_regen`, gated to
`REGISTER_PATH` and `RECORD_SAVE_PATH`). Rebuilding wholesale rather than patching is deliberate:
new activities and assets appear with no migration, no incremental-update bugs, and no
client-side fetch to go stale. That trade is **kept**.

What it costs, audited 2026-08-03/04 after a reported owner "discrepancy" that turned out to be
an activity node and an asset node legitimately holding different owners:

- **Edits made outside the MCP never reach the page.** SharePoint is an ordinary document
  library — web UI, Teams, sync client, Excel. The agent sees such a change on its next read;
  the page keeps the old snapshot indefinitely. **Accepted, no fix exists** — mitigation is the
  data-age headline below, which makes the staleness visible instead of invisible.
- **A failed regen is swallowed.** `_graph_regen` catches `Exception`, logs a warning, and the
  write still returns `written: true`, so the user reads "✓ Saved" over a stale page. **Accepted:**
  0 occurrences in 30 days of journal at audit time. If it ever bites, return a non-blocking
  `graph: stale` note in the write result rather than journalling into the void.
- **The amendment audit trail lives only on VPS disk** — `bank_and_regen` appends corrections to
  `public/graph/<company>/evidence.json`; SharePoint never receives them. **Accepted** because
  that path is git-tracked, so committing after corrections preserves it. The thorough fix
  (bank amendments into SharePoint) touches the save path and was judged not worth the risk.

Two related holes were **fixed** rather than accepted, both 2026-08-04: the headline used to
carry the render clock, so a page built from a three-day-old register still read "Updated
<today>" — it now dates each source (`_data_age`); and a record *read failure* used to fall
through to `record = None` and publish a page with every BIA activity missing while the write
reported success — only a genuine 404 now means "this room has no BIA" (`generate`).

## Room code decides storage (2026-08-24)

~50 external BCM managers (event cohort first, standing link after) must each run the BIA journey
against their **own** complete copy of the fictional room, and must **see and download** the files
their run creates — which SharePoint cannot give anonymous users. Per-`output/` isolation fails
because runs also mutate room-level files (dependency register, approval log). The owner picked
the data-plane swap under the `graph_files` seam; the ruling deliberately un-pauses the C4-shaped
work paused 2026-08-20.

**The mechanism: room code = company slug = storage folder = download URL. The channel never
matters** — all three channels (MS Teams Copilot agent, the Bruno buzz seat, the public web agent)
hit one endpoint with one bearer, and the server provably cannot tell them apart
(`ms-agent-install.md` §Isolation). A room is a directory under `/srv/addendum/demo-rooms/`;
admission is directory existence, never a `COMPANIES` entry — a second allowlist entry would flip
every tool's `company` default to `""` and re-open the measured 2026-08-20 "which company?"
regression. Routing lands at the four Graph I/O sites in `graph_files.py` only, so every gate,
receipt, regen hook and the referee run identically on both backends by construction; the room
lane banks the old bytes to `.versions/` before an overwrite, standing in for SharePoint's
version history. Codes are `<word>-<6 typeable chars>` (~30 bits, no i/l/o/0/1): the root listing
404s, dotpaths 404 inside the rooms location, fail2ban stands in front, and a guessed code yields
synthetic fiction. Minting is an operator script (`mint_demo_rooms.py`) — an agent-callable mint
is an abuse surface — and rooms are disposable: recovery is refresh-seed + re-mint. Accepted
openly: within-room H3 unchanged, no per-room write locking (Graph had none either), `.versions/`
growth unbounded, `bia-file` write/delete stay Graph-only on rooms.
