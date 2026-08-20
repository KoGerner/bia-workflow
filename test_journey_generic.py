"""Guards: (1) the active run-bia journey is 100% method — zero company/engine tokens;
(2) retired company packs (butcher, blue-harbour) leave zero traces anywhere in the app tree."""
import pathlib

ROOT = pathlib.Path(__file__).parent
JOURNEY = ROOT / "design" / "run-bia.yaml"
BLACKLIST = [
    "blue harbour", "blueharbour", "tuna", "butcher",
    "bia_runner", "render_html", "demo.sh",
    "demo/latest/sources", "company-data/",
]

# Retired-pack tokens must not reappear anywhere in the live tree.
RETIRED = ["butcher", "blue-harbour", "blueharbour", "blue harbour"]
SCAN_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".yaml", ".yml", ".sh", ".txt"}
# ponytail: data/ = built artifact (chunks); its source lives in the vault and is republished
# by publish_knowledge.sh — the runbook's deploy-verify step covers post-publish chunk checks.
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "archived", "data", "deploy"}
SKIP_FILES = {
    pathlib.Path(__file__).name,   # this guard names the tokens on purpose
    "p8-promise-audit-2026-07-28.md",  # promise ledger quotes the retired P-34 row
}


def test_run_bia_journey_is_company_agnostic():
    text = JOURNEY.read_text(encoding="utf-8").lower()
    hits = [tok for tok in BLACKLIST if tok in text]
    assert not hits, f"company/engine tokens in run-bia.yaml: {hits}"


def test_no_retired_company_tokens_in_tree():
    offenders = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in SCAN_SUFFIXES or p.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore").lower()
        for tok in RETIRED:
            if tok in text:
                offenders.append(f"{p.relative_to(ROOT)}: {tok!r}")
    assert not offenders, "retired company tokens found:\n" + "\n".join(offenders)
