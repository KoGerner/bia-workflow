# Security policy

## Reporting a vulnerability

Email **konstantin@kgerner.at**. Please do not open a public issue for a security report.

Say what you found, how to reproduce it, and what you think the impact is. You will get an
acknowledgement; if the report is valid you will be told when a fix ships, and credited if you
want to be.

## Supported versions

The `main` branch is the only supported version. There are no maintained release branches.

## In scope

- The MCP server and its tools (`server.py`, `addendum_tools.py`, `graph_files.py`)
- The bearer-token middleware and the approval gates on every write
- The static page builders and anything they emit into `public/`

Two things worth knowing before you report them as findings:

**The demo endpoints in `deploy/` are intentionally public.** They serve a demonstration of the
workflow against a synthetic company. The data behind them is invented; the company, its
departments and its people do not exist.

**The knowledge base is deliberately absent.** `data/` here holds a synthetic fixture corpus, not
the real one. A report that the shipped corpus is small or generic is expected behaviour, not a
vulnerability.

## Out of scope

- Findings that require an account or credential you were not given
- Denial of service through volume alone
- Missing hardening headers on the demo pages, absent a demonstrated exploit
