"""voice_check — grades how Bruno's turns read (length, stage cards, narration, bad echoes) so
the 'sounds like a colleague' plan is falsifiable from CLI output, not vibes. Every counter is
driven off fixture strings here; --relay is tested with subprocess.run monkeypatched — the real
relay is never called."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import journeys
import voice_check

# ---- fixture turns, one per case the plan's verification section names -----------------------
CARD_NEW = ("Stage 1 of 5 · Identification of scope\n\nWe are starting the BIA for marschkamp.\n\n"
            "Next: 1) confirm the scope")
CARD_LEGACY = ("Stage 2 · Structured interview (conversational)\n\nLet's begin the interview.\n\n"
               "Next: 1) continue")
# The agent's own bad example 1 records this exact bolded shape — the graded run emitted it.
CARD_BOLD = ("**Stage 1 of 5 · Identification of scope**\n\nWe are starting the BIA for marschkamp.\n\n"
             "Next: 1) confirm the scope")
CARD_HASH_HEADED = ("### Stage 1 of 5 · Identification of scope\n\nWe are starting the BIA for "
                    "marschkamp.\n\nNext: 1) confirm the scope")
CARD_UNDERSCORE = ("_Stage 1 of 5 · Identification of scope_\n\nWe are starting the BIA for "
                   "marschkamp.\n\nNext: 1) confirm the scope")
BARE = "Understood — MTPD is the maximum tolerable period of disruption."
NEXT_NO_CARD = "Here is a quick answer to your question.\n\nNext: 1) proceed with stage 2"
TWO_LINKS = "See [the addendum](http://example.com/a) and [the register](http://example.com/b) for details."
NARRATION = "I'm applying the retrieval tool now.\n\nOne moment."
NOTHING_SAVED = "Nothing has been saved yet — would you like me to save a draft?"
BAD_ECHO = "This copy is byte-identical to the referee-validated record."
ALL_TURNS = [CARD_NEW, CARD_LEGACY, BARE, NEXT_NO_CARD, TWO_LINKS, NARRATION, NOTHING_SAVED, BAD_ECHO]


# ---- per-turn predicates --------------------------------------------------

def test_is_card_turn_matches_new_and_legacy_forms():
    assert voice_check.is_card_turn(CARD_NEW)
    assert voice_check.is_card_turn(CARD_LEGACY)
    assert not voice_check.is_card_turn(BARE)


def test_is_card_turn_matches_the_3a_owner_loop_form():
    assert voice_check.is_card_turn("Stage 3a · Missing-owner loop\n\nWho owns this dependency?")


@pytest.mark.parametrize("turn,expected", [
    (CARD_NEW, True),
    (CARD_LEGACY, True),
    (CARD_BOLD, True),
    (CARD_HASH_HEADED, True),
    (CARD_UNDERSCORE, True),
    (BARE, False),
], ids=["plain", "legacy", "bold", "hash-headed", "underscore", "non-card"])
def test_is_card_turn_matches_bold_headed_and_underscore_cards(turn, expected):
    """CARD_RE used to require the banner to start the line with a bare 'Stage' — a bolded or
    ###-headed card (the shape the agent actually emitted in the graded run) fell through, so
    an --expect-card no thread PASSED with a banner present and an --expect-card yes thread
    FAILED with one present."""
    assert voice_check.is_card_turn(turn) is expected


def test_is_escaped_newline_turn():
    """2026-08-19: the owner spotted, by eye, that stage cards had lost their line breaks — the
    turn reached the relay with a literal backslash-n where a newline belonged, so the client
    showed one wall of text with no header and no list. `voice_check` graded that run PASS.

    Not a bia-workflow defect: it predates the voice work (first seen 2026-08-18 19:18), it
    arrives in contiguous session-length blocks, and it lands on the codex-acp seats
    (bruno 17 of 51, chief 1 of 15, hans 0 of 29 — only bruno and pa run codex-acp).
    Detected here anyway, because a grader that passes an unreadable turn is worse than none."""
    assert voice_check.is_escaped_newline_turn("Stage 1 of 5 · Scope\\n\\nPick one.")
    assert not voice_check.is_escaped_newline_turn("Stage 1 of 5 · Scope\n\nPick one.")
    assert not voice_check.is_escaped_newline_turn(BARE)


def test_verdict_fails_on_escaped_newlines():
    status, reasons = voice_check.verdict(_counters(escaped_newlines=2))
    assert status == "FAIL" and any("escaped_newlines" in r for r in reasons)


def test_is_multilink_turn_requires_more_than_one_link():
    assert voice_check.is_multilink_turn(TWO_LINKS)
    assert not voice_check.is_multilink_turn("See [the addendum](http://example.com/a) for details.")


def test_is_narration_opener_is_case_insensitive():
    assert voice_check.is_narration_opener(NARRATION)
    assert voice_check.is_narration_opener("LET ME check that for you.")
    assert not voice_check.is_narration_opener(BARE)


def test_is_narration_opener_excludes_ordinary_sentences_that_start_with_the_bare_words():
    """The opener list used to carry bare 'applying', 'retrieving' and 'let me ' — so an
    ordinary sentence that merely starts with one of those words tripped a hard FAIL, not just
    an actual before-the-fact announcement."""
    assert not voice_check.is_narration_opener("Let me know if the 8 h works.")
    assert not voice_check.is_narration_opener(
        "Applying the 8 h MTPD to the cutting line, the gap is 4 h.")


def test_is_narration_opener_still_catches_the_announcement_forms():
    assert voice_check.is_narration_opener(
        "I'm applying the BIA facilitation method and retrieving the verified handover…")
    assert voice_check.is_narration_opener("Let me retrieve the register first.")


def test_has_nothing_saved_is_case_insensitive():
    assert voice_check.has_nothing_saved(NOTHING_SAVED)
    assert voice_check.has_nothing_saved("NOTHING HAS BEEN SAVED.")
    assert not voice_check.has_nothing_saved(BARE)


def test_has_bad_echo_builtin_phrase_and_independence_from_nothing_saved():
    assert voice_check.has_bad_echo(BAD_ECHO, voice_check.BUILTIN_BAD_PHRASES)
    # the built-in phrase is "Nothing has been saved." (period, no "yet") — a near-miss must not count
    assert not voice_check.has_bad_echo(NOTHING_SAVED, voice_check.BUILTIN_BAD_PHRASES)


def test_is_open_ended():
    """W11b, "never a dead end", as a POSITIVE check — the first in this script. Eight of the
    nine fail reasons here are prohibitions, so a set of turns that did nothing at all scored
    PASS. 0 for 3 on the bare turns of both 2026-08-19 runs is what this catches."""
    assert voice_check.is_open_ended("I'd start with Slaughter (1), or choose 2 or 3.")
    assert voice_check.is_open_ended("Carry on with the scope note?")
    assert voice_check.is_open_ended("Reply 1 to reuse it, or 2 to amend it first.")
    # the live failure, verbatim — the standards answer that ended flat
    assert not voice_check.is_open_ended(
        "For the BIA method I use ISO/TS 22317:2015. One limitation: the estate's source "
        "is the 2015 first edition, which ISO/TS 22317:2021 has superseded.")


def test_is_open_ended_accepts_the_number_and_word_form():
    """Hans's ruling 2026-08-19, asked as the manager who has to answer these: numbers always,
    but keep the word next to the number — "on a phone i want to press one key, not first
    decide whether this card is a word card or a number card", and his own objection to bare
    digits is that "three days later the thread is a column of 1s and i cant tell what i
    approved". Before this, the checker knew (1) / reply 1 / choose 2 / 2 or 3 and would have
    failed the exact format he specified."""
    assert voice_check.is_open_ended("By default I'll use those six. 1 yes, 2 amend.")
    assert voice_check.is_open_ended("Reply 1 yes or 2 amend.")
    assert voice_check.is_open_ended("I'd record both — 1 record, 2 change a number first.")


def test_is_open_ended_does_not_mistake_measurements_for_a_numbered_choice():
    """The naive pattern for "1 <word> … 2 <word>" matches "RTO 1 h and MTPD 2 days", which is
    an ordinary BIA sentence and a dead end. Option labels are separated by a comma or 'or';
    measurements are not."""
    assert not voice_check.is_open_ended("KA-01: RTO 1 h and MTPD 2 days.")
    assert not voice_check.is_open_ended("Impact after 2 h, 8 h, 24 h, 72 h, and 1 week.")


def test_is_open_ended_does_not_care_how_the_options_are_punctuated():
    """Fourth false FAIL from this check in one evening, and the last one caused by encoding a
    RENDERING instead of the property. The live 22:52 Teams card closed
    `1 yes = use this scope. 2 amend = change the activities first.` — separated by a full stop,
    not the comma the pattern knew. The property is "two or more numbered options the reader can
    answer with one key"; the punctuation between them is not part of it."""
    for closing in (
        "1 yes = use this scope. 2 amend = change the activities first.",
        "1 yes, 2 amend.",
        "1 yes — use this scope\n2 amend — change the activity list",
        "Reply 1 yes or 2 amend.",
    ):
        assert voice_check.is_open_ended(closing), closing


def test_a_digit_pair_that_is_not_an_option_menu_is_still_a_dead_end():
    """The guards that keep the looser rule honest, all real BIA sentences. Options run
    consecutively from 1 or 2; measurements and counts do not."""
    for closing in (
        "KA-01: RTO 1 h and MTPD 2 days.",              # units
        "Impact after 2 h, 8 h, 24 h, 72 h, and 1 week.",
        "The scope covers 4 sites and 6 activities.",   # digits, but not consecutive from 1 or 2
        "The register lists one gap.\n\n1 KA-01 — central refrigeration.",  # a single option
    ):
        assert not voice_check.is_open_ended(closing), closing


def test_is_open_ended_sees_a_numbered_menu_on_its_own_lines():
    """Live turn 2026-08-19 22:38, verbatim. Hans's `1 yes, 2 amend` renders naturally as a
    two-line menu, and reading only the last line sees `2 unrecorded — …` — a statement. The
    turn plainly leaves the reader something to press, so scoring it a dead end is a false
    FAIL, and a false fail is what made B7 mark corrected behaviour down this morning."""
    turn = ("This remains unrecorded. \u201cTwo weeks\u201d could be a proposed MTPD or RTO.\n\n"
            "1 definition — explain MTPD versus RTO.\n"
            "2 unrecorded — leave the statement unrecorded.")
    assert voice_check.is_open_ended(turn)


def test_a_numbered_menu_needs_more_than_one_option():
    """One numbered line is a list item, not a choice — and a turn that ends on a single
    enumerated fact is exactly the dead end this check exists to catch."""
    assert not voice_check.is_open_ended(
        "The register lists one gap.\n\n1 KA-01 — central refrigeration, RTO 4 h.")


def test_a_menu_of_measurements_is_not_a_choice():
    """The impact grid renders as digit-led lines too. `2 h — …` must not read as option 2."""
    assert not voice_check.is_open_ended(
        "Impact over time for payroll:\n\n2 h — none.\n8 h — none.\n72 h — statutory deadline missed.")


def test_is_open_ended_reads_the_last_line_not_the_whole_turn():
    """A turn that asked its question in the middle and then trailed off into prose is a dead
    end for the reader, whatever it contains further up."""
    assert not voice_check.is_open_ended(
        "Shall I record the gap?\n\nThe supplier's 8 h clock is the binding one either way.")
    assert voice_check.is_open_ended(
        "The supplier's 8 h clock is the binding one.\n\nShall I record the gap?")



# ---- aggregate counters ----------------------------------------------------

def test_compute_counters_over_fixture_turns():
    c = voice_check.compute_counters(ALL_TURNS)
    assert c["turns"] == 8
    assert c["card_turns"] == 2          # CARD_NEW + CARD_LEGACY
    assert c["next_turns"] == 3          # CARD_NEW, CARD_LEGACY, NEXT_NO_CARD — all defects now
    assert c["multilink_turns"] == 1     # TWO_LINKS
    assert c["narration_openers"] == 1   # NARRATION
    assert c["nothing_saved"] == 1       # NOTHING_SAVED
    assert c["bad_echoes"] == 1          # BAD_ECHO
    assert c["open_endings"] == 1        # NOTHING_SAVED — the only fixture that ends open
    assert c["max_chars"] == len(max(ALL_TURNS, key=len))


def test_median_p90_max_on_synthetic_sizes():
    turns = ["x" * n for n in range(100, 1001, 100)]  # ten turns: 100, 200, ..., 1000 chars
    assert voice_check.median_chars(turns) == 550
    assert voice_check.p90_chars(turns) == 900   # ceil(0.9*10)-1 == 8 -> sorted[8] == 900
    assert voice_check.max_chars(turns) == 1000


def test_p90_single_turn_is_its_own_length():
    assert voice_check.p90_chars(["abcde"]) == 5


def test_median_p90_max_on_empty_list_do_not_crash():
    assert voice_check.median_chars([]) == 0
    assert voice_check.p90_chars([]) == 0
    assert voice_check.max_chars([]) == 0


# ---- bad-phrase loading from design/personas.json --------------------------

def test_load_bad_phrases_from_personas_json(tmp_path):
    p = tmp_path / "personas.json"
    p.write_text(json.dumps([
        {"id": "bia-facilitator", "examples": [{"bad": "This is a bad phrase.", "good": "This is fine."}]},
        {"id": "plan-reviewer"},
    ]))
    assert voice_check.load_bad_phrases(p) == ["This is a bad phrase."]


def test_load_bad_phrases_is_empty_when_examples_key_absent(tmp_path):
    p = tmp_path / "personas.json"
    p.write_text(json.dumps([{"id": "bia-facilitator"}]))  # today's real shape — no examples yet
    assert voice_check.load_bad_phrases(p) == []


def test_load_bad_phrases_is_empty_when_file_missing(tmp_path):
    assert voice_check.load_bad_phrases(tmp_path / "nope.json") == []


def test_count_bad_echoes_includes_loaded_extra_phrases():
    turns = ["This is a bad phrase in the reply."]
    assert voice_check.count_bad_echoes(turns, extra_phrases=["This is a bad phrase"]) == 1
    assert voice_check.count_bad_echoes(turns) == 0  # not one of the built-ins


def test_every_persona_good_example_ends_open():
    """The worked examples are what the rule is FOR, so an exemplar that ends flat teaches the
    opposite of the property. Caught 2026-08-19: "the manager stalls" closed on "…I can show
    what it rests on." — a soft dead end, and the one shape H6 exists to count."""
    personas = json.loads(journeys.PERSONAS_FILE.read_text(encoding="utf-8"))
    for persona in personas:
        for ex in persona.get("examples") or []:
            assert voice_check.is_open_ended(ex["good"]), (
                f"{persona['id']} example {ex['when']!r} ends closed: "
                f"{ex['good'].splitlines()[-1]!r}")



# ---- verdict rules (pure) ---------------------------------------------------

def _counters(**over):
    base = {"turns": 5, "median_chars": 100, "p90_chars": 100, "max_chars": 100, "card_turns": 1,
            "next_turns": 0, "escaped_newlines": 0, "multilink_turns": 0, "narration_openers": 0,
            "nothing_saved": 0, "bad_echoes": 0, "open_endings": 5}
    return {**base, **over}


def test_verdict_passes_clean_counters():
    assert voice_check.verdict(_counters()) == ("PASS", [])


def test_verdict_fails_when_a_turn_ends_closed():
    status, reasons = voice_check.verdict(_counters(open_endings=4))
    assert status == "FAIL" and any("open_endings" in r for r in reasons)


def test_verdict_does_not_demand_open_endings_of_an_empty_run():
    """No turns is already exit 2 ("turns: 0"); it must not ALSO invent a shortfall — a
    divide-by-nothing reason would read as a voice defect where there was no voice."""
    status, reasons = voice_check.verdict(_counters(turns=0, open_endings=0))
    assert status == "PASS" and reasons == []


def test_verdict_fails_over_median():
    status, reasons = voice_check.verdict(_counters(median_chars=701))
    assert status == "FAIL" and any("median_chars" in r for r in reasons)


def test_verdict_fails_over_max_chars_only_when_limit_set():
    assert voice_check.verdict(_counters(max_chars=5000))[0] == "PASS"  # no --max-chars given
    status, reasons = voice_check.verdict(_counters(max_chars=5000), max_chars_limit=700)
    assert status == "FAIL" and any("max_chars" in r for r in reasons)


def test_verdict_expect_card_yes_fails_with_no_card():
    status, reasons = voice_check.verdict(_counters(card_turns=0), expect_card="yes")
    assert status == "FAIL" and any("card" in r for r in reasons)


def test_verdict_expect_card_no_fails_with_a_card():
    assert voice_check.verdict(_counters(card_turns=1), expect_card="no")[0] == "FAIL"


def test_verdict_fails_on_bad_echoes_narration_and_nothing_saved():
    assert voice_check.verdict(_counters(bad_echoes=1))[0] == "FAIL"
    assert voice_check.verdict(_counters(narration_openers=1))[0] == "FAIL"
    assert voice_check.verdict(_counters(nothing_saved=1))[0] == "FAIL"


def test_verdict_fails_on_a_next_label():
    """2026-08-19 owner ruling: the literal label is retired everywhere, so ANY turn carrying it
    is a defect — not, as before, only one carrying it without a card. `next_without_card` named
    a distinction that no longer exists and is gone with it."""
    status, reasons = voice_check.verdict(_counters(next_turns=1))
    assert status == "FAIL" and any("next_turns" in r for r in reasons)


# ---- --file splitter --------------------------------------------------------

def test_turns_from_file_splits_on_bare_dashes(tmp_path):
    p = tmp_path / "thread.txt"
    p.write_text("Turn one.\nstill turn one.\n----\nTurn two.\n----\n\n")
    assert voice_check.turns_from_file(p) == ["Turn one.\nstill turn one.", "Turn two."]


def test_turns_from_file_single_turn_with_no_delimiter(tmp_path):
    p = tmp_path / "thread.txt"
    p.write_text("Just one turn, no delimiter line.")
    assert voice_check.turns_from_file(p) == ["Just one turn, no delimiter line."]


def test_turns_from_file_empty_file_yields_no_turns(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("")
    assert voice_check.turns_from_file(p) == []


# ---- main(): verdict + exit codes, driven through --file --------------------

def test_main_file_mode_pass(tmp_path, capsys):
    p = tmp_path / "thread.txt"
    p.write_text("A short, plain reply. Shall I carry on with the scope note?")
    rc = voice_check.main(["voice_check.py", "--file", str(p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "turns: 1" in out and "verdict: PASS" in out


def test_main_file_mode_fails_on_bad_echo(tmp_path, capsys):
    p = tmp_path / "thread.txt"
    p.write_text(BAD_ECHO)
    rc = voice_check.main(["voice_check.py", "--file", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "verdict: FAIL" in out and "fail: bad_echoes" in out


def test_main_no_turns_exits_2(tmp_path, capsys):
    p = tmp_path / "empty.txt"
    p.write_text("")
    rc = voice_check.main(["voice_check.py", "--file", str(p)])
    assert rc == 2
    assert "turns: 0" in capsys.readouterr().out


def test_main_no_mode_selected_is_a_usage_error(capsys):
    rc = voice_check.main(["voice_check.py"])
    assert rc == 2
    assert "specify one of" in capsys.readouterr().err


def test_main_expect_card_yes_fails_without_a_card(tmp_path, capsys):
    p = tmp_path / "thread.txt"
    p.write_text(BARE)
    rc = voice_check.main(["voice_check.py", "--file", str(p), "--expect-card", "yes"])
    out = capsys.readouterr().out
    assert rc == 1 and "fail:" in out


def test_main_max_chars_flag_fails_a_long_turn_without_tripping_median(tmp_path, capsys):
    p = tmp_path / "thread.txt"
    p.write_text("short one\n----\nshort two\n----\n" + "x" * 800)  # median stays low; max doesn't
    rc = voice_check.main(["voice_check.py", "--file", str(p), "--max-chars", "700"])
    out = capsys.readouterr().out
    assert rc == 1 and "fail: max_chars" in out


def test_help_flag_prints_usage_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        voice_check.main(["voice_check.py", "--help"])
    assert exc.value.code == 0
    assert "--relay" in capsys.readouterr().out


# ---- --transcripts -----------------------------------------------------------

def test_default_transcripts_dir_uses_data_dir_env(monkeypatch):
    monkeypatch.setenv("BIA_WORKFLOW_DATA_DIR", "/x/data")
    assert voice_check.default_transcripts_dir() == Path("/x/data/bia-usage")


def test_default_transcripts_dir_falls_back_to_cwd_relative(monkeypatch):
    monkeypatch.delenv("BIA_WORKFLOW_DATA_DIR", raising=False)
    assert voice_check.default_transcripts_dir() == Path("data/bia-usage")


def test_turns_from_transcripts_reads_bot_turns_only(tmp_path):
    """Shaped like the real Dataverse rows (test_usage_digest.py's real-shape fixture): from.role
    0 = bot / 1 = user, activities live inside a JSON-string `content` field."""
    acts = [
        {"type": "message", "from": {"role": 1}, "text": "Start a BIA for marschkamp.", "timestamp": 1786514325},
        {"type": "message", "from": {"role": 0},
         "text": "Stage 1 of 5 · Identification of scope\n\nNext: 1) confirm", "timestamp": 1786514330},
        {"type": "message", "from": {"role": 0}, "text": "Nothing has been saved yet.", "timestamp": 1786514335},
    ]
    (tmp_path / "transcripts-2026-W34.jsonl").write_text(json.dumps(
        {"conversationtranscriptid": "c1", "createdon": "2026-08-18T09:00:30Z",
         "content": json.dumps({"activities": acts})}) + "\n")
    turns = voice_check.turns_from_transcripts(tmp_path)
    assert turns == ["Stage 1 of 5 · Identification of scope\n\nNext: 1) confirm",
                      "Nothing has been saved yet."]


def test_main_transcripts_mode_end_to_end(tmp_path, capsys):
    acts = [{"type": "message", "from": {"role": 0}, "text": "A plain reply. Carry on?", "timestamp": 1}]
    (tmp_path / "transcripts-x.jsonl").write_text(json.dumps(
        {"conversationtranscriptid": "c1", "createdon": "2026-08-18T09:00:30Z",
         "content": json.dumps({"activities": acts})}) + "\n")
    rc = voice_check.main(["voice_check.py", "--transcripts", str(tmp_path)])
    assert rc == 0
    assert "turns: 1" in capsys.readouterr().out


def test_main_transcripts_bare_flag_uses_the_default_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BIA_WORKFLOW_DATA_DIR", raising=False)
    rc = voice_check.main(["voice_check.py", "--transcripts"])
    assert rc == 2  # no ./data/bia-usage here -> no turns, and no crash on a missing dir
    assert "turns: 0" in capsys.readouterr().out


# ---- --payload -----------------------------------------------------------

def test_payload_mode_runs_and_prints_the_budget_table(capsys):
    rc = voice_check.main(["voice_check.py", "--payload"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "protocol_chars:" in out
    assert "payload_sum:" in out and "payload_max:" in out
    assert "Stage 1 of 5 · Identification of scope" in out  # journeys.py's real card format


# ---- --relay: parser tested on a fixture list; subprocess.run always monkeypatched -----------

def test_default_author_is_brunos_pubkey():
    args = voice_check.build_parser().parse_args(["--relay", "chan-uuid"])
    assert args.author == voice_check.BRUNO_PUBKEY


def test_filter_relay_turns_accepts_the_relays_integer_kind_and_created_at():
    """The live relay returns kind/created_at as ints (measured 2026-08-19 against #bia-run)
    while the CLI's examples quote them. Both shapes must survive, or a real run grades as
    `turns: 0` and reads as "the agent said nothing"."""
    events = [
        {"content": "second", "created_at": 200, "id": "b", "kind": 9, "pubkey": voice_check.BRUNO_PUBKEY, "tags": []},
        {"content": "first", "created_at": 100, "id": "a", "kind": 9, "pubkey": voice_check.BRUNO_PUBKEY, "tags": []},
        {"content": "wrong kind", "created_at": 1, "id": "d", "kind": 1, "pubkey": voice_check.BRUNO_PUBKEY, "tags": []},
    ]
    assert voice_check.filter_relay_turns(events, voice_check.BRUNO_PUBKEY) == ["first", "second"]


def test_filter_relay_turns_keeps_author_and_kind9_sorted_by_created_at():
    events = [
        {"content": "second", "created_at": "200", "id": "b", "kind": "9",
         "pubkey": voice_check.BRUNO_PUBKEY, "tags": []},
        {"content": "not bruno", "created_at": "50", "id": "c", "kind": "9",
         "pubkey": "someone-else", "tags": []},
        {"content": "first", "created_at": "100", "id": "a", "kind": "9",
         "pubkey": voice_check.BRUNO_PUBKEY, "tags": []},
        {"content": "wrong kind", "created_at": "1", "id": "d", "kind": "1",
         "pubkey": voice_check.BRUNO_PUBKEY, "tags": []},
    ]
    assert voice_check.filter_relay_turns(events, voice_check.BRUNO_PUBKEY) == ["first", "second"]


def test_fetch_relay_turns_shells_buzz_messages_get(monkeypatch):
    events = [{"content": "hi", "created_at": "1", "id": "a", "kind": "9",
               "pubkey": voice_check.BRUNO_PUBKEY, "tags": []}]
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(events), stderr="")

    monkeypatch.setattr(voice_check.subprocess, "run", fake_run)
    turns = voice_check.fetch_relay_turns("chan-uuid", None, voice_check.BRUNO_PUBKEY)
    assert turns == ["hi"]
    assert captured["cmd"] == ["buzz", "messages", "get", "--channel", "chan-uuid", "--limit", "500"]


def test_fetch_relay_turns_passes_since_when_given(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(voice_check.subprocess, "run", fake_run)
    voice_check.fetch_relay_turns("chan-uuid", 1755000000, voice_check.BRUNO_PUBKEY)
    assert captured["cmd"] == ["buzz", "messages", "get", "--channel", "chan-uuid",
                                "--since", "1755000000", "--limit", "500"]


def test_main_relay_mode_prints_stderr_and_exits_2_on_failure(monkeypatch, capsys):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="error: unknown channel\n")

    monkeypatch.setattr(voice_check.subprocess, "run", fake_run)
    rc = voice_check.main(["voice_check.py", "--relay", "bad-channel"])
    assert rc == 2
    assert "error: unknown channel" in capsys.readouterr().err


def test_main_relay_mode_pass_through_to_verdict(monkeypatch, capsys):
    events = [{"content": "A short reply. Carry on?", "created_at": "1", "id": "a", "kind": "9",
               "pubkey": voice_check.BRUNO_PUBKEY, "tags": []}]

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(events), stderr="")

    monkeypatch.setattr(voice_check.subprocess, "run", fake_run)
    rc = voice_check.main(["voice_check.py", "--relay", "chan-uuid"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "turns: 1" in out and "verdict: PASS" in out


# ---- the relay address line, and the two menu renderings it arrived with ----------------------
# All three fixtures are verbatim live turns from the owner-driven run of 2026-08-20 (Bruno, in
# #bia-workflow). Each one is CORRECT conduct that the counters scored as a failure — the false
# FAIL that is_open_ended's own docstring names as the failure mode to fix. 35 of Bruno's 43
# turns since 2026-08-19 open by addressing someone; on a five-member channel that address is
# delivery, not prose, and Hans complained when it named the wrong person.
LIVE_CARD_UNDER_ADDRESS = (
    "@Konstantin\n\n**Stage 1 of 5 · Identification of scope**\n\n"
    "Decision: use one scope — Chilling, Storage & Dispatch.\n\n"
    "1 — yes — use this scope and environment\n2 — amend — change scope or activities")
LIVE_MENU_EM_DASH = ("Decision: use one scope — Chilling, Storage & Dispatch.\n\n"
                     "1 — yes — use this scope and environment\n"
                     "2 — amend — change scope or activities")
LIVE_MENU_DOTTED = ("Which baseline wording do you want carried forward?\n\n"
                    "1. Keep the three-part owner priority, with the QS qualification above.\n"
                    "2. Fix cold chain and packing in order, but leave QS formally unranked.")


def test_is_card_turn_sees_the_card_under_a_relay_address_line():
    assert voice_check.is_card_turn(LIVE_CARD_UNDER_ADDRESS)


def test_is_narration_opener_sees_the_announcement_under_a_relay_address_line():
    assert voice_check.is_narration_opener("@Konstantin\n\nLet me check the dependency register.")


def test_is_open_ended_accepts_a_numbered_menu_separated_by_an_em_dash():
    assert voice_check.is_open_ended(LIVE_MENU_EM_DASH)


def test_is_open_ended_accepts_a_numbered_menu_written_as_an_ordered_list():
    assert voice_check.is_open_ended(LIVE_MENU_DOTTED)
