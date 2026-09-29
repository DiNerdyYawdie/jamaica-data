"""One-time backfill: every Jamaican parliamentary election since 1944.

Source: official Electoral Commission of Jamaica (ECJ) result PDFs.
Runs in GitHub Actions (see .github/workflows/backfill-elections.yml).

How it works
  1. Read the ECJ list of parliamentary elections and each event's PDF links.
  2. Render every page of the *summary* PDF to an image and have Gemini read
     it into JSON (constituency, electors, candidates, votes, rejected, total).
     This works for both the typed 2002+ PDFs and the scanned 1944–1997 ones.
  3. Modern summaries (2002+) list party votes but not candidate names. For
     those, names come from the page headers of the detailed PDF (text layer,
     no OCR), matched to parties by Gemini in one text-only call.
  4. Every constituency is checked against the ECJ's own printed totals:
       sum(candidate votes) + rejected == total votes  ->  "verified"
     Anything else is marked "check" for a human to look at.
  5. Writes data/history/elections/<id>.json. Existing files are skipped
     unless --force, so a rate-limited run can simply be re-run.

Usage
  GEMINI_API_KEY=... python scripts/backfill_elections.py            # everything
  GEMINI_API_KEY=... python scripts/backfill_elections.py --only 1980,1983
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "history" / "elections"
CACHE = ROOT / ".cache" / "ecj"

ECJ_LIST = "https://www.ecj.com.jm/elections/election-results/parliamentary-elections/"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
UA = "JamaicaDataAPI/0.1 (https://dinerdapps.com; Di Nerd Apps) python-requests"
GEMINI_MIN_GAP = float(os.environ.get("GEMINI_MIN_GAP", "7"))  # free tier ≈ 10 req/min

http = requests.Session()
http.headers["User-Agent"] = UA


# ---------------------------------------------------------------- ECJ crawl

def slug_for(year: str, date: str, kind: str, url: str) -> str:
    if kind.lower().startswith("general"):
        return f"{year}-general"
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"-?(by-)?election-?|-?\d{4}$", "", tail).strip("-")
    month = re.sub(r"[^a-z]", "", date.lower())[:3]
    return f"{year}-{month}-by-election" + (f"-{tail}" if tail else "")


def list_events() -> list[dict]:
    soup = BeautifulSoup(http.get(ECJ_LIST, timeout=60).text, "html.parser")
    events = []
    for tr in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        link = tr.find("a", href=True)
        if len(cells) < 3 or not link or not re.fullmatch(r"\d{4}", cells[0]):
            continue
        year, date, kind = cells[0], cells[1], cells[2]
        url = urljoin(ECJ_LIST, link["href"])
        events.append({"id": slug_for(year, date, kind, url), "year": int(year),
                       "date": f"{date}, {year}", "type": kind, "results_page": url})
    if len(events) < 30:
        sys.exit(f"::error::Only found {len(events)} ECJ events; the page layout may have changed")
    return events


def pdf_links(page_url: str) -> dict:
    soup = BeautifulSoup(http.get(page_url, timeout=60).text, "html.parser")
    pdfs = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if href.lower().endswith(".pdf") and "/uploads/" in href and not re.search(
                r"Passport|Security", href) and href not in pdfs:
            pdfs.append(href)
    summary = next((p for p in pdfs if "summary" in p.lower()), None)
    detailed = next((p for p in pdfs if p != summary), None)
    return {"summary": summary, "detailed": detailed}


def download(url: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1])
    if not path.exists():
        r = http.get(url, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path.read_bytes()


# ---------------------------------------------------------------- Gemini

_last_call = 0.0
_model: str | None = None


def pick_model(key: str) -> str:
    global _model
    if _model:
        return _model
    if os.environ.get("GEMINI_MODEL"):
        _model = os.environ["GEMINI_MODEL"]
        return _model
    models = http.get(f"{GEMINI}/models", params={"key": key, "pageSize": 200}, timeout=60).json()
    names = [m["name"].split("/", 1)[1] for m in models.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not re.search(
        r"lite|image|tts|live|audio|thinking|exp|preview|8b", n)]

    def version(n: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", n)[:2]) or (0,)

    if not flash:
        sys.exit(f"::error::No Gemini flash model available. Models: {names[:20]}")
    _model = sorted(flash, key=version, reverse=True)[0]
    print(f"Using Gemini model: {_model}")
    return _model


def gemini(parts: list[dict], schema: dict, key: str) -> dict:
    global _last_call
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "responseSchema": schema},
    }
    url = f"{GEMINI}/models/{pick_model(key)}:generateContent"
    for attempt in range(8):
        wait = GEMINI_MIN_GAP - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        r = http.post(url, params={"key": key}, json=body, timeout=300)
        if r.status_code in (429, 500, 502, 503, 504):
            delay = min(300, 15 * 2 ** attempt)
            print(f"  Gemini {r.status_code}, retrying in {delay}s")
            time.sleep(delay)
            continue
        if r.status_code != 200:
            sys.exit(f"::error::Gemini error {r.status_code}: {r.text[:500]}")
        cand = r.json().get("candidates", [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print(f"  Gemini returned invalid JSON (finish={cand.get('finishReason')}), retrying")
    sys.exit("::error::Gemini kept failing; re-run later (finished elections are kept)")


PAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"constituencies": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "number": {"type": "INTEGER", "nullable": True},
            "name": {"type": "STRING"},
            "electors": {"type": "INTEGER", "nullable": True},
            "total_votes": {"type": "INTEGER", "nullable": True,
                            "description": "Total votes cast INCLUDING rejected ballots"},
            "rejected": {"type": "INTEGER", "nullable": True},
            "percent_voted": {"type": "NUMBER", "nullable": True,
                              "description": "Turnout percentage if printed (older reports)"},
            "continues_from_previous_page": {"type": "BOOLEAN"},
            "candidates": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING", "nullable": True,
                             "description": "Candidate name exactly as printed; null if the page only shows parties"},
                    "party": {"type": "STRING", "description": "Party code as printed, e.g. JLP, PNP, NDM, IND, INDA"},
                    "votes": {"type": "INTEGER"},
                },
                "required": ["party", "votes"],
            }},
        },
        "required": ["name", "candidates"],
    }}},
    "required": ["constituencies"],
}

PAGE_PROMPT = """This is one page of an official Electoral Commission of Jamaica summary of
parliamentary election results. Extract every constituency on this page.

Rules:
- The table may be rotated or transposed (constituencies as rows OR columns). Read it carefully.
- Copy numbers exactly as printed. Never estimate or calculate a number that is not printed.
- A party column with no votes for a constituency (blank, 0, "-") means that party had no candidate
  there: leave it out.
- Keep candidate names as printed ("Surname, First names"); use null if names are not shown.
- If a constituency's rows started on the previous page, set continues_from_previous_page=true.
- If the page has no constituency results (cover page, totals only), return an empty list.
"""


DETAILED_PROMPT = """This is an official Electoral Commission of Jamaica box-by-box (polling station)
results report for a parliamentary by-election. Return the FINAL constituency totals only
(use the constituency TOTAL rows, not individual polling stations): each candidate's name, party
code and total votes, plus total electors, total votes cast including rejected ballots, and
rejected ballots. Copy printed numbers exactly; use null for anything not printed.
"""


def read_detailed_only(pdf: bytes, key: str) -> list[dict]:
    if len(pdf) > 18_000_000:
        print("  ::warning::detailed PDF too large to send inline; skipped")
        return []
    res = gemini([{"text": DETAILED_PROMPT},
                  {"inline_data": {"mime_type": "application/pdf",
                                   "data": base64.b64encode(pdf).decode()}}], PAGE_SCHEMA, key)
    return res.get("constituencies", [])


def render_pages(pdf: bytes, dpi: int = 200) -> list[bytes]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf)
    images = []
    for i in range(len(doc)):
        img = doc[i].render(scale=dpi / 72).to_pil()
        if img.width > 3000 or img.height > 3000:
            img.thumbnail((3000, 3000))
        buf = io.BytesIO()
        img.convert("L").save(buf, format="PNG", optimize=True)
        images.append(buf.getvalue())
    return images


def read_summary(pdf: bytes, key: str) -> list[dict]:
    rows: list[dict] = []
    pages = render_pages(pdf)
    for n, png in enumerate(pages, 1):
        print(f"  page {n}/{len(pages)}")
        res = gemini([{"text": PAGE_PROMPT},
                      {"inline_data": {"mime_type": "image/png",
                                       "data": base64.b64encode(png).decode()}}],
                     PAGE_SCHEMA, key)
        for c in res.get("constituencies", []):
            c["source_page"] = n
            prev = rows[-1] if rows else None
            if prev and (c.get("continues_from_previous_page") or
                         norm_name(prev["name"]) == norm_name(c["name"])):
                prev["candidates"] += c["candidates"]
                for f in ("electors", "total_votes", "rejected", "number"):
                    prev[f] = prev.get(f) or c.get(f)
            else:
                rows.append(c)
    return rows


# ------------------------------------------- modern PDFs: names from headers

NAMES_SCHEMA = {
    "type": "OBJECT",
    "properties": {"constituencies": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "candidates": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"name": {"type": "STRING"}, "party": {"type": "STRING"}},
                "required": ["name", "party"]}},
        },
        "required": ["name", "candidates"]}}},
    "required": ["constituencies"],
}

NAMES_PROMPT = """Below are the header lines from each constituency's first page of an official
Jamaican election results report. Each header lists the candidates' surnames and first names,
followed by the party codes in the same left-to-right order (e.g. "MCKENZIE DESMOND WITTER JOSEPH
... JLP PNP" means Desmond McKenzie = JLP, Joseph Witter = PNP).
For each constituency return the candidates with properly capitalised names ("Desmond McKenzie")
and their party code. Only use information in the text.
"""


def header_texts(pdf: bytes) -> dict[str, str]:
    import pdfplumber
    headers: dict[str, str] = {}
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for page in doc.pages:
            text = page.extract_text() or ""
            m = re.search(r"CONSTITUENCY:\s*(.+?)\s*\(\d+\)", text)
            if m and m.group(1) not in headers:
                cut = text.find("ELECTORAL DIVISION")
                headers[m.group(1).strip()] = re.sub(r"\s+", " ", text[:cut if cut > 0 else 1500])
    return headers


def add_names(rows: list[dict], detailed_pdf: bytes, key: str) -> None:
    headers = header_texts(detailed_pdf)
    if not headers:
        print("  detailed PDF has no text layer; names left empty")
        return
    blob = "\n\n".join(f"### {k}\n{v}" for k, v in headers.items())
    res = gemini([{"text": NAMES_PROMPT + "\n\n" + blob}], NAMES_SCHEMA, key)
    by_con = {norm_name(c["name"]): c["candidates"] for c in res.get("constituencies", [])}
    for row in rows:
        names = by_con.get(norm_name(row["name"]), [])
        for cand in row["candidates"]:
            if cand.get("name"):
                continue
            match = [n for n in names if party_key(n["party"]) == party_key(cand["party"])]
            if len(match) == 1:
                cand["name"] = match[0]["name"]


# ---------------------------------------------------------------- cleanup

def norm_name(s: str) -> str:
    s = re.sub(r"^\(?\d+\)?\s*", "", s.upper())
    s = re.sub(r"\bST\.?\s", "SAINT ", s)
    s = s.replace("&", "AND")
    return re.sub(r"[^A-Z ]", "", s).strip()


def party_key(p: str) -> str:
    return re.sub(r"[^A-Z]", "", (p or "").upper())


def pretty_constituency(s: str) -> str:
    s = re.sub(r"^\(?\d+\)?\s*", "", s).strip()
    s = re.sub(r"\bST\.?\s", "Saint ", s, flags=re.I)
    return " ".join(w if w in ("&",) else w.capitalize() for w in s.split())


def finalize(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        cands = [c for c in r["candidates"] if isinstance(c.get("votes"), int) and c["votes"] >= 0]
        # drop exact duplicates from page-boundary merges
        seen, uniq = set(), []
        for c in cands:
            k = (c.get("name"), party_key(c["party"]), c["votes"])
            if k not in seen:
                seen.add(k)
                uniq.append(c)
        uniq.sort(key=lambda c: -c["votes"])
        cand_sum = sum(c["votes"] for c in uniq)
        rejected, total = r.get("rejected"), r.get("total_votes")
        checks = []
        electors, pct = r.get("electors"), r.get("percent_voted")
        if total is None and electors and pct:
            # Older reports print electors and "% voted" instead of a total.
            # % is rounded to 0.1, so allow that much slack.
            implied = electors * pct / 100
            if abs(cand_sum + (rejected or 0) - implied) <= electors * 0.0006 + 2:
                status = "verified"
                checks.append(f"matched printed turnout {pct}% of {electors} electors")
            else:
                status = "check"
                checks.append(f"votes {cand_sum} don't match printed turnout {pct}% of {electors}")
        elif total is None:
            status = "unchecked"
            checks.append("no printed total to check against")
        elif cand_sum + (rejected or 0) == total:
            status = "verified"
        elif cand_sum == total:            # some reports print totals excluding rejects
            status = "verified"
        else:
            status = "check"
            checks.append(f"candidate votes {cand_sum} + rejected {rejected} != total {total}")
        total = total if total is not None else (cand_sum + (rejected or 0) if status == "verified" else None)
        if len(uniq) >= 2 and uniq[0]["votes"] == uniq[1]["votes"]:
            status = "check"
            checks.append("tie for first place")
        if not uniq:
            status = "check"
            checks.append("no candidates read")
        winner = uniq[0] if uniq else None
        out.append({
            "number": r.get("number"),
            "constituency": pretty_constituency(r["name"]),
            "electors": r.get("electors"),
            "total_votes": total,
            "rejected": rejected,
            "turnout_pct": round(100 * total / r["electors"], 2) if total and r.get("electors") else None,
            "winner": {"name": winner.get("name"), "party": winner["party"], "votes": winner["votes"]} if winner else None,
            "margin": uniq[0]["votes"] - uniq[1]["votes"] if len(uniq) >= 2 else None,
            "candidates": uniq,
            "status": status,
            **({"notes": checks} if checks else {}),
            "source_page": r.get("source_page"),
        })
    return out


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated years or event ids")
    ap.add_argument("--force", action="store_true", help="redo elections already saved")
    args = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY") or sys.exit("::error::GEMINI_API_KEY is not set")
    wanted = {x.strip() for x in args.only.split(",") if x.strip()}

    OUT.mkdir(parents=True, exist_ok=True)
    events = list_events()
    report = []
    try:
        run_events(events, wanted, args.force, key, report)
    finally:
        write_report(report)


def run_events(events, wanted, force, key, report) -> None:
    for ev in events:
        if wanted and str(ev["year"]) not in wanted and ev["id"] not in wanted:
            continue
        path = OUT / f"{ev['id']}.json"
        if path.exists() and not force:
            print(f"{ev['id']}: already done, skipping")
            continue
        print(f"{ev['id']}: {ev['results_page']}")
        links = pdf_links(ev["results_page"])
        if links["summary"]:
            rows = read_summary(download(links["summary"]), key)
        elif links["detailed"]:
            rows = read_detailed_only(download(links["detailed"]), key)
        else:
            print("  ::warning::no PDF found")
            report.append((ev["id"], 0, 0, "no PDF"))
            continue
        if links["summary"] and links["detailed"] and rows and not any(
                c.get("name") for r in rows for c in r["candidates"]):
            add_names(rows, download(links["detailed"]), key)
        results = finalize(rows)

        doc = {
            "meta": {
                "title": f"Jamaica {ev['type'].lower()} — {ev['date']}",
                "source": ev["results_page"],
                "source_pdfs": [p for p in links.values() if p],
                "extracted_with": f"Gemini ({pick_model(key)}) from ECJ PDFs, checked against printed totals",
                "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "license": "Public record (Electoral Commission of Jamaica)",
            },
            "election": {k: ev[k] for k in ("id", "year", "date", "type")},
            "summary": {
                "seats": len(results),
                "verified": sum(r["status"] == "verified" for r in results),
                "needs_check": sum(r["status"] != "verified" for r in results),
                "seats_by_party": count_seats(results),
            },
            "results": results,
        }
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        s = doc["summary"]
        report.append((ev["id"], s["seats"], s["verified"], json.dumps(s["seats_by_party"])))
        print(f"  saved {s['seats']} seats, {s['verified']} verified")


def count_seats(results: list[dict]) -> dict:
    seats: dict[str, int] = {}
    for r in results:
        if r["winner"]:
            p = r["winner"]["party"]
            seats[p] = seats.get(p, 0) + 1
    return dict(sorted(seats.items(), key=lambda kv: -kv[1]))


def write_report(report: list[tuple]) -> None:
    lines = ["## Election history backfill", "",
             "| Election | Seats read | Verified | Seats by party |", "|---|---|---|---|"]
    for eid, seats, ver, parties in report:
        flag = "✅" if seats and ver == seats else "⚠️"
        lines.append(f"| {eid} | {seats} | {flag} {ver} | {parties} |")
    lines += ["", "**Verified** = the candidates' votes add up to the ECJ's printed total for that seat.",
              "Seats marked `\"status\": \"check\"` need a quick look against the linked PDF (`source_page`).",
              "Compare seat counts with the known result for each election before merging."]
    text = "\n".join(lines)
    print(text)
    (ROOT / ".cache").mkdir(exist_ok=True)
    (ROOT / ".cache" / "backfill_report.md").write_text(text)


if __name__ == "__main__":
    main()
