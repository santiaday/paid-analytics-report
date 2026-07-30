#!/usr/bin/env python3
"""quality_store.py — daily snapshot + change-diff of keyword Quality Scores for the
multi-channel report. Parallels changelog_store.py.

Usage:  python3 quality_store.py <ANCHOR YYYY-MM-DD>

Reads the raw daily pulls (read-only, written by the routine):
    backfill/quality_raw/google.json      (backfill/google_quality.py)
    backfill/quality_raw/microsoft.json   (backfill/ms_quality.py)
Updates the local stores (the "saved copy" we diff against each day):
    history/quality_google.json
    history/quality_microsoft.json

WHY a local store + diff (not a fact-table tail): Quality Score is an OUTPUT-ONLY *current*
value — neither Google nor Microsoft returns a historical QS for a past day. So there is NO
backfill: change detection starts on the SECOND run. Each day we snapshot the live values,
diff them against the previous snapshot, record the per-keyword changes (Overall QS 1-10 +
the three components — Expected CTR, Ad Relevance, Landing Page Experience), and overwrite the
"current" snapshot. build.py turns the latest day's changes into Daily-Alert rows (active
keywords only) and embeds the whole store for the collapsed "Quality Scores" browse table.

Store schema (per platform):
  {platform, updated, anchor,
   keywords:      {key: {C,G,KW,MT,QS,CTR,REL,LPX,IMP}},   # the latest live snapshot
   prev_keywords: {key: ...}, prev_date,                   # the snapshot before `anchor`
                                                           # (so a same-day rerun is idempotent)
   daily: [ {date, changes:[{k,c,g,kw,mt,f,from,to,dir,active}]} ]}   # rolling 14 days
  key = "C\x1fG\x1fKW\x1fMT"; f ∈ {qs,ctr,rel,lpx}; component codes AA>AV>BA>?; dir ±1/0.
"""
import json, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "backfill", "quality_raw")
RETAIN = 14
RANK = {"AA": 3, "AV": 2, "BA": 1, "?": 0}
SEP = "\x1f"
PLATS = [("google", "google.json", "quality_google.json"),
         ("microsoft", "microsoft.json", "quality_microsoft.json")]

def keyof(r):
    return SEP.join([r.get("C", ""), r.get("G", ""), r.get("KW", ""), r.get("MT", "")])

def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def diff(base, new):
    """Per-keyword field changes for keys present in BOTH snapshots."""
    out = []
    for k, n in new.items():
        b = base.get(k)
        if not b:
            continue                         # brand-new keyword → baseline, not a change
        for f, comp in (("QS", False), ("CTR", True), ("REL", True), ("LPX", True)):
            ov, nv = b.get(f), n.get(f)
            if ov is None or nv is None or ov == nv:
                continue
            if comp:
                dirn = (RANK.get(nv, 0) > RANK.get(ov, 0)) - (RANK.get(nv, 0) < RANK.get(ov, 0))
            else:
                dirn = (nv > ov) - (nv < ov)
            out.append({"k": k, "c": n["C"], "g": n["G"], "kw": n["KW"], "mt": n["MT"],
                        "f": f.lower(), "from": ov, "to": nv, "dir": dirn,
                        "active": (n.get("IMP", 0) or 0) > 0})
    # drops first (most actionable), then by campaign/keyword
    out.sort(key=lambda c: (c["dir"] >= 0, c["c"], c["g"], c["kw"], c["f"]))
    return out

def main():
    anchor = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    warn = []
    for plat, rawname, storename in PLATS:
        raw = load(os.path.join(RAW, rawname), None)
        store_path = os.path.join(HERE, "history", storename)
        store = load(store_path, {})
        if not raw or not raw.get("rows"):
            warn.append(f"{plat}: no raw pull ({rawname}) — store unchanged (tail stale)")
            continue
        new = {keyof(r): r for r in raw["rows"]}
        # idempotent reruns: if we already stamped `anchor`, diff vs the day-before snapshot
        if store.get("anchor") == anchor:
            base, base_date = store.get("prev_keywords", {}), store.get("prev_date")
        else:
            base, base_date = store.get("keywords", {}), store.get("anchor")
        changes = diff(base, new) if base else []
        daily = [d for d in store.get("daily", []) if d.get("date") != anchor]
        daily.append({"date": anchor, "changes": changes})
        daily.sort(key=lambda d: d["date"])
        daily = daily[-RETAIN:]
        out = {"platform": plat, "updated": anchor, "anchor": anchor,
               "prev_keywords": base, "prev_date": base_date,
               "keywords": new, "daily": daily}
        json.dump(out, open(store_path, "w"), separators=(",", ":"))
        act = sum(1 for c in changes if c["active"])
        base_note = "BASELINE (first snapshot)" if not base else f"vs {base_date}"
        print(f"✅ {plat}: {len(new)} keywords, {len(changes)} changes ({act} active) {base_note} → {storename}")
    for w in warn:
        print("  ⚠", w)

if __name__ == "__main__":
    main()
