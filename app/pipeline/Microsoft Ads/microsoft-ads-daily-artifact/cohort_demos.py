#!/usr/bin/env python3
"""
Build the Brain Day-0 cohort demo override for the Microsoft Ads WEEKLY or
MONTHLY report — the BING side of DoorLoop's Paid Media Optimizer cube.

Reads a raw `gtm-paid-media-daily-cohort` pull from DoorLoop's Brain MCP, keeps
only SOURCE == "bing" (= Microsoft Ads), and aggregates DEMO_SCHEDULED_AT_DAY0
into the report's 12-bucket grid, keyed by CAMPAIGN_ID -> Microsoft campaign
name (so demos line up with spend). Writes data/<period>-<key>/cohort_demos.json,
which report_common.render() auto-detects to swap Microsoft-Ads platform demos
for Brain Day-0 cohort demos (same-day-as-click, apples-to-apples, never matures).

Why the Brain cohort for Bing: Microsoft's own Reporting API undercounts demos
(the offline-import/attribution gap — e.g. the Competitors campaign reports ~0
demos there), and platform conversions mature over ~90 days so recent weeks look
artificially low. The Brain bing cohort fixes both. Bing CAMPAIGN_ID equals the
Microsoft Ads campaign id (verified 2026-06-08); the CAMPAIGN text column is a
messy SFDC label, so we MUST map by CAMPAIGN_ID, not name. See
/opt/mcp/Master Context/Brain Connector/MEMORY.md for the pull procedure + the bing confirmation.

Usage:
    # 1) In the MAIN session (never a subagent — the JSON is large), pull the
    #    window the report needs (covers BOTH google-ads + bing; we filter to bing):
    #      WEEKLY  (12 Mon-Sun weeks):   start = WEEK_END-90d,  end = WEEK_END+1d
    #      MONTHLY (12 calendar months): start = first day of (MONTH-11 months),
    #                                    end   = MONTH_END+1d
    #    brain_run_metric_query(name="gtm-paid-media-daily-cohort",
    #        start_date=.., end_date=.., limit=8000)  # paginate/merge if truncated
    #    -> it auto-saves to a tool-results/*.txt file. Pass that path here:
    python3 cohort_demos.py <RAW_BRAIN_FILE> daily   <ANCHOR>     # YYYY-MM-DD (the report day; reads spend_cur.json)
    python3 cohort_demos.py <RAW_BRAIN_FILE> weekly  <WEEK_END>   # YYYY-MM-DD (the Sunday)
    python3 cohort_demos.py <RAW_BRAIN_FILE> monthly <MONTH>      # YYYY-MM
    # Back-compat: `cohort_demos.py <RAW_BRAIN_FILE> <WEEK_END>` (2 args) == weekly.

RAW_BRAIN_FILE = the {"result": "<json>"} file the Brain pull saved (or a merged
                 file with the same {"result": "<json with rows>"} shape).
"""
import json, sys, os, subprocess
from datetime import date, datetime, timedelta
from collections import defaultdict

N = 12               # buckets (matches build_weekly.N_BUCKETS / build_monthly.N_BUCKETS)
DAILY_WINDOW = 30    # days in the daily report's "Clicks & Demos per Day" trend (build.CLICKS_DAYS)
SOURCE = "bing"      # Microsoft Ads side of the paid-media cube (Google's copy uses "google-ads")


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


def main_daily(raw_file, anchor_str, root="."):
    """Daily report cohort: Day-0 demos for ANCHOR + the prior weekday (ANCHOR−7), plus a
    30-day daily series for the Clicks & Demos chart. Reads spend_cur.json for the campaign
    id→name map (the daily report's per-campaign id source — NOT camp_cur.json)."""
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d").date()
    prior = anchor - timedelta(days=7)
    a_iso, p_iso = anchor.isoformat(), prior.isoformat()
    win_set = {(anchor - timedelta(days=i)).isoformat() for i in range(DAILY_WINDOW)}  # ANCHOR−29..ANCHOR
    data_dir = "%s/data/%s" % (root, anchor_str)

    rep_name_by_id = {int(r["campaign.id"]): r["campaign.name"]
                      for r in json.load(open("%s/spend_cur.json" % data_dir))
                      if r.get("campaign.id") is not None}
    if not rep_name_by_id:
        sys.exit("ERROR: %s/spend_cur.json has no campaign.id values — daily cohort needs them." % data_dir)

    raw = json.load(open(raw_file))
    origin = raw.get("origin", "brain")  # "salesforce" when sf_cohort.js wrote the raw file
    rows = json.loads(raw["result"])["rows"]
    if rows and "DEMO_SCHEDULED_AT_DAY0" not in rows[0]:
        sys.exit("ERROR: raw file has no DEMO_SCHEDULED_AT_DAY0 — wrong metric? cols=%s" % list(rows[0].keys()))

    # GUARD + AUTO-FALLBACK (ported from the Google daily artifact, 2026-06-22): the Brain cube must
    # actually contain the ANCHOR day. The Brain 24h cache / upstream Snowflake load often lags, so a
    # morning pull can return a cube ending at ANCHOR-1 with NO anchor row — silently summing that
    # yields a false "0 demos". A true zero day still has the anchor ROW present (some source logged
    # that date), so "no row for the date at all" = missing data, not a real zero. The Brain
    # refresh=true retry is MCP-only (lives in the routine prompt). If we still arrive with a Brain
    # file lacking the anchor, AUTO-FALL-BACK IN CODE to the real-time Salesforce Day-0 cohort
    # (sf_cohort.js, read-only, bing) — SF always has yesterday. If SF also fails, exit without
    # writing so build.py's absent-file fallback to the Demo_Scheduled platform goal takes over. Only
    # enforced for origin=="brain"; the SF fallback file is per-anchor + real-time (absent anchor = a
    # real zero, written as-is).
    if origin == "brain" and rows and not any(str(r.get("DT"))[:10] == a_iso for r in rows):
        max_dt = max((str(r.get("DT"))[:10] for r in rows), default="(none)")
        sys.stderr.write("WARN: Brain cohort cube has NO row for ANCHOR %s (cube ends at %s) — the "
                         "cube lagged. Auto-falling back to the real-time Salesforce Day-0 cohort "
                         "(sf_cohort.js).\n" % (a_iso, max_dt))
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sf_raw = os.path.join(script_dir, "data", anchor_str, "cohort_raw_sf.json")
        try:
            subprocess.run(["node", os.path.join(script_dir, "sf_cohort.js"), "daily", anchor_str],
                           cwd=script_dir, check=True)
        except Exception as e:
            sys.exit("ERROR: Brain cube lacked ANCHOR %s AND the Salesforce fallback failed (%s). "
                     "NOT writing cohort_demos.json — build.py will fall back to platform demos." % (a_iso, e))
        if not os.path.exists(sf_raw):
            sys.exit("ERROR: Brain cube lacked ANCHOR %s and sf_cohort.js produced no %s. NOT writing "
                     "cohort_demos.json — build.py will fall back to platform demos." % (a_iso, sf_raw))
        sys.stderr.write("OK: Salesforce Day-0 fallback pulled → rebuilding cohort from %s\n" % sf_raw)
        return main_daily(sf_raw, anchor_str, root)

    cur, pri = defaultdict(float), defaultdict(float)
    series = defaultdict(lambda: defaultdict(float))
    unattr_cur = unattr_pri = 0.0
    for r in rows:
        if r.get("SOURCE") != SOURCE:
            continue
        d0 = r.get("DEMO_SCHEDULED_AT_DAY0") or 0
        if not d0:
            continue
        ds = str(r["DT"])[:10]
        name = rep_name_by_id.get(_toint(r.get("CAMPAIGN_ID")))
        if ds == a_iso:
            if name: cur[name] += d0
            else:    unattr_cur += d0
        if ds == p_iso:
            if name: pri[name] += d0
            else:    unattr_pri += d0
        if ds in win_set and name:
            series[name][ds] += d0

    rnd = lambda v: round(v, 2)
    out = {
        "basis": "cohort_day0",
        "data_source": origin,
        "metric": "gtm-paid-media-daily-cohort",
        "column": "DEMO_SCHEDULED_AT_DAY0",
        "source": ("Salesforce Day-0 cohort (CDF1, Lead.campaign_ID__c) via sf_cohort.js (UTM_Source=%s)" % SOURCE)
                  if origin == "salesforce" else
                  "DoorLoop Brain MCP (53a5aee5) - paid-media-optimizer - Cohort basis (SOURCE=%s)" % SOURCE,
        "brain_source": SOURCE,
        "key": "campaign_id",
        "period": "daily",
        "anchor": anchor_str,
        "prior": p_iso,
        "pulled": datetime.now().strftime("%Y-%m-%d"),
        "campaign_cur": {n: rnd(v) for n, v in cur.items()},
        "campaign_prior": {n: rnd(v) for n, v in pri.items()},
        "daily_series": {n: {d: rnd(v) for d, v in dm.items()} for n, dm in series.items()},
        "unattributed_cur": rnd(unattr_cur),
        "unattributed_prior": rnd(unattr_pri),
    }
    outpath = "%s/cohort_demos.json" % data_dir
    json.dump(out, open(outpath, "w"), indent=2)
    tot, ptot = sum(out["campaign_cur"].values()), sum(out["campaign_prior"].values())
    print("Wrote", outpath, "(daily %s, SOURCE=%s)" % (anchor_str, SOURCE))
    print("  ANCHOR %s Day-0 demos by campaign (prior %s):" % (anchor_str, p_iso))
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
        sys.exit("period must be 'weekly' or 'monthly', got %r" % period)

    rep_name_by_id = {int(r["campaign.id"]): r["campaign.name"]
                      for r in json.load(open("%s/camp_cur.json" % data_dir))
                      if r.get("campaign.id") is not None}
    rep_ids = set(rep_name_by_id)
    if not rep_ids:
        sys.exit("ERROR: %s/camp_cur.json has no campaign.id values — cohort mapping needs "
                 "campaign ids. Re-fetch with the updated fetch_data.py (adds CampaignId)." % data_dir)

    raw = json.load(open(raw_file))
    origin = raw.get("origin", "brain")  # "salesforce" when sf_cohort.js wrote the raw file
    rows = json.loads(raw["result"])["rows"]
    if rows and "DEMO_SCHEDULED_AT_DAY0" not in rows[0]:
        sys.exit("ERROR: raw file has no DEMO_SCHEDULED_AT_DAY0 — wrong metric? cols=%s"
                 % list(rows[0].keys()))

    def toint(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    series = {n: [0.0] * N for n in rep_name_by_id.values()}
    unattributed = [0.0] * N
    for r in rows:
        if r.get("SOURCE") != SOURCE:
            continue
        d0 = r.get("DEMO_SCHEDULED_AT_DAY0") or 0
        if not d0:
            continue
        i = idx_of(date.fromisoformat(str(r["DT"])[:10]))
        if i is None:
            continue
        cid = toint(r.get("CAMPAIGN_ID"))
        if cid in rep_ids:
            series[rep_name_by_id[cid]][i] += d0
        else:
            unattributed[i] += d0

    series = {n: [round(v, 2) for v in arr] for n, arr in series.items()}
    unattributed = [round(v, 2) for v in unattributed]
    out = {
        "basis": "cohort_day0",
        "data_source": origin,
        "metric": "gtm-paid-media-daily-cohort",
        "column": "DEMO_SCHEDULED_AT_DAY0",
        "source": ("Salesforce Day-0 cohort (CDF1, Lead.campaign_ID__c) via sf_cohort.js (UTM_Source=%s)" % SOURCE)
                  if origin == "salesforce" else
                  "DoorLoop Brain MCP (53a5aee5) - paid-media-optimizer - Cohort basis (SOURCE=%s)" % SOURCE,
        "brain_source": SOURCE,
        "key": "campaign_id",
        "period": period,
        "period_key": key,
        "pulled": datetime.now().strftime("%Y-%m-%d"),
        "bucket_labels": bucket_labels,
        "series": series,
        "unattributed_series": unattributed,
        "campaign_cur": {n: arr[N - 1] for n, arr in series.items()},
        "campaign_prior": {n: arr[N - 2] for n, arr in series.items()},
    }
    outpath = "%s/cohort_demos.json" % data_dir
    json.dump(out, open(outpath, "w"), indent=2)

    tot = sum(out["campaign_cur"].values())
    print("Wrote", outpath, "(%s %s, SOURCE=%s)" % (period, key, SOURCE))
    print("  Report period (last bucket %s) Day-0 demos by campaign:" % last_bucket)
    for n in sorted(out["campaign_cur"], key=lambda k: -out["campaign_cur"][k]):
        print("    %-34s %6.1f" % (n, out["campaign_cur"][n]))
    print("    %-34s %6.1f   (+%.1f unattributed)" % ("TOTAL", tot, unattributed[-1]))
    print("  12-bucket All series:", [round(sum(series[n][i] for n in series), 1) for i in range(N)])
    return outpath


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    import os
    raw_file = args[0]
    # Back-compat: `cohort_demos.py <file> <WEEK_END>` (2 args) == weekly.
    if len(args) == 2:
        period, key = "weekly", args[1]
    else:
        period, key = args[1], args[2]
    main(raw_file, period, key, root=os.path.dirname(os.path.abspath(__file__)))
