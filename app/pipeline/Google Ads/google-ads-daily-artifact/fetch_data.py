#!/usr/bin/env python3
"""fetch_data.py — READ-ONLY puller for the Google Ads daily artifact.

Pulls all 14 daily queries via the Google Ads API REST searchStream (the SAME proven
pattern as multi-channel/backfill/google_agrankis.py) and writes them straight to
data/<ANCHOR>/<name>.json — so the bulk NEVER round-trips through an agent's context
(no MCP token-cap overflow, no hand-saving). This brings the Google daily in line with
the Microsoft / Meta / all-channels fetch scripts.

Usage:
    python3 fetch_data.py daily <ANCHOR>            # normal: respects the weekly vocab/converted90 cache
    python3 fetch_data.py daily <ANCHOR> --force    # re-pull EVERYTHING incl. weekly caches (clean test re-runs)
    python3 fetch_data.py daily <ANCHOR> --skip-existing   # resume: skip files already present + valid
    python3 fetch_data.py daily <ANCHOR> --outdir /tmp/x   # write elsewhere (verification diffing)

Read-only: every call is a searchStream (SELECT). NEVER mutates Google Ads.
Credentials (OAuth refresh token + developer token) come from ~/.claude.json's `google-ads`
MCP server env — never copied into this folder.

Output shape EXACTLY mirrors what mcp__google-ads__search returned (flat dotted snake_case
keys, typed values), so build.py / cohort_demos.py / pipeline_funnel.py and the search-terms
cleanup consume it unchanged. The weekly vocab/converted90 cache logic is delegated to
build.py (--vocab-due / --refresh-vocab / --converted90-due / --refresh-converted90) so there
is ONE source of truth for the cache.
"""
import sys, os, json, datetime, subprocess, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CUSTOMER_ID = __import__("os").environ.get("GADS_CUSTOMER_ID", "")
API_VERSION = os.environ.get("GADS_API_VERSION", "v21")
DEMO_ACTIONS = "('2. Demo Scheduled','Demo Scheduled Salesforce Conversion')"

# ---- auth + transport (copied verbatim from backfill/google_agrankis.py) ----------------
def creds():
    cfg = json.load(open(os.path.expanduser("~/.claude.json")))
    return cfg["mcpServers"]["google-ads"]["env"]

def access_token(env):
    body = urllib.parse.urlencode({
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["access_token"]

def search_stream(env, token, query, _retried=False):
    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{CUSTOMER_ID}/googleAds:searchStream"
    login = "".join(ch for ch in str(env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")) if ch.isdigit())
    headers = {"Authorization": "Bearer " + token,
               "developer-token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
               "Content-Type": "application/json"}
    if login:
        headers["login-customer-id"] = login
    req = urllib.request.Request(url, data=json.dumps({"query": query}).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            payload = json.load(r)
    except urllib.error.URLError:
        if _retried:
            raise
        return search_stream(env, token, query, _retried=True)   # retry once (transient)
    rows = []
    for batch in payload:
        rows.extend(batch.get("results", []))
    return rows

# ---- proto JSON -> flat dotted snake_case (matches the MCP output exactly) --------------
def _camel(seg):
    parts = seg.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])

INT_FIELDS   = {"campaign.id", "ad_group.id", "metrics.cost_micros",
                "metrics.clicks", "metrics.impressions"}
FLOAT_FIELDS = {"metrics.conversions", "metrics.all_conversions", "metrics.average_cpc",
                "metrics.search_rank_lost_impression_share",
                "metrics.search_budget_lost_impression_share",
                "metrics.search_impression_share"}
BOOL_FIELDS  = {"recommendation.dismissed"}

def _dig(row, field):
    cur = row
    for seg in field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(_camel(seg))
    return cur

def flatten(row, fields):
    out = {}
    for f in fields:
        v = _dig(row, f)
        if f in INT_FIELDS:
            out[f] = int(v) if v not in (None, "") else 0
        elif f in FLOAT_FIELDS:
            out[f] = float(v) if v not in (None, "") else 0.0
        elif f in BOOL_FIELDS:
            out[f] = bool(v)
        else:
            out[f] = v if v is not None else ""
    return out

# ---- the 14 queries (mirror CLAUDE.md "The 14 queries") --------------------------------
def queries(anchor, prior, a29, a89, a364):
    base = "campaign.status = 'ENABLED'"
    search = "campaign.advertising_channel_type = 'SEARCH'"
    def dt(d1, d2): return f"segments.date BETWEEN '{d1}' AND '{d2}'"
    return {
      "spend_cur":  dict(resource="campaign",
        fields=["campaign.id","campaign.name","metrics.cost_micros","metrics.clicks","metrics.impressions"],
        where=[dt(anchor,anchor),base,search,"metrics.cost_micros > 0"]),
      "spend_prior": dict(resource="campaign",
        fields=["campaign.id","campaign.name","metrics.cost_micros","metrics.clicks","metrics.impressions"],
        where=[dt(prior,prior),base,search,"metrics.cost_micros > 0"]),
      "demos_cur": dict(resource="campaign",
        fields=["campaign.name","segments.conversion_action_name","metrics.conversions"],
        where=[dt(anchor,anchor),base,search,f"segments.conversion_action_name IN {DEMO_ACTIONS}"]),
      "demos_prior": dict(resource="campaign",
        fields=["campaign.name","segments.conversion_action_name","metrics.conversions"],
        where=[dt(prior,prior),base,search,f"segments.conversion_action_name IN {DEMO_ACTIONS}"]),
      "ranklost": dict(resource="campaign",
        fields=["campaign.name","metrics.search_rank_lost_impression_share",
                "metrics.search_budget_lost_impression_share","metrics.cost_micros"],
        where=[dt(anchor,anchor),base,search,"metrics.cost_micros > 0"]),
      "searchterms": dict(resource="search_term_view",
        fields=["search_term_view.search_term","campaign.name","ad_group.name","metrics.cost_micros",
                "metrics.impressions","metrics.clicks","metrics.conversions","metrics.average_cpc"],
        where=[dt(anchor,anchor),base,"metrics.cost_micros > 0"],
        order="metrics.cost_micros DESC", limit=500),
      "searchterms_prior": dict(resource="search_term_view",
        fields=["search_term_view.search_term","metrics.average_cpc","metrics.clicks","metrics.cost_micros"],
        where=[dt(prior,prior),base,"metrics.clicks > 0"],
        order="metrics.average_cpc DESC", limit=30),
      "monthspend": dict(resource="campaign",
        fields=["campaign.name","segments.date","metrics.cost_micros"],
        where=["segments.date DURING THIS_MONTH",base,search,"metrics.cost_micros > 0"], limit=1000),
      "converted90": dict(resource="search_term_view",
        fields=["search_term_view.search_term","segments.conversion_action_name","metrics.conversions"],
        where=[dt(a89,anchor),base,f"segments.conversion_action_name IN {DEMO_ACTIONS}","metrics.conversions > 0"],
        limit=5000, weekly="converted90"),
      # SERVED-in-last-365-days keywords — a lean, RECENTLY-ACTIVE "is this term on-topic?"
      # bag-of-words for the bad-terms rescue (~480 keywords vs ~27k enabled, of which only ~300
      # serve in a week). Complete (well under the cap) → deterministic; written to disk daily by
      # this script → no weekly cache. Any on-topic/competitor term this lean vocab surfaces is
      # handled MANUALLY via the curated protection lists + /remove-bad-search-term-keyword-block
      # (David 2026-06-24 — do NOT auto-wire the daily report to those lists; a negatived term stops
      # getting clicks and drops out of the report on its own). Only the keyword TEXT is used.
      "keyword_vocab": dict(resource="keyword_view",
        fields=["ad_group_criterion.keyword.text"],
        where=[dt(a364, anchor), base, "metrics.impressions > 0"], limit=5000),
      "clicks_30d": dict(resource="ad_group",
        fields=["campaign.name","ad_group.name","segments.date","metrics.clicks",
                "metrics.cost_micros","metrics.impressions"],
        where=[dt(a29,anchor),base,"ad_group.status = 'ENABLED'",search,"metrics.clicks > 0"],
        order="segments.date", limit=5000),
      "demos_30d": dict(resource="ad_group",
        fields=["campaign.name","ad_group.name","segments.date","segments.conversion_action_name","metrics.all_conversions"],
        where=[dt(a29,anchor),base,"ad_group.status = 'ENABLED'",search,
               f"segments.conversion_action_name IN {DEMO_ACTIONS}","metrics.all_conversions > 0"],
        order="segments.date", limit=5000),
      "rankis_30d": dict(resource="campaign",
        fields=["campaign.name","segments.date","metrics.search_rank_lost_impression_share",
                "metrics.search_impression_share","metrics.cost_micros"],
        where=[dt(a29,anchor),base,search,"metrics.cost_micros > 0"],
        order="segments.date", limit=1000),
      "recommendations": dict(resource="recommendation",
        fields=["recommendation.type","recommendation.campaign","recommendation.dismissed"],
        where=["recommendation.dismissed = false"], limit=500),
    }

def build_gaql(q):
    sql = f"SELECT {', '.join(q['fields'])} FROM {q['resource']}"
    if q.get("where"):
        sql += " WHERE " + " AND ".join(q["where"])
    if q.get("order"):
        sql += f" ORDER BY {q['order']}"
    if q.get("limit"):
        sql += f" LIMIT {q['limit']}"
    return sql

def _build_due(anchor, kind):
    """Ask build.py whether the weekly cache is due. Returns True (FETCH) / False (SKIP)."""
    flag = "--vocab-due" if kind == "vocab" else "--converted90-due"
    p = subprocess.run([sys.executable, os.path.join(HERE, "build.py"), flag, anchor],
                       capture_output=True, text=True)
    return "FETCH" in (p.stdout or "").upper()

def _build_refresh(anchor, kind):
    flag = "--refresh-vocab" if kind == "vocab" else "--refresh-converted90"
    subprocess.run([sys.executable, os.path.join(HERE, "build.py"), flag, anchor], check=False)

def valid(path, key):
    try:
        d = json.load(open(path))
        return isinstance(d, list) and len(d) > 0 and key in d[0]
    except Exception:
        return False

def main():
    if len(sys.argv) < 3 or sys.argv[1] != "daily":
        sys.exit("usage: fetch_data.py daily <ANCHOR> [--force] [--skip-existing] [--outdir DIR]")
    anchor = sys.argv[2]
    force = "--force" in sys.argv
    skip_existing = "--skip-existing" in sys.argv
    outdir = HERE + f"/data/{anchor}"
    if "--outdir" in sys.argv:
        outdir = sys.argv[sys.argv.index("--outdir") + 1]
    d = datetime.date.fromisoformat(anchor)
    prior = (d - datetime.timedelta(days=7)).isoformat()
    a29   = (d - datetime.timedelta(days=29)).isoformat()
    a89   = (d - datetime.timedelta(days=89)).isoformat()
    a364  = (d - datetime.timedelta(days=364)).isoformat()   # served-365d window for keyword_vocab
    os.makedirs(outdir, exist_ok=True)

    env = creds(); token = access_token(env)
    qs = queries(anchor, prior, a29, a89, a364)
    keyfield = {  # the field build.py preflight asserts per file (for --skip-existing)
        "spend_cur":"campaign.name","spend_prior":"campaign.name","demos_cur":"campaign.name",
        "demos_prior":"campaign.name","ranklost":"campaign.name","searchterms":"search_term_view.search_term",
        "searchterms_prior":"search_term_view.search_term","monthspend":"campaign.name",
        "converted90":"search_term_view.search_term","keyword_vocab":"ad_group_criterion.keyword.text",
        "clicks_30d":"campaign.name","demos_30d":"campaign.name","rankis_30d":"campaign.name",
        "recommendations":"recommendation.type"}

    for name, q in qs.items():
        path = os.path.join(outdir, name + ".json")
        weekly = q.get("weekly")
        if weekly and not force:
            if not _build_due(anchor, weekly):
                print(f"  SKIP   {name} (weekly cache fresh)"); continue
        if skip_existing and not (weekly and force) and valid(path, keyfield[name]):
            print(f"  resume {name} (already present)"); continue
        rows = [flatten(r, q["fields"]) for r in search_stream(env, token, build_gaql(q))]
        json.dump(rows, open(path, "w"))
        print(f"  wrote  {name}.json  ({len(rows)})")
        if weekly:
            _build_refresh(anchor, weekly)
    print(f"DAILY fetch complete → {outdir}")

if __name__ == "__main__":
    main()
