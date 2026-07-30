#!/usr/bin/env python3
"""
Microsoft Ads data puller — the data layer for the daily/weekly/monthly reports.

Microsoft's Reporting API is ASYNC + CSV (submit → poll → download a zipped CSV),
unlike Google's inline-JSON MCP. So instead of asking Claude to orchestrate that
every morning, this deterministic script does it: it submits every report the
renderer needs, polls them, downloads + parses the CSVs, and writes them out as
the SAME flat dotted-key JSON shape that build.py / report_common.py already expect
(e.g. "campaign.name", "metrics.cost_micros"). That way the proven Google renderers
render Microsoft data with almost no change.

Auth reuses the Microsoft Ads MCP exactly (same refresh token + app). Secrets are
read from the MCP repo's git-ignored .env — never hard-coded here.

Usage:
    python3 fetch_data.py daily   [YYYY-MM-DD]   # default = yesterday (America/New_York)
    python3 fetch_data.py weekly  [WEEK_END_SUN] # default = last completed Sunday (Mon–Sun week)
    python3 fetch_data.py monthly [YYYY-MM]      # default = last completed month

Read-only: this NEVER writes to Microsoft Advertising.
"""
import os, sys, io, csv, json, time, zipfile, urllib.request
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import build  # shared config (DEMO_GOALS) + weekly vocab-cache helpers

DEMO_GOALS = set(getattr(build, "DEMO_GOALS", ["Demo_Scheduled"]))

# The Competitors campaign changed its conversion goal from "Online Demo Scheduled"
# to "Demo_Scheduled" on 2026-06-04. To show historical demos (pre-change) AND
# new demos (post-change) without a gap, we accept BOTH goals for Competitors only.
# For all other campaigns we count only Demo_Scheduled (the single source of truth).
# Note: Brand fires both goals too, but its goal-segmented data only has Demo_Scheduled
# counted (it uses account default and the filter is per-campaign), so no double-count.
COMPETITORS_DEMO_GOALS = {"Demo_Scheduled", "Online Demo Scheduled"}

def _demo_goals_for(campaign_name):
    """Return the accepted demo-goal set for this campaign."""
    if "competitor" in (campaign_name or "").lower():
        return COMPETITORS_DEMO_GOALS
    return DEMO_GOALS

def _is_brand(campaign_name):
    """The Brand campaign (name contains 'brand'). Brand bids on Target Impression
    Share, so its OVERALL search impression share is a broad, volatile estimate that
    badly mismatches the Microsoft UI (e.g. 50% here vs 77% Auction-Insights). For
    Brand ONLY we report the TOP-OF-PAGE (mainline) impression-share family instead —
    'are we in the top 2–4 ad spots on our own brand SERP', which is the question that
    matters for a brand campaign and matches what David inspects in the UI. See
    _brand_is() and the IS mappers; calibrated 2026-06-12."""
    return "brand" in (campaign_name or "").lower()

def _brand_is(r, top_share_col, top_rank_col, overall_share_col, overall_rank_col):
    """For a branded campaign return (rank_lost, impr_share) from the TOP-OF-PAGE
    columns, falling back to the overall columns if the top fields are blank (back-
    compat / older windows). For every other campaign return the overall pair. Returns
    raw Microsoft strings ('48.34%') — the caller applies ratio()."""
    if _is_brand(r.get("CampaignName")):
        ts, tr = r.get(top_share_col), r.get(top_rank_col)
        share = ts if (ts not in (None, "")) else r.get(overall_share_col)
        rank  = tr if (tr not in (None, "")) else r.get(overall_rank_col)
        return rank, share
    return r.get(overall_rank_col), r.get(overall_share_col)

# ── Auth — identical creds to the Microsoft Ads MCP (secrets from its .env) ────
MCP_REPO = "/opt/mcp/MCP/microsoft-ads-mcp/repo"
ACCOUNT_ID  = "REDACTED"     # DoorLoop (not secret — an account identifier)
CUSTOMER_ID = __import__("os").environ.get("MSADS_CUSTOMER_ID", "")

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

_env = _load_env_file(os.path.join(MCP_REPO, ".env"))
os.environ.setdefault("MICROSOFT_ADS_DEVELOPER_TOKEN", _env.get("MICROSOFT_ADS_DEVELOPER_TOKEN", ""))
os.environ.setdefault("MICROSOFT_ADS_CLIENT_ID",       _env.get("MICROSOFT_ADS_CLIENT_ID", ""))
os.environ.setdefault("MICROSOFT_ADS_ACCOUNT_ID",  ACCOUNT_ID)
os.environ.setdefault("MICROSOFT_ADS_CUSTOMER_ID", CUSTOMER_ID)
sys.path.insert(0, MCP_REPO)
import server                                   # noqa: E402
from bingads.service_client import ServiceClient  # noqa: E402

_SVC = None
_AUTH = None
def svc():
    global _SVC, _AUTH
    if _SVC is None:
        _AUTH = server.get_authorization()
        _SVC = ServiceClient(service="ReportingService", version=13, authorization_data=_AUTH)
    return _SVC

# ── Report engine (custom date range, submit / poll / download / parse) ───────
def _mk_date(d):
    o = svc().factory.create("Date"); o.Day = d.day; o.Month = d.month; o.Year = d.year
    return o

def build_request(kind, columns, start, end, aggregation="Summary"):
    r = svc().factory.create(f"{kind}ReportRequest")
    r.Format = "Csv"
    r.ReportName = f"{kind} {start}..{end}"
    r.ReturnOnlyCompleteData = False
    r.Aggregation = aggregation
    t = svc().factory.create("ReportTime")
    t.PredefinedTime = None
    t.CustomDateRangeStart = _mk_date(start)
    t.CustomDateRangeEnd = _mk_date(end)
    t.ReportTimeZone = None
    r.Time = t
    sk = "AccountThroughCampaignReportScope" if kind == "CampaignPerformance" else "AccountThroughAdGroupReportScope"
    scope = svc().factory.create(sk)
    scope.AccountIds = svc().factory.create("ns1:ArrayOflong")
    scope.AccountIds.long.append(int(svc().authorization_data.account_id))
    r.Scope = scope
    cols = svc().factory.create(f"ArrayOf{kind}ReportColumn")
    getattr(cols, f"{kind}ReportColumn").extend([c.strip() for c in columns])
    r.Columns = cols
    return r

def submit(req):
    res = svc().SubmitGenerateReport(ReportRequest=req)
    return getattr(res, "ReportRequestId", None) or str(res)

def poll(rid, tries=90, delay=4):
    for _ in range(tries):
        resp = svc().PollGenerateReport(ReportRequestId=rid)
        st = getattr(resp, "ReportRequestStatus", resp)
        if st.Status == "Success":
            return getattr(st, "ReportDownloadUrl", None) or "EMPTY"
        if st.Status == "Error":
            return "ERROR"
        time.sleep(delay)
    return "TIMEOUT"

def download_rows(url):
    """Download + parse a report CSV (handles the zip) into a list of dicts."""
    if url in ("ERROR", "TIMEOUT", "EMPTY", None):
        return []
    data = urllib.request.urlopen(url).read()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        text = z.read(z.namelist()[0]).decode("utf-8-sig")
    except zipfile.BadZipFile:
        text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    # The header row is the one that contains a known column name (skip the report
    # title/meta block Microsoft prepends).
    marker = {"CampaignName", "SearchQuery", "Keyword", "TimePeriod", "Goal", "AdGroupName"}
    hdr_i = next((i for i, r in enumerate(rows) if marker & set(r)), None)
    if hdr_i is None:
        return []
    hdr = rows[hdr_i]
    out = []
    for r in rows[hdr_i + 1:]:
        if not r or not any(c.strip() for c in r):
            break          # blank line = end of data, before the ©/totals footer
        out.append(dict(zip(hdr, r)))
    return out

# ── value parsers (Microsoft formats: "$1,234.50", "28%", "1,234") ────────────
def _f(x):
    s = str(x or "").replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0
def micros(x):  return int(round(_f(x) * 1e6))     # dollars → micros (build.micros() reverses)
def ratio(x):   return _f(x) / 100.0               # "28%" → 0.28 (build.pct() multiplies back)
def inti(x):    return int(round(_f(x)))

# ── a small spec system: submit many reports, poll, then map rows → JSON files ─
class Job:
    def __init__(self, kind, columns, start, end, aggregation="Summary"):
        self.kind, self.columns, self.start, self.end, self.agg = kind, columns, start, end, aggregation
        self.rid = None
        self.rows = []

def run_jobs(jobs):
    """jobs: dict name->Job. Submits all, polls all, downloads all. Fills job.rows."""
    for name, j in jobs.items():
        try:
            j.rid = submit(build_request(j.kind, j.columns, j.start, j.end, j.agg))
            print(f"  submitted {name} ({j.kind}/{j.agg})", flush=True)
        except Exception as e:
            print(f"  ❌ submit {name}: {str(e)[:160]}", flush=True)
    for name, j in jobs.items():
        if not j.rid:
            continue
        url = poll(j.rid)
        j.rows = download_rows(url)
        tag = url if url in ("ERROR", "TIMEOUT", "EMPTY") else f"{len(j.rows)} rows"
        print(f"  fetched  {name}: {tag}", flush=True)
    return jobs

def write(key, name, rows):
    d = os.path.join(ROOT, "data", key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as f:
        json.dump(rows, f, separators=(",", ":"))
    print(f"  wrote    data/{key}/{name}  ({len(rows)})", flush=True)

# ── column → Google-style-key mappers ─────────────────────────────────────────
def map_campaign_spend(rows):
    return [{"campaign.id": r.get("CampaignId"), "campaign.name": r.get("CampaignName"),
             "metrics.cost_micros": micros(r.get("Spend")),
             "metrics.clicks": inti(r.get("Clicks")),
             "metrics.impressions": inti(r.get("Impressions"))} for r in rows]

def map_campaign_ranklost(rows):
    # Brand reports TOP-OF-PAGE rank-lost (consistent with its chart chips); budget-lost
    # stays overall (Brand bids Target Impression Share, rarely budget-constrained).
    out = []
    for r in rows:
        rank = (r.get("TopImpressionShareLostToRankPercent")
                if _is_brand(r.get("CampaignName")) and r.get("TopImpressionShareLostToRankPercent") not in (None, "")
                else r.get("ImpressionLostToRankAggPercent"))
        out.append({"campaign.name": r.get("CampaignName"),
                    "metrics.search_rank_lost_impression_share": ratio(rank),
                    "metrics.search_budget_lost_impression_share": ratio(r.get("ImpressionLostToBudgetPercent")),
                    "metrics.cost_micros": micros(r.get("Spend"))})
    return out

def map_demos(rows):
    """Goal-segmented campaign rows → one row per (campaign, demo-goal). Mirrors
    Google's conversion-action rows. Competitors accepts old + new goal for history."""
    out = []
    for r in rows:
        goal = (r.get("Goal") or "").strip()
        if goal in _demo_goals_for(r.get("CampaignName")):
            out.append({"campaign.name": r.get("CampaignName"),
                        "segments.conversion_action_name": goal,
                        "metrics.all_conversions": _f(r.get("Conversions"))})
    return out

def map_searchterms(rows):
    return [{"search_term_view.search_term": r.get("SearchQuery"),
             "campaign.name": r.get("CampaignName"),
             "ad_group.name": r.get("AdGroupName"),
             "metrics.clicks": inti(r.get("Clicks")),
             "metrics.cost_micros": micros(r.get("Spend")),
             "metrics.impressions": inti(r.get("Impressions")),
             "metrics.all_conversions": _f(r.get("Conversions")),
             "metrics.average_cpc": micros(r.get("AverageCpc"))} for r in rows]

def map_monthspend(rows):
    return [{"segments.date": r.get("TimePeriod"), "campaign.name": r.get("CampaignName"),
             "metrics.cost_micros": micros(r.get("Spend"))} for r in rows]

def map_converted90(rows):
    seen, out = set(), []
    for r in rows:
        if _f(r.get("Conversions")) > 0:
            term = r.get("SearchQuery")
            if term not in seen:
                seen.add(term)
                out.append({"search_term_view.search_term": term})
    return out

def map_vocab(rows):
    return [{"ad_group_criterion.keyword.text": r.get("Keyword")} for r in rows if r.get("Keyword")]

def map_clicks_daily(rows):
    return [{"segments.date": r.get("TimePeriod"), "campaign.name": r.get("CampaignName"),
             "ad_group.name": r.get("AdGroupName"),
             "metrics.clicks": inti(r.get("Clicks")),
             "metrics.impressions": inti(r.get("Impressions")),
             "metrics.cost_micros": micros(r.get("Spend"))} for r in rows]

def map_demos_daily(rows):
    out = []
    for r in rows:
        goal = (r.get("Goal") or "").strip()
        if goal in _demo_goals_for(r.get("CampaignName")):
            out.append({"segments.date": r.get("TimePeriod"), "campaign.name": r.get("CampaignName"),
                        "ad_group.name": r.get("AdGroupName"),
                        "metrics.all_conversions": _f(r.get("Conversions"))})
    return out

def map_rankis_daily(rows):
    out = []
    for r in rows:
        rank, share = _brand_is(r, "TopImpressionSharePercent", "TopImpressionShareLostToRankPercent",
                                "ImpressionSharePercent", "ImpressionLostToRankAggPercent")
        out.append({"segments.date": r.get("TimePeriod"), "campaign.name": r.get("CampaignName"),
                    "metrics.search_rank_lost_impression_share": ratio(rank),
                    "metrics.search_impression_share": ratio(share),
                    "metrics.cost_micros": micros(r.get("Spend"))})
    return out

# ── DAILY ─────────────────────────────────────────────────────────────────────
def fetch_daily(anchor):
    a = datetime.strptime(anchor, "%Y-%m-%d").date()
    prior = a - timedelta(days=7)
    m_start = a.replace(day=1)
    d30 = a - timedelta(days=29)
    d90 = a - timedelta(days=89)
    key = anchor
    print(f"DAILY fetch for {anchor} (prior {prior})")

    CAMP_RICH = ["CampaignName", "CampaignId", "Impressions", "Clicks", "Spend",
                 "ImpressionLostToRankAggPercent", "ImpressionLostToBudgetPercent",
                 "TopImpressionShareLostToRankPercent"]
    GOAL_COLS = ["CampaignName", "Goal", "Conversions"]
    SQ_COLS = ["SearchQuery", "Keyword", "CampaignName", "AdGroupName", "Clicks", "Spend",
               "Impressions", "Conversions", "AverageCpc"]

    jobs = {
        "camp_cur":   Job("CampaignPerformance", CAMP_RICH, a, a),
        "camp_prior": Job("CampaignPerformance", CAMP_RICH, prior, prior),
        "demos_cur":  Job("CampaignPerformance", GOAL_COLS, a, a),
        "demos_prior":Job("CampaignPerformance", GOAL_COLS, prior, prior),
        "searchterms":      Job("SearchQueryPerformance", SQ_COLS, a, a),
        "searchterms_prior":Job("SearchQueryPerformance", SQ_COLS, prior, prior),
        "monthspend": Job("CampaignPerformance", ["TimePeriod", "CampaignName", "Spend"], m_start, a, "Daily"),
        "converted90":Job("SearchQueryPerformance", ["SearchQuery", "Conversions"], d90, a),
        "clicks_30d": Job("AdGroupPerformance", ["TimePeriod", "CampaignName", "AdGroupName", "Clicks", "Impressions", "Spend"], d30, a, "Daily"),
        "demos_30d":  Job("AdGroupPerformance", ["TimePeriod", "CampaignName", "AdGroupName", "Goal", "Conversions"], d30, a, "Daily"),
        "rankis_30d": Job("CampaignPerformance", ["TimePeriod", "CampaignName", "ImpressionLostToRankAggPercent", "ImpressionSharePercent", "TopImpressionSharePercent", "TopImpressionShareLostToRankPercent", "Spend"], d30, a, "Daily"),
    }
    # keyword vocab is weekly-cached (one fewer report 6 days out of 7)
    vstate = build.vocab_due(anchor)
    if vstate == "FETCH":
        jobs["keyword_vocab"] = Job("KeywordPerformance", ["Keyword", "AdGroupName", "CampaignName", "Clicks"], d30, a)
    print(f"  keyword vocab: {vstate}")

    run_jobs(jobs)

    write(key, "spend_cur.json",   map_campaign_spend(jobs["camp_cur"].rows))
    write(key, "spend_prior.json", map_campaign_spend(jobs["camp_prior"].rows))
    write(key, "ranklost.json",    map_campaign_ranklost(jobs["camp_cur"].rows))
    write(key, "demos_cur.json",   map_demos(jobs["demos_cur"].rows))
    write(key, "demos_prior.json", map_demos(jobs["demos_prior"].rows))
    write(key, "searchterms.json",       map_searchterms(jobs["searchterms"].rows))
    write(key, "searchterms_prior.json", map_searchterms(jobs["searchterms_prior"].rows))
    write(key, "monthspend.json",  map_monthspend(jobs["monthspend"].rows))
    write(key, "converted90.json", map_converted90(jobs["converted90"].rows))
    write(key, "clicks_30d.json",  map_clicks_daily(jobs["clicks_30d"].rows))
    write(key, "demos_30d.json",   map_demos_daily(jobs["demos_30d"].rows))
    write(key, "rankis_30d.json",  map_rankis_daily(jobs["rankis_30d"].rows))
    if vstate == "FETCH":
        write(key, "keyword_vocab.json", map_vocab(jobs["keyword_vocab"].rows))
        build.refresh_vocab(anchor)
    print("DAILY fetch complete.")


# ── mappers shared by WEEKLY + MONTHLY (report_common contract) ───────────────
# report_common.py reads metrics.conversions for demos and search_impression_share /
# search_rank_lost_impression_share for the per-campaign table + metric charts.
def map_camp_metrics(rows):
    out = []
    for r in rows:
        rank, share = _brand_is(r, "TopImpressionSharePercent", "TopImpressionShareLostToRankPercent",
                                "ImpressionSharePercent", "ImpressionLostToRankAggPercent")
        out.append({"campaign.name": r.get("CampaignName"),
                    "campaign.id": r.get("CampaignId"),   # for the bing cohort demo mapping (by campaign id)
                    "metrics.cost_micros": micros(r.get("Spend")),
                    "metrics.clicks": inti(r.get("Clicks")),
                    "metrics.impressions": inti(r.get("Impressions")),
                    "metrics.search_impression_share": ratio(share),
                    "metrics.search_rank_lost_impression_share": ratio(rank)})
    return out

def map_ag_metrics(rows):
    return [{"campaign.name": r.get("CampaignName"), "ad_group.name": r.get("AdGroupName"),
             "metrics.cost_micros": micros(r.get("Spend")),
             "metrics.clicks": inti(r.get("Clicks")),
             "metrics.impressions": inti(r.get("Impressions")),
             "metrics.search_impression_share": ratio(r.get("ImpressionSharePercent")),
             "metrics.search_rank_lost_impression_share": ratio(r.get("ImpressionLostToRankAggPercent"))}
            for r in rows]

def map_camp_demos_conv(rows):
    return [{"campaign.name": r.get("CampaignName"), "metrics.conversions": _f(r.get("Conversions"))}
            for r in rows if (r.get("Goal") or "").strip() in _demo_goals_for(r.get("CampaignName"))]

def map_ag_demos_conv(rows):
    return [{"campaign.name": r.get("CampaignName"), "ad_group.name": r.get("AdGroupName"),
             "metrics.conversions": _f(r.get("Conversions"))}
            for r in rows if (r.get("Goal") or "").strip() in _demo_goals_for(r.get("CampaignName"))]

def norm_month(tp):
    """Normalize a Monthly-aggregation TimePeriod to YYYY-MM-01 (so the monthly
    builder's date→bucket math, which parses YYYY-MM-DD, works regardless of the
    exact format Microsoft returns)."""
    import re
    s = (tp or "").strip()
    m = re.match(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-01"
    for fmt in ("%m/%d/%Y", "%b %Y", "%B %Y"):
        try:
            d = datetime.strptime(s, fmt)
            return f"{d.year:04d}-{d.month:02d}-01"
        except ValueError:
            pass
    return s

def map_series_metrics(rows, monthly=False):
    out = []
    for r in rows:
        rank, share = _brand_is(r, "TopImpressionSharePercent", "TopImpressionShareLostToRankPercent",
                                "ImpressionSharePercent", "ImpressionLostToRankAggPercent")
        out.append({"segments.date": (norm_month(r.get("TimePeriod")) if monthly else r.get("TimePeriod")),
                    "campaign.name": r.get("CampaignName"),
                    "metrics.cost_micros": micros(r.get("Spend")),
                    "metrics.clicks": inti(r.get("Clicks")),
                    "metrics.impressions": inti(r.get("Impressions")),
                    "metrics.search_impression_share": ratio(share),
                    "metrics.search_rank_lost_impression_share": ratio(rank)})
    return out

def map_series_demos(rows, monthly=False):
    return [{"segments.date": (norm_month(r.get("TimePeriod")) if monthly else r.get("TimePeriod")),
             "campaign.name": r.get("CampaignName"), "metrics.conversions": _f(r.get("Conversions"))}
            for r in rows if (r.get("Goal") or "").strip() in _demo_goals_for(r.get("CampaignName"))]

# ── date helpers (mirror build_weekly.py / build_monthly.py) ───────────────────
def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)

def last_completed_sunday(today):
    off = (today.weekday() - 6) % 7      # Sun = 6  (Mon–Sun weeks, match the Brain UI)
    return today - timedelta(days=off if off > 0 else 7)   # strictly before today

def last_completed_month(today):
    return add_months(today.replace(day=1), -1)

# column sets reused by weekly + monthly
_CM = ["CampaignName", "CampaignId", "Impressions", "Clicks", "Spend", "ImpressionSharePercent", "ImpressionLostToRankAggPercent", "TopImpressionSharePercent", "TopImpressionShareLostToRankPercent"]
_AM = ["CampaignName", "AdGroupName", "Impressions", "Clicks", "Spend", "ImpressionSharePercent", "ImpressionLostToRankAggPercent"]
_CG = ["CampaignName", "Goal", "Conversions"]
_AG = ["CampaignName", "AdGroupName", "Goal", "Conversions"]
_SM = ["TimePeriod", "CampaignName", "Impressions", "Clicks", "Spend", "ImpressionSharePercent", "ImpressionLostToRankAggPercent", "TopImpressionSharePercent", "TopImpressionShareLostToRankPercent"]
_SG = ["TimePeriod", "CampaignName", "Goal", "Conversions"]

def _write_period(key, jobs, monthly):
    write(key, "camp_cur.json",        map_camp_metrics(jobs["camp_cur"].rows))
    write(key, "camp_prior.json",      map_camp_metrics(jobs["camp_prior"].rows))
    write(key, "camp_demos_cur.json",  map_camp_demos_conv(jobs["camp_demos_cur"].rows))
    write(key, "camp_demos_prior.json",map_camp_demos_conv(jobs["camp_demos_prior"].rows))
    write(key, "ag_cur.json",          map_ag_metrics(jobs["ag_cur"].rows))
    write(key, "ag_prior.json",        map_ag_metrics(jobs["ag_prior"].rows))
    write(key, "ag_demos_cur.json",    map_ag_demos_conv(jobs["ag_demos_cur"].rows))
    write(key, "ag_demos_prior.json",  map_ag_demos_conv(jobs["ag_demos_prior"].rows))
    write(key, "series_metrics.json",  map_series_metrics(jobs["series_metrics"].rows, monthly))
    write(key, "series_demos.json",    map_series_demos(jobs["series_demos"].rows, monthly))

# ── WEEKLY ────────────────────────────────────────────────────────────────────
def fetch_weekly(week_end):
    we = datetime.strptime(week_end, "%Y-%m-%d").date()
    ws = we - timedelta(days=6)
    pe = we - timedelta(days=7); ps = ws - timedelta(days=7)
    series_start = ws - timedelta(days=7 * 11)   # 12 weekly buckets
    d30 = we - timedelta(days=29)
    key = f"weekly-{week_end}"
    print(f"WEEKLY fetch for {week_end} (week {ws}..{we}; prior {ps}..{pe})")
    jobs = {
        "camp_cur":         Job("CampaignPerformance", _CM, ws, we),
        "camp_prior":       Job("CampaignPerformance", _CM, ps, pe),
        "camp_demos_cur":   Job("CampaignPerformance", _CG, ws, we),
        "camp_demos_prior": Job("CampaignPerformance", _CG, ps, pe),
        "ag_cur":           Job("AdGroupPerformance", _AM, ws, we),
        "ag_prior":         Job("AdGroupPerformance", _AM, ps, pe),
        "ag_demos_cur":     Job("AdGroupPerformance", _AG, ws, we),
        "ag_demos_prior":   Job("AdGroupPerformance", _AG, ps, pe),
        "series_metrics":   Job("CampaignPerformance", _SM, series_start, we, "Daily"),
        "series_demos":     Job("CampaignPerformance", _SG, series_start, we, "Daily"),
        "clicks_30d":       Job("AdGroupPerformance", ["TimePeriod", "CampaignName", "AdGroupName", "Clicks", "Spend"], d30, we, "Daily"),
    }
    run_jobs(jobs)
    _write_period(key, jobs, monthly=False)
    write(key, "clicks_30d.json", map_clicks_daily(jobs["clicks_30d"].rows))
    print("WEEKLY fetch complete.")

# ── MONTHLY ───────────────────────────────────────────────────────────────────
def fetch_monthly(month):
    ms = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    me = add_months(ms, 1) - timedelta(days=1)
    pms = add_months(ms, -1); pme = ms - timedelta(days=1)
    series_start = add_months(ms, -11)            # 12 monthly buckets
    key = f"monthly-{month}"
    print(f"MONTHLY fetch for {month} ({ms}..{me}; prior {pms}..{pme})")
    jobs = {
        "camp_cur":         Job("CampaignPerformance", _CM, ms, me),
        "camp_prior":       Job("CampaignPerformance", _CM, pms, pme),
        "camp_demos_cur":   Job("CampaignPerformance", _CG, ms, me),
        "camp_demos_prior": Job("CampaignPerformance", _CG, pms, pme),
        "ag_cur":           Job("AdGroupPerformance", _AM, ms, me),
        "ag_prior":         Job("AdGroupPerformance", _AM, pms, pme),
        "ag_demos_cur":     Job("AdGroupPerformance", _AG, ms, me),
        "ag_demos_prior":   Job("AdGroupPerformance", _AG, pms, pme),
        "series_metrics":   Job("CampaignPerformance", _SM, series_start, me, "Monthly"),
        "series_demos":     Job("CampaignPerformance", _SG, series_start, me, "Monthly"),
    }
    run_jobs(jobs)
    _write_period(key, jobs, monthly=True)
    print("MONTHLY fetch complete (no clicks_30d — monthly omits the trend chart).")


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
