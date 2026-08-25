"""Anonymous end-to-end probe of the BIA-Workflow (public) agent.

Answers the one question no other check can: does a visitor with NO tenant identity get a
working agent pointed at the right room? Copilot Studio Instructions cannot be read back
programmatically (ms-agent-install.md §Part D), so Part D drift is invisible until behaviour
fails — and a detached or unauthorized MCP tool looks identical to a healthy agent in the
maker's test panel. Both were live on 2026-08-10 and neither was visible from this tree.

Why not copilot_eval.py: that lane needs a Copilot Studio test set, a maker sign-in and an
MCS_CONNECTION_ID. This needs none of them, because it enters through the front door the
audience uses.

The webchat URL is READ OUT of the published page rather than configured here, so the probe
can never drift from what visitors actually load.

Run: <app venv>/bin/python probe_public_agent.py [company]
    (playwright + chromium go into the app venv at Stage 3 of the cleaner build — ruled
    2026-08-18 — so this stays the one playwright consumer in the tree)

Exit 0 = the agent named the expected room AND returned a real file listing.
Exit 1 = drift, a missing/unauthorized tool, or no reply. Transcript is printed either way.

Costs one real conversation against the live agent (Copilot Studio PAYG). Cheap, not free —
this is a pre-send-out gate, not something to loop.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PAGE = Path(__file__).resolve().parent / "deploy" / "bia-live-embed.html"
PROMPT = "Which company data room are you configured to work with? List the files in it using your file tools."
# Proof the answer came from a tool, not from the model's memory of the prompt: these are
# top-level entries of the room, which only list_company_files can supply.
LISTING_MARKERS = ("01_Organisation", "03_Dependencies")


def webchat_url(page: Path = PAGE) -> str:
    """Microsoft's own hosted canvas for this agent, taken FROM the embed page so the probe
    follows the page rather than a constant.

    Until 2026-08-25 that was the page's iframe src. The page now renders its own WebChat
    canvas (§A.18: it must speak the room prompt itself, which a cross-origin frame cannot),
    and the hosted URL survives as the header's "Chat blank?" fallback link — so the probe
    reads src= OR href=. Deliberately still the hosted canvas: this probe drives a browser
    to prove the PUBLISHED agent answers anonymously, and the hosted page is the surface
    that needs no token minting of its own."""
    m = re.search(r'(?:src|href)="(https://copilotstudio\.microsoft\.com/[^"]+)"',
                  page.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"no copilotstudio agent URL (src= or href=) in {page}")
    return m.group(1)


def converse(url: str, prompts: list[str], *, timeout_s: int = 300, on_turn=None) -> list[str]:
    """Send prompts one after another on ONE webchat page; return the settled page text
    after each turn (cumulative — the caller slices on its own prompt echo).

    Top-level, never in an iframe: embedded cross-origin the Direct Line handshake needs
    third-party storage and the frame can render permanently blank (§note 4 of the embed).
    Multi-turn since 2026-08-16 so a stage-1 run (start → yes → approve save → what is
    saved?) can prove the save the way a visitor experiences it — one conversation, not four.
    """
    # Imported here, not at module scope: playwright is optional in the app venv (Stage 3
    # installs it), so a top-level import would break collection of the pure-function tests.
    from playwright.sync_api import sync_playwright

    texts: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 1400})
        page.goto(url, wait_until="load", timeout=60_000)
        page.wait_for_timeout(9_000)
        for prompt in prompts:
            box = page.locator("input[type=text], textarea").first
            box.wait_for(timeout=30_000)
            box.click()
            box.fill(prompt)
            page.keyboard.press("Enter")
            # Settle on two identical reads with no typing indicator: the reply streams in, and
            # "has it stopped growing" is the only signal a cross-origin webchat offers.
            text, prev, stable = "", "", 0
            for _ in range(timeout_s // 10):
                page.wait_for_timeout(10_000)
                text = " ".join(page.inner_text("body").split())
                stable = stable + 1 if text == prev and "typing indicator" not in text else 0
                if stable >= 2:
                    break
                prev = text
            texts.append(text)
            if on_turn:
                on_turn(len(texts), text)  # progress hook: a long run must not be silent until the end
        browser.close()
    return texts


def ask(url: str, prompt: str, *, timeout_s: int = 300) -> str:
    """Send one message top-level and return the settled page text."""
    return converse(url, [prompt], timeout_s=timeout_s)[0]


def verdict(text: str, company: str) -> tuple[bool, list[str]]:
    reply = text.split(PROMPT)[-1]          # the echo of our own prompt is not evidence
    problems = []
    # NOT `company in reply`: every retired room name here has been the live one plus a suffix
    # (marschkamp-demo), so a substring test passes the exact drift this probe exists to catch.
    if not re.search(rf"\b{re.escape(company)}(?![\w-])", reply):
        problems.append(f"agent did not name {company!r} — Part D may be stale or unpasted")
    missing = [m for m in LISTING_MARKERS if m not in reply]
    if missing:
        problems.append("no real file listing (missing " + ", ".join(missing) +
                        ") — MCP tool detached, unauthorized, or its connection is invalid")
    return not problems, problems


def main(company: str = "marschkamp") -> int:
    text = ask(webchat_url(), PROMPT)
    ok, problems = verdict(text, company)
    print(text or "(no reply)")
    print()
    for p in problems:
        print("FAIL:", p)
    print("PASS — anonymous visitor reached the agent and it listed %s" % company if ok else "")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "marschkamp"))
