# Contributing

## The knowledge base is not in this repository

The workflow is driven by a knowledge base built from the BCI AI Addendum. That document is not
the project's to relicense, so it does not ship here. What ships instead is a **synthetic
fixture corpus** in `tests/fixtures/`, mirrored at `data/` so the file-relative default
resolves. Every fixture chunk carries `"source_file": "fixture"`.

This means the whole suite runs for you, and nothing you can read here is addendum text.

## Running the tests

```bash
git clone --recurse-submodules https://github.com/KoGerner/bia-workflow.git
cd bia-workflow
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
BIA_WORKFLOW_DATA_DIR=$PWD/tests/fixtures .venv/bin/python -m pytest -q
```

`BIA_WORKFLOW_DATA_DIR` is not optional: one test reads it directly to prove both static page
builders follow the same knob.

If you forget `--recurse-submodules`, the journeys in `design/` will be missing and the suite
will not collect. Fix it with `git submodule update --init`.

## Regenerating the fixture

`tests/fixtures/chunks.json` is generated, not hand-written:

```bash
.venv/bin/python scripts/make_fixture_chunks.py --out tests/fixtures
```

It derives the chunk ids it must produce from `design/*.yaml`, so a journey that cites a new
section fails by name instead of becoming a mystery collection error. If you add a chunk, invent
its text — nothing in the fixture may be copied from a real corpus.

## What a change should look like

- One reason per commit, and a message that says what changed for a reader rather than for the
  system.
- A test that fails before the change and passes after it.
- No new dependency without a line saying which module imports it.

## Credits

The workflow was shaped by practitioner review rather than by its author alone.

**Willem Hoekstra** — business continuity practitioner. Ran the workflow end to end and
produced the requirement set (W4, W7, W9, W11a/b, W12) that drove most of what the assistant now
does: the read gate before stage 1, the deadline rejection rule, the cross-department conflict
check, and the vocabulary the stages carry.

**BC Consulting** — independent review of the same run.

## Licensing of contributions

Contributions are offered under the project licence (MIT). The project may move to another
OSI-approved permissive licence in future; by contributing you agree your contribution may be
distributed under it.
