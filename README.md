# 🇯🇲 Jamaica Data API

Open, self-updating data on Jamaica's government and culture, from Di Nerd Apps.

## Endpoints (GitHub Pages)

| Endpoint | What |
|---|---|
| `/v1/index.json` | Every endpoint |
| `/v1/mps.json` | All 63 Members of Parliament |
| `/v1/mps/parish/saint-andrew.json` | MPs by parish |
| `/v1/mps/party/jlp.json` | MPs by party (`jlp`, `pnp`) |
| `/v1/pms.json` | Every Prime Minister term since 1962 |
| `/v1/pms/current.json` | Sitting Prime Minister |
| `/v1/national.json` | Parishes, national symbols, dish, heroes |
| `/v1/elections.json` | Every parliamentary election since 1944 *(after backfill)* |
| `/v1/elections/1980-general.json` | One election, seat by seat, with every candidate's votes |
| `/v1/constituencies/saint-andrew-southern.json` | Every result for one seat over time |

## How it updates itself

```
Every day, 7:17am ET (GitHub Actions)
  ├─ scrapers/mps.py  → Wikipedia "15th Parliament of Jamaica" + Wikidata IDs
  ├─ scrapers/pms.py  → Wikidata + data/overrides/pms.json fixes
  ├─ checks: 63 seats, known parties, exactly 1 sitting PM...
  │     └─ check fails → run goes red, GitHub emails you, nothing publishes
  └─ data changed? → opens a PR with a plain-English summary
                       └─ you merge it (GitHub app on your phone) → API redeploys
```

- **No change, no PR.** Unrelated Wikipedia edits don't trigger anything.
- **After an election**, the MP scraper finds the "16th Parliament of Jamaica" article on its own and switches once it has all 63 seats.
- **Wikidata mistakes** get fixed in `data/overrides/`. Each fix removes itself once Wikidata catches up.
- **`national.json`** is curated by hand because it almost never changes.

## Setup (about 10 minutes)

1. Create a GitHub repo (e.g. `nerdyYawdie/jamaica-data`) and push this folder.
2. **Settings → Pages → Source: GitHub Actions**
3. **Settings → Actions → General → Workflow permissions:** Read and write, plus tick **"Allow GitHub Actions to create and approve pull requests"**
4. **Actions → Update Jamaica data → Run workflow** to test it.
5. Optional: point `api.dinerdapps.com` at Pages with a CNAME.

Run locally with `pip install -r requirements.txt && python -m scrapers.mps && python -m scrapers.pms`. Run tests with `python -m pytest`.

## Election history backfill (one-time)

`scripts/backfill_elections.py` reads the official **Electoral Commission of Jamaica** result PDFs
for all 36 parliamentary elections and by-elections since 1944. Gemini reads each page, including
the scanned 1944–1997 reports. Every seat is then checked against the ECJ's own printed totals:

- `verified`: the candidates' votes add up to the printed total (or the printed turnout % for old reports)
- `check`: the numbers don't add up. Compare with the PDF page in `source_page`

To run it: add the `GEMINI_API_KEY` secret, then go to **Actions → Backfill election history → Run workflow**.
It takes about 30–60 minutes on the free tier and opens a PR. If it stops on a rate limit, run it again;
finished elections are kept. To try one election first, enter `2020` in the "only" box.

## Licensing

MP data comes from Wikipedia (**CC BY-SA 4.0**), so it needs attribution and derivatives must use the same license. It's fine to publish and charge for access, but you can't make the data itself proprietary. PM data from Wikidata is CC0. Each file's `meta.source` gives the source.

## Roadmap

- [ ] Senators (21 seats)
- [ ] Cabinet and ministers (JIS / OPM press releases + LLM extraction)
- [ ] BOJ exchange rates, JSE data (reuse the existing JSE scraper)
- [ ] Cloudflare Worker for query filters and API keys; Muse connector
