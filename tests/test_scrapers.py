"""Offline tests: run with `python -m pytest` (no network needed)."""
import json
from pathlib import Path

from scrapers import mps, pms

FIX = Path(__file__).parent / "fixtures"


def test_parse_mp_table():
    rows = mps.parse_table((FIX / "mps_table.html").read_text())
    assert [r["constituency"] for r in rows] == [
        "Clarendon Central", "Kingston Central", "Saint Andrew Southern", "Saint Ann North Eastern"]
    kc = rows[1]
    assert kc["name"] == "Donovan Williams"            # footnote [2] stripped
    assert kc["wikipedia_title"] == "Donovan Williams (politician)"
    assert kc["parish"] == "Kingston" and kc["party_abbr"] == "JLP"
    assert rows[2]["party_abbr"] == "PNP" and rows[2]["parish"] == "Saint Andrew"
    assert rows[3]["wikipedia_title"] is None          # red link ignored
    assert rows[3]["party_abbr"] == "IND"


def test_validate_catches_bad_data():
    rows = mps.parse_table((FIX / "mps_table.html").read_text())
    problems = mps.validate(rows)
    assert any("expected 63" in p for p in problems)
    rows[0]["party_abbr"] = None
    assert any("unknown party" in p for p in mps.validate(rows))


def test_seeded_mps_file_is_valid():
    doc = json.loads(Path("data/mps.json").read_text())
    assert mps.validate(doc["data"]) == []


def test_parish_prefers_longest_match():
    assert mps.parish_for("Saint Andrew West Rural") == "Saint Andrew"
    assert mps.parish_for("Kingston East & Port Royal") == "Kingston"
    assert mps.ordinal(15) == "15th" and mps.ordinal(16) == "16th" and mps.ordinal(21) == "21st"


def test_pms_with_override():
    terms = pms.apply_overrides(pms.parse_sparql(json.loads((FIX / "pms_sparql.json").read_text())))
    names = [t["name"] for t in sorted(terms, key=lambda t: t["start"])]
    assert len(terms) == 12
    assert names[5] == "Michael Manley"                 # 1989–1992 term restored
    assert sum(t["end"] is None for t in terms) == 1


def test_override_skipped_once_wikidata_has_it():
    data = json.loads((FIX / "pms_sparql.json").read_text())
    data["results"]["bindings"].append({
        "person": {"value": "http://www.wikidata.org/entity/Q365395"},
        "personLabel": {"value": "Michael Manley"},
        "start": {"value": "1989-02-10T00:00:00Z"}, "end": {"value": "1992-03-30T00:00:00Z"}})
    assert len(pms.apply_overrides(pms.parse_sparql(data))) == 12
