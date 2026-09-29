"""Prime Ministers of Jamaica, from Wikidata, with manual fixes applied.

Wikidata is good but not perfect. It is currently missing Michael Manley's
second term (1989–1992). Fixes live in data/overrides/pms.json, so
corrections survive every automatic refresh.
"""
from __future__ import annotations

import json

from scrapers.common import DATA, fail, get_json, write_if_changed

SPARQL = "https://query.wikidata.org/sparql"
PM_POSITION = "Q1430943"  # Prime Minister of Jamaica

QUERY = f"""
SELECT ?person ?personLabel ?start ?end ?partyLabel WHERE {{
  ?person p:P39 ?s. ?s ps:P39 wd:{PM_POSITION}.
  OPTIONAL {{ ?s pq:P580 ?start }}
  OPTIONAL {{ ?s pq:P582 ?end }}
  OPTIONAL {{ ?person wdt:P102 ?party }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} ORDER BY ?start
"""


def parse_sparql(result: dict) -> list[dict]:
    terms: dict[tuple, dict] = {}
    for b in result["results"]["bindings"]:
        qid = b["person"]["value"].rsplit("/", 1)[-1]
        start = b.get("start", {}).get("value", "")[:10] or None
        key = (qid, start)
        term = terms.setdefault(key, {
            "wikidata_id": qid,
            "name": b["personLabel"]["value"],
            "start": start,
            "end": b.get("end", {}).get("value", "")[:10] or None,
            "parties": [],
        })
        party = b.get("partyLabel", {}).get("value")
        if party and party not in term["parties"]:
            term["parties"].append(party)
    return list(terms.values())


def apply_overrides(terms: list[dict]) -> list[dict]:
    path = DATA / "overrides" / "pms.json"
    if not path.exists():
        return terms
    ov = json.loads(path.read_text())
    have = {(t["wikidata_id"], t["start"]) for t in terms}
    for extra in ov.get("add", []):
        if (extra["wikidata_id"], extra["start"]) not in have:   # skip once Wikidata catches up
            terms.append({k: v for k, v in extra.items() if k != "note"})
    return terms


def main() -> None:
    terms = apply_overrides(parse_sparql(get_json(SPARQL, query=QUERY, format="json")))
    terms.sort(key=lambda t: t["start"] or "")

    if len(terms) < 12:
        fail(f"only {len(terms)} PM terms, expected at least 12")
    current = [t for t in terms if t["end"] is None]
    if len(current) != 1:
        fail(f"expected exactly 1 sitting PM, found {len(current)}: {[t['name'] for t in current]}")

    for i, t in enumerate(terms, 1):
        t["term_number"] = i
        t["current"] = t["end"] is None

    write_if_changed("pms", terms, {
        "title": "Prime Ministers of Jamaica",
        "source": f"https://www.wikidata.org/wiki/{PM_POSITION}",
        "license": "CC0 (Wikidata) + manual corrections",
    })


if __name__ == "__main__":
    main()
