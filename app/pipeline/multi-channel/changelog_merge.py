#!/usr/bin/env python3
"""changelog_merge.py — maintain the changelog RAW files incrementally.

Why: the daily changelog pull used to re-fetch the FULL 12-month Notion + PostHog history
every day (~50k tokens) just to rebuild the identical store. Instead we fetch only the last
~7 days and UPSERT into the raw accumulator files, then let changelog_store.py rebuild exactly
as before. changelog_store.py and its parsers are UNTOUCHED — this only maintains the raw arrays.

Modes (called by backfill/changelog_pull.md):
  full-due <anchor>                 -> prints FETCH/SKIP (weekly full-reconcile gate, 7 days)
  <src> upsert  <query_result.json> -> add/replace recent rows in raw/<src>.json by key
  <src> replace <query_result.json> -> full-replace raw/<src>.json (weekly reconcile)
  stamp-full <anchor>               -> record that a full reconcile ran today
  selftest                          -> runnable self-check (no network, no real files)

src ∈ {notion, posthog}. Notion rows are mapped from the SQL-mode shape to the exact 7-key
shape parse_notion reads (url normalized to the /p/ form); PostHog rows are kept as-is.
Upsert key: notion=url, posthog=id. Daily upsert never drops rows; the weekly `replace`
(current live set) reconciles edits/status-flips/deletions and re-bounds the file.

ponytail: upsert-only daily means a pure text-edit or deletion of an OLD row lags until the
weekly `replace`. Upgrade path if that ever matters: shorten FULL_MAX_AGE or replace daily.
"""
import json, os, sys
from datetime import datetime

HERE  = os.path.dirname(os.path.abspath(__file__))
RAW   = os.path.join(HERE, "backfill", "changelog_raw")
STAMP = os.path.join(RAW, ".last_full_pull")
FULL_MAX_AGE = 7   # days; force a full Notion+PostHog reconcile at most weekly

# the ONLY keys parse_notion reads — keep the raw file in exactly the view-pull shape.
# NOTE (2026-07-13): the Change Log DB dropped its "Date Implimented" property — the only date
# column left is "Date Submitted", so that is now the effective date. (verified against the live
# 126eb00b… schema.)
NOTION_KEYS = ["Platform", "date:Date Submitted:start",
               "Description (Reason + Anticipated Impact)", "Status", "Title", "url"]
KEYFIELD = {"notion": "url", "posthog": "id"}


def _norm_notion(rows):
    """SQL-mode row -> view-pull shape: keep the 7 parse_notion keys, drop null/empty date
    keys (the view omits them), normalize url to https://app.notion.com/p/<id>."""
    out = []
    for r in rows:
        e = {k: r[k] for k in NOTION_KEYS if k in r and r[k] not in (None, "")}
        u = (r.get("url") or "").rstrip("/")
        nid = u.rsplit("/", 1)[-1] if u else ""
        if nid:
            e["url"] = "https://app.notion.com/p/" + nid
        out.append(e)
    return out


def _rows_from(infile):
    """Accept a saved query result that is a bare array OR {'results':[...]} / {'result':[...]}."""
    data = json.load(open(infile))
    if isinstance(data, dict):
        return data.get("results") or data.get("result") or []
    return data or []


def _load(path):
    try:
        return json.load(open(path))
    except Exception:
        return []


def full_due(anchor):
    if not os.path.exists(STAMP):
        return "FETCH"
    try:
        fetched = open(STAMP).read().strip()
        age = (datetime.strptime(anchor, "%Y-%m-%d") - datetime.strptime(fetched, "%Y-%m-%d")).days
        return "FETCH" if (age >= FULL_MAX_AGE or age < 0) else "SKIP"
    except Exception:
        return "FETCH"


def merge(src, mode, rows):
    rows = _norm_notion(rows) if src == "notion" else rows
    dest = os.path.join(RAW, f"{src}.json")
    if mode == "replace":
        json.dump(rows, open(dest, "w"), ensure_ascii=False)
        return f"{src}: replaced -> {len(rows)} rows"
    key = KEYFIELD[src]
    existing = _load(dest)
    idx = {r.get(key): i for i, r in enumerate(existing) if r.get(key)}
    added = updated = 0
    for r in rows:
        k = r.get(key)
        if k in idx:
            existing[idx[k]] = r; updated += 1
        else:
            existing.append(r); idx[k] = len(existing) - 1; added += 1
    json.dump(existing, open(dest, "w"), ensure_ascii=False)
    return f"{src}: upserted (+{added} new, {updated} updated) -> {len(existing)} rows"


def selftest():
    import tempfile
    global RAW
    d = tempfile.mkdtemp(); RAW = d
    # notion mapping + upsert
    sql = [{"url": "https://app.notion.com/abc123", "Platform": "[\"Google Ads\"]",
            "Status": "Implimented", "Title": "T1",
            "date:Date Submitted:start": "2026-06-22", "date:Date Submitted:end": None,
            "Description (Reason + Anticipated Impact)": "x", "createdTime": "2026-06-22 00:00Z"}]
    assert "+1 new" in merge("notion", "upsert", sql)
    saved = json.load(open(os.path.join(d, "notion.json")))[0]
    assert set(saved.keys()) <= set(NOTION_KEYS), saved.keys()           # only the kept keys
    assert "date:Date Submitted:end" not in saved                        # extras dropped
    assert saved["url"] == "https://app.notion.com/p/abc123", saved["url"]  # /p/ normalized
    # re-upsert same url with a changed Status -> updated, not duplicated
    sql[0]["Status"] = "Implimenting"
    assert "0 new, 1 updated" in merge("notion", "upsert", sql)
    assert len(json.load(open(os.path.join(d, "notion.json")))) == 1
    # posthog upsert by id (kept verbatim)
    ph = [{"id": 9, "content": "deploy", "date_marker": "2026-06-22", "deleted": False}]
    assert "+1 new" in merge("posthog", "upsert", ph)
    # replace shrinks to the given set
    assert "replaced -> 1" in merge("notion", "replace", sql)
    print("selftest OK")


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    if a[0] == "selftest":
        selftest(); return
    if a[0] == "full-due":
        print(full_due(a[1])); return
    if a[0] == "stamp-full":
        open(STAMP, "w").write(a[1]); print(f"stamped full reconcile {a[1]}"); return
    src, mode, infile = a[0], a[1], a[2]
    print(merge(src, mode, _rows_from(infile)))


if __name__ == "__main__":
    main()
