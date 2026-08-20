"""reset_company_file.py — delete ONE company file, as the operator, so a measurement can re-run.

Why this is a script and not an MCP tool
----------------------------------------
H3 is open: nothing binds a session to a BIA folder. Watched live 2026-08-20 09:37:58, a run
chose `output/slaughter/` from its conversation and attempted a write there; the only thing that
stopped it was a filename collision. Today that hole is ADDITIVE — the worst case is a stray
file. An agent-callable delete would make the same hole destructive, and `write_company_file`
already refuses to truncate a document to zero bytes, so destruction is a thing this design
deliberately prevents. The capability therefore exists for a human and the agent never sees it.

Why no systemd unit
-------------------
`brain` has no sudo lane to `svc-bia` (checked 2026-08-20: it may start `brain-*.service`, reload
nginx, and run four `pa-*` helpers as `svc-pa`, nothing else). A unit would need a root round to
install. The operator already holds the sudo rights this needs, so running it directly costs no
new privilege at all:

    sudo -u svc-bia /opt/apps/venvs/bia-workflow/bin/python \
        /opt/apps/bia-workflow/reset_company_file.py marschkamp output/logistics/stage1-scope-and-guide.md

SharePoint keeps the version history and the recycle bin, so this is recoverable, not final.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import graph_files

# The service's own location. graph_files derives the Graph secret from BIA_WORKFLOW_TOKEN_FILE,
# which the systemd unit sets and a bare shell does not — run by hand this looked in the app root
# and died on `missing graph secret file: /opt/apps/bia-workflow/graph-secret`. An operator lane
# that needs four environment variables spelled correctly is not an operator lane, so the
# deployed path is the default and an explicit BIA_WORKFLOW_TOKEN_FILE still wins.
DEPLOYED_TOKEN_FILE = "/srv/addendum/secret"


def check(company: str, path: str) -> str:
    """Refuse anything outside the run folder, by shape, before any network call.

    Company source material — the method, the register, the org chart, the prior cycle — is what
    every BIA is built from and is not reproducible from this tree. A test artefact lives in
    `output/`. `_jail` resolves traversal, so `output/../01_Organisation/x.md` is caught here
    rather than by hoping the string looks safe.
    """
    jailed = graph_files._jail(company, path)
    if jailed is None or jailed == company:
        raise SystemExit(f"refused: {path!r} is not a path inside {company}")
    rel = jailed[len(company) + 1:] if jailed.startswith(company + "/") else jailed
    if not rel.startswith("output/"):
        raise SystemExit(f"refused: {rel!r} is not under output/ — this resets test artefacts, "
                         "never company source material")
    return rel


def secret_path() -> Path:
    """Where the Graph secret lives, with or without the service environment."""
    return Path(os.environ.get("BIA_WORKFLOW_TOKEN_FILE") or DEPLOYED_TOKEN_FILE).parent / "graph-secret"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return int(bool(sys.stderr.write(f"usage: {argv[0]} <company> <output/…/file.md>\n")) or 2)
    company, path = argv[1], argv[2]
    rel = check(company, path)
    # Set explicitly rather than by import order: graph_files computes SECRET_FILE at import
    # time, so an env tweak here would depend on which module imported first.
    graph_files.SECRET_FILE = secret_path()
    if not graph_files.SECRET_FILE.exists():
        raise SystemExit(f"no Graph secret at {graph_files.SECRET_FILE} — run this as svc-bia, "
                         "or set BIA_WORKFLOW_TOKEN_FILE to the service's token file")
    jailed = graph_files._jail(company, path)
    url = f"{graph_files.GRAPH}/drives/{graph_files._drive()}/root:/{jailed}"
    with graph_files._client() as c:
        r = c.delete(url, headers={"Authorization": f"Bearer {graph_files._token()}"})
    if r.status_code == 404:
        print(f"already absent: {jailed}")
        return 0
    r.raise_for_status()
    print(f"deleted: {jailed} (recoverable from the SharePoint recycle bin and version history)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
