# BIA-Workflow

An MCP server that runs a **Business Impact Analysis** as a guided journey: the assistant
prepares every step, a human decides, approves and owns the result.

It is built around one constraint. A BIA is a record somebody signs, so the assistant is never
allowed to be the one who decides. It drafts the interview guide, restates what a department
said, notices that two departments describe the same dependency differently — and then stops at
an approval gate. Every write is human-gated, path-jailed, and verified by read-back.

## The method/data split

```
Copilot Studio agent ──┐
any MCP client ────────┴─ MCP server (method, 0% company data)
                                    │
                                    └─ jailed Graph file tools ─ SharePoint
                                       (100% company data, human-gated writes)
```

The server carries the **method**: the guided journeys, the stage contracts, and a deterministic
referee that checks a finished BIA record against the template rather than asking a model whether
it looks right. Company data stays in the customer's own Microsoft 365 and is reached at call
time. The server never holds it.

`server.py` publishes 15 tools — the `@mcp.tool` blocks there are the authoritative list.
`design/run-bia.yaml` is the BIA journey: six stages, each with a goal, a prompt, an approval
gate and a reviewer checklist. `design/draft-plan.yaml` is a four-stage plan-drafting journey.

## Quickstart

```bash
git clone --recurse-submodules https://github.com/KoGerner/bia-workflow.git
cd bia-workflow
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
BIA_WORKFLOW_DATA_DIR=$PWD/tests/fixtures .venv/bin/python -m pytest -q
```

`--recurse-submodules` matters: the journeys live in `design/`, a submodule. Without it the
suite will not collect. `git submodule update --init` fixes an existing clone.

To run the server against the fixture corpus:

```bash
BIA_WORKFLOW_MCP_TOKEN=dev-token BIA_WORKFLOW_DATA_DIR=$PWD/tests/fixtures \
  .venv/bin/python server.py
```

Configuration is environment variables, all optional except the token: `BIA_WORKFLOW_ROOT`
(defaults to the checkout), `BIA_WORKFLOW_DATA_DIR` (defaults to `<checkout>/data`),
`BIA_WORKFLOW_JOURNEYS_DIR` (defaults to `<checkout>/design`).

## Three licences, on purpose

| What | Where | Licence |
|---|---|---|
| The code | this repository | **MIT** — see [LICENSE](LICENSE) |
| The method | [`design/`](https://github.com/KoGerner/workflow-design), a submodule | **CC BY 4.0** |
| The knowledge base | **not in this repository** — published at [ai4bcm.org](https://agent.ai4bcm.org) | not redistributable here |

The knowledge base is built from the BCI AI Addendum. That document is not this project's to
relicense, so it does not ship. In its place `data/` and `tests/fixtures/` hold a **synthetic
fixture corpus** — 22 invented chunks, every one marked `"source_file": "fixture"` — which covers
every section the journeys cite. That is what makes the suite runnable by someone who does not
have the real corpus, and it is why a search here returns fixture prose rather than BCM guidance.

Regenerate it with `python scripts/make_fixture_chunks.py --out tests/fixtures`.

## Layout

| Path | What |
|---|---|
| `server.py` | the MCP server: tool registration, bearer-token middleware, startup gate |
| `addendum_tools.py` | the tool implementations and the journey state machine |
| `journeys.py` | loads and validates the journeys in `design/` |
| `retrieval.py` | the in-memory retrieval index |
| `bia_referee.py` | deterministic checks on a finished BIA record |
| `graph_files.py`, `graph_admin.py` | the jailed Microsoft Graph file tools |
| `build_chunks.py`, `build_kb_pages.py`, `build_guide_page.py` | corpus and static page builders |
| `deploy/` | nginx configs and the public demo pages |
| `docs/decision-record.md` | why the architecture is shaped this way |
| `docs/ms-agent-install.md` | installing the workflow on a Copilot Studio agent |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: run the suite with the fixture corpus,
one reason per commit, and a test that fails before your change.

The workflow was shaped by practitioner review rather than by its author alone — the credits are
in CONTRIBUTING.md and they are not decoration.
