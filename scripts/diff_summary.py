"""Write a plain-English summary of what changed, for the pull request body.

Usage: python scripts/diff_summary.py > pr_body.md
Compares each data/*.json file with the version in the last commit.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = {"mps": "constituency", "pms": ("wikidata_id", "start")}


def old_version(path: str):
    try:
        out = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out).get("data", [])
    except subprocess.CalledProcessError:
        return []


def key_of(row, key):
    return tuple(row[k] for k in key) if isinstance(key, tuple) else row[key]


def label(row) -> str:
    return row.get("constituency") or row.get("name", "?")


def summarize(name: str, key) -> list[str]:
    path = f"data/{name}.json"
    new = json.loads((ROOT / path).read_text())["data"]
    old = old_version(path)
    before = {key_of(r, key): r for r in old}
    after = {key_of(r, key): r for r in new}
    lines = []
    for k in after.keys() - before.keys():
        lines.append(f"- ➕ **{label(after[k])}**: {after[k].get('name')} ({after[k].get('party_abbr') or ', '.join(after[k].get('parties', []))})")
    for k in before.keys() - after.keys():
        lines.append(f"- ➖ **{label(before[k])}** removed ({before[k].get('name')})")
    for k in after.keys() & before.keys():
        a, b = before[k], after[k]
        changed = [f"{f}: `{a.get(f)}` → `{b.get(f)}`" for f in sorted(set(a) | set(b)) if a.get(f) != b.get(f)]
        if changed:
            lines.append(f"- ✏️ **{label(b)}**: " + "; ".join(changed))
    return lines


def main() -> None:
    body = ["## Jamaica Data: changes detected", ""]
    for name, key in KEYS.items():
        lines = summarize(name, key)
        if lines:
            body += [f"### {name}", *sorted(lines), ""]
    body += ["---", "Check the source links in each file's `meta` block, then **merge to publish** or close to reject."]
    print("\n".join(body))


if __name__ == "__main__":
    main()
