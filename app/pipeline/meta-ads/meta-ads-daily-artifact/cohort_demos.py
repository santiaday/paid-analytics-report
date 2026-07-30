#!/usr/bin/env python3
"""
Build the cohort demo override for the Meta Ads DAILY / WEEKLY / MONTHLY reports.

Meta's demo basis differs from Google/Microsoft because the Brain's
`gtm-paid-media-daily-cohort` cube carries ONLY google-ads + bing (verified
2026-06-11). The Meta Brain source is the marketing-attribution funnel instead:
metric `ma-funnel-daily` filtered to `current_source = [fb, ig]` — session-date
grain, LAST-TOUCH attribution, demo column `DEMO_SCHEDULED_TOTAL` (PostHog
in-session demos + SFDC same-day-MQL'd demos). It has NO campaign column; the
campaign split comes from one pull per campaign via the `current_campaign` filter.

So this script accepts TWO raw shapes (the `origin` field decides):

1. origin="brain" — `cohort_raw_brain.json`, ASSEMBLED BY THE ROUTINE from the
   per-campaign ma-funnel-daily pulls (rows come back inline, not as files):
     {"origin": "brain",
      "window": ["YYYY-MM-DD", "YYYY-MM-DD"],
      "all":       [{"DT": "YYYY-MM-DD", "DEMO_SCHEDULED_TOTAL": n}, ...],   # no campaign filter
      "campaigns": {"Prospecting_V3": [rows...], "Retargeting_Funnel_Steps": [rows...]}}
   Campaign keys = the EXACT Meta campaign names (the current_campaign filter
   values). Unattributed = the "all" series minus the named campaigns' sum.

2. origin="salesforce" — `cohort_raw_sf.json` written by sf_cohort.js (Brain-row
   shape, SF Day-0 cohort CDF1, UTM fb/ig, keyed by Lead.campaign_ID__c = the
   Meta campaign id):
     {"origin": "salesforce", "result": "{\\"rows\\": [{\\"DT\\":.., \\"SOURCE\\": \\"meta\\",
      \\"CAMPAIGN_ID\\":.., \\"DEMO_SCHEDULED_AT_DAY0\\": n}, ...]}"}
   Ids are mapped to names via spend_cur.json (daily) / camp_cur.json (weekly/monthly).

⚠️ Basis difference (verified 2026-06-11): SF Day-0 is FIRST-CLICK, so it
undercounts Retargeting ~2x vs the Brain's last-touch (May: SF 23 vs Brain 51)
while Prospecting matches near-exactly (145 vs 148); SF total runs ~3-13% below
Brain. Documented, not a bug — the report pill names the basis used.

Usage:
    python3 cohort_demos.py <RAW_FILE> daily   <ANCHOR>     # YYYY-MM-DD (reads spend_cur.json)
    python3 cohort_demos.py <RAW_FILE> weekly  <WEEK_END>   # YYYY-MM-DD (the Sunday, Mon–Sun weeks)
    python3 cohort_demos.py <RAW_FILE> monthly <MONTH>      # YYYY-MM

Writes data/<dir>/cohort_demos.json — the same contract the Google/Microsoft
artifacts use, so build.py / report_common.py consume it unchanged.
"""
import json, sys
from datetime import date, datetime, timedelta
from collections import defaultdict

N = 12               # buckets (matches build_weekly.N_BUCKETS / build_monthly.N_BUCKETS)
DAILY_WINDOW = 30    # days in the daily report's "Clicks & Demos per Day" trend (build.CLICKS_DAYS)
SOURCE = "meta"      # tag sf_cohort.js stamps on its Brain-shaped rows
BRAIN_COL = "DEMO_SCHEDULED_TOTAL"      # ma-funnel-daily demo column (brain shape)
SF_COL = "DEMO_SCHEDULED_AT_DAY0"       # Day-0 column (salesforce shape, Brain-row format)


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def weekly_grid(week_end):
    """12 Monday–Sunday weekly buckets ending the week that ends on WEEK_END (a Sunday)."""
    we = datetime.strptime(week_end, "%Y-%m-%d").date()
    ws = we - timedelta(days=6)                       # Monday
    series_start = ws - timedelta(days=7 * (N - 1))
    buckets = [series_start + timedelta(days=7 * i) for i in range(N)]
    labels = ["%d/%d" % (b.month, b.day) for b in buckets]

    def idx_of(d):
        wk = d - timedelta(days=d.weekday())          # Monday anchoring the row's week
        i = (wk - series_start).days // 7
        return i if 0 <= i < N else None
    return labels, idx_of, buckets[-1]


def monthly_grid(month):
    """12 calendar-month buckets ending with MONTH (YYYY-MM)."""
    ms = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    series_start = add_months(ms, -(N - 1))
    buckets = [add_months(series_start, i) for i in range(N)]
    ss_key = series_start.year * 12 + series_start.month
    labels = []
    for i, b in enumerate(buckets):
        lbl = b.strftime("%b")
        if b.month == 1 or i == 0:                    # disambiguate year boundaries
            lbl += " ’" + b.strftime("%y")
        labels.append(lbl)

    def idx_of(d):
        i = (d.year * 12 + d.month) - ss_key
        return i if 0 <= i < N else None
    return labels, idx_of, buckets[-1]


def _toint(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def iter_demo_rows(raw, name_by_id):
    """Yield (date_str, campaign_name_or_None, demos) from either raw shape.
    campaign_name None = unattributed."""
    origin = raw.get("origin", "brain")
    if origin == "salesforce":
        rows = json.loads(raw["result"])["rows"]
        if rows and SF_COL not in rows[0]:
            sys.exit("ERROR: salesforce raw file has no %s — cols=%s" % (SF_COL, list(rows[0].keys())))
        for r in rows:
            if r.get("SOURCE") != SOURCE:
                continue
            d0 = r.get(SF_COL) or 0
            if not d0:
                continue
            yield str(r["DT"])[:10], name_by_id.get(_toint(r.get("CAMPAIGN_ID"))), float(d0)
        return
    # brain shape: per-campaign row lists keyed by NAME + an "all" list for unattributed
    camps = raw.get("campaigns") or {}
    if not camps:
        sys.exit("ERROR: brain raw file has no 'campaigns' dict — see this script's docstring "
                 "for the shape the routine must assemble.")
    probe = next((rows[0] for rows in camps.values() if rows), None)
    if probe is not None and BRAIN_COL not in probe:
        sys.exit("ERROR: brain raw rows have no %s — wrong metric? cols=%s" % (BRAIN_COL, list(probe.keys())))
    named_by_day = defaultdict(float)
    for cname, rows in camps.items():
        for r in rows:
            d0 = r.get(BRAIN_COL) or 0
            if not d0:
                continue
            ds = str(r["DT"])[:10]
            named_by_day[ds] += float(d0)
            yield ds, cname, float(d0)
    # unattributed = the unfiltered "all" pull minus the named campaigns, per day
    for r in (raw.get("all") or []):
        d0 = float(r.get(BRAIN_COL) or 0)
        ds = str(r["DT"])[:10]
        rem = d0 - named_by_day.get(ds, 0.0)
        if rem > 0.004:
            yield ds, None, rem


def _meta(origin, period, key_extra):
    src = ("Salesforce Day-0 cohort (CDF1, Lead.campaign_ID__c, UTM fb/ig) via sf_cohort.js"
           if origin == "salesforce" else
           "DoorLoop Brain MCP (53a5aee5) - marketing-attribution ma-funnel-daily "
           "(current_source=fb,ig, last-touch, DEMO_SCHEDULED_TOTAL)")
    out = {
        "basis": "day0_cohort" if origin == "salesforce" else "session_last_touch",
        "data_source": origin,
        "metric": "ma-funnel-daily" if origin != "salesforce" else "sf-day0-cohort",
        "column": SF_COL if origin == "salesforce" else BRAIN_COL,
        "source": src,
        "brain_source": SOURCE,
        "key": "campaign_id" if origin == "salesforce" else "campaign_name",
        "period": period,
        "pulled": datetime.now().strftime("%Y-%m-%d"),
    }
    out.update(key_extra)
    return out


def main_daily(raw_file, anchor_str, root="."):
    """Daily report cohort: demos for ANCHOR + the prior weekday (ANCHOR−7), plus a
    30-day daily series for the Clicks & Demos chart. Reads spend_cur.json for the
    campaign id→name map (needed for the salesforce shape)."""
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d").date()
    prior = anchor - timedelta(days=7)
    a_iso, p_iso = anchor.isoformat(), prior.isoformat()
    win_set = {(anchor - timedelta(days=i)).isoformat() for i in range(DAILY_WINDOW)}
    data_dir = "%s/data/%s" % (root, anchor_str)

    name_by_id = {int(r["campaign.id"]): r["campaign.name"]
                  for r in json.load(open("%s/spend_cur.json" % data_dir))
                  if r.get("campaign.id") is not None}

    raw = json.load(open(raw_file))
    origin = raw.get("origin", "brain")
    cur, pri = defaultdict(float), defaultdict(float)
    series = defaultdict(lambda: defaultdict(float))
    unattr_cur = unattr_pri = 0.0
    for ds, name, d0 in iter_demo_rows(raw, name_by_id):
        if ds == a_iso:
            if name: cur[name] += d0
            else:    unattr_cur += d0
        if ds == p_iso:
            if name: pri[name] += d0
            else:    unattr_pri += d0
        if ds in win_set and name:
            series[name][ds] += d0

    rnd = lambda v: round(v, 2)
    out = _meta(origin, "daily", {
        "anchor": anchor_str,
        "prior": p_iso,
        "campaign_cur": {n: rnd(v) for n, v in cur.items()},
        "campaign_prior": {n: rnd(v) for n, v in pri.items()},
        "daily_series": {n: {d: rnd(v) for d, v in dm.items()} for n, dm in series.items()},
        "unattributed_cur": rnd(unattr_cur),
        "unattributed_prior": rnd(unattr_pri),
    })
    outpath = "%s/cohort_demos.json" % data_dir
    json.dump(out, open(outpath, "w"), indent=2)
    tot, ptot = sum(out["campaign_cur"].values()), sum(out["campaign_prior"].values())
    print("Wrote", outpath, "(daily %s, origin=%s)" % (anchor_str, origin))
    print("  ANCHOR %s demos by campaign (prior %s):" % (anchor_str, p_iso))
    for n in sorted(out["campaign_cur"], key=lambda k: -out["campaign_cur"][k]):
        print("    %-34s %6.1f   (prior %.1f)" % (n, out["campaign_cur"][n], out["campaign_prior"].get(n, 0.0)))
    print("    %-34s %6.1f   (prior %.1f; +%.1f/%.1f unattributed)" % ("TOTAL", tot, ptot, unattr_cur, unattr_pri))
    return outpath


def main(raw_file, period, key, root="."):
    if period == "daily":
        return main_daily(raw_file, key, root)
    if period == "weekly":
        bucket_labels, idx_of, last_bucket = weekly_grid(key)
        data_dir = "%s/data/weekly-%s" % (root, key)
    elif period == "monthly":
        bucket_labels, idx_of, last_bucket = monthly_grid(key)
        data_dir = "%s/data/monthly-%s" % (root, key)
    else:
        sys.exit("period must be 'daily', 'weekly' or 'monthly', got %r" % period)

    name_by_id = {int(r["campaign.id"]): r["campaign.name"]
                  for r in json.load(open("%s/camp_cur.json" % data_dir))
                  if r.get("campaign.id") is not None}

    raw = json.load(open(raw_file))
    origin = raw.get("origin", "brain")
    series = defaultdict(lambda: [0.0] * N)
    unattributed = [0.0] * N
    for ds, name, d0 in iter_demo_rows(raw, name_by_id):
        i = idx_of(date.fromisoformat(ds))
        if i is None:
            continue
        if name:
            series[name][i] += d0
        else:
            unattributed[i] += d0

    series = {n: [round(v, 2) for v in arr] for n, arr in series.items()}
    unattributed = [round(v, 2) for v in unattributed]
    out = _meta(origin, period, {
        "period_key": key,
        "bucket_labels": bucket_labels,
        "series": series,
        "unattributed_series": unattributed,
        "campaign_cur": {n: arr[N - 1] for n, arr in series.items()},
        "campaign_prior": {n: arr[N - 2] for n, arr in series.items()},
    })
    outpath = "%s/cohort_demos.json" % data_dir
    json.dump(out, open(outpath, "w"), indent=2)

    tot = sum(out["campaign_cur"].values())
    print("Wrote", outpath, "(%s %s, origin=%s)" % (period, key, origin))
    print("  Report period (last bucket %s) demos by campaign:" % last_bucket)
    for n in sorted(out["campaign_cur"], key=lambda k: -out["campaign_cur"][k]):
        print("    %-34s %6.1f" % (n, out["campaign_cur"][n]))
    print("    %-34s %6.1f   (+%.1f unattributed)" % ("TOTAL", tot, unattributed[-1]))
    print("  12-bucket All series:", [round(sum(series[n][i] for n in series), 1) for i in range(N)])
    return outpath


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 3:
        sys.exit(__doc__)
    import os
    raw_file, period, key = args[0], args[1], args[2]
    main(raw_file, period, key, root=os.path.dirname(os.path.abspath(__file__)))
