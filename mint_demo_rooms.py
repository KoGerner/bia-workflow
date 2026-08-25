"""mint_demo_rooms.py — operator lane for demo rooms: refresh-seed, mint, list.

Why a script and not an MCP tool: an agent-callable mint is an abuse surface — one
injected prompt away from a disk full of rooms. The operator already holds the shell
this needs (reset_company_file.py precedent, same zero-env wiring):

    sudo -u svc-bia /opt/apps/venvs/bia-workflow/bin/python \
        /opt/apps/bia-workflow/mint_demo_rooms.py refresh-seed
    ...                                           mint 50
    ...                                           list

`refresh-seed` builds demo-seed.new (from the archive snapshot with --from DIR, else a
live BFS pull of the source room through list_files/read_file), applies the hygiene
contract, then swaps. It never touches minted rooms. `mint N` copies the seed N times
under fresh codes and pre-generates each room's public graph page, so the handout's
graph link is live before the first write.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

import graph_files
import rearm_register
from reset_company_file import DEPLOYED_TOKEN_FILE

# Typeable beats maximal entropy (~30 bits): the root listing 404s, fail2ban exists, and a
# guessed code yields synthetic fiction. No i/l/o/0/1 — codes get read out loud at an event.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
WORDS = ("adler", "falke", "kranich", "milan", "reiher", "storch", "kiebitz", "sperber",
         "habicht", "bussard", "kauz", "specht", "star", "fink", "drossel", "amsel")
SYNTHETIC_APPROVAL = ('{"synthetic": true, "event": "seed", '
                      '"note": "demo room — everything above this line is reset fiction"}\n')


def _wire() -> tuple[Path, Path]:
    """Explicit assignment, not import order (reset_company_file precedent): the deployed
    location is the default, an explicit BIA_WORKFLOW_TOKEN_FILE still wins. Zero other env."""
    base = Path(os.environ.get("BIA_WORKFLOW_TOKEN_FILE") or DEPLOYED_TOKEN_FILE).parent
    graph_files.SECRET_FILE = base / "graph-secret"
    graph_files.ROOMS_DIR = base / "demo-rooms"
    return graph_files.ROOMS_DIR, base / "demo-seed"


def _rand_code() -> str:
    word = secrets.choice(WORDS)
    return f"{word}-{''.join(secrets.choice(ALPHABET) for _ in range(6))}"


def _new_code(rooms: Path) -> str:
    while True:
        code = _rand_code()
        if code not in graph_files.COMPANIES and not (rooms / code).exists():
            return code


def _hygiene(tree: Path) -> None:
    """The seed contract: no run output, no golden answers beside the poison fixture, no
    stale backup packs, a one-line synthetic approval log, and a re-armed register with
    the root fiction marker intact."""
    out = tree / "output"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    ev = tree / "09_Evaluation"
    if ev.is_dir():
        for p in ev.iterdir():
            if p.name != "rt1-poison.md":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in sorted(tree.rglob("pack-backup*"), reverse=True):
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    (tree / "approval-log.jsonl").write_text(SYNTHETIC_APPROVAL, encoding="utf-8")
    reg_path = tree / "03_Dependencies" / "dependency-register.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        reg.setdefault("LF-ABP-01", {}).update(rearm_register.ARMED)
        reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")


def _pull(tree: Path) -> None:
    """Live BFS through the same two tools the agent uses — no second Graph client."""
    source = graph_files.DEFAULT_COMPANY
    pending = [""]
    while pending:
        base = pending.pop()
        listing = graph_files.list_files(source, base)
        if "error" in listing:
            raise SystemExit(f"pull failed at '{base or '/'}': {listing['error']}")
        for it in listing["files"]:
            rel = f"{base}/{it['name']}" if base else it["name"]
            if it["is_folder"]:
                (tree / rel).mkdir(parents=True, exist_ok=True)
                pending.append(rel)
                continue
            got = graph_files.read_file(source, rel)
            if "error" in got:
                # ponytail: the room is a text room (md/json/jsonl); binaries and oversizes
                # are skipped loudly, not fetched by a second lane.
                print(f"  skip {rel}: {got['error']}")
                continue
            p = tree / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(got["content"], encoding="utf-8")


def refresh_seed(seed: Path, src: str | None) -> int:
    new = seed.with_name(seed.name + ".new")
    if new.exists():
        shutil.rmtree(new)
    if src:
        shutil.copytree(src, new, ignore=shutil.ignore_patterns("export-sharepoint.zip"))
    else:
        new.mkdir(parents=True)
        _pull(new)
    _hygiene(new)
    if seed.exists():
        shutil.rmtree(seed)
    new.rename(seed)
    n = sum(1 for p in seed.rglob("*") if p.is_file())
    print(f"seed refreshed: {seed} ({n} files)")
    return 0


def _next_prefix_code(rooms: Path, prefix: str) -> str:
    """First free <prefix><i> from 1 upward — fills gaps, never clobbers, never shadows a
    COMPANIES name. Sequential codes are speakable at an event ('I am bia7'); the guessable
    namespace is an accepted trade (owner ruling 2026-08-25): data is synthetic and the QR
    claim lane hands codes out server-side, so nobody types one."""
    i = 1
    while (rooms / f"{prefix}{i}").exists() or f"{prefix}{i}" in graph_files.COMPANIES:
        i += 1
    return f"{prefix}{i}"


def mint(rooms: Path, seed: Path, n: int, embed_base: str | None = None,
         code_prefix: str | None = None) -> int:
    if not seed.is_dir():
        raise SystemExit(f"no seed at {seed} — run refresh-seed first")
    rooms.mkdir(parents=True, exist_ok=True)
    # With --embed-base the first column is the whole handout: one personal URL whose page
    # fills the prompt and swaps its doors to the room. The base is operator-passed because
    # the live-<hash> directory name is password-derived and must never sit in this repo.
    print("code\tlink\tfiles\tgraph" if embed_base else "code\tfiles\tgraph")
    for _ in range(n):
        code = _next_prefix_code(rooms, code_prefix) if code_prefix else _new_code(rooms)
        shutil.copytree(seed, rooms / code)
        try:
            import dep_graph
            dep_graph.generate(code, graph_files.read_file)
        except Exception as exc:  # noqa: BLE001 — the room stands; the page can be regened
            print(f"  warning: graph page for {code} not pre-generated: {exc}",
                  file=sys.stderr)
        personal = f"{embed_base}?room={code}\t" if embed_base else ""
        print(f"{code}\t{personal}{graph_files.ROOMS_URL}/{code}/\t"
              f"https://agent.ai4bcm.org/demo/graph/{code}/")
    return 0


def list_rooms(rooms: Path) -> int:
    codes = sorted(p.name for p in rooms.iterdir() if p.is_dir()) if rooms.is_dir() else []
    for code in codes:
        files = sum(1 for q in (rooms / code).rglob("*") if q.is_file())
        print(f"{code}\t{files} files\t{graph_files.ROOMS_URL}/{code}/")
    print(f"{len(codes)} room(s)")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog=Path(argv[0]).name)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="inventory of minted rooms")
    p_mint = sub.add_parser("mint", help="mint N rooms from the seed; prints handout rows")
    p_mint.add_argument("n", type=int)
    p_mint.add_argument("--embed-base", dest="embed_base", default=None,
                        help="embed page URL (the live-<hash> dir); adds one personal "
                             "?room= link per row")
    p_mint.add_argument("--code-prefix", dest="code_prefix", default=None,
                        help="sequential speakable codes <prefix>1..N (event/QR lane) "
                             "instead of random bird codes")
    p_seed = sub.add_parser("refresh-seed", help="rebuild the seed (never touches rooms)")
    p_seed.add_argument("--from", dest="src", default=None,
                        help="archive snapshot dir (offline); default: live pull")
    args = ap.parse_args(argv[1:])
    rooms, seed = _wire()
    if args.cmd == "refresh-seed":
        return refresh_seed(seed, args.src)
    if args.cmd == "mint":
        return mint(rooms, seed, args.n, args.embed_base, args.code_prefix)
    return list_rooms(rooms)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
