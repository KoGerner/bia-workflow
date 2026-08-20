"""Operator-only SharePoint CRUD — move/delete/create_folder/create_file, unjailed.

NOT an MCP tool. NOT imported by server.py. This module exists so a human directing Claude
Code (or any operator script) can maintain company-data content in SharePoint on request —
move/rename/delete/reorganize — the same maintenance work SharePoint's own UI does, without
opening a browser. It reuses graph_files' credentials, token cache, and path-jail/traversal
guard (same company allowlist, same "../" refusal) — but drops graph_files.write_file's
output/-only restriction and the user_confirmed gate, because the trust model here is
different: a human is directing every call in real time, not an autonomous agent a third
party could manipulate into writing where it shouldn't.

The security boundary is structural, not credential-based: server.py — the only thing
https://agent.ai4bcm.org/mcp exposes to Copilot Studio's BIA-Workflow agent — never
imports this module (guard-tested: test_graph_admin.py::test_admin_never_imported_by_server).
Deleted items land in SharePoint's recycle bin (Graph DELETE is a soft delete) — recoverable,
consistent with this project's existing "version history = undo" safety model.
"""
from __future__ import annotations

import graph_files as gf

GRAPH = gf.GRAPH


def move_file(company: str, src_path: str, dst_path: str) -> dict:
    """Move and/or rename in one call. dst_path is the full new relative path."""
    src = gf._jail(company, src_path)
    dst = gf._jail(company, dst_path)
    if src is None or dst is None or src == company or dst == company:
        return gf._err("invalid path — use a relative path inside the company folder")
    src_folder, _, _ = src.rpartition("/")
    dst_folder, _, dst_name = dst.rpartition("/")
    body = {"name": dst_name}
    if dst_folder != src_folder:
        body["parentReference"] = {"path": f"/drive/root:/{dst_folder}"}
    with gf._client() as http:
        r = http.patch(
            f"{GRAPH}/drives/{gf._drive()}/root:/{src}",
            headers={"Authorization": f"Bearer {gf._token()}", "Content-Type": "application/json"},
            json=body,
        )
    if r.status_code == 404:
        return gf._err(f"source not found: {src}")
    r.raise_for_status()
    return {"moved": True, "from": src, "to": dst}


def delete_file(company: str, path: str) -> dict:
    """Soft-delete (SharePoint recycle bin — recoverable)."""
    jailed = gf._jail(company, path)
    if jailed is None or jailed == company:
        return gf._err("invalid path — use a relative path inside the company folder")
    with gf._client() as http:
        r = http.delete(
            f"{GRAPH}/drives/{gf._drive()}/root:/{jailed}",
            headers={"Authorization": f"Bearer {gf._token()}"},
        )
    if r.status_code == 404:
        return gf._err(f"not found: {jailed}")
    r.raise_for_status()
    return {"deleted": True, "path": jailed}


def create_folder(company: str, path: str) -> dict:
    jailed = gf._jail(company, path)
    if jailed is None or jailed == company:
        return gf._err("invalid path — use a relative path inside the company folder")
    parent, _, name = jailed.rpartition("/")
    parent_url = (f"{GRAPH}/drives/{gf._drive()}/root:/{parent}:/children" if parent
                 else f"{GRAPH}/drives/{gf._drive()}/root/children")
    with gf._client() as http:
        r = http.post(
            parent_url,
            headers={"Authorization": f"Bearer {gf._token()}", "Content-Type": "application/json"},
            json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
        )
    if r.status_code == 409:
        return gf._err(f"already exists: {jailed}")
    r.raise_for_status()
    return {"created": True, "path": jailed}


def create_file(company: str, path: str, content: str, overwrite: bool = False) -> dict:
    """Create a text file anywhere in the company folder — operator-only, NOT jailed to output/.

    Completes the operator CRUD verb set. graph_files.write_file is jailed to output/ + gated;
    this is not, because a human directs every call. Refuses an existing path unless
    overwrite=True (SharePoint version history keeps the prior copy). Used e.g. to plant the
    red-team poison transcript into 07_Interviews/ and to stage human-reviewed new evidence.
    """
    jailed = gf._jail(company, path)
    if jailed is None or jailed == company:
        return gf._err("invalid path — use a relative path inside the company folder")
    data = content.encode("utf-8")
    if len(data) > gf.MAX_WRITE:
        return gf._err(f"content too large ({len(data)} bytes > {gf.MAX_WRITE})")
    if not overwrite and gf._get(f"{GRAPH}/drives/{gf._drive()}/root:/{jailed}").status_code == 200:
        return gf._err(f"already exists: {jailed} — pass overwrite=True to replace "
                       "(version history keeps the old copy)")
    with gf._client() as http:
        r = http.put(
            f"{GRAPH}/drives/{gf._drive()}/root:/{jailed}:/content",
            headers={"Authorization": f"Bearer {gf._token()}",
                     "Content-Type": "text/plain; charset=utf-8"},
            content=data,
        )
    r.raise_for_status()
    return {"created": True, "path": jailed, "size": len(data)}


if __name__ == "__main__":  # manual smoke: python3 graph_admin.py <company>
    import json as _json
    import sys as _sys
    comp = _sys.argv[1] if len(_sys.argv) > 1 else "marschkamp"
    print("graph_admin loaded for", comp, "— call move_file/delete_file/create_folder/create_file directly.")
