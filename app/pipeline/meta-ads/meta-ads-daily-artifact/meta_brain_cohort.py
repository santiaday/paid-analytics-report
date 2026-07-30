#!/usr/bin/env python3
"""meta_brain_cohort.py — assemble data/<ANCHOR>/cohort_raw_brain.json from the raw
ma-funnel-daily Brain pulls, so the routine NEVER hand-extracts DT+DEMO_SCHEDULED_TOTAL or
hand-zips per-campaign series (that ad-hoc step shipped a bad consistency-assert on 2026-06-24).

The Brain (ma-funnel-daily) has no non-MCP API, so the PULLS still happen in the main session
via brain_run_metric_query (one ALL pull + one per ACTIVE campaign, current_source ["fb","ig"]).
But instead of transforming them by hand, dump each raw result verbatim to a file and run THIS
script — the transformation is now vetted code.

Usage:
  python3 meta_brain_cohort.py <ANCHOR> --all <all_file> \
      --camp "<Campaign Name>" <camp_file> [--camp "<Name2>" <file2> ...]

Each <file> may be ANY of: the raw {"result":"<json-string>"} wrapper that brain_run_metric_query
returns, the already-parsed {"rows":[...]}, or a bare [...] list of rows — all handled.

Writes data/<ANCHOR>/cohort_raw_brain.json (origin=brain) in the exact shape cohort_demos.py reads:
  {"origin":"brain","window":[<ANCHOR-29d>,<ANCHOR>],
   "all":[{"DT":"YYYY-MM-DD","DEMO_SCHEDULED_TOTAL":n}, ...],
   "campaigns":{"<name>":[ ...same row shape... ], ...}}
Read-only w.r.t. every external system; only writes the one local JSON file.
"""
import sys, os, json, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))

def rows_of(path):
    """Extract the row list from any of the 3 accepted shapes."""
    d = json.load(open(path))
    if isinstance(d, list):
        rows = d
    elif isinstance(d, dict) and "rows" in d:
        rows = d["rows"]
    elif isinstance(d, dict) and "result" in d:
        r = d["result"]
        r = json.loads(r) if isinstance(r, str) else r
        rows = r["rows"] if isinstance(r, dict) else r
    else:
        raise SystemExit(f"ERROR: {path} is not a recognized ma-funnel result "
                         "(expected a list, {'rows':[...]}, or {'result': '<json>'}).")
    out = []
    for row in rows:
        dt = str(row.get("DT", ""))[:10]
        if not dt:
            continue
        v = row.get("DEMO_SCHEDULED_TOTAL", 0) or 0
        v = int(v) if float(v).is_integer() else float(v)
        out.append({"DT": dt, "DEMO_SCHEDULED_TOTAL": v})
    out.sort(key=lambda r: r["DT"])
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("anchor")
    ap.add_argument("--all", required=True, dest="all_file")
    ap.add_argument("--camp", nargs=2, action="append", default=[], metavar=("NAME", "FILE"))
    a = ap.parse_args()
    anchor = datetime.date.fromisoformat(a.anchor)
    a29 = (anchor - datetime.timedelta(days=29)).isoformat()

    out = {"origin": "brain", "window": [a29, a.anchor],
           "all": rows_of(a.all_file),
           "campaigns": {name: rows_of(f) for name, f in a.camp}}

    outpath = os.path.join(HERE, "data", a.anchor, "cohort_raw_brain.json")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    json.dump(out, open(outpath, "w"))

    def anchor_val(rows):
        return next((r["DEMO_SCHEDULED_TOTAL"] for r in rows if r["DT"] == a.anchor), 0)
    print(f"Wrote {outpath}")
    print(f"  window {a29} → {a.anchor}  |  ALL anchor demos = {anchor_val(out['all'])}")
    for name, rows in out["campaigns"].items():
        print(f"    {name:<34} anchor={anchor_val(rows)}  ({len(rows)} days)")
    # NOTE: ALL >= sum(active campaigns) is normal — ALL includes now-inactive campaigns from
    # earlier in the 30-day window, so do NOT assert equality (that false-failed on 2026-06-24).

if __name__ == "__main__":
    main()
