"""Re-arm the LF-ABP-01 OWNER MISSING stall on the live SharePoint register.

Deterministic fallback for when SharePoint version-history restore fails (it did, 2026-07-21,
leaving a mixed state). Resets every field an acceptance run mutates to the pristine armed
values (captured verbatim from the pre-run register, 2026-07-21), using the live file as the
base so the other 14 assets stay byte-current. The 2026-07-21 acceptance run proved a run
touches more than owner/deputy: owner_role, second_source_owner, quality_flags, notes,
current_capability and contract_status all changed.

Idempotent — exits 0 without writing when the stall is already armed.

CLI: .venv/bin/python rearm_register.py [company]   (default marschkamp, which since
2026-08-10 is the only room — the marschkamp-demo copy is archived. The company argument
stays because the script is room-agnostic, not because a second room exists.)
"""
from __future__ import annotations

import json
import sys

import graph_files

PATH = "03_Dependencies/dependency-register.json"

# Pristine armed values, verbatim from the pre-run register (read 2026-07-21, STEP 0).
ARMED = {
    "owner_role": "QA / Environmental — responsible person for backup renderer Category 3 (open)",
    "owner_name": None,
    "stellvertreter": None,
    "second_source": None,
    "second_source_owner": "TBD (open)",
    "quality_flags": [
        "Missing owner: backup renderer/second source Category 3 not named (open / TBD) — "
        "trigger of the reality loop (owner capture → register writeback).",
        "Single renderer (NTV); containers fill in hours → slaughter stop on a collection failure.",
    ],
    "current_capability": "Single renderer (NTV) with daily collection; NO contractually bound "
                          "backup renderer and NO named owner for the second source.",
    "contract_status": "Primary contract NTV ABP-2023-07 in force; backup renderer open "
                       "(no contract, no owner).",
    "notes": "Deliberately seeded gap: the primary Category 3 collection by NTV (ABP-2023-07) is "
             "contractually secured and professionally owned by QA (Dr K. Sauer), but there is NO "
             "contractually bound backup renderer and no named owner for it. owner_name stays "
             "empty so that the reality-loop demo (dependency_flags → OWNER MISSING) triggers.",
}


def main(company: str = "marschkamp") -> int:
    cur = graph_files.read_file(company, PATH)
    if "error" in cur:
        print(f"cannot read {PATH}: {cur['error']}")
        return 1
    raw = cur["content"]
    reg = json.loads(raw)
    lf = reg["LF-ABP-01"]

    print("=== LF-ABP-01 BEFORE ===")
    print(json.dumps({k: lf.get(k) for k in ARMED}, ensure_ascii=False, indent=2))

    if all(lf.get(k) == v for k, v in ARMED.items()):
        print("\nAlready armed — no write needed.")
        return 0

    lf.update(ARMED)

    print("\n=== LF-ABP-01 AFTER (to write) ===")
    print(json.dumps({k: lf.get(k) for k in ARMED}, ensure_ascii=False, indent=2))

    new_content = json.dumps(reg, ensure_ascii=False, indent=2) + "\n"
    print(f"\nbytes: {len(raw.encode('utf-8'))} -> {len(new_content.encode('utf-8'))}")

    res = graph_files.write_file(company, PATH, new_content, user_confirmed=True,
                                 mode="overwrite")
    print("write result:", res)

    back = json.loads(graph_files.read_file(company, PATH)["content"])["LF-ABP-01"]
    for k, v in ARMED.items():
        assert back.get(k) == v, (k, back.get(k))
    print("\nREAD-BACK OK — stall re-armed: owner_name=%r, deputy=%r"
          % (back["owner_name"], back["stellvertreter"]))
    return 0


if __name__ == "__main__":  # guard: importing must never write to the live register
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "marschkamp"))
