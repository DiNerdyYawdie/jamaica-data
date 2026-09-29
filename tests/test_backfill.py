"""Offline tests for the election backfill (no network, no Gemini)."""
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("backfill", Path("scripts/backfill_elections.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)


def test_event_ids():
    assert bf.slug_for("1944", "December 14", "General Election",
                       "https://x/election-results/general-elections-1944/") == "1944-general"
    assert bf.slug_for("2024", "November 22", "By-Election",
                       "https://x/election-results/by-election-2024/") == "2024-nov-by-election"
    assert bf.slug_for("2009", "June 16", "By-Election",
                       "https://x/election-results/st-catherine-north-eastern-election-2009/") \
        == "2009-jun-by-election-st-catherine-north-eastern"


def test_names_normalise():
    assert bf.norm_name("(9) ST. ANDREW SOUTHERN") == bf.norm_name("Saint Andrew Southern")
    assert bf.pretty_constituency("(3) KINGSTON EAST & PORT ROYAL") == "Kingston East & Port Royal"
    assert bf.pretty_constituency("ST. ANN NORTH EASTERN") == "Saint Ann North Eastern"


def test_verified_when_totals_add_up():
    [r] = bf.finalize([{"name": "(1) KINGSTON WESTERN", "electors": 20000, "total_votes": 9000,
                        "rejected": 100, "candidates": [
                            {"name": None, "party": "PNP", "votes": 900},
                            {"name": None, "party": "JLP", "votes": 8000}]}])
    assert r["status"] == "verified"
    assert r["winner"]["party"] == "JLP" and r["margin"] == 7100
    assert r["turnout_pct"] == 45.0


def test_check_when_totals_dont_add_up():
    [r] = bf.finalize([{"name": "X", "total_votes": 9000, "rejected": 0, "candidates": [
        {"party": "PNP", "votes": 900}, {"party": "JLP", "votes": 7000}]}])
    assert r["status"] == "check" and "notes" in r


def test_old_reports_checked_against_turnout_percent():
    # 1944 style: electors + % voted, no total printed. 431+74+5417+1283+4352 = 11557
    rows = [{"name": "KINGSTON EAST & PORT ROYAL", "electors": 22367, "percent_voted": 51.7,
             "candidates": [{"name": "Glasspole, Florizel", "party": "P.N.P.", "votes": 5417},
                            {"name": "Valentine, Gilbert Enos", "party": "J.L.P.", "votes": 4352},
                            {"name": "Russell, James Nelson", "party": "J.D.P.", "votes": 1283},
                            {"name": "Campbell, E. E. A.", "party": "Other P.", "votes": 431},
                            {"name": "Durham, Vivian Francis", "party": "Other P.", "votes": 74}]}]
    [r] = bf.finalize(rows)
    assert r["status"] == "verified"
    assert r["winner"]["name"] == "Glasspole, Florizel"
    rows[0]["percent_voted"] = 60.0
    assert bf.finalize(rows)[0]["status"] == "check"


def test_page_boundary_duplicates_removed():
    c = {"name": "A", "party": "JLP", "votes": 10}
    [r] = bf.finalize([{"name": "X", "total_votes": 10, "rejected": 0, "candidates": [c, dict(c)]}])
    assert len(r["candidates"]) == 1 and r["status"] == "verified"


def test_build_site_with_history(tmp_path, monkeypatch):
    import shutil, subprocess
    work = tmp_path / "repo"
    shutil.copytree(".", work, ignore=shutil.ignore_patterns(".git", "site", ".cache"))
    hist = work / "data" / "history" / "elections"
    hist.mkdir(parents=True)
    [r] = bf.finalize([{"name": "(9) ST. ANDREW SOUTHERN", "total_votes": 10, "rejected": 0,
                        "candidates": [{"name": "Omar Davies", "party": "PNP", "votes": 10}]}])
    (hist / "2016-general.json").write_text(json.dumps({
        "meta": {}, "election": {"id": "2016-general", "year": 2016, "date": "February 25, 2016",
                                 "type": "General Election"},
        "summary": {"seats": 1, "verified": 1, "needs_check": 0, "seats_by_party": {"PNP": 1}},
        "results": [r]}))
    subprocess.run([sys.executable, "scripts/build_site.py"], cwd=work, check=True)
    seat = json.loads((work / "site/v1/constituencies/saint-andrew-southern.json").read_text())
    assert seat["data"]["elections"][0]["winner"]["name"] == "Omar Davies"
    assert json.loads((work / "site/v1/elections.json").read_text())["data"][0]["id"] == "2016-general"
