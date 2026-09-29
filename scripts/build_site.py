"""Build the static API into ./site for GitHub Pages.

Endpoints (all JSON):
  /v1/index.json                    list of endpoints
  /v1/mps.json                      all 63 MPs
  /v1/mps/parish/<slug>.json        MPs for one parish, e.g. saint-andrew
  /v1/mps/party/<abbr>.json         MPs for one party, e.g. jlp
  /v1/pms.json                      every Prime Minister term
  /v1/pms/current.json              sitting Prime Minister
  /v1/national.json                 national symbols, heroes, dish, etc.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "v1"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def dump(rel: str, obj) -> None:
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def main() -> None:
    shutil.rmtree(ROOT / "site", ignore_errors=True)
    endpoints = []

    for f in sorted(DATA.glob("*.json")):
        doc = json.loads(f.read_text())
        dump(f.name, doc)
        endpoints.append(f"/v1/{f.name}")

    mps = json.loads((DATA / "mps.json").read_text())
    for key, folder in (("parish", "parish"), ("party_abbr", "party")):
        groups: dict[str, list] = {}
        for r in mps["data"]:
            groups.setdefault(slug(r[key]), []).append(r)
        for k, rows in groups.items():
            dump(f"mps/{folder}/{k}.json", {"meta": mps["meta"], "data": rows})
            endpoints.append(f"/v1/mps/{folder}/{k}.json")

    pms = json.loads((DATA / "pms.json").read_text())
    dump("pms/current.json", {"meta": pms["meta"], "data": next(t for t in pms["data"] if t["current"])})
    endpoints.append("/v1/pms/current.json")

    dump("index.json", {
        "name": "Jamaica Data API",
        "by": "Di Nerd Apps",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endpoints": sorted(endpoints),
    })
    (ROOT / "site" / ".nojekyll").touch()
    print(f"Built {len(endpoints) + 1} endpoints into site/")


if __name__ == "__main__":
    main()
