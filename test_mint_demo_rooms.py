"""mint_demo_rooms — operator lane: refresh-seed hygiene, mint, list. No network in here:
the archive lane copies a fixture tree, the live lane is exercised through monkeypatched
list/read functions."""
import json
import re

import pytest

import graph_files as gf
import mint_demo_rooms as mint
import rearm_register


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Zero-env wiring made explicit for tests: the token-file knob decides the base dir,
    exactly as deployed (/srv/addendum → demo-rooms, demo-seed beside secret)."""
    monkeypatch.setenv("BIA_WORKFLOW_TOKEN_FILE", str(tmp_path / "secret"))
    return tmp_path


@pytest.fixture()
def snapshot(tmp_path):
    """A fixture tree shaped like the SharePoint export snapshot, dirty on purpose:
    run output, golden transcripts beside the poison fixture, a stale backup pack,
    a used approval log, a fired (dis-armed) register entry, and the export zip."""
    src = tmp_path / "export"
    (src / "02_BCM-Method").mkdir(parents=True)
    (src / "03_Dependencies").mkdir()
    (src / "07_Interviews").mkdir()
    (src / "09_Evaluation").mkdir()
    (src / "output" / "slaughter").mkdir(parents=True)
    (src / "output" / "pack-backup-77").mkdir()
    (src / "company-profile.md").write_text("# Marschkamp Fleisch GmbH", encoding="utf-8")
    (src / "02_BCM-Method" / "method.json").write_text('{"scale": "1-4"}', encoding="utf-8")
    register = {"synthetic": True}
    for i in range(12):
        register[f"AS-{i:02d}"] = {"asset_id": f"AS-{i:02d}", "owner_name": f"Owner {i}",
                                   "notes": "x" * 700}
    register["LF-ABP-01"] = {"asset_id": "LF-ABP-01", "owner_name": "Olga Milevska",
                             "quality_flags": [], "notes": "owner captured — fired state"}
    (src / "03_Dependencies" / "dependency-register.json").write_text(
        json.dumps(register, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (src / "07_Interviews" / "2026-07-01-petra-louven.md").write_text("28 KB of canon",
                                                                     encoding="utf-8")
    (src / "09_Evaluation" / "rt1-poison.md").write_text("POISON FIXTURE", encoding="utf-8")
    (src / "09_Evaluation" / "golden-run-1.md").write_text("the answers", encoding="utf-8")
    (src / "output" / "slaughter" / "stage1-scope-and-guide.md").write_text("a run",
                                                                           encoding="utf-8")
    (src / "output" / "pack-backup-77" / "register.json").write_text("{}", encoding="utf-8")
    (src / "approval-log.jsonl").write_text(
        '{"approved": "scope", "by": "Hans"}\n{"approved": "record", "by": "Hans"}\n',
        encoding="utf-8")
    (src / "export-sharepoint.zip").write_bytes(b"PK\x03\x04zip")
    return src


def _seed(wired):
    return wired / "demo-seed"


def test_refresh_seed_applies_the_hygiene_contract(wired, snapshot, capsys):
    assert mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)]) == 0
    seed = _seed(wired)
    assert (seed / "company-profile.md").read_text(encoding="utf-8") == \
        "# Marschkamp Fleisch GmbH"
    assert (seed / "07_Interviews" / "2026-07-01-petra-louven.md").exists()
    assert (seed / "output").is_dir() and list((seed / "output").iterdir()) == []
    assert [p.name for p in (seed / "09_Evaluation").iterdir()] == ["rt1-poison.md"]
    assert not list(seed.rglob("pack-backup*"))
    assert not (seed / "export-sharepoint.zip").exists()
    assert (seed / "approval-log.jsonl").read_text(encoding="utf-8") == \
        mint.SYNTHETIC_APPROVAL
    reg = json.loads((seed / "03_Dependencies" / "dependency-register.json")
                     .read_text(encoding="utf-8"))
    assert reg["synthetic"] is True, "the fiction marker survives the seed rebuild"
    for k, v in rearm_register.ARMED.items():
        assert reg["LF-ABP-01"][k] == v, f"seed register must be re-armed ({k})"


def test_refresh_seed_replaces_the_seed_but_never_touches_rooms(wired, snapshot):
    room = wired / "demo-rooms" / "kranich-x7k2mp"
    room.mkdir(parents=True)
    (room / "in-flight.md").write_text("a manager's live run", encoding="utf-8")
    stale = _seed(wired)
    (stale / "output").mkdir(parents=True)
    (stale / "stale.md").write_text("old seed", encoding="utf-8")
    assert mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)]) == 0
    assert not (stale / "stale.md").exists(), "refresh-seed swaps the whole seed tree"
    assert (room / "in-flight.md").read_text(encoding="utf-8") == "a manager's live run"


def test_refresh_seed_live_pull_walks_list_and_read(wired, monkeypatch):
    """No --from: the seed is pulled through the same two tools the agent uses."""
    tree = {"": [("company-profile.md", False), ("02_BCM-Method", True)],
            "02_BCM-Method": [("method.json", False)]}
    bodies = {"company-profile.md": "# pulled", "02_BCM-Method/method.json": '{"scale": 1}'}
    monkeypatch.setattr(gf, "list_files", lambda company, base="": {
        "company": company,
        "files": [{"name": n, "size": 1, "is_folder": f, "path": n} for n, f in tree[base]]})
    monkeypatch.setattr(gf, "read_file",
                        lambda company, rel: {"path": rel, "content": bodies[rel], "size": 1})
    assert mint.main(["mint_demo_rooms.py", "refresh-seed"]) == 0
    seed = _seed(wired)
    assert (seed / "company-profile.md").read_text(encoding="utf-8") == "# pulled"
    assert (seed / "02_BCM-Method" / "method.json").read_text(encoding="utf-8") == '{"scale": 1}'
    assert (seed / "output").is_dir() and list((seed / "output").iterdir()) == []
    assert (seed / "approval-log.jsonl").read_text(encoding="utf-8") == \
        mint.SYNTHETIC_APPROVAL


def test_mint_creates_rooms_with_handout_rows_and_live_graph_pages(wired, snapshot, capsys,
                                                                   tmp_path):
    mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)])
    capsys.readouterr()
    assert mint.main(["mint_demo_rooms.py", "mint", "3"]) == 0
    handout = capsys.readouterr().out
    rooms = sorted(p.name for p in (wired / "demo-rooms").iterdir())
    assert len(rooms) == 3
    for code in rooms:
        assert re.fullmatch(rf"[a-z]+-[{mint.ALPHABET}]{{6}}", code), code
        assert code not in gf.COMPANIES
        assert (wired / "demo-rooms" / code / "company-profile.md").exists()
        assert list((wired / "demo-rooms" / code / "output").iterdir()) == []
        assert f"https://agent.ai4bcm.org/demo/rooms/{code}/" in handout
        assert f"https://agent.ai4bcm.org/demo/graph/{code}/" in handout
        page = tmp_path / "graph-pages" / code / "index.html"
        assert page.exists(), "the handout graph link must be live before the first write"


def test_mint_never_reuses_an_existing_room_or_a_company_name(wired, snapshot, monkeypatch,
                                                              capsys):
    mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)])
    taken = wired / "demo-rooms" / "adler-abc234"
    taken.mkdir(parents=True)
    (taken / "keep.md").write_text("do not clobber", encoding="utf-8")
    codes = iter(["marschkamp", "adler-abc234", "adler-abc234", "falke-fresh2"])
    monkeypatch.setattr(mint, "_rand_code", lambda: next(codes))
    assert mint.main(["mint_demo_rooms.py", "mint", "1"]) == 0
    assert (taken / "keep.md").read_text(encoding="utf-8") == "do not clobber"
    assert (wired / "demo-rooms" / "falke-fresh2" / "company-profile.md").exists()


def test_mint_without_a_seed_refuses(wired, capsys):
    with pytest.raises(SystemExit):
        mint.main(["mint_demo_rooms.py", "mint", "1"])


def test_list_inventories_the_rooms(wired, snapshot, capsys):
    mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)])
    mint.main(["mint_demo_rooms.py", "mint", "2"])
    capsys.readouterr()
    assert mint.main(["mint_demo_rooms.py", "list"]) == 0
    out = capsys.readouterr().out
    for p in (wired / "demo-rooms").iterdir():
        assert p.name in out


def test_mint_code_prefix_mints_sequential_codes(wired, snapshot, capsys):
    """Event handout (owner ruling 2026-08-25): speakable codes bia1..biaN instead of the
    random bird codes — the QR claim lane hands them out in order, and 'I am bia7' works
    out loud at a booth. Random stays the default; sequential is opt-in per mint."""
    mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)])
    capsys.readouterr()
    assert mint.main(["mint_demo_rooms.py", "mint", "3", "--code-prefix", "bia",
                      "--embed-base", "https://agent.ai4bcm.org/demo/live-x/"]) == 0
    out = capsys.readouterr().out
    rooms = sorted(p.name for p in (wired / "demo-rooms").iterdir())
    assert rooms == ["bia1", "bia2", "bia3"]
    assert "https://agent.ai4bcm.org/demo/live-x/?room=bia1" in out
    for code in rooms:
        assert (wired / "demo-rooms" / code / "company-profile.md").exists()


def test_mint_code_prefix_skips_existing_rooms(wired, snapshot, capsys):
    mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)])
    taken = wired / "demo-rooms" / "bia2"
    taken.mkdir(parents=True)
    (taken / "keep.md").write_text("do not clobber", encoding="utf-8")
    assert mint.main(["mint_demo_rooms.py", "mint", "2", "--code-prefix", "bia"]) == 0
    names = sorted(p.name for p in (wired / "demo-rooms").iterdir())
    assert names == ["bia1", "bia2", "bia3"]
    assert (taken / "keep.md").read_text(encoding="utf-8") == "do not clobber"
    assert (wired / "demo-rooms" / "bia1" / "company-profile.md").exists()
    assert (wired / "demo-rooms" / "bia3" / "company-profile.md").exists()


def test_mint_embed_base_prints_one_personal_link_per_room(wired, snapshot, capsys):
    """The handout is ONE URL per manager: the embed page with ?room=<code>. The page fills
    the prompt and swaps its doors; nothing to hand-edit. The base is operator-passed because
    the live-<hash> directory name is password-derived and must never sit in this repo."""
    mint.main(["mint_demo_rooms.py", "refresh-seed", "--from", str(snapshot)])
    capsys.readouterr()
    assert mint.main(["mint_demo_rooms.py", "mint", "2", "--embed-base",
                      "https://agent.ai4bcm.org/demo/live-x/"]) == 0
    out = capsys.readouterr().out
    rooms = sorted(p.name for p in (wired / "demo-rooms").iterdir())
    assert len(rooms) == 2
    for code in rooms:
        assert f"https://agent.ai4bcm.org/demo/live-x/?room={code}" in out
