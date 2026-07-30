#!/usr/bin/env python3
"""
Meta Ads data puller — the data layer for the daily/weekly/monthly reports.

Pulls the Meta Marketing API (Graph API insights endpoint) directly and writes the
SAME flat dotted-key JSON shape the Google/Microsoft renderers expect
(e.g. "campaign.name", "metrics.cost_micros"), so the proven renderers work with
almost no change. The official `meta-ads` CLI has no `level=` parameter, so this
script hits the Graph API itself — same read-only token, from the MCP folder's .env.

Levels used: level=campaign (summaries, month-to-date) and level=adset
("ad groups" = ad sets — the chart-chip entities). Demos ride along in the
`actions` array of the same calls (no extra requests): action_type in DEMO_ACTIONS.

Usage:
    python3 fetch_data.py daily   [YYYY-MM-DD]   # default = yesterday (America/New_York)
    python3 fetch_data.py weekly  [WEEK_END_SUN] # default = last completed Sunday (Mon–Sun week)
    python3 fetch_data.py monthly [YYYY-MM]      # default = last completed month

Read-only: GET requests only. This NEVER writes to Meta.
"""
import os, sys, json, time, urllib.request, urllib.parse
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Platform demo definition (LAST-RESORT basis only — the reports normally use the
# Brain / Salesforce cohort, see CLAUDE.md). "Demo Scheduled - URL Traffic (Thanks)"
# is the thank-you-page custom conversion. Meta's attribution windows (7d click +
# 1d view) make it run ~2.5-3x ABOVE the Salesforce demo count — documented, not a bug.
DEMO_ACTIONS = {
    "offsite_conversion.custom." + __import__("os").environ.get("META_DEMO_CONVERSION_ID","") + "": "Demo Scheduled - URL Traffic (Thanks)",
}

# ── Auth — same read-only system-user token as the meta-ads CLI ────────────────
MCP_DIR = __import__("os").environ.get("META_MCP_DIR", "/tmp/meta-mcp")
API_VER = "v21.0"

def _load_env_file(path):
    env = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

_env = _load_env_file(os.path.join(MCP_DIR, ".env"))
TOKEN = _env.get("ACCESS_TOKEN", "")
ACCOUNT = _env.get("AD_ACCOUNT_ID", "")
if not TOKEN or not ACCOUNT:
    print(f"❌ ACCESS_TOKEN / AD_ACCOUNT_ID not found in {MCP_DIR}/.env")
    sys.exit(1)

# ── Graph API insights engine (sync GET + cursor paging) ───────────────────────
def insights(level, since, until, time_increment=None, fields=None, retries=3):
    """One insights pull, follows paging. Returns the raw row dicts."""
    fields = fields or ["campaign_id", "campaign_name", "spend", "clicks", "impressions", "actions"]
    params = {
        "level": level,
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since, "until": until}),
        "limit": "500",
        "access_token": TOKEN,
    }
    if time_increment:
        params["time_increment"] = str(time_increment)
    url = f"https://graph.facebook.com/{API_VER}/{ACCOUNT}/insights?" + urllib.parse.urlencode(params)
    rows = []
    while url:
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=120) as resp:
                    data = json.load(resp)
                break
            except Exception as e:
                if attempt == retries - 1:
                    raise
                print(f"  retry {attempt + 1} after error: {str(e)[:120]}", flush=True)
                time.sleep(15 * (attempt + 1))
        rows.extend(data.get("data", []))
        url = (data.get("paging") or {}).get("next")
    return rows

# ── value helpers ──────────────────────────────────────────────────────────────
def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0
def micros(x): return int(round(_f(x) * 1e6))   # dollars → micros (build.micros() reverses)
def inti(x):   return int(round(_f(x)))

def demo_count(row):
    """Sum the demo conversions in a row's actions array."""
    return sum(_f(a.get("value")) for a in (row.get("actions") or [])
               if a.get("action_type") in DEMO_ACTIONS)

DEMO_LABEL = next(iter(DEMO_ACTIONS.values()))

def write(key, name, rows):
    d = os.path.join(ROOT, "data", key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as f:
        json.dump(rows, f, separators=(",", ":"))
    print(f"  wrote    data/{key}/{name}  ({len(rows)})", flush=True)

# ── row → Google-style-key mappers ─────────────────────────────────────────────
def map_campaign_spend(rows):
    return [{"campaign.id": r.get("campaign_id"), "campaign.name": r.get("campaign_name"),
             "metrics.cost_micros": micros(r.get("spend")),
             "metrics.clicks": inti(r.get("clicks")),
             "metrics.impressions": inti(r.get("impressions"))}
            for r in rows if _f(r.get("spend")) > 0]

def map_demos(rows):
    """Campaign rows → one row per campaign with the platform demo conversion."""
    out = []
    for r in rows:
        n = demo_count(r)
        if n > 0:
            out.append({"campaign.name": r.get("campaign_name"),
                        "segments.conversion_action_name": DEMO_LABEL,
                        "metrics.all_conversions": n})
    return out

def map_monthspend(rows):
    return [{"segments.date": r.get("date_start"), "campaign.name": r.get("campaign_name"),
             "metrics.cost_micros": micros(r.get("spend"))}
            for r in rows if _f(r.get("spend")) > 0]

def map_clicks_daily(rows):
    return [{"segments.date": r.get("date_start"), "campaign.name": r.get("campaign_name"),
             "ad_group.name": r.get("adset_name"),
             "metrics.clicks": inti(r.get("clicks")),
             "metrics.impressions": inti(r.get("impressions")),
             "metrics.cost_micros": micros(r.get("spend"))}
            for r in rows if inti(r.get("clicks")) > 0 or _f(r.get("spend")) > 0]

def map_demos_daily(rows):
    out = []
    for r in rows:
        n = demo_count(r)
        if n > 0:
            out.append({"segments.date": r.get("date_start"), "campaign.name": r.get("campaign_name"),
                        "ad_group.name": r.get("adset_name"),
                        "metrics.all_conversions": n})
    return out

ADSET_FIELDS = ["campaign_id", "campaign_name", "adset_id", "adset_name",
                "spend", "clicks", "impressions", "actions"]

# ── DAILY ─────────────────────────────────────────────────────────────────────
def fetch_daily(anchor):
    a = datetime.strptime(anchor, "%Y-%m-%d").date()
    prior = a - timedelta(days=7)
    m_start = a.replace(day=1)
    d30 = a - timedelta(days=29)
    key = anchor
    print(f"DAILY fetch for {anchor} (prior {prior})")

    camp_cur = insights("campaign", anchor, anchor)
    print(f"  fetched  camp_cur: {len(camp_cur)} rows", flush=True)
    camp_prior = insights("campaign", str(prior), str(prior))
    print(f"  fetched  camp_prior: {len(camp_prior)} rows", flush=True)
    monthspend = insights("campaign", str(m_start), anchor, time_increment=1,
                          fields=["campaign_name", "spend"])
    print(f"  fetched  monthspend: {len(monthspend)} rows", flush=True)
    adset_30d = insights("adset", str(d30), anchor, time_increment=1, fields=ADSET_FIELDS)
    print(f"  fetched  adset_30d: {len(adset_30d)} rows", flush=True)

    write(key, "spend_cur.json",   map_campaign_spend(camp_cur))
    write(key, "spend_prior.json", map_campaign_spend(camp_prior))
    write(key, "demos_cur.json",   map_demos(camp_cur))
    write(key, "demos_prior.json", map_demos(camp_prior))
    write(key, "monthspend.json",  map_monthspend(monthspend))
    write(key, "clicks_30d.json",  map_clicks_daily(adset_30d))
    write(key, "demos_30d.json",   map_demos_daily(adset_30d))
    print("DAILY fetch complete (4 API calls).")

# ── mappers shared by WEEKLY + MONTHLY (report_common contract) ────────────────
def map_camp_metrics(rows):
    return [{"campaign.name": r.get("campaign_name"),
             "campaign.id": r.get("campaign_id"),   # for the cohort demo mapping (by campaign id)
             "metrics.cost_micros": micros(r.get("spend")),
             "metrics.clicks": inti(r.get("clicks")),
             "metrics.impressions": inti(r.get("impressions"))}
            for r in rows if _f(r.get("spend")) > 0]

def map_ag_metrics(rows):
    return [{"campaign.name": r.get("campaign_name"), "ad_group.name": r.get("adset_name"),
             "metrics.cost_micros": micros(r.get("spend")),
             "metrics.clicks": inti(r.get("clicks")),
             "metrics.impressions": inti(r.get("impressions"))}
            for r in rows if _f(r.get("spend")) > 0]

def map_camp_demos_conv(rows):
    out = []
    for r in rows:
        n = demo_count(r)
        if n > 0:
            out.append({"campaign.name": r.get("campaign_name"), "metrics.conversions": n})
    return out

def map_ag_demos_conv(rows):
    out = []
    for r in rows:
        n = demo_count(r)
        if n > 0:
            out.append({"campaign.name": r.get("campaign_name"), "ad_group.name": r.get("adset_name"),
                        "metrics.conversions": n})
    return out

def map_series_metrics(rows, monthly=False):
    return [{"segments.date": (r.get("date_start")[:7] + "-01" if monthly else r.get("date_start")),
             "campaign.name": r.get("campaign_name"),
             "metrics.cost_micros": micros(r.get("spend")),
             "metrics.clicks": inti(r.get("clicks")),
             "metrics.impressions": inti(r.get("impressions"))}
            for r in rows if _f(r.get("spend")) > 0]

def map_series_demos(rows, monthly=False):
    out = []
    for r in rows:
        n = demo_count(r)
        if n > 0:
            out.append({"segments.date": (r.get("date_start")[:7] + "-01" if monthly else r.get("date_start")),
                        "campaign.name": r.get("campaign_name"), "metrics.conversions": n})
    return out

# ── date helpers (mirror build_weekly.py / build_monthly.py) ───────────────────
def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)

def last_completed_sunday(today):
    off = (today.weekday() - 6) % 7      # Sun = 6  (Mon–Sun weeks, match the Brain UI)
    return today - timedelta(days=off if off > 0 else 7)   # strictly before today

def last_completed_month(today):
    return add_months(today.replace(day=1), -1)

CAMP_FIELDS = ["campaign_id", "campaign_name", "spend", "clicks", "impressions", "actions"]

def _write_period(key, jobs, monthly):
    write(key, "camp_cur.json",         map_camp_metrics(jobs["camp_cur"]))
    write(key, "camp_prior.json",       map_camp_metrics(jobs["camp_prior"]))
    write(key, "camp_demos_cur.json",   map_camp_demos_conv(jobs["camp_cur"]))
    write(key, "camp_demos_prior.json", map_camp_demos_conv(jobs["camp_prior"]))
    write(key, "ag_cur.json",           map_ag_metrics(jobs["ag_cur"]))
    write(key, "ag_prior.json",         map_ag_metrics(jobs["ag_prior"]))
    write(key, "ag_demos_cur.json",     map_ag_demos_conv(jobs["ag_cur"]))
    write(key, "ag_demos_prior.json",   map_ag_demos_conv(jobs["ag_prior"]))
    write(key, "series_metrics.json",   map_series_metrics(jobs["series"], monthly))
    write(key, "series_demos.json",     map_series_demos(jobs["series"], monthly))

# ── WEEKLY ────────────────────────────────────────────────────────────────────
def fetch_weekly(week_end):
    we = datetime.strptime(week_end, "%Y-%m-%d").date()
    if we.weekday() != 6:
        print(f"❌ weekly key must be a Sunday (Mon–Sun weeks), got {week_end}")
        sys.exit(2)
    ws = we - timedelta(days=6)
    pe = we - timedelta(days=7); ps = ws - timedelta(days=7)
    series_start = ws - timedelta(days=7 * 11)   # 12 weekly buckets
    key = f"weekly-{week_end}"
    print(f"WEEKLY fetch for {week_end} (week {ws}..{we}; prior {ps}..{pe})")
    jobs = {
        "camp_cur":  insights("campaign", str(ws), str(we), fields=CAMP_FIELDS),
        "camp_prior": insights("campaign", str(ps), str(pe), fields=CAMP_FIELDS),
        "ag_cur":    insights("adset", str(ws), str(we), fields=ADSET_FIELDS),
        "ag_prior":  insights("adset", str(ps), str(pe), fields=ADSET_FIELDS),
        "series":    insights("campaign", str(series_start), str(we), time_increment=1, fields=CAMP_FIELDS),
    }
    for n, r in jobs.items():
        print(f"  fetched  {n}: {len(r)} rows", flush=True)
    _write_period(key, jobs, monthly=False)
    print("WEEKLY fetch complete (5 API calls).")

# ── MONTHLY ───────────────────────────────────────────────────────────────────
def fetch_monthly(month):
    ms = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    me = add_months(ms, 1) - timedelta(days=1)
    pms = add_months(ms, -1); pme = ms - timedelta(days=1)
    series_start = add_months(ms, -11)            # 12 monthly buckets
    key = f"monthly-{month}"
    print(f"MONTHLY fetch for {month} ({ms}..{me}; prior {pms}..{pme})")
    jobs = {
        "camp_cur":  insights("campaign", str(ms), str(me), fields=CAMP_FIELDS),
        "camp_prior": insights("campaign", str(pms), str(pme), fields=CAMP_FIELDS),
        "ag_cur":    insights("adset", str(ms), str(me), fields=ADSET_FIELDS),
        "ag_prior":  insights("adset", str(pms), str(pme), fields=ADSET_FIELDS),
        "series":    insights("campaign", str(series_start), str(me), time_increment="monthly", fields=CAMP_FIELDS),
    }
    for n, r in jobs.items():
        print(f"  fetched  {n}: {len(r)} rows", flush=True)
    _write_period(key, jobs, monthly=True)
    print("MONTHLY fetch complete (5 API calls; no clicks_30d — monthly omits the trend chart).")


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "daily"
    arg = args[1] if len(args) > 1 else None
    if mode == "daily":
        fetch_daily(arg or (date.today() - timedelta(days=1)).strftime("%Y-%m-%d"))
    elif mode == "weekly":
        fetch_weekly(arg or last_completed_sunday(date.today()).strftime("%Y-%m-%d"))
    elif mode == "monthly":
        fetch_monthly(arg or last_completed_month(date.today()).strftime("%Y-%m"))
    else:
        print(f"Unknown mode {mode!r}. Use: daily | weekly | monthly")
        sys.exit(2)

if __name__ == "__main__":
    main()
