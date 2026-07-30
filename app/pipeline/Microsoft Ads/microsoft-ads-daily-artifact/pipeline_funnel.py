#!/usr/bin/env python3
"""Build data/<ANCHOR>/pipeline_30d.json — the booked-on-day Opportunities / Accounts / ARR
series for the daily report's Metric Grid (and current-view chips).

Source = the Brain **event-basis** funnel `gtm-paid-media-daily-funnel` (NOT the cohort cube):
each count is booked to the day the EVENT happened — opps by Opportunity-created date,
accounts + ARR by their booked (Stripe first-invoice) date — exactly the basis the user asked
for (no retroactive cohort growth). ARR = new_arr_after_coupons (what we actually received).
Microsoft/Bing only (SOURCE=='bing'); campaign attribution is by CAMPAIGN_ID = the Microsoft Ads campaign id (same join
cohort_demos.py uses). Opps/accounts/ARR whose CAMPAIGN_ID is None or maps to no active Google
campaign are bucketed as "unattributed" and listed in `mismatch_ids` so the report can FLAG them.

Usage:  python3 pipeline_funnel.py <raw_brain_file> <ANCHOR>            # Brain (default)
        python3 pipeline_funnel.py <raw_sf_file>    <ANCHOR> salesforce # SF fallback (same row shape)

Brain raw file = the tool-results .txt from brain_run_metric_query (gtm-paid-media-daily-funnel,
30-day window ending ANCHOR). Reads data/<ANCHOR>/spend_cur.json + spend_prior.json for the
campaign id→name map. Writes data/<ANCHOR>/pipeline_30d.json. Read-only on Salesforce.
"""
import json, os, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
N_DAYS = 30


def load_rows(path):
    raw = json.load(open(path))
    # Brain tool-results shape: {"result": "<json-string>"}; SF fallback may already be rows.
    if isinstance(raw, dict) and "result" in raw:
        inner = raw["result"]
        data = json.loads(inner) if isinstance(inner, str) else inner
        if data.get("truncated"):
            sys.exit("❌ Brain result truncated — re-pull with a higher limit.")
        return data["rows"]
    if isinstance(raw, dict) and "rows" in raw:
        return raw["rows"]
    return raw


def id2name(anchor):
    m = {}
    for fn in ("spend_cur.json", "spend_prior.json"):
        p = os.path.join(ROOT, "data", anchor, fn)
        if not os.path.exists(p):
            continue
        for r in json.load(open(p)):
            cid, nm = r.get("campaign.id"), r.get("campaign.name")
            if cid and nm:
                m[str(cid)] = nm
    return m


def build(raw_path, anchor, source="brain"):
    end = datetime.strptime(anchor, "%Y-%m-%d").date()
    days = [end - timedelta(days=N_DAYS - 1 - i) for i in range(N_DAYS)]
    didx = {d.isoformat(): i for i, d in enumerate(days)}
    names = id2name(anchor)

    rows = load_rows(raw_path)
    camps = {}            # name -> {"opps":[..],"accts":[..],"arr":[..]}
    unatt = {"opps": [0.0] * N_DAYS, "accts": [0.0] * N_DAYS, "arr": [0.0] * N_DAYS}
    mism = {}             # campaign_id -> {label, opps, accts, arr}

    def cell(name):
        return camps.setdefault(name, {"opps": [0.0] * N_DAYS, "accts": [0.0] * N_DAYS, "arr": [0.0] * N_DAYS})

    for r in rows:
        if (r.get("SOURCE") or "").lower() not in ("bing", ""):   # Microsoft/Bing only (HARD rule #5)
            continue
        i = didx.get(str(r.get("DT") or "")[:10])
        if i is None:
            continue
        o = float(r.get("OPPS") or 0); a = float(r.get("ACCOUNTS") or 0); ar = float(r.get("NEW_ARR") or 0)
        if not (o or a or ar):
            continue
        cid = r.get("CAMPAIGN_ID")
        nm = names.get(str(cid)) if cid is not None else None
        if nm:
            c = cell(nm)
            c["opps"][i] += o; c["accts"][i] += a; c["arr"][i] += ar
        else:
            unatt["opps"][i] += o; unatt["accts"][i] += a; unatt["arr"][i] += ar
            k = str(cid)
            d = mism.setdefault(k, {"label": r.get("CAMPAIGN") or "(none)", "opps": 0.0, "accts": 0.0, "arr": 0.0})
            d["opps"] += o; d["accts"] += a; d["arr"] += ar

    out = {
        "metric_source": source,
        "anchor": anchor,
        "days": [d.isoformat() for d in days],
        "campaigns": camps,
        "unattributed": unatt,
        "mismatch_ids": [dict(id=k, **v) for k, v in sorted(mism.items(), key=lambda kv: -kv[1]["opps"])],
        "totals": {
            "opps": round(sum(sum(c["opps"]) for c in camps.values()), 2),
            "accts": round(sum(sum(c["accts"]) for c in camps.values()), 2),
            "arr": round(sum(sum(c["arr"]) for c in camps.values()), 2),
            "unattr_opps": round(sum(unatt["opps"]), 2),
            "unattr_accts": round(sum(unatt["accts"]), 2),
            "unattr_arr": round(sum(unatt["arr"]), 2),
        },
    }
    outp = os.path.join(ROOT, "data", anchor, "pipeline_30d.json")
    with open(outp, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    t = out["totals"]
    print("Wrote", outp)
    print(f"  source={source}  campaigns={len(camps)}  "
          f"attributed: opps={t['opps']:.0f} accts={t['accts']:.0f} arr=${t['arr']:,.0f}")
    print(f"  unattributed: opps={t['unattr_opps']:.0f} accts={t['unattr_accts']:.0f} "
          f"arr=${t['unattr_arr']:,.0f}  ({len(mism)} unmatched campaign id(s))")
    for m in out["mismatch_ids"][:8]:
        print(f"       ⚠ id={m['id']} {m['label']!r}: opps={m['opps']:.0f} accts={m['accts']:.0f} arr=${m['arr']:,.0f}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: pipeline_funnel.py <raw_brain_or_sf_file> <ANCHOR> [brain|salesforce]")
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "brain")
