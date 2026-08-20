# Fixture corpus

`chunks.json` and `index.json` here are a **synthetic stand-in** for the knowledge base. Every
sentence in them was invented for this fixture. Nothing was copied from the AI Addendum, and
every chunk carries `"source_file": "fixture"` so a fixture chunk can never be mistaken for real
content.

## Why they exist

The knowledge base is built from the BCI AI Addendum, which is not this project's to relicense,
so it is not part of this repository. But `server.py` builds its retrieval index at **module
scope** and validates every journey's `cites:` against it at import time. With no corpus at all,
pytest does not fail a dozen tests — it aborts collection and the whole suite fails to run.

So the fixture is not a convenience. It is what makes the repository testable by someone who
does not have the knowledge base.

## Running the suite against them

```bash
BIA_WORKFLOW_DATA_DIR=$PWD/tests/fixtures python -m pytest -q
```

## Regenerating

Do not hand-edit `chunks.json`. It is generated:

```bash
python scripts/make_fixture_chunks.py --out tests/fixtures
```

The generator derives its required chunk ids from `design/*.yaml`, so a journey that starts
citing a new section makes the generator fail by name instead of turning into a mystery
collection error. When that happens, add the chunk to `CHUNKS` in the generator — with invented
text — and re-run.

The generator also places certain vocabulary deliberately, because tests read the consequence:
exercise and scenario wording lives only in a medium-risk chunk, because `identify_ai_risks`
takes the highest risk level among the top four search hits. The comments in the generator say
which test depends on what.
