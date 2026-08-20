"""Offline tests for probe_public_agent — the pure parts only, no browser, no live agent.

The probe's whole value is that it FAILS when the public agent drifts, so the failure paths
are what need covering: both were real on 2026-08-10 and both were invisible from this tree.
"""
import pytest

import probe_public_agent as probe

PROMPT = probe.PROMPT
GOOD = (f"You said: {PROMPT} Bot said: I am configured to work with the company data room "
        'named "marschkamp". 01_Organisation/ 02_BCM-Method/ 03_Dependencies/ output/')


def test_healthy_agent_passes():
    ok, problems = probe.verdict(GOOD, "marschkamp")
    assert ok and problems == []


def test_stale_part_d_fails():
    """2026-08-10: the agent kept answering 'marschkamp-demo' after a re-paste that never saved."""
    text = GOOD.replace("marschkamp", "marschkamp-demo")
    ok, problems = probe.verdict(text, "marschkamp")
    assert not ok and any("Part D" in p for p in problems)


def test_detached_tool_fails():
    """2026-08-10: MCP tool gone from the agent — it named the room but could list nothing."""
    text = (f"You said: {PROMPT} Bot said: I am configured to work with marschkamp, but the "
            "available data source returned no file inventory.")
    ok, problems = probe.verdict(text, "marschkamp")
    assert not ok and any("MCP tool" in p for p in problems)


def test_echoed_prompt_is_not_evidence():
    """The page text repeats what we typed. A verdict read off the echo would pass every time,
    including against a dead agent — so everything before the last echo is discarded."""
    ok, _ = probe.verdict(f"You said: {PROMPT} Bot said: I cannot help with that.", "marschkamp")
    assert not ok


def test_url_comes_from_the_published_page(tmp_path):
    """Read from the embed, never hardcoded: a probe aimed at a stale URL proves nothing."""
    page = tmp_path / "embed.html"
    page.write_text('<iframe src="https://copilotstudio.microsoft.com/environments/E/bots/B/'
                    'webchat?__version__=2" title="x"></iframe>', encoding="utf-8")
    assert probe.webchat_url(page).endswith("/bots/B/webchat?__version__=2")


def test_missing_iframe_is_loud(tmp_path):
    page = tmp_path / "embed.html"
    page.write_text("<p>no iframe here</p>", encoding="utf-8")
    with pytest.raises(SystemExit):
        probe.webchat_url(page)


def test_probes_the_real_published_embed():
    """Guard the coupling itself: if the deployed page stops carrying a copilotstudio iframe,
    this fails here rather than as a confusing browser timeout at send-out time."""
    assert probe.webchat_url().startswith("https://copilotstudio.microsoft.com/")
