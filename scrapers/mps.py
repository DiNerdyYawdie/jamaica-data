"""Current Members of Parliament (House of Representatives).

Source: the English Wikipedia article for the current Parliament
("15th Parliament of Jamaica"), read through the MediaWiki API, plus
Wikidata IDs for each MP. The official Parliament site has no member
roster, so this is the most complete machine-readable source.

After an election the scraper checks for the next Parliament's article
and switches to it on its own once that article has a full MP table.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from scrapers.common import fail, get_json, write_if_changed

API = "https://en.wikipedia.org/w/api.php"
CURRENT_PARLIAMENT = 15   # bump after an election (the scraper also auto-detects)
SEATS = 63

PARTIES = {
    "Jamaica Labour Party": "JLP",
    "People's National Party": "PNP",
    "Independent": "IND",
}
PARISHES = [
    "Kingston", "Saint Andrew", "Saint Thomas", "Portland", "Saint Mary",
    "Saint Ann", "Trelawny", "Saint James", "Hanover", "Westmoreland",
    "Saint Elizabeth", "Manchester", "Clarendon", "Saint Catherine",
]


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def page_title(n: int) -> str:
    return f"{ordinal(n)} Parliament of Jamaica"


def clean(text: str) -> str:
    text = re.sub(r"\[\d+\]|\[[a-z]\]", "", text)      # footnote markers like [2]
    return re.sub(r"\s+", " ", text).strip()


def parish_for(constituency: str) -> str | None:
    for p in sorted(PARISHES, key=len, reverse=True):
        if constituency.startswith(p):
            return p
    return None


def parse_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (t for t in soup.select("table.wikitable")
         if "Constituency" in t.find("tr").get_text() and "MP" in t.find("tr").get_text()),
        None,
    )
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        constituency = clean(cells[0].get_text())
        mp_cell = cells[1]
        link = mp_cell.find("a")
        href = link.get("href", "") if link else ""
        wiki_title = None
        if href.startswith("/wiki/"):              # skip red links (page doesn't exist)
            wiki_title = unquote(href[len("/wiki/"):]).replace("_", " ")
        party_name = clean(cells[-1].get_text())
        rows.append({
            "constituency": constituency,
            "parish": parish_for(constituency),
            "name": clean(mp_cell.get_text()),
            "party": party_name,
            "party_abbr": PARTIES.get(party_name),
            "wikipedia_title": wiki_title,
        })
    return rows


def validate(rows: list[dict]) -> list[str]:
    problems = []
    if len(rows) != SEATS:
        problems.append(f"expected {SEATS} MPs, found {len(rows)}")
    names = [r["constituency"] for r in rows]
    if len(set(names)) != len(names):
        problems.append("duplicate constituencies")
    for r in rows:
        if not r["name"]:
            problems.append(f"{r['constituency']}: empty MP name")
        if r["party_abbr"] is None:
            problems.append(f"{r['constituency']}: unknown party '{r['party']}'")
        if r["parish"] is None:
            problems.append(f"{r['constituency']}: can't work out parish")
    return problems


def fetch_parliament(n: int) -> tuple[list[dict], int] | None:
    j = get_json(API, action="parse", page=page_title(n), prop="text|revid",
                 format="json", formatversion=2, redirects=1)
    if "error" in j:
        return None
    return parse_table(j["parse"]["text"]), j["parse"]["revid"]


def add_wikidata_ids(rows: list[dict]) -> None:
    titles = [r["wikipedia_title"] for r in rows if r["wikipedia_title"]]
    ids: dict[str, str | None] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        q = get_json(API, action="query", prop="pageprops", ppprop="wikibase_item",
                     redirects=1, format="json", formatversion=2, titles="|".join(chunk))["query"]
        norm = {x["from"]: x["to"] for x in q.get("normalized", [])}
        redir = {x["from"]: x["to"] for x in q.get("redirects", [])}
        by_title = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in q["pages"]}
        for t in chunk:
            key = redir.get(norm.get(t, t), norm.get(t, t))
            ids[t] = by_title.get(key)
    for r in rows:
        r["wikidata_id"] = ids.get(r["wikipedia_title"]) if r["wikipedia_title"] else None


def main() -> None:
    parliament = CURRENT_PARLIAMENT
    # After an election, a new "Nth Parliament" article appears. Switch once it's complete.
    nxt = fetch_parliament(parliament + 1)
    if nxt and not validate(nxt[0]):
        print(f"::notice::Switching to the {page_title(parliament + 1)}")
        parliament += 1
        rows, revid = nxt
    else:
        cur = fetch_parliament(parliament)
        if cur is None:
            fail(f"Wikipedia page '{page_title(parliament)}' not found")
        rows, revid = cur

    problems = validate(rows)
    if problems:
        fail("MP data failed checks: " + "; ".join(problems))

    add_wikidata_ids(rows)
    rows.sort(key=lambda r: r["constituency"])

    write_if_changed("mps", rows, {
        "title": "Members of the House of Representatives of Jamaica",
        "parliament": parliament,
        "seats": SEATS,
        "source": f"https://en.wikipedia.org/wiki/{page_title(parliament).replace(' ', '_')}",
        "source_revision": revid,
        "license": "CC BY-SA 4.0 (Wikipedia)",
    })


if __name__ == "__main__":
    main()
