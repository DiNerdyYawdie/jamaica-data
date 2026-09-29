"""Shared helpers for the Jamaica Data scrapers."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Wikimedia asks every bot to identify itself with a contact.
USER_AGENT = "JamaicaDataAPI/0.1 (https://dinerdapps.com; Di Nerd Apps) python-requests"

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def get_json(url: str, **params) -> dict:
    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fail(msg: str) -> None:
    """Abort the run. The GitHub Action goes red and no PR is opened,
    so a broken scraper can never publish bad data."""
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def write_if_changed(name: str, data, meta: dict) -> bool:
    """Write data/<name>.json only when the *data* changed.

    Metadata like revision IDs changes on every unrelated Wikipedia edit, so
    comparing only `data` keeps pull requests to real changes.
    """
    path = DATA / f"{name}.json"
    if path.exists():
        old = json.loads(path.read_text())
        if old.get("data") == data:
            print(f"{name}: no change")
            return False
    meta = {**meta, "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    path.write_text(json.dumps({"meta": meta, "data": data}, indent=2, ensure_ascii=False) + "\n")
    print(f"{name}: UPDATED")
    return True
