#!/usr/bin/env python3
"""
Microsoft Ads Daily Snapshot — HTML builder.

Reads the raw Microsoft Ads report results saved under data/<ANCHOR_DATE>/ and
renders a single self-contained HTML file to outputs/<ANCHOR_DATE>.html.

Usage:
    python3 build.py 2026-06-03
    (ANCHOR_DATE = the day being reported, i.e. "yesterday". Prior comparison
     period is automatically ANCHOR_DATE minus 7 days.)

This script does NO network calls. `fetch_data.py` pulls the data from the
Microsoft Ads Reporting API and writes the JSON files (in Google-style dotted-key
shape, e.g. "campaign.name"); this script only computes + renders. That data shim
is why this renderer is a near-verbatim copy of the Google daily builder.
See CLAUDE.md → "Daily routine" for the exact reports and file names.
"""
import json, os, re, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Config ────────────────────────────────────────────────────────────────
CPD_TARGET   = 2100          # north-star Cost-Per-Demo monitoring target ($)
RANK_WARN    = 0.40          # rank-lost IS warn / err thresholds
RANK_ERR     = 0.60
RANK_MIN_SPEND = 100         # $ single-day spend floor for the "Rank-lost IS (Highest)" pick:
                             # a tiny-spend campaign at a high IS is noise, not a real signal.
                             # Falls back to the global highest if no campaign clears it.
CPC_RED      = 200           # search-term Avg CPC flagged red above this ($)

# ── Daily Alerts (anomaly detection) — robust baseline + double gate ─────────
# Methodology (see random/2026-06-10-daily-alerts-design.md): per entity × metric,
# the report day is compared against the MEDIAN of the prior 28 days, weekdays
# against weekdays and weekends against weekends. Gate 1 ("is it unusual?") =
# deviation ≥ Z MADs (median absolute deviation, floored so flat series can't
# over-fire). Gate 2 ("does it matter?") = per-metric absolute floors + an entity
# spend floor, which silences low-volume noise (10 imps → 5 imps is never an alert).
# Demos use a count rule instead (0–5/day counts break z-scores). Constants below
# were CALIBRATED on a 90-day replay (/opt/mcp/Skills - Claude Code/Google Ads/google-ads-daily-artifact/random/calibrate_alerts.py, run on this folder) targeting
# 3–5 alerts/day on normal days — don't hand-tweak without re-running it.
ALERTS_CFG = {
    "BASELINE_DAYS": 28,      # lookback window the pool is drawn from
    "MIN_POOL": 4,            # min same-day-type points to judge at all
    "Z": 3.0,                 # Gate 1: |today − median| / MAD must reach this (MS-calibrated)
    "MAD_FLOOR_FRAC": 0.15,   # MAD floor = max(MAD, 15% of median) — no div-by-zero
    "MIN_SPEND": 100.0,       # Gate 2: entity day-spend floor (today or baseline)
    "FLOORS": {               # Gate 2: per-metric absolute |Δ| floors (re-based to
        "spend": 250.0,       #   Google's values 2026-06-10 when the CONFIRM_REL
        "spend_ag": 100.0,    #   recency gate took over continuation suppression)
        "clicks": 20,         #   clicks
        "imps": 500,          #   impressions
        "cpc": 5.0,           #   avg CPC ($)
        "rankis": 0.10,       #   rank-lost IS (share points, 0–1)
        "imprshare": 0.10,    #   impression share (share points, 0–1)
    },
    "MAD_MIN": {              # absolute MAD floors (a 0-variance/0-baseline series
        "spend": 25.0,        #  must not produce absurd deviation scores)
        "clicks": 5.0, "imps": 100.0, "cpc": 2.0, "rankis": 0.02, "imprshare": 0.02,
    },
    "CPC_MIN_CLICKS": 20,     # CPC judged only on days with at least this many clicks
    "DEMO_MIN_BASE": 3.0,     # demo count rule: needs baseline median ≥ this
    "DEMO_SPIKE": 3.0,        #   spike = today ≥ this × baseline median
    "DEMO_ABS": 3.0,          #   and |Δ| ≥ this
    "RED_DEV": 1.5,           # 🔴 when deviation ≥ RED_DEV × Z …
    "RED_IMPACT": 1000.0,     # … or est. $ impact ≥ this; else 🟡
    "MAX_SHOW": 10,           # rows shown before the "+N more" expander
    "CONFIRM_REL": 0.30,      # final recency gate: yesterday must differ by ≥ this
                              # share from BOTH the prior day AND the same weekday
                              # last week, else the move is a continuation of a
                              # known level shift (deliberate tCPA/budget ramps)
                              # that the 28-day median hasn't absorbed — old news,
                              # don't re-fire daily. Applies to MAD metrics only
                              # (demos keep the count rule).
}

BAD_SPEND_FLOOR = 5          # Floor for the default-BAD path (off-vocab, zero demos). Was $20
                             # to compensate for the overly-permissive vocab rescue; now that the
                             # rescue uses the PM_GENERIC-stripped distinctive-token logic (see
                             # tokens_distinctive / classify step 6), the floor can match the spend
                             # gate. Taxonomy + competitor hits still flag at any spend.
OVERLAP_RESCUE = 0.50        # ≥ this share of a term's tokens must be in the account
                             # keyword vocabulary to rescue it from a BAD verdict.
                             # (Per-ad-group matching is impossible here: the keyword_view
                             #  join returns a stale single ad-group label — see CLAUDE.md.
                             #  We use a GLOBAL account vocabulary instead, so the bar is
                             #  higher than the taxonomy's per-ad-group 50%.)
# THE Microsoft Ads conversion goal that counts as a "Demo Scheduled".
# Investigated live 2026-06-04 — the account has TWO demo goals that track via DIFFERENT
# mechanisms and are NOT the same count:
#   • Demo_Scheduled        = OfflineConversion, imported from Salesforce/CRM ← the direct
#                             analog of the Google report's "Demo Scheduled Salesforce Conversion".
#   • Online Demo Scheduled = Url goal (real-time website pixel on the booking page).
# Different campaigns are wired to different goals (30d: New_search 44 offline / 0 online;
# Competitors 0 offline / 17 online; Brand 28 / 36; Exact 4 / 0), so SUMMING both double-counts
# Brand. We use the single CRM-confirmed goal Demo_Scheduled to match the Google demo KPI
# (apples-to-apples across platforms) and avoid double-counting.
# ⚠️ KNOWN GAP: the *Competitors* campaign's offline import attributes 0 Demo_Scheduled — its
#    demos only fire the web "Online Demo Scheduled" goal. That's a Bing offline-import attribution
#    gap (worth fixing in the account), NOT a report bug; Competitors shows ~0 demos here.
# To change the definition, edit this list (e.g. ["Online Demo Scheduled"] for the real-time web
# goal, or list both to sum) AND re-run fetch_data.py — the goal filter is applied at fetch time.
# All account goals seen: ARR, Demo_Scheduled, Demo_Scheduled_10_Units, Lead_Created,
# Online Demo Scheduled, Opportunity (+ paused MQL, Marketing Qualified Lead).
DEMO_GOALS = ["Demo_Scheduled"]

# Chart.js 4.5.0 UMD via jsDelivr + SRI (same pinned build as the period reports —
# see CLAUDE.md "Charts"). crossorigin=anonymous is required for SRI enforcement.
CHARTJS_SRC = "https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js"
CHARTJS_SRI = "sha384-XcdcwHqIPULERb2yDEM4R0XaQKU3YnDsrTmjACBZyfdVVqjh6xQ4/DCMd7XLcA6Y"
CLICKS_DAYS = 30             # "Clicks per Day" trend window

# ── Bad-term taxonomy (mirrors google-ads-negative-keyword-alerts) ──────────
TAXONOMY = [
    ("WRONG_PRODUCT", re.compile(r"self[ -]?storage|mini storage|airbnb|vrbo|short[ -]?term rental|vacation rental|boat slip|marina|parking (lot|garage)|rv (park|lot)|campground|hotel|hostel|motel|bed and breakfast|b&b|timeshare", re.I)),
    ("WRONG_INTENT",  re.compile(r"\bjobs?\b|careers?|hiring|salar(y|ies)|free download|crack|torrent|pirate|keygen|complaint|lawsuit|\bsue\b|refund|scam|wikipedia|encyclopedia", re.I)),
    ("WRONG_AUDIENCE",re.compile(r"passive investor|hands-off investor|fund of funds|syndicat(e|ion|or)|\breit\b|real estate investment trust|accredited investor", re.I)),
    ("LOW_INTENT",    re.compile(r"history of|\bwiki\b|market trends|thesis|coursework", re.I)),
]
COMPETITORS = re.compile(
    r"appfolio|yardi|buildium|realpage|rentec|tenant ?cloud|rent ?manager|propertyware|magicdoor"
    r"|hemlane|innago|\bavail\b|turbotenant|\bcozy\b"
    r"|entrata|simplifyem|rentredi|rent ?redi|rentvine|stessa|landlord studio"
    r"|re.?leased|resman|stratafolio|propertyboss|knock crm|managecasa|manage ?casa",
    re.I)
STOPWORDS = {
    "the","a","an","for","of","to","and","or","in","on","my","your","best","top",
    "software","app","apps","tool","tools",
    # common function words that aren't already covered but appear in search terms
    "with","by","from","at","as","is","are","was","no","not","its","it","this",
    "that","vs","per","via","etc","how","what","when","where","who","which",
}

# Generic property-management words that appear in almost every PM search term.
# These are excluded when computing distinctive-token overlap (see tokens_distinctive)
# so that a term like "bluesky property manager" doesn't get rescued purely because
# "property" and "manager" happen to be in our keyword vocabulary. The key identifier
# ("bluesky") is not in vocab → BAD.  Terms made up entirely of PM_GENERIC words
# (e.g. "rental property management") fall back to the classic full-overlap rescue.
PM_GENERIC = {
    # Direct PM domain words
    "management","manager","managers","managing",
    "portal","portals",
    "tenant","tenants",
    "payment","payments","pay","paying",
    "review","reviews",
    "property","properties","real","estate",
    "rent","rental","rentals",
    "resident","residents","residential",
    "landlord","landlords",
    "building","buildings",
    "platform","platforms",
    "solution","solutions",
    "service","services",
    "system","systems",
    "operations","screening",
    # Common PM feature words (also used in competitor searches)
    "background","check","checks",          # "software with background checks"
    "fees","fee",                            # "property management fees" research
    # Common English modifiers that aren't brand identifiers
    "rated","reliable","affordable","modern","smart","advanced","automated",
    "simple","easy","fast","quick","efficient","complete","full","basic",
    "same","new","old","free","cheap","online","digital","cloud","remote",
}

# ── [UNUSED IN MICROSOFT BUILD] Google Ads "recommendations" ──────────────────
# Microsoft Advertising has no equivalent "recommendations" API, so the Microsoft daily
# report OMITS the Recommendations section and its top neg-keyword alert box. The REC_*
# constants and render_recommendations() below are dead code kept from the Google original
# for reference only — nothing in this build calls them.
# Recommendation TYPES the user has opted OUT of (pure noise for this account). These
# are hidden from the alert list (only an aggregate "hidden" tally is shown). Mapped
# from the user's 9 opt-outs; ad-strength variants beyond RSAs are treated as the same
# low-signal class as #2 ("improve your responsive search ads").
REC_EXCLUDE = {
    "DYNAMIC_IMAGE_EXTENSION_OPT_IN",                  # 1 add dynamic images
    "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH",        # 2 improve your RSAs
    "IMPROVE_DEMAND_GEN_AD_STRENGTH",                  # 2 (Demand Gen ad-strength — same class)
    "IMPROVE_PERFORMANCE_MAX_AD_STRENGTH",             # 2 (PMax ad-strength — same class)
    "RESPONSIVE_SEARCH_AD",                            # 3 add responsive search ads
    "DISPLAY_EXPANSION_OPT_IN",                        # 4 use Display Expansion
    "REMOVE_REDUNDANT_KEYWORDS",                       # 5 remove redundant keywords
    "KEYWORD",                                         # 6 add new keywords
    "SEARCH_PARTNERS_OPT_IN",                          # 7 expand reach w/ search partners
    "LEAD_FORM_ASSET",                                 # 8 add lead form ads
    "MOVE_UNUSED_BUDGET", "MARGINAL_ROI_CAMPAIGN_BUDGET",  # 9 portfolio strategy w/ shared budget
    "FORECASTING_CAMPAIGN_BUDGET",                     # 9 (budget-forecast variant)
}
# Friendly labels for the types we DO surface (humanized fallback otherwise).
REC_LABELS = {
    "TARGET_CPA_OPT_IN": "Switch to Target CPA bidding",
    "TARGET_ROAS_OPT_IN": "Switch to Target ROAS bidding",
    "MAXIMIZE_CONVERSIONS_OPT_IN": "Switch to Maximize Conversions",
    "MAXIMIZE_CONVERSION_VALUE_OPT_IN": "Switch to Maximize Conversion Value",
    "ENHANCED_CPC_OPT_IN": "Enable Enhanced CPC",
    "RAISE_TARGET_CPA": "Raise Target CPA", "LOWER_TARGET_ROAS": "Lower Target ROAS",
    "RAISE_TARGET_CPA_BID_TOO_LOW": "Raise Target CPA (bid too low)",
    "FORECASTING_SET_TARGET_CPA": "Set a Target CPA (forecast)",
    "FORECASTING_SET_TARGET_ROAS": "Set a Target ROAS (forecast)",
    "USE_BROAD_MATCH_KEYWORD": "Use broad match keywords",
    "KEYWORD_MATCH_TYPE": "Change a keyword's match type",
    "OPTIMIZE_AD_ROTATION": "Optimize ad rotation",
    "PERFORMANCE_MAX_OPT_IN": "Add a Performance Max campaign",
    "SITELINK_ASSET": "Add sitelink assets", "CALLOUT_ASSET": "Add callout assets",
    "CALL_ASSET": "Add call assets", "REFRESH_CUSTOMER_MATCH_LIST": "Refresh Customer Match list",
    "IMPROVE_GOOGLE_TAG_COVERAGE": "Improve Google tag coverage",
    "REMOVE_CONFLICTING_NEGATIVE_KEYWORDS": "Remove conflicting negative keywords",
}
# Types that get HIGHLIGHTED (the user cares most about negatives blocking traffic /
# conflicts). Matched as substrings against the type enum.
REC_HIGHLIGHT = ("NEGATIVE", "CONFLICT", "BLOCK", "DISAPPROV", "SUSPEN")

# ── Helpers ─────────────────────────────────────────────────────────────────
def load(d, name):
    p = os.path.join(ROOT, "data", d, name)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)

def load_known_bad_brands():
    """Load references/known-bad-brands.txt → frozenset of lowercase phrase strings.

    Each non-blank, non-comment line is a phrase David confirmed as a bad brand/portal.
    The classifier checks if the search term CONTAINS any of these as a substring
    (case-insensitive). David edits this file directly to add confirmed negatives —
    no code change needed. Equivalent to the Google Ads skill's Notion audit list.
    """
    p = os.path.join(ROOT, "references", "known-bad-brands.txt")
    if not os.path.exists(p):
        return frozenset()
    with open(p) as f:
        return frozenset(
            line.strip().lower()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        )

# ── Account keyword vocabulary — pulled at most WEEKLY, then cached ──────────
# The 523 KB keyword_vocab pull is used only to build a bag-of-words "is this term
# on-topic?" set, which barely changes day to day. The routine pulls it at most once
# a week (vocab_due → FETCH/SKIP) and reuses references/keyword_vocab_cache.json the
# other days. Removes the single biggest daily pull + one of two overflow-prone files.
VOCAB_CACHE   = os.path.join(ROOT, "references", "keyword_vocab_cache.json")
VOCAB_MAX_AGE = 7            # days; re-pull the vocabulary at most weekly

def load_vocab(anchor):
    """Vocabulary rows: prefer the day's fresh pull (data/<anchor>/keyword_vocab.json),
    else fall back to the weekly cache (on days the routine skipped the pull)."""
    rows = load(anchor, "keyword_vocab.json")
    if rows:
        return rows
    if os.path.exists(VOCAB_CACHE):
        with open(VOCAB_CACHE) as f:
            c = json.load(f)
        return c.get("rows", []) if isinstance(c, dict) else (c or [])
    return []

def vocab_due(anchor):
    """'FETCH' if the cache is missing or >= VOCAB_MAX_AGE days older than `anchor`,
    else 'SKIP'. Timezone-free (compares anchor dates only)."""
    if not os.path.exists(VOCAB_CACHE):
        return "FETCH"
    try:
        with open(VOCAB_CACHE) as f:
            c = json.load(f)
        fetched = c.get("fetched") if isinstance(c, dict) else None
        if not fetched:
            return "FETCH"
        age = (datetime.strptime(anchor, "%Y-%m-%d")
               - datetime.strptime(fetched, "%Y-%m-%d")).days
        return "FETCH" if (age >= VOCAB_MAX_AGE or age < 0) else "SKIP"
    except Exception:
        return "FETCH"

def refresh_vocab(anchor):
    """Copy the day's fresh keyword_vocab.json into the weekly cache, stamped with the
    anchor date. The routine calls this right after a FETCH-day pull."""
    rows = load(anchor, "keyword_vocab.json")
    if not rows:
        print(f"refresh-vocab: no data/{anchor}/keyword_vocab.json to cache")
        return
    os.makedirs(os.path.dirname(VOCAB_CACHE), exist_ok=True)
    with open(VOCAB_CACHE, "w") as f:
        json.dump({"fetched": anchor, "rows": rows}, f, separators=(",", ":"))
    print(f"refresh-vocab: cached {len(rows)} vocab rows (fetched {anchor})")

def g(row, key):
    """Get a field whether stored flat ('campaign.name') or nested ('campaign':{'name'})."""
    if key in row:
        return row[key]
    parts = key.split(".")
    cur = row
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur

def micros(v):  return (float(v) / 1e6) if v not in (None, "") else 0.0
def is_branded(name): return bool(re.search(r"\bbrand", name or "", re.I))  # MS brand campaign = "*Search: Brand - JF (US)"
def is_comp_campaign(name): return bool(re.search(r"competitor", name or "", re.I))

def money(v):   return "—" if v is None else "$" + format(round(v), ",d")
def money2(v):  return "—" if v is None else "$" + format(v, ",.2f")
def conv(v):
    if v is None: return "—"
    return str(round(v)) if abs(v - round(v)) < 0.05 else format(v, ".1f")
def intf(v):    return "—" if v is None else format(int(round(v)), ",d")
def pct(v):     return "—" if v is None else format(v * 100, ".1f") + "%"
def sdiff(cur, prior, kind="money", lower_is_better=False, neutral=False):
    """Signed difference (cur − prior) as a colored span for the summary Difference
    column. kind: 'money' | 'conv'. green=good / red=bad unless neutral."""
    if cur is None or prior is None:
        return '<span class="delta flat">—</span>'
    dv = cur - prior; mag = abs(dv); sign = "+" if dv >= 0 else "−"
    if kind == "money":
        body = sign + "$" + format(int(round(mag)), ",d"); zero = round(mag) == 0
    else:
        body = sign + (str(int(round(mag))) if abs(mag - round(mag)) < 0.05 else format(mag, ".1f")); zero = mag < 0.05
    if zero:
        return '<span class="delta flat">0</span>'
    cls = "flat" if neutral else ("good" if ((dv < 0) if lower_is_better else (dv > 0)) else "bad")
    return f'<span class="delta {cls}">{body}</span>'
def esc(s):
    return (str(s or "").replace("&","&amp;").replace("<","&lt;")
            .replace(">","&gt;").replace('"',"&quot;"))

def tip(html, align="left"):
    """Info icon ⓘ with a hover/focus tooltip carrying the descriptive prose that used to sit
    under a section title. `html` is already-escaped markup (may contain <p>/<b>/<a>). align
    'right' anchors the popover to the icon's right edge (for titles near the page edge)."""
    cls = "tip tip-r" if align == "right" else "tip"
    return f'<span class="{cls}" tabindex="0" role="note" aria-label="Details">i<span class="tipbox">{html}</span></span>'

def short_name(n):
    return re.sub(r"\s{2,}", " ", re.sub(r"\bsearch\b","", re.sub(r"\bcampaign\b","", n or "", flags=re.I), flags=re.I)).strip()

def tokens(s):
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if t and t not in STOPWORDS]

def tokens_distinctive(s):
    """Return tokens that are neither STOPWORDS nor PM_GENERIC.

    These are the 'brand/company identifier' tokens — the ones that signal
    the search is about a specific non-DoorLoop product or portal.  If the
    list is non-empty AND any token is absent from the account vocabulary,
    the term is likely a tenant-portal lookup or competitor search rather
    than a genuine PM-software buying signal.

    If the list is EMPTY (the entire term is generic PM words), the caller
    falls back to the classic full-overlap rescue so queries like
    "rental property management" still get rescued correctly.
    """
    all_stop = STOPWORDS | PM_GENERIC
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
            if t and t not in all_stop]

# ── Status logic ────────────────────────────────────────────────────────────
def status_cpd(v):
    if v is None: return "—"
    if v <= CPD_TARGET: return "✅"
    if v <= CPD_TARGET * 1.3: return "🟡"
    return "🔴"

def status_demos(cur, prior):
    if not prior: return "✅" if cur > 0 else "🟡"
    r = cur / prior
    return "✅" if r >= 1 else ("🟡" if r >= 0.8 else "🔴")

def status_rank(worst):
    if worst is None: return "—"
    if worst >= RANK_ERR: return "🔴"
    if worst >= RANK_WARN: return "🟡"
    return "✅"

# ── Aggregation ─────────────────────────────────────────────────────────────
def agg_spend(rows):
    out = {}
    for r in rows:
        n = g(r, "campaign.name") or ""
        out.setdefault(n, {"name": n, "cost": 0.0, "clicks": 0, "imps": 0})
        out[n]["cost"]   += micros(g(r, "metrics.cost_micros"))
        out[n]["clicks"] += int(g(r, "metrics.clicks") or 0)
        out[n]["imps"]   += int(g(r, "metrics.impressions") or 0)
    return list(out.values())

def agg_demos(rows):
    out = {}
    for r in rows:
        n = g(r, "campaign.name") or ""
        out[n] = out.get(n, 0.0) + float(g(r, "metrics.all_conversions") or g(r, "metrics.conversions") or 0)
    return out  # {name: demos}

def load_cohort_demos(anchor):
    """Daily Brain Day-0 cohort override, if the routine wrote data/<anchor>/cohort_demos.json
    (SOURCE=bing, via cohort_demos.py daily). Absent → platform Demo_Scheduled demos (the daily
    fallback). load() returns [] when missing (falsy) and the parsed dict when present (truthy)."""
    obj = load(anchor, "cohort_demos.json")
    return obj or None

def load_pipeline(anchor):
    """Booked-on-day Opportunities / Accounts / ARR series, if the routine wrote
    data/<anchor>/pipeline_30d.json (Brain event-funnel via pipeline_funnel.py, SF fallback).
    Absent → None → the grid simply omits the opps/accounts/ARR tiles."""
    obj = load(anchor, "pipeline_30d.json")
    return obj or None

def sum_group(spend_rows, demos_map, excl_branded):
    keep = (lambda n: not is_branded(n)) if excl_branded else (lambda n: True)
    spend = sum(r["cost"] for r in spend_rows if keep(r["name"]))
    demos = sum(d for n, d in demos_map.items() if keep(n))
    cpd = (spend / demos) if demos > 0 else None
    return spend, demos, cpd

# ── Search-term CPC (top 20) ────────────────────────────────────────────────
def search_term_cpcs(rows):
    agg = {}
    for r in rows:
        term = g(r, "search_term_view.search_term") or ""
        camp = g(r, "campaign.name") or ""
        ag   = g(r, "ad_group.name") or ""
        k = (term, camp, ag)
        a = agg.setdefault(k, {"term": term, "camp": camp, "ag": ag, "clicks": 0, "cost": 0.0})
        a["clicks"] += int(g(r, "metrics.clicks") or 0)
        a["cost"]   += micros(g(r, "metrics.cost_micros"))
    out = []
    for a in agg.values():
        a["cpc"] = (a["cost"] / a["clicks"]) if a["clicks"] > 0 else 0.0
        out.append(a)
    out.sort(key=lambda x: x["cpc"], reverse=True)
    return out

def top3_cpc(rows):
    return " / ".join(money(o["cpc"]) for o in search_term_cpcs(rows)[:3]) or "—"

# ── Rank-lost IS (cost-weighted, ranked worst-first) ────────────────────────
def rank_lost_ranked(rows):
    """Campaigns ranked by cost-weighted rank-lost IS, worst first. Each item carries
    its single-day cost so the caller can apply a spend floor (a tiny-spend campaign at
    a high IS is noise, not a real signal — see RANK_MIN_SPEND)."""
    agg = {}
    for r in rows:
        n = g(r, "campaign.name") or ""
        cost = micros(g(r, "metrics.cost_micros"))
        rl = float(g(r, "metrics.search_rank_lost_impression_share") or 0)
        a = agg.setdefault(n, {"w": 0.0, "c": 0.0})
        a["w"] += rl * cost; a["c"] += cost
    items = [{"name": n, "rl": (v["w"]/v["c"] if v["c"] > 0 else 0), "cost": v["c"]}
             for n, v in agg.items()]
    items.sort(key=lambda x: x["rl"], reverse=True)
    return items

# ── Search Lost IS (Budget) — report-day only, names every campaign losing volume ──
def budget_lost(rows):
    """Campaigns losing search impression share to BUDGET on the report day.
    Returns [{name, bl}] for campaigns with budget-lost IS > 0, worst first.
    (Single-day file → no prior-week comparison, per the spec.)"""
    out = []
    for r in rows:
        n = g(r, "campaign.name") or ""
        bl = float(g(r, "metrics.search_budget_lost_impression_share") or 0)
        if bl > 0:
            out.append({"name": n, "bl": bl})
    out.sort(key=lambda x: x["bl"], reverse=True)
    return out

# ── Daily Alerts engine (robust baseline + double gate — see ALERTS_CFG) ─────
def _median(xs):
    s = sorted(xs); n = len(s)
    if n == 0: return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0

def _mad(xs, med):
    return _median([abs(x - med) for x in xs])

# Per-metric display/eval meta: (label, floor key, formatter, direction)
# direction: which way is BAD — 'up', 'down', or 'neutral' (spend: a swing either
# way is worth a look, but neither direction is automatically bad).
ALERT_METRICS = {
    "spend":     ("Spend",        "spend",     lambda v: money(v),  "neutral"),
    "clicks":    ("Clicks",       "clicks",    lambda v: intf(v),   "down"),
    "imps":      ("Impressions",  "imps",      lambda v: intf(v),   "down"),
    "cpc":       ("Avg CPC",      "cpc",       lambda v: money2(v), "up"),
    "rankis":    ("Rank-Lost IS", "rankis",    lambda v: pct(v),    "up"),
    "imprshare": ("Impr Share",   "imprshare", lambda v: pct(v),    "down"),
    "demos":     ("Demos",        None,        lambda v: conv(v),   "down"),
}

def alert_entities(data):
    """Flatten clicks_daily_data() output into alertable entities.
    Account roll-ups + campaigns come from the All-Campaigns chart entry (its
    campaign datasets carry rankis/imprshare); ad groups from the per-campaign
    entries (their 'All ad groups' total is skipped — it duplicates the campaign).
    Ad groups are spend/clicks/imps/CPC only (demos too sparse — by design)."""
    ents = []
    camps = data.get("campaigns") or []
    if not camps:
        return ents
    root = camps[0]["datasets"][0]["label"] if camps[0]["datasets"] else "All campaigns"
    for ds in camps[0]["datasets"]:
        kind = "account" if (ds.get("total") or ds.get("emph")) else "campaign"
        parent = None if ds.get("total") else root   # Ex-Branded + campaigns roll up to All
        ents.append({"kind": kind, "name": ds["label"], "parent": parent, "ds": ds})
    for c in camps[1:]:
        for ds in c["datasets"]:
            if ds.get("total"):
                continue
            ents.append({"kind": "adgroup", "name": f"{c['name']} › {ds['label']}",
                         "parent": c["name"], "ds": ds})
    return ents

def compute_alerts(data, anchor_date, cfg=ALERTS_CFG, eval_idx=None):
    """Material day-over-baseline changes for the day at `eval_idx` (default: the
    last day = the report day). `data` = clicks_daily_data(..., daily=True) output.
    Exposing eval_idx lets the calibration replay (random/calibrate_alerts.py)
    reuse THIS exact code — the detector is never duplicated."""
    n = len(data.get("labels") or [])
    if not n:
        return []
    end = datetime.strptime(anchor_date, "%Y-%m-%d").date()
    days = [end - timedelta(days=n - 1 - i) for i in range(n)]
    if eval_idx is None:
        eval_idx = n - 1
    wknd = days[eval_idx].weekday() >= 5
    pool_idx = [i for i in range(max(0, eval_idx - cfg["BASELINE_DAYS"]), eval_idx)
                if (days[i].weekday() >= 5) == wknd]
    if len(pool_idx) < cfg["MIN_POOL"]:
        return []
    has_demos = bool(data.get("has_demos"))

    def mad_check(y, pool, floor_abs, mad_min):
        vals = [v for v in pool if v is not None]
        if y is None or len(vals) < cfg["MIN_POOL"]:
            return None
        med = _median(vals)
        madf = max(_mad(vals, med), abs(med) * cfg["MAD_FLOOR_FRAC"], mad_min)
        dev = abs(y - med) / madf
        if dev < cfg["Z"] or abs(y - med) < floor_abs:
            return None
        return med, dev

    def is_new_move(series, floor_abs):
        """Final recency gate: True only when yesterday differs materially from
        BOTH the prior day AND the same weekday last week. A continuation of a
        known level shift (e.g. a deliberate tCPA/budget ramp) looks anomalous
        vs the slow 28-day median for weeks — but it's old news after day one,
        so similarity to either recent reference suppresses the alert."""
        y = series[eval_idx]
        if y is None:
            return False
        for back in (1, 7):
            j = eval_idx - back
            if j < 0:
                continue
            ref = series[j]
            if ref is None:
                continue
            d = abs(y - ref)
            if d < floor_abs or d < abs(ref) * cfg["CONFIRM_REL"]:
                return False
        return True

    alerts = []
    def add(ent, metric, y, med, dev, impact, fresh=True):
        label, _, fmt, direction = ALERT_METRICS[metric]
        d = y - med
        worse = (d > 0) if direction == "up" else ((d < 0) if direction == "down" else None)
        sev = "red" if (dev >= cfg["RED_DEV"] * cfg["Z"] or impact >= cfg["RED_IMPACT"]) else "yellow"
        alerts.append({
            "sev": sev, "entity": ent["name"], "kind": ent["kind"],
            "parent": ent.get("parent"), "metric": metric, "fresh": fresh,
            "label": label, "y": y, "base": med, "delta": d,
            "pct": (d / med if med else None), "dev": dev, "impact": impact,
            "fmt": fmt, "worse": worse,
        })

    for ent in alert_entities(data):
        ds = ent["ds"]
        cost, clicks, imps = ds["cost"], ds["clicks"], ds.get("imps") or [0] * n
        spend_y = cost[eval_idx]
        spend_med = _median([cost[i] for i in pool_idx]) or 0.0
        if max(spend_y, spend_med) < cfg["MIN_SPEND"]:
            continue   # Gate 2: too small to matter, whatever it did
        clicks_med = _median([clicks[i] for i in pool_idx]) or 0.0
        imps_med = _median([imps[i] for i in pool_idx]) or 0.0
        base_cpc = (spend_med / clicks_med) if clicks_med > 0 else 0.0

        # spend / clicks / impressions — straight MAD on the daily series
        for metric, series, floor_key, impact_fn in (
            ("spend",  cost,   ("spend_ag" if ent["kind"] == "adgroup" else "spend"),
             lambda d: abs(d)),
            ("clicks", clicks, "clicks", lambda d: abs(d) * base_cpc),
            ("imps",   imps,   "imps",
             lambda d: abs(d) * (spend_med / imps_med if imps_med > 0 else 0.0)),
        ):
            r = mad_check(series[eval_idx], [series[i] for i in pool_idx],
                          cfg["FLOORS"][floor_key], cfg["MAD_MIN"][metric])
            if r:
                med, dev = r
                add(ent, metric, series[eval_idx], med, dev,
                    impact_fn(series[eval_idx] - med),
                    fresh=is_new_move(series, cfg["FLOORS"][floor_key]))

        # CPC — only judged on ≥ CPC_MIN_CLICKS days (both today and the pool)
        if clicks[eval_idx] >= cfg["CPC_MIN_CLICKS"]:
            cpc_series = [(cost[i] / clicks[i]) if clicks[i] >= cfg["CPC_MIN_CLICKS"] else None
                          for i in range(n)]
            cpc_pool = [cpc_series[i] for i in pool_idx if cpc_series[i] is not None]
            r = mad_check(cpc_series[eval_idx], cpc_pool,
                          cfg["FLOORS"]["cpc"], cfg["MAD_MIN"]["cpc"])
            if r:
                med, dev = r
                add(ent, "cpc", cpc_series[eval_idx], med, dev,
                    abs(cpc_series[eval_idx] - med) * clicks[eval_idx],
                    fresh=is_new_move(cpc_series, cfg["FLOORS"]["cpc"]))

        # IS metrics — campaign-level only (no ad-group split); $ at stake ≈ Δ × spend
        if ent["kind"] != "adgroup":
            for metric in ("rankis", "imprshare"):
                series = ds.get(metric)
                if not series:
                    continue
                r = mad_check(series[eval_idx], [series[i] for i in pool_idx],
                              cfg["FLOORS"][metric], cfg["MAD_MIN"][metric])
                if r:
                    med, dev = r
                    add(ent, metric, series[eval_idx], med, dev,
                        abs(series[eval_idx] - med) * spend_med,
                        fresh=is_new_move(series, cfg["FLOORS"][metric]))

        # Demos — count rule, account/campaign only (0–5/day counts break z-scores)
        if ent["kind"] != "adgroup" and has_demos:
            demos = ds["demos"]
            d_y = demos[eval_idx]
            d_med = _median([demos[i] for i in pool_idx]) or 0.0
            if d_med >= cfg["DEMO_MIN_BASE"]:
                zero_day = d_y == 0
                spike = (d_y >= cfg["DEMO_SPIKE"] * d_med
                         and abs(d_y - d_med) >= cfg["DEMO_ABS"])
                if zero_day or spike:
                    dev = (abs(d_y - d_med) / max(_mad([demos[i] for i in pool_idx], d_med), 1.0))
                    add(ent, "demos", d_y, d_med, dev, abs(d_y - d_med) * CPD_TARGET)

    parents = {e["name"]: e.get("parent") for e in alert_entities(data)}
    return dedupe_alerts(alerts, parents)

# Correlated-metric groups: one underlying event (e.g. a spend surge) moves spend,
# clicks, impressions and CPC together — that's ONE alert, not four. The IS metrics
# and demos are independent signals and stay their own groups.
ALERT_GROUP = {"spend": "volume", "clicks": "volume", "imps": "volume", "cpc": "volume",
               "rankis": "rankis", "imprshare": "imprshare", "demos": "demos"}

def _pct_txt(a):
    return "—" if a["pct"] is None else format(a["pct"] * 100, "+.0f") + "%"

def dedupe_alerts(alerts, parents):
    """Collapse the raw alert list into one row per underlying event:
    1. Same entity + same metric group → one row (highest-impact metric leads,
       the rest land in `also`).
    2. A child whose ancestor (ad group → campaign → All campaigns) alerted on the
       same group is folded into the ancestor's `drivers` list instead of getting
       its own row — an account-wide spend jump is one event, not 1+N rows.
    3. Rows with NO fresh metric are dropped at the end (the recency gate): a
       continuation of a known level shift is old news, and its folded drivers
       (the ad groups carrying a known ramp) vanish with it. The fold happens
       BEFORE the freshness drop on purpose — otherwise suppressing a stale
       campaign row would un-fold its ad groups into new standalone noise.
       Freshness propagates UP on fold: a fresh child folding into a stale
       ancestor keeps that ancestor's row alive (something new happened inside
       it), so a fresh driver is never silently swallowed."""
    by_ent = {}
    for a in alerts:
        by_ent.setdefault((a["entity"], ALERT_GROUP[a["metric"]]), []).append(a)
    merged = []
    for (ent, grp), items in by_ent.items():
        items.sort(key=lambda x: -x["impact"])
        prim = dict(items[0])
        prim["group"] = grp
        prim["also"] = [f"{i['label']} {_pct_txt(i)}" for i in items[1:]]
        prim["drivers"] = []
        prim["sev"] = "red" if any(i["sev"] == "red" for i in items) else "yellow"
        prim["fresh"] = any(i.get("fresh", True) for i in items)
        merged.append(prim)
    idx = {(m["entity"], m["group"]): m for m in merged}
    KORD = {"account": 0, "campaign": 1, "adgroup": 2}
    out = []
    for m in sorted(merged, key=lambda x: KORD[x["kind"]]):
        anc, folded = parents.get(m["entity"]), False
        while anc is not None:
            p = idx.get((anc, m["group"]))
            if p is not None and not p.get("_folded"):
                short = m["entity"].split(" › ")[-1]
                p["drivers"].append(f"{short} {m['label']} {_pct_txt(m)}")
                if m["fresh"]:
                    p["fresh"] = True
                m["_folded"] = folded = True
                break
            anc = parents.get(anc)
        if not folded:
            out.append(m)
    out = [m for m in out if m["fresh"]]   # AFTER all folds — a child can refresh its parent
    out.sort(key=lambda a: -a["impact"])
    return out

def render_alerts_section(alerts, available, cfg=ALERTS_CFG):
    """HTML for the anomaly half of the Daily Alerts card. Returns (body, count)."""
    if not available:
        return ('<div class="warn-banner">⚠️ Anomaly alerts unavailable — the 30-day trend data '
                '(clicks_30d.json) was not fetched for this date, so there is no baseline to '
                'compare against.</div>', 0)
    if not alerts:
        return ('<p class="ok-line">✅ No material changes vs the 28-day baseline '
                '(campaigns, ad groups &amp; account roll-ups).</p>', 0)
    KIND = {"account": "Account", "campaign": "Campaign", "adgroup": "Ad group"}
    rows = ""
    for i, a in enumerate(alerts):
        hide = ' class="alert-more"' if i >= cfg["MAX_SHOW"] else ""
        icon = "🔴" if a["sev"] == "red" else "🟡"
        cls = "bad" if a["worse"] else ("flat" if a["worse"] is None else "good")
        arrow = "▲" if a["delta"] > 0 else "▼"
        pc = "—" if a["pct"] is None else format(a["pct"] * 100, "+.0f") + "%"
        sub = ""
        if a.get("also"):
            sub += "also: " + " · ".join(esc(x) for x in a["also"])
        if a.get("drivers"):
            dr = a["drivers"]
            sub += ("<br>" if sub else "") + "driven by: " + " · ".join(esc(x) for x in dr[:4])
            if len(dr) > 4:
                sub += f" · +{len(dr) - 4} more"
        sub = f'<div class="alert-sub">{sub}</div>' if sub else ""
        rows += (f'<tr{hide}><td class="status">{icon}</td>'
                 f'<td><span class="kind-tag">{KIND[a["kind"]]}</span> {esc(a["entity"])}{sub}</td>'
                 f'<td>{esc(a["label"])}</td>'
                 f'<td class="num">{a["fmt"](a["y"])}</td>'
                 f'<td class="num muted">{a["fmt"](a["base"])}</td>'
                 f'<td class="num"><span class="delta {cls}">{arrow} {pc}</span></td>'
                 f'<td class="num">{money(a["impact"])}</td></tr>')
    body = ('<table><thead><tr><th class="status"></th><th>Entity</th><th>Metric</th>'
            '<th class="num">Yesterday</th><th class="num">Baseline</th>'
            '<th class="num">Δ</th><th class="num">Est. impact</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')
    extra = len(alerts) - cfg["MAX_SHOW"]
    if extra > 0:
        body += (f'<button class="alert-toggle" id="alertMoreBtn" '
                 f'onclick="document.querySelectorAll(\'.alert-more\').forEach(function(r)'
                 f'{{r.classList.toggle(\'show\')}});this.textContent='
                 f'this.textContent.indexOf(\'+\')===0?\'– show fewer\':\'+ {extra} more\';">'
                 f'+ {extra} more</button>')
    return body, len(alerts)


# ── Projected monthly spend ─────────────────────────────────────────────────
def projected_month(rows, run_date):
    by_date = {}
    for r in rows:
        d = g(r, "segments.date") or ""
        by_date[d] = by_date.get(d, 0.0) + micros(g(r, "metrics.cost_micros"))
    closed = sorted(d for d in by_date if d < run_date)
    mtd_closed = sum(by_date[d] for d in closed)
    today_spend = by_date.get(run_date, 0.0)
    def is_weekend(ds):
        return datetime.strptime(ds, "%Y-%m-%d").weekday() >= 5
    wkday = [by_date[d] for d in closed if not is_weekend(d)]
    wkend = [by_date[d] for d in closed if is_weekend(d)]
    wkday_avg = sum(wkday)/len(wkday) if wkday else 0
    wkend_avg = sum(wkend)/len(wkend) if wkend else 0
    y, m, _ = map(int, run_date.split("-"))
    dim = (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)).day
    rd = int(run_date.split("-")[2])
    wd_rem = we_rem = 0
    for day in range(rd + 1, dim + 1):
        ds = f"{y}-{m:02d}-{day:02d}"
        if is_weekend(ds): we_rem += 1
        else: wd_rem += 1
    return mtd_closed + today_spend + wkday_avg * wd_rem + wkend_avg * we_rem

# ── Bad-term classification ─────────────────────────────────────────────────
def build_bad_terms(rows, converted90, vocab, known_bad=frozenset()):
    cand = {}
    for r in rows:
        term = g(r, "search_term_view.search_term") or ""
        camp = g(r, "campaign.name") or ""
        ag   = g(r, "ad_group.name") or ""
        k = (term, camp, ag)
        a = cand.setdefault(k, {"term": term, "camp": camp, "ag": ag,
                                "cost": 0.0, "imps": 0, "clicks": 0, "conv": 0.0})
        a["cost"]   += micros(g(r, "metrics.cost_micros"))
        a["imps"]   += int(g(r, "metrics.impressions") or 0)
        a["clicks"] += int(g(r, "metrics.clicks") or 0)
        a["conv"]   += float(g(r, "metrics.all_conversions") or g(r, "metrics.conversions") or 0)
    out = []
    for a in cand.values():
        v = classify(a, converted90, vocab, known_bad)
        if v:
            a["reason"] = v
            out.append(a)
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out

def _bt_norm(s):
    """Normalize a term or campaign/ad-group slot for cross-source matching."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def render_bad_terms_section(anchor, rule_bad, overlap):
    """Render the Bad Search Terms section from the RULE-BASED audit only.

    Microsoft has no Notion negative-keyword audit feeding this build, so (unlike the
    Google version) there is no union / learning loop — just the rule-based detector
    (build_bad_terms / classify). Signature + return shape are kept identical to the
    Google builder (6-tuple, gaps always []) so render() stays unchanged.

    Returns (rows_html, count, banner_html, foot_html, source_label, gaps)."""
    rows = ""
    if not rule_bad:
        rows = '<tr><td colspan="9" class="muted">No bad search terms flagged for this date. 🎉</td></tr>'
    for i, b in enumerate(rule_bad, 1):
        slot = f"{short_name(b['camp'])} / {b['ag']}"
        rows += (f'<tr><td class="num">{i}</td>'
                 f'<td><span class="verdict-bad">BAD</span></td>'
                 f'<td class="term">{esc(b["term"])}</td>'
                 f'<td>{esc(slot)}</td>'
                 f'<td class="num">{money(b["cost"])}</td>'
                 f'<td class="num">{intf(b["imps"]) if b["imps"] else "—"}</td>'
                 f'<td class="num">{intf(b["clicks"])}</td>'
                 f'<td class="num">{conv(b["conv"])}</td>'
                 f'<td class="reason">{esc(b["reason"])}</td></tr>')
    foot = (f'<p><b>Rule-based audit (no AI).</b> A search term is flagged BAD when it clears the '
            f'spend gate (spend&gt;$5 or clicks≥3), has zero demo conversions in the trailing 90 days, '
            f'and either matches an ICP hard-no pattern, is a competitor brand outside the Competitors '
            f'campaign, or has under {overlap}% of its words in the account keyword vocabulary. '
            f'(Microsoft has no Notion audit supplement — this is the rule-based detector alone.)</p>')
    foot = tip(foot)   # move the whole description into an info-icon tooltip on the section title
    return rows, len(rule_bad), "", foot, f"rule {len(rule_bad)}", []


def classify(t, converted90, vocab, known_bad=frozenset()):
    """Return a BAD reason string, or None if the term is GOOD.

    `vocab`     — GLOBAL set of account keyword tokens (see tokens_distinctive note).
    `known_bad` — frozenset of confirmed-bad brand/portal phrases from
                  references/known-bad-brands.txt; substring-matched against the term.
    """
    # 1. spend gate
    if not (t["cost"] > 5 or t["clicks"] >= 3):
        return None
    term = t["term"].lower()
    # 2. branded-campaign exception (brand queries are supposed to look weird)
    if is_branded(t["camp"]) and not re.search(r"scam|lawsuit|alternative", term):
        return None
    # 2.5. term-level DoorLoop brand check: "appdoorloop", "doorlopp", "door loop", etc.
    if re.search(r"doorloop|door\s*loop|doorlop|doorlopp|dorrloop", term):
        return None
    # 3. converted on a demo action in the trailing 90 days → GOOD
    if term in converted90:
        return None
    # 3.5. known-bad-brands deterministic check (highest confidence, no threshold).
    #      David edits references/known-bad-brands.txt to add confirmed negatives.
    #      Checked BEFORE competitor/taxonomy so confirmed brands fire even if they
    #      happen to look like generic PM terms.
    for phrase in known_bad:
        if phrase in term:
            return f"Known bad brand/portal: {phrase!r}"
    # 4. competitor brand: GOOD inside a Competitors campaign (intentional bidding),
    #    BAD anywhere else. Checked BEFORE taxonomy so that valid competitor terms
    #    like "innago reviews complaints" inside the Competitors campaign aren't
    #    killed by the WRONG_INTENT pattern.
    cm = COMPETITORS.search(term)
    if cm:
        if is_comp_campaign(t["camp"]):
            return None
        return f"COMPETITOR ({cm.group(0)}) outside Competitors campaign"
    # 5. ICP hard-no patterns → BAD
    for label, rx in TAXONOMY:
        m = rx.search(term)
        if m:
            return f"{label}: {m.group(0)}"
    # 6. Distinctive-token rescue (replaces flat overlap rescue).
    #
    # Strip generic PM words (PM_GENERIC) to isolate the brand/company identifier.
    # • If distinctive tokens exist AND any is absent from the keyword vocabulary →
    #   the term is about someone else's portal/product → fall through to step 7 BAD.
    # • If distinctive tokens exist AND all are in vocab → on-topic term → GOOD.
    # • If no distinctive tokens (all words are generic PM words, e.g. "rental
    #   property management") → fall back to the classic full-overlap rescue so
    #   pure-PM-word queries aren't accidentally flagged.
    #
    # Background: the previous flat overlap on tokens() rescued terms like
    # "fundigo software reviews" (50%) and "bluesky property manager" (67%) because
    # "reviews" / "property" / "manager" are in the vocabulary. These are tenant-portal
    # lookups and unknown brands, not PM-software buying signals.
    dt = tokens_distinctive(t["term"])
    if dt:
        unknown = [tok for tok in dt if tok not in vocab]
        if unknown:
            # Has an unrecognised brand/company identifier → BAD (skip the floor check
            # — if it cleared the $5 spend gate it's worth flagging regardless of amount)
            return f"Unknown brand/portal: {unknown[0]!r} not in account keyword vocabulary"
        else:
            return None  # all distinctive tokens recognised → on-topic
    else:
        # No distinctive tokens: fall back to classic full-overlap rescue
        tt = tokens(t["term"])
        if tt and sum(1 for x in tt if x in vocab) / len(tt) >= OVERLAP_RESCUE:
            return None
    # 7. default → BAD only when spend cleared the floor (no demos in 90d, failed rescue)
    if t["cost"] < BAD_SPEND_FLOOR:
        return None
    return "Zero demo conv 90d + not in account keyword vocabulary"

# ── Clicks-per-day chart (shared by the daily build + report_common weekly) ──
def clicks_daily_data(rows, anchor_date, n_days=CLICKS_DAYS, demos_rows=None,
                      rankis_rows=None, daily=False, camp_demos=None, pipeline=None):
    """Per-day series for the trend charts, grouped by campaign → ad group.

    Each dataset carries clicks[], demos[], cost[] arrays (and, where meaningful, a
    rankis[] array of search rank-lost impression-share fractions) so ONE chart can
    toggle Clicks / Demos / Both / CPD / Rank-Lost IS with no duplicated charts.

    `rows` = ad_group×date×clicks×cost_micros. `demos_rows` (opt) = ad_group×date×
    all_conversions. `rankis_rows` (opt) = campaign×date×search_rank_lost_impression_
    share×cost_micros (campaign-level only — impression share has NO ad-group split).

    `daily=True` turns on the daily report's extras (this keeps the weekly/monthly
    reports, which call with just rows+anchor, rendering EXACTLY as before): the
    Clicks total line is navy and an Ex-Branded line is added to the All-Campaigns
    chart. Weekly/monthly (daily=False) keep the light-blue total and no Ex-Branded.

    Returns {labels, has_demos, has_cost, has_rankis, style, campaigns:[{id,name,
        span?,datasets:[{label,color,total?,emph?,clicks,demos,cost,rankis?}]}]}.
    First entry = full-width All-Campaigns chart (per-campaign lines + combined total
    [+ Ex-Branded when daily]). Remaining = one chart per campaign (per-ad-group lines
    + campaign total). Rank-lost IS is campaign-level: on per-campaign charts only the
    total line carries rankis; ad-group lines omit it (so the mode can't be combined)."""
    end = datetime.strptime(anchor_date, "%Y-%m-%d").date()
    days = [end - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    didx = {d.isoformat(): i for i, d in enumerate(days)}
    labels = [f"{d.month}/{d.day}" for d in days]

    camps = {}    # campaign -> {ad_group -> {"clicks":[],"demos":[],"cost":[]}}
    def cell(camp, ag):
        return camps.setdefault(camp, {}).setdefault(
            ag, {"clicks": [0] * n_days, "demos": [0.0] * n_days, "cost": [0.0] * n_days,
                 "imps": [0] * n_days})

    has_cost = False
    has_imps = False
    for r in rows:
        i = didx.get((g(r, "segments.date") or "")[:10])
        if i is None:
            continue
        c = cell(g(r, "campaign.name") or "", g(r, "ad_group.name") or "")
        c["clicks"][i] += int(g(r, "metrics.clicks") or 0)
        cm = micros(g(r, "metrics.cost_micros"))
        if cm:
            has_cost = True
        c["cost"][i] += cm
        im = int(g(r, "metrics.impressions") or 0)
        if im:
            has_imps = True
        c["imps"][i] += im

    has_demos = False
    for r in (demos_rows or []):
        i = didx.get((g(r, "segments.date") or "")[:10])
        if i is None:
            continue
        v = float(g(r, "metrics.all_conversions") or g(r, "metrics.conversions") or 0)
        if v:
            has_demos = True
        cell(g(r, "campaign.name") or "", g(r, "ad_group.name") or "")["demos"][i] += v

    # Daily Brain Day-0 cohort override: campaign-level demos (keyed by campaign name) replace
    # the platform ad-group demos. The cohort cube has no ad-group split, so per-ad-group demo
    # lines go flat and only the campaign total carries demos. camp_demos = {campaign: {date:
    # day0}}; absent → platform demos as before (the daily fallback).
    cdemo_arr = None
    if camp_demos is not None:
        cdemo_arr = {}
        for c, dmap in camp_demos.items():
            arr = [0.0] * n_days
            for ds, v in dmap.items():
                j = didx.get(str(ds)[:10])
                if j is not None:
                    arr[j] += v
            cdemo_arr[c] = arr
        has_demos = any(v > 0 for arr in cdemo_arr.values() for v in arr)

    # Campaign-level rank-lost IS series + the campaign's daily cost (for cost-weighting
    # group/account roll-ups). Impression share has no ad-group breakdown.
    camp_ris = {}      # campaign -> [rank-lost IS or None /day]
    camp_is  = {}      # campaign -> [search impression share or None /day]
    camp_riscost = {}  # campaign -> [cost /day]  (weights the IS roll-ups)
    has_rankis = False
    has_imprshare = False
    for r in (rankis_rows or []):
        i = didx.get((g(r, "segments.date") or "")[:10])
        if i is None:
            continue
        ris = g(r, "metrics.search_rank_lost_impression_share")
        ish = g(r, "metrics.search_impression_share")
        if ris is None and ish is None:
            continue
        n = g(r, "campaign.name") or ""
        camp_riscost.setdefault(n, [0.0] * n_days)
        camp_riscost[n][i] = micros(g(r, "metrics.cost_micros"))
        if ris is not None:
            has_rankis = True
            camp_ris.setdefault(n, [None] * n_days)
            camp_ris[n][i] = float(ris)
        if ish is not None:
            has_imprshare = True
            camp_is.setdefault(n, [None] * n_days)
            camp_is[n][i] = float(ish)

    def wavg(series, names):
        """Cost-weighted IS-style metric (rank-lost IS or impression share) across
        `names`, per day (None when no cost). Impression share has no ad-group split,
        so these roll-ups are campaign-level only."""
        out = [None] * n_days
        for i in range(n_days):
            num = den = 0.0
            for n in names:
                if n in series and series[n][i] is not None:
                    w = camp_riscost[n][i]
                    num += series[n][i] * w
                    den += w
            out[i] = (num / den) if den > 0 else None
        return out

    def total(agmap, key):
        t = [0.0] * n_days
        for c in agmap.values():
            for i, v in enumerate(c[key]):
                t[i] += v
        return t
    def iclicks(arr):  return [int(v) for v in arr]
    def rnd(arr):      return [round(v, 2) for v in arr]
    def rris(arr):     return [None if v is None else round(v, 4) for v in arr]

    def cdemos(camp):
        """Campaign-level daily demos: the cohort override when present, else the platform
        ad-group total. (Cohort has no ad-group split — so ad-group demo lines go flat.)"""
        if cdemo_arr is not None:
            return cdemo_arr.get(camp, [0.0] * n_days)
        return total(camps[camp], "demos")

    # Booked-on-day pipeline (Opportunities / Accounts / ARR) from pipeline_30d.json — campaign
    # grain only (no ad-group split, like the cohort demos). Aligned to this chart's day window
    # by date. Absent (weekly/monthly, or no file) → empty → has_opps/accts/arr stay False.
    pipe_camp = {}
    if pipeline:
        pdidx = {str(d)[:10]: j for j, d in enumerate(pipeline.get("days", []))}
        def _palign(src):
            arr = [0.0] * n_days
            for ds, j in didx.items():
                k = pdidx.get(ds)
                if k is not None and k < len(src):
                    arr[j] = src[k]
            return arr
        for nm, pc in pipeline.get("campaigns", {}).items():
            pipe_camp[nm] = {kk: _palign(pc.get(kk, [])) for kk in ("opps", "accts", "arr")}
    def pdata(name, key):
        return (pipe_camp.get(name) or {}).get(key, [0.0] * n_days)
    def psum(names, key):
        out = [0.0] * n_days
        for nm in names:
            a = pdata(nm, key)
            for i in range(n_days):
                out[i] += a[i]
        return out
    has_opps  = any(any(c["opps"])  for c in pipe_camp.values())
    has_accts = any(any(c["accts"]) for c in pipe_camp.values())
    has_arr   = any(any(c["arr"])   for c in pipe_camp.values())

    corder = sorted(camps, key=lambda c: sum(total(camps[c], "clicks")), reverse=True)
    N_camps = max(len(corder), 1)
    nonbrand = [c for c in camps if not is_branded(c)]

    # "All Campaigns" full-width chart — combined total + (daily) Ex-Branded + per-campaign
    all_clicks = [0] * n_days; all_demos = [0.0] * n_days; all_cost = [0.0] * n_days; all_imps = [0] * n_days
    xb_clicks = [0] * n_days;  xb_demos = [0.0] * n_days;  xb_cost = [0.0] * n_days;  xb_imps = [0] * n_days
    for camp, agmap in camps.items():
        ct, dt, st = total(agmap, "clicks"), cdemos(camp), total(agmap, "cost")
        it = total(agmap, "imps")
        for i in range(n_days):
            all_clicks[i] += ct[i]; all_demos[i] += dt[i]; all_cost[i] += st[i]; all_imps[i] += it[i]
            if not is_branded(camp):
                xb_clicks[i] += ct[i]; xb_demos[i] += dt[i]; xb_cost[i] += st[i]; xb_imps[i] += it[i]
    all_dsets = [{"label": "All campaigns", "color": "#3185FC", "total": True,
                  "clicks": iclicks(all_clicks), "demos": rnd(all_demos), "cost": rnd(all_cost),
                  "imps": iclicks(all_imps),
                  "opps": rnd(psum(list(camps.keys()), "opps")),
                  "accts": rnd(psum(list(camps.keys()), "accts")),
                  "arr": rnd(psum(list(camps.keys()), "arr")),
                  "rankis": rris(wavg(camp_ris, list(camps.keys()))),
                  "imprshare": rris(wavg(camp_is, list(camps.keys())))}]
    if daily:
        all_dsets.append({"label": "Ex-Branded", "color": "#FF4998", "emph": True,
                          "clicks": iclicks(xb_clicks), "demos": rnd(xb_demos), "cost": rnd(xb_cost),
                          "imps": iclicks(xb_imps),
                          "opps": rnd(psum(nonbrand, "opps")),
                          "accts": rnd(psum(nonbrand, "accts")),
                          "arr": rnd(psum(nonbrand, "arr")),
                          "rankis": rris(wavg(camp_ris, nonbrand)),
                          "imprshare": rris(wavg(camp_is, nonbrand))})
    for ci, camp in enumerate(corder):
        hue = round(ci * 360 / N_camps)
        all_dsets.append({"label": short_name(camp), "color": f"hsl({hue},62%,52%)",
                          "clicks": iclicks(total(camps[camp], "clicks")),
                          "demos": rnd(cdemos(camp)),
                          "cost": rnd(total(camps[camp], "cost")),
                          "imps": iclicks(total(camps[camp], "imps")),
                          "opps": rnd(pdata(camp, "opps")),
                          "accts": rnd(pdata(camp, "accts")),
                          "arr": rnd(pdata(camp, "arr")),
                          "rankis": rris(camp_ris[camp]) if camp in camp_ris else None,
                          "imprshare": rris(camp_is[camp]) if camp in camp_is else None})
    out = [{"id": "clk-all", "name": "All Campaigns", "datasets": all_dsets, "span": True}]

    for ci, camp in enumerate(corder):
        agmap = camps[camp]
        agorder = sorted(agmap, key=lambda a: sum(agmap[a]["clicks"]), reverse=True)
        N = max(len(agorder), 1)
        dsets = [{"label": "All ad groups", "color": "#3185FC", "total": True,
                  "clicks": iclicks(total(agmap, "clicks")), "demos": rnd(cdemos(camp)),
                  "cost": rnd(total(agmap, "cost")),
                  "imps": iclicks(total(agmap, "imps")),
                  "opps": rnd(pdata(camp, "opps")),
                  "accts": rnd(pdata(camp, "accts")),
                  "arr": rnd(pdata(camp, "arr")),
                  "rankis": rris(camp_ris[camp]) if camp in camp_ris else None,
                  "imprshare": rris(camp_is[camp]) if camp in camp_is else None}]
        for idx, ag in enumerate(agorder):
            hue = round(idx * 360 / N)
            dsets.append({"label": ag, "color": f"hsl({hue},62%,52%)",
                          "clicks": iclicks(agmap[ag]["clicks"]),
                          "demos": ([0.0] * n_days if cdemo_arr is not None else rnd(agmap[ag]["demos"])),
                          "cost": rnd(agmap[ag]["cost"]),
                          "imps": iclicks(agmap[ag]["imps"]),
                          # pipeline (opps/accts/arr) has no ad-group split → flat on ad-group lines
                          "opps": [0.0] * n_days, "accts": [0.0] * n_days, "arr": [0.0] * n_days})
                          # no rankis/imprshare: IS has no ad-group split
        out.append({"id": f"clk-{ci}", "name": short_name(camp), "datasets": dsets})

    style = {"clicks": "#162050" if daily else "#3185FC", "demos": "#3185FC",
             "cpc": "#0D9488", "cpd": "#7C3AED", "rankis": "#B45309",
             "imps": "#64748B", "imprshare": "#15803D", "spend": "#EA580C", "ctd": "#DB2777",
             "opps": "#0EA5E9", "accts": "#9333EA", "arr": "#059669"}
    # Click→Demo conversion-rate chip (demos ÷ clicks, %). Daily-only so the weekly/monthly
    # reports — which call clicks_daily_data without daily and never set has_ctd — stay unchanged.
    has_ctd = bool(daily and has_demos)
    result = {"labels": labels, "has_demos": has_demos, "has_cost": has_cost,
              "has_rankis": has_rankis, "has_imps": has_imps, "has_imprshare": has_imprshare,
              "has_ctd": has_ctd,
              "has_opps": has_opps, "has_accts": has_accts, "has_arr": has_arr,
              "style": style, "campaigns": out}
    if pipeline:
        # source pill + a flag for opps/accounts/ARR that didn't map to an active campaign
        result["pipeline_source"] = pipeline.get("metric_source", "brain")
        t = pipeline.get("totals", {})
        uo, ua, ur = t.get("unattr_opps", 0), t.get("unattr_accts", 0), t.get("unattr_arr", 0)
        if uo or ua or ur:
            ids = ", ".join(f"{m['label']} (id {m['id']})" for m in pipeline.get("mismatch_ids", [])[:6])
            result["pipeline_note"] = (
                f"⚠ {uo:.0f} opps / {ua:.0f} accounts / ${ur:,.0f} ARR could not be matched to an "
                f"active campaign and are excluded from the lines above (retired/blank campaign IDs: {ids}).")
    if daily:
        # Weekday names (aligned with labels) so the daily tooltip title reads "Sunday 6/7".
        # Daily-only: weekly/monthly call without daily → no `dow` → tooltip stays "6/7".
        result["dow"] = [d.strftime("%A") for d in days]
        # Moving-average toggle (〜 chip). 7-day window kills the weekday/weekend sawtooth.
        # Daily-only here; weekly/monthly get their own window via bucket_clicks_data.
        result["avg"] = {"n": 7, "label": "7-day avg"}
        # View toggle (daily-only): Current view (one chart, swap metric) vs Metric Grid
        # (every metric as its own small tile for the selected entity). Weekly/monthly
        # call bucket_clicks_data which never sets this → they render exactly as before.
        result["views"] = True
    return result


def clicks_section_html(title_suffix):
    """Markup for the trend card: a grid the JS fills with one mini-chart
    (+ a metric toggle and entity chips) per campaign, preceded by a full-width
    All Campaigns chart."""
    return (
        '<div class="card">'
        f'<h2>Daily Metrics <span class="dt">— {esc(title_suffix)}</span>' + tip(
        '<p>First chart: <b>All Campaigns</b> total · toggle the '
        '<b>Ex-Branded</b> chip (everything except Branded) or any campaign chip to overlay it. Remaining '
        'charts: one per campaign with ad-group chips. Each chart toggles '
        '<b>Demos</b> (default) · <b>Clicks</b> · <b>Both</b> · <b>Impressions</b> · <b>Spend</b> (total cost, $) · '
        '<b>CPC</b> (avg cost per click, $) · '
        '<b>CPD</b> (cost per demo, $) · <b>Rank-Lost IS</b> (% axis, shown on its own) · '
        '<b>Impr Share</b> (% axis, shown on its own). '
        'Default shows totals only. The <b>〜 7-day avg</b> chip overlays a dashed trend line '
        '(ratio metrics use rolling 7-day totals, so low-volume days don\'t distort it).</p>') + '</h2>'
        '<div class="clk-grid" id="clkGrid"></div>'
        '</div>')


# JS injected as a .format VALUE (so its single braces are not re-parsed by format()).
# Builds one <canvas> + metric toggle + chip row per campaign inside #clkGrid.
CLICKS_JS = r"""
(function(){
  var grid=document.getElementById('clkGrid'); if(!grid) return;
  var blob=document.getElementById('clicks-data'); if(!blob) return;
  if(typeof Chart==='undefined'){
    grid.innerHTML='<div style="color:#B91C1C;font-size:12px;padding:8px">Chart.js failed to load (offline?).</div>';return;}
  var D=JSON.parse(blob.textContent), L=D.labels, DOW=D.dow||null, gridc='rgba(120,130,150,.15)';
  var HAS_DEMOS=!!D.has_demos, HAS_COST=!!D.has_cost, HAS_RANKIS=!!D.has_rankis;
  var HAS_IMPS=!!D.has_imps, HAS_IMPRSHARE=!!D.has_imprshare, HAS_CTD=!!D.has_ctd;
  var HAS_OPPS=!!D.has_opps, HAS_ACCTS=!!D.has_accts, HAS_ARR=!!D.has_arr;   // booked-on-day pipeline (Brain → SF)
  var ST=D.style||{clicks:'#3185FC',demos:'#3185FC',cpc:'#0D9488',cpd:'#7C3AED',rankis:'#B45309',imps:'#64748B',imprshare:'#15803D',spend:'#EA580C',ctd:'#DB2777'};
  var AVG=D.avg||null;   // {n,label} — drives the moving-average toggle (absent → no chip)

  function tavg(arr,N){  // trailing moving average, gap-tolerant (counts + IS fractions)
    var out=[],i,j,s,c;
    for(i=0;i<arr.length;i++){ s=0;c=0;
      for(j=Math.max(0,i-N+1);j<=i;j++){ if(arr[j]!=null){s+=arr[j];c++;} }
      out.push(c?s/c:null); }
    return out; }
  function rsum(num,den,N){ // rolling-sum ÷ rolling-sum — volume-weighted MA for ratio metrics
    var out=[],i,j,sn,sd;   // (CPC/CPD/Click→Demo): 7-day cost ÷ 7-day clicks, not a mean of daily ratios
    for(i=0;i<num.length;i++){ sn=0;sd=0;
      for(j=Math.max(0,i-N+1);j<=i;j++){ sn+=(num[j]||0); sd+=(den[j]||0); }
      out.push(sd>0?sn/sd:null); }
    return out; }
  var MLBL={demos:{t:'Demos',h:'Demos scheduled per day'},
            clicks:{t:'Clicks',h:'Clicks per day'},
            both:{t:'Both',h:'Clicks + Demos (demos on the right axis)'},
            imps:{t:'Impressions',h:'Impressions per day'},
            spend:{t:'Spend',h:'Total ad spend (cost) per day'},
            cpc:{t:'CPC',h:'Average Cost Per Click per day'},
            cpd:{t:'CPD',h:'Cost Per Demo Scheduled'},
            rankis:{t:'Rank-Lost IS',h:'Search rank-lost impression share — shown on its own (cannot combine)'},
            imprshare:{t:'Impr Share',h:'Search impression share — shown on its own (cannot combine)'},
            ctd:{t:'Click→Demo',h:'Click-to-Demo conversion rate per day (demos ÷ clicks, %)'},
            opps:{t:'Opportunities',h:'Opportunities created per day — booked-on-day (Opportunity Created date), from the Brain (Salesforce fallback)'},
            accts:{t:'Accounts',h:'New customer accounts per day — booked-on-day (Stripe first-invoice date), from the Brain (Salesforce fallback)'},
            arr:{t:'ARR',h:'New ARR booked per day (after coupons) — booked-on-day, from the Brain (Salesforce fallback)'}};
  var MWORD={clicks:'clicks',demos:'demos',imps:'impressions',spend:'',cpc:'',cpd:'',rankis:'',imprshare:'',ctd:'',opps:'opps',accts:'accounts',arr:''};

  function fmt(v,m){
    if(v==null) return '—';
    if(m==='demos')  return Math.round(v*100)/100;
    if(m==='imps')   return Math.round(v).toLocaleString();
    if(m==='spend')  return '$'+Math.round(v).toLocaleString();
    if(m==='cpc')    return '$'+(Math.round(v*100)/100).toLocaleString();
    if(m==='cpd')    return '$'+Math.round(v).toLocaleString();
    if(m==='arr')    return '$'+Math.round(v).toLocaleString();
    if(m==='opps'||m==='accts') return Math.round(v*100)/100;
    if(m==='rankis'||m==='imprshare'||m==='ctd') return (Math.round(v*1000)/10)+'%';
    return v;
  }

  function renderCurrent(){ grid.innerHTML='';
  (D.campaigns||[]).forEach(function(camp){
    var ents=camp.datasets;                       // {label,color,total?,emph?,clicks,demos,cost,imps,rankis?,imprshare?}
    var modes=[];                                 // Demos first by default
    if(HAS_DEMOS){ modes=['demos','clicks']; } else { modes=['clicks']; }
    if(HAS_IMPS) modes.push('imps');              // impressions per day
    if(HAS_COST) modes.push('spend');             // total spend (cost) per day
    if(HAS_COST) modes.push('cpc');               // avg CPC per day (cost / clicks)
    if(HAS_DEMOS && HAS_COST) modes.push('cpd');  // cost per demo per day
    if(HAS_CTD) modes.push('ctd');                // click-to-demo conversion rate % (daily only)
    if(HAS_OPPS) modes.push('opps');              // booked-on-day pipeline (Brain → SF)
    if(HAS_ACCTS) modes.push('accts');
    if(HAS_ARR) modes.push('arr');
    if(HAS_RANKIS) modes.push('rankis');
    if(HAS_IMPRSHARE) modes.push('imprshare');    // search impression share (own line)
    var mode=modes[0];
    var avgOn=false;                              // moving-average toggle (default off)
    var hidden={};                                // entity label -> hidden?
    ents.forEach(function(e){ hidden[e.label]=!e.total; });

    var card=document.createElement('div'); card.className='clk-card';
    if(camp.span) card.style.gridColumn='1/-1';
    var title=document.createElement('div'); title.className='clk-title'; title.textContent=camp.name;
    card.appendChild(title);

    // metric toggle — only when there's more than one mode to pick
    var seg=null;
    if(modes.length>1){
      seg=document.createElement('div'); seg.className='metric-toggle';
      modes.forEach(function(m){
        var btn=document.createElement('span'); btn.className='mbtn'+(m===mode?' on':'');
        btn.textContent=MLBL[m].t; btn.title=MLBL[m].h; btn.dataset.m=m;
        seg.appendChild(btn);
      });
    }
    // moving-average chip (〜 7-day avg / 4-week avg / 3-month avg), default off
    var avgBtn=null;
    if(AVG){
      avgBtn=document.createElement('span'); avgBtn.className='chip avgbtn';
      avgBtn.textContent='〜 '+AVG.label;
      avgBtn.title='Overlay a dashed trailing moving average on every visible line. '+
        'Ratio metrics (CPC/CPD/Click→Demo) use rolling totals (e.g. '+AVG.n+'-bucket cost ÷ clicks).';
    }
    if(seg||avgBtn){
      var bar=document.createElement('div');
      bar.style.cssText='display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap';
      if(seg) bar.appendChild(seg); if(avgBtn) bar.appendChild(avgBtn);
      card.appendChild(bar);
    }

    var chips=document.createElement('div'); chips.className='chips clicks';
    var cw=document.createElement('div'); cw.className='canvas-wrap clk';
    var cv=document.createElement('canvas'); cw.appendChild(cv);
    card.appendChild(chips); card.appendChild(cw); grid.appendChild(card);

    function totalCol(metric){ return ST[metric]||ST.clicks||'#3185FC'; }
    function mkLine(e,data,axis,metric,dashed,suffix){
      var col=e.total?totalCol(metric):e.color;   // total line colored by metric; entities keep their hue
      return {label:e.label+(suffix||''),data:data,borderColor:col,backgroundColor:col,
        tension:.25,borderWidth:e.total?3:(e.emph?2.5:1.5),pointRadius:0,spanGaps:true,
        borderDash:dashed?[5,3]:[],yAxisID:axis,hidden:!!hidden[e.label],_entity:e.label,_metric:metric};
    }
    function cpdArr(e){   // cost per demo per day (null on days with no demos → gap)
      var a=[]; for(var i=0;i<e.cost.length;i++){ var d=e.demos[i]; a.push(d>0?(e.cost[i]/d):null); } return a;
    }
    function cpcArr(e){   // avg cost per click per day (null on days with no clicks → gap)
      var a=[]; for(var i=0;i<e.clicks.length;i++){ var c=e.clicks[i]; a.push(c>0?(e.cost[i]/c):null); } return a;
    }
    function ctdArr(e){   // click-to-demo conversion rate per day = demos/clicks (null on 0-click days → gap)
      var a=[]; for(var i=0;i<e.clicks.length;i++){ var c=e.clicks[i]; a.push(c>0?(e.demos[i]/c):null); } return a;
    }
    function maFor(e,m){  // MA series for entity e in mode m (null → no MA for this line)
      if(!AVG) return null; var N=AVG.n;
      if(m==='cpc') return rsum(e.cost,e.clicks,N);    // rolling cost ÷ rolling clicks
      if(m==='cpd') return rsum(e.cost,e.demos,N);     // rolling cost ÷ rolling demos
      if(m==='ctd') return rsum(e.demos,e.clicks,N);   // rolling demos ÷ rolling clicks
      if(m==='clicks') return tavg(e.clicks,N);
      if(m==='demos') return tavg(e.demos,N);
      if(m==='imps') return e.imps?tavg(e.imps,N):null;
      if(m==='spend') return e.cost?tavg(e.cost,N):null;
      if(m==='opps') return e.opps?tavg(e.opps,N):null;
      if(m==='accts') return e.accts?tavg(e.accts,N):null;
      if(m==='arr') return e.arr?tavg(e.arr,N):null;
      if(m==='rankis') return e.rankis?tavg(e.rankis,N):null;
      if(m==='imprshare') return e.imprshare?tavg(e.imprshare,N):null;
      return null;
    }
    function addMA(out,e,m,baseHidden){ // dashed companion line; chips toggle it with its entity
      if(!avgOn) return;
      var ma=maFor(e,m); if(!ma) return;
      var ln=mkLine(e,ma,'y',m,true,' · '+AVG.label);
      ln.borderWidth=1.5; ln.tension=.3; ln._noLabel=true;
      if(baseHidden!==undefined) ln.hidden=baseHidden;
      out.push(ln);
    }
    function datasetsFor(m){
      var out=[];
      ents.forEach(function(e){
        if(m==='clicks'){ out.push(mkLine(e,e.clicks,'y','clicks',false,'')); addMA(out,e,m); }
        else if(m==='demos'){ out.push(mkLine(e,e.demos,'y','demos',false,'')); addMA(out,e,m); }
        else if(m==='imps'){ out.push(mkLine(e,e.imps,'y','imps',false,'')); addMA(out,e,m); }
        else if(m==='spend'){ out.push(mkLine(e,e.cost,'y','spend',false,'')); addMA(out,e,m); }
        else if(m==='cpc'){ out.push(mkLine(e,cpcArr(e),'y','cpc',false,'')); addMA(out,e,m); }
        else if(m==='cpd'){ out.push(mkLine(e,cpdArr(e),'y','cpd',false,'')); addMA(out,e,m); }
        else if(m==='ctd'){ out.push(mkLine(e,ctdArr(e),'y','ctd',false,'')); addMA(out,e,m); }
        else if(m==='opps'){ out.push(mkLine(e,e.opps,'y','opps',false,'')); addMA(out,e,m); }
        else if(m==='accts'){ out.push(mkLine(e,e.accts,'y','accts',false,'')); addMA(out,e,m); }
        else if(m==='arr'){ out.push(mkLine(e,e.arr,'y','arr',false,'')); addMA(out,e,m); }
        else if(m==='rankis'){ if(e.rankis){ var lr=mkLine(e,e.rankis,'y','rankis',false,''); if(!camp.span) lr.hidden=false; out.push(lr); addMA(out,e,m,lr.hidden); } }
        else if(m==='imprshare'){ if(e.imprshare){ var li=mkLine(e,e.imprshare,'y','imprshare',false,''); if(!camp.span) li.hidden=false; out.push(li); addMA(out,e,m,li.hidden); } }
        else { out.push(mkLine(e,e.clicks,'y','clicks',false,' (clicks)'));
               out.push(mkLine(e,e.demos,'y1','demos',true,' (demos)')); }
      });
      return out;
    }
    function scalesFor(m){
      var s={x:{grid:{display:false},ticks:{font:{size:9},maxRotation:0,autoSkip:true,maxTicksLimit:10}}};
      if(m==='both'){
        s.y={type:'linear',position:'left',beginAtZero:true,grid:{color:gridc},ticks:{precision:0},title:{display:true,text:'Clicks',font:{size:9}}};
        s.y1={type:'linear',position:'right',beginAtZero:true,grid:{display:false},ticks:{precision:0},title:{display:true,text:'Demos',font:{size:9}}};
      } else if(m==='spend'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return '$'+v.toLocaleString();}},title:{display:true,text:'Spend',font:{size:9}}};
      } else if(m==='cpc'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return '$'+v;}},title:{display:true,text:'Avg CPC',font:{size:9}}};
      } else if(m==='cpd'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return '$'+v;}},title:{display:true,text:'Cost / Demo',font:{size:9}}};
      } else if(m==='arr'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return '$'+v.toLocaleString();}},title:{display:true,text:'New ARR',font:{size:9}}};
      } else if(m==='opps'||m==='accts'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{precision:0},title:{display:true,text:(m==='opps'?'Opportunities':'Accounts'),font:{size:9}}};
      } else if(m==='ctd'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return (Math.round(v*1000)/10)+'%';}},title:{display:true,text:'Click→Demo %',font:{size:9}}};
      } else if(m==='rankis'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return Math.round(v*100)+'%';}},title:{display:true,text:'Rank-Lost IS',font:{size:9}}};
      } else if(m==='imprshare'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return Math.round(v*100)+'%';}},title:{display:true,text:'Impr Share',font:{size:9}}};
      } else if(m==='imps'){
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{precision:0},title:{display:true,text:'Impressions',font:{size:9}}};
      } else {
        s.y={beginAtZero:true,grid:{color:gridc},ticks:{precision:0}};
      }
      return s;
    }
    var ch=new Chart(cv,{type:'line',
      plugins:[makeLineLabels(function(i,v){return fmt(v,mode);})],
      data:{labels:L,datasets:datasetsFor(mode)},
      options:{responsive:true,maintainAspectRatio:false,layout:{padding:{right:52,top:16,bottom:6}},interaction:{mode:'index',intersect:false},
        plugins:{legend:{display:false},tooltip:{callbacks:{
          title:function(items){ if(!items.length) return ''; var i=items[0].dataIndex;
            return (DOW&&DOW[i]?DOW[i]+' ':'')+L[i]; },
          label:function(t){
          var m=t.dataset._metric||'clicks';
          return ' '+t.dataset.label+': '+fmt(t.parsed.y,m)+(MWORD[m]?(' '+MWORD[m]):'');}}}},
        scales:scalesFor(mode)}});

    if(seg){
      seg.addEventListener('click',function(e){
        var m=e.target&&e.target.dataset?e.target.dataset.m:null; if(!m||m===mode) return;
        mode=m;
        seg.querySelectorAll('.mbtn').forEach(function(b){ b.classList.toggle('on',b.dataset.m===mode); });
        ch.data.datasets=datasetsFor(mode); ch.options.scales=scalesFor(mode); ch.update();
      });
    }
    if(avgBtn){
      avgBtn.addEventListener('click',function(){
        avgOn=!avgOn; avgBtn.classList.toggle('on',avgOn);
        avgBtn.style.background=avgOn?'#64748B':''; avgBtn.style.borderColor=avgOn?'#64748B':'';
        avgBtn.style.color=avgOn?'#fff':'';
        ch.data.datasets=datasetsFor(mode); ch.update();
      });
    }

    ents.forEach(function(e){
      var b=document.createElement('span'); b.className='chip'+(hidden[e.label]?'':' on'); b.textContent=e.label;
      b.style.borderColor=e.color; if(!hidden[e.label]){b.style.background=e.color;b.style.color='#fff';}
      b.addEventListener('click',function(ev){ev.stopPropagation();
        hidden[e.label]=!hidden[e.label];
        b.classList.toggle('on',!hidden[e.label]); b.style.background=hidden[e.label]?'':e.color; b.style.color=hidden[e.label]?'':'#fff';
        ch.data.datasets.forEach(function(d){ if(d._entity===e.label) d.hidden=hidden[e.label]; });
        ch.update();
      });
      chips.appendChild(b);
    });
  });
  }

  // ───────────────────── Metric Grid view (daily-only; gated on D.views) ─────────────────────
  // The first chart's datasets (All campaigns / Ex-Branded / per-campaign) are the selectable
  // entities; in grid view each available METRIC becomes its own small tile for the chosen
  // entity. Lines are the default blue with NO peak/trough/last labels (clean), but keep the
  // day-of-week + date hover. Pick a campaign chip → every tile re-draws for that entity.
  function gridEnts(){ return (D.campaigns&&D.campaigns[0]&&D.campaigns[0].datasets)||[]; }
  function gridModes(e){
    var ms=[]; if(HAS_DEMOS) ms.push('demos'); ms.push('clicks');
    if(HAS_IMPS) ms.push('imps'); if(HAS_COST){ ms.push('spend'); ms.push('cpc'); }
    if(HAS_DEMOS&&HAS_COST) ms.push('cpd'); if(HAS_CTD) ms.push('ctd');
    if(HAS_OPPS&&e.opps) ms.push('opps'); if(HAS_ACCTS&&e.accts) ms.push('accts'); if(HAS_ARR&&e.arr) ms.push('arr');
    if(HAS_RANKIS&&e.rankis) ms.push('rankis'); if(HAS_IMPRSHARE&&e.imprshare) ms.push('imprshare');
    return ms;
  }
  function gridArr(e,m){
    var i,a;
    if(m==='clicks') return e.clicks; if(m==='demos') return e.demos;
    if(m==='imps') return e.imps;     if(m==='spend') return e.cost;
    if(m==='opps') return e.opps||null; if(m==='accts') return e.accts||null; if(m==='arr') return e.arr||null;
    if(m==='cpc'){ a=[]; for(i=0;i<e.clicks.length;i++){ a.push(e.clicks[i]>0?e.cost[i]/e.clicks[i]:null); } return a; }
    if(m==='cpd'){ a=[]; for(i=0;i<e.cost.length;i++){ a.push(e.demos[i]>0?e.cost[i]/e.demos[i]:null); } return a; }
    if(m==='ctd'){ a=[]; for(i=0;i<e.clicks.length;i++){ a.push(e.clicks[i]>0?e.demos[i]/e.clicks[i]:null); } return a; }
    if(m==='rankis') return e.rankis||null; if(m==='imprshare') return e.imprshare||null;
    return null;
  }
  function gridScale(m){
    var s={x:{grid:{display:false},ticks:{font:{size:9},maxRotation:0,autoSkip:true,maxTicksLimit:8}}};
    if(m==='spend'||m==='arr'){ s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return '$'+v.toLocaleString();}}}; }
    else if(m==='cpc'||m==='cpd'){ s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return '$'+v;}}}; }
    else if(m==='rankis'||m==='imprshare'){ s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return Math.round(v*100)+'%';}}}; }
    else if(m==='ctd'){ s.y={beginAtZero:true,grid:{color:gridc},ticks:{callback:function(v){return (Math.round(v*1000)/10)+'%';}}}; }
    else { s.y={beginAtZero:true,grid:{color:gridc},ticks:{precision:0}}; }
    return s;
  }
  var gridSel=null;   // selected entity label (persists across re-renders)
  function renderGrid(){
    grid.innerHTML='';
    var ents=gridEnts(); if(!ents.length){ renderCurrent(); return; }
    var sel=null,k; for(k=0;k<ents.length;k++){ if(ents[k].label===gridSel){ sel=ents[k]; break; } }
    if(!sel){ sel=ents[0]; gridSel=sel.label; }                 // default = "All campaigns"
    var chrow=document.createElement('div'); chrow.className='chips clicks'; chrow.style.gridColumn='1/-1';
    ents.forEach(function(e){
      var b=document.createElement('span'); b.className='chip'+(e.label===gridSel?' on':''); b.textContent=e.label;
      b.style.borderColor=e.color; if(e.label===gridSel){ b.style.background=e.color; b.style.color='#fff'; }
      b.addEventListener('click',function(){ if(gridSel!==e.label){ gridSel=e.label; renderGrid(); } });
      chrow.appendChild(b);
    });
    grid.appendChild(chrow);
    if(D.pipeline_note && (gridSel===((gridEnts()[0]||{}).label))){   // show the unattributed flag on the All-campaigns view
      var pn=document.createElement('div'); pn.className='clk-pipenote'; pn.style.gridColumn='1/-1';
      pn.textContent=D.pipeline_note; grid.appendChild(pn);
    }
    gridModes(sel).forEach(function(m){
      var arr=gridArr(sel,m); if(!arr) return;
      var card=document.createElement('div'); card.className='clk-card clk-tile';
      var ttl=document.createElement('div'); ttl.className='clk-title'; ttl.textContent=MLBL[m].t; card.appendChild(ttl);
      var lastv=null,i; for(i=arr.length-1;i>=0;i--){ if(arr[i]!=null){ lastv=arr[i]; break; } }
      var sub=document.createElement('div'); sub.className='clk-sub'; sub.innerHTML='yesterday: <b>'+fmt(lastv,m)+'</b>'; card.appendChild(sub);
      var cw=document.createElement('div'); cw.className='canvas-wrap clk-tilewrap'; var cv=document.createElement('canvas'); cw.appendChild(cv); card.appendChild(cw);
      grid.appendChild(card);
      new Chart(cv,{type:'line',
        data:{labels:L,datasets:[{label:sel.label,data:arr,borderColor:'#3185FC',backgroundColor:'#3185FC',tension:.25,borderWidth:2,pointRadius:0,spanGaps:true}]},
        options:{responsive:true,maintainAspectRatio:false,layout:{padding:{right:8,top:6,bottom:4}},interaction:{mode:'index',intersect:false},
          plugins:{legend:{display:false},tooltip:{callbacks:{
            title:function(items){ if(!items.length) return ''; var ix=items[0].dataIndex; return (DOW&&DOW[ix]?DOW[ix]+' ':'')+L[ix]; },
            label:function(t){ var w=MWORD[m]; return ' '+MLBL[m].t+': '+fmt(t.parsed.y,m)+(w?(' '+w):''); }}}},
          scales:gridScale(m)}});
    });
  }

  // view toggle — only when the data blob opts in (daily report); weekly/monthly skip it
  if(D.views){
    var vbar=document.createElement('div'); vbar.className='clk-views';
    var bCur=document.createElement('span'); bCur.className='vbtn on'; bCur.textContent='Current view';
    var bGrid=document.createElement('span'); bGrid.className='vbtn'; bGrid.textContent='Metric grid';
    vbar.appendChild(bCur); vbar.appendChild(bGrid);
    grid.parentNode.insertBefore(vbar,grid);
    var setView=function(v){
      bCur.classList.toggle('on',v==='current'); bGrid.classList.toggle('on',v==='grid');
      grid.className='clk-grid'+(v==='grid'?' grid-metrics':'');
      if(v==='grid') renderGrid(); else renderCurrent();
    };
    bCur.addEventListener('click',function(){ setView('current'); });
    bGrid.addEventListener('click',function(){ setView('grid'); });
  }
  renderCurrent();
})();
"""


# Shared Chart.js inline plugin: draw the LAST value at the end of every visible line, plus
# the peak & trough ONLY when a single line is visible (auto-declutters as lines are toggled
# on). Injected as a .format VALUE into BOTH the daily template and report_common's template
# (single source — defines globals makeLineLabels/drawLab that the chart IIFEs call). fmtFn(i,v)
# returns the same string the tooltip shows, so labels match the hover value exactly.
LINE_LABELS_JS = r"""
function drawLab(ctx,t,x,y,color,align,halo){
  ctx.font='700 10px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif';
  ctx.textAlign=align; ctx.textBaseline='middle';
  ctx.lineWidth=3.5; ctx.strokeStyle=halo; ctx.strokeText(t,x,y);
  ctx.fillStyle=color; ctx.fillText(t,x,y);
}
function makeLineLabels(fmtFn){ return {
  id:'lineLabels',
  afterDatasetsDraw:function(chart){
    if(chart.config.type!=='line') return;
    var ctx=chart.ctx, ca=chart.chartArea; if(!ca) return;
    var halo=(getComputedStyle(document.documentElement).getPropertyValue('--bg-card')||'').trim()||'#fff';
    // _noLabel datasets (moving-average companions) carry no labels and don't count
    // toward the single-line check, so peak/trough still show with the MA overlaid.
    var vis=[]; chart.data.datasets.forEach(function(d,i){ if(chart.isDatasetVisible(i)&&!d._noLabel) vis.push(i); });
    ctx.save();
    chart.data.datasets.forEach(function(ds,i){
      if(!chart.isDatasetVisible(i)||ds._noLabel) return;
      var meta=chart.getDatasetMeta(i), col=ds.borderColor||'#162050', li=-1, j, v;
      for(j=ds.data.length-1;j>=0;j--){ if(ds.data[j]!=null){ li=j; break; } }
      if(li<0) return;
      var pl=meta.data[li]; if(pl) drawLab(ctx,fmtFn(i,ds.data[li]),pl.x+6,pl.y,col,'left',halo);
      if(vis.length===1){          // peak + trough only when a single line is showing
        var mn=Infinity,mx=-Infinity,mni=-1,mxi=-1;
        for(j=0;j<ds.data.length;j++){ v=ds.data[j]; if(v==null) continue;
          if(v<mn){mn=v;mni=j;} if(v>mx){mx=v;mxi=j;} }
        if(mxi>=0 && mxi!==li){ var pm=meta.data[mxi]; if(pm){ var py=(pm.y-ca.top<16)?pm.y+14:pm.y-12;
          drawLab(ctx,fmtFn(i,mx),pm.x,py,col,'center',halo); } }
        if(mni>=0 && mni!==mxi && mni!==li){ var pn=meta.data[mni]; if(pn){ var ny=(ca.bottom-pn.y<18)?pn.y-12:pn.y+14;
          drawLab(ctx,fmtFn(i,mn),pn.x,ny,col,'center',halo); } }
      }
    });
    ctx.restore();
  }
};}
"""


def render_recommendations(anchor, id2name):
    """Render the 'Google's Recommendations' section from recommendations.json
    (the routine pulls the `recommendation` resource via the MCP, dismissed=false).
    Hides the user's opted-out noise types (REC_EXCLUDE), surfaces everything else,
    and HIGHLIGHTS negative-keyword / conflict / disapproval / suspension items.
    Returns (rows_html, kept_count, note_html, foot_html, alert_html). `alert_html`
    is a top-of-page banner, non-empty only when a negative-keyword block / conflict
    is flagged (the thing the user most wants surfaced)."""
    recs = load(anchor, "recommendations.json")
    if not isinstance(recs, list):
        note = ('<div class="warn-banner">⚠️ Recommendations were not fetched for this date '
                '(recommendations.json missing). The routine pulls them from the Google Ads MCP.</div>')
        return '<tr><td colspan="3" class="muted">—</td></tr>', 0, note, '', ''

    def camp_name(res):
        if not res:
            return "Account-level"
        cid = res.rstrip("/").split("/")[-1]
        return id2name.get(cid, f"Campaign {cid}")
    def friendly(t):
        return REC_LABELS.get(t) or t.replace("_OPT_IN", " (opt-in)").replace("_", " ").title()
    def is_hi(t):
        return any(s in t for s in REC_HIGHLIGHT)

    kept = {}     # type -> {"camps": set, "hi": bool}
    hidden = {}   # type -> count
    for r in recs:
        t = g(r, "recommendation.type") or ""
        if g(r, "recommendation.dismissed"):
            continue
        if t in REC_EXCLUDE:
            hidden[t] = hidden.get(t, 0) + 1
            continue
        e = kept.setdefault(t, {"camps": set(), "hi": is_hi(t)})
        e["camps"].add(camp_name(g(r, "recommendation.campaign")))

    order = sorted(kept.items(), key=lambda kv: (not kv[1]["hi"], -len(kv[1]["camps"]), kv[0]))
    rows = ""
    if not kept:
        rows = ('<tr><td colspan="3" class="muted">Nothing to act on — every active recommendation '
                'is in your muted list. 🎉</td></tr>')
    for t, e in order:
        flag = '<span class="src-tag src-notion">review ⚠</span> ' if e["hi"] else ''
        cls = ' class="gap-row"' if e["hi"] else ''
        rows += (f'<tr{cls}><td>{flag}{esc(friendly(t))} '
                 f'<span class="muted" style="font-size:11px">({esc(t)})</span></td>'
                 f'<td class="num">{len(e["camps"])}</td>'
                 f'<td>{esc(", ".join(sorted(e["camps"])))}</td></tr>')

    note = ""
    if hidden:
        bits = ", ".join(f"{friendly(t)} ×{n}" for t, n in sorted(hidden.items(), key=lambda x: -x[1]))
        note = (f'<div class="foot" style="margin:0 0 10px"><p>{sum(hidden.values())} low-signal '
                f'suggestion(s) hidden per your opt-out filter: {esc(bits)}.</p></div>')

    foot = ('<p>Pulled daily from the Google Ads <b>recommendations</b> API via MCP '
            '(active/non-dismissed only). Your 9 opted-out types are hidden above. Rows tagged '
            '<b>review ⚠</b> relate to negative-keyword conflicts, blocks, disapprovals or '
            'suspensions. Note: Google rarely emits an explicit "your negatives are blocking traffic" '
            'item — the deep negative-list blocking / canary analysis lives in the daily Notion audit '
            '(sections 1 &amp; 4). This section surfaces whatever Google itself flags.</p>')

    # Top-of-page alert — only when Google flags a negative-keyword block / conflict.
    hi = [(t, e) for t, e in kept.items() if e["hi"]]
    alert = ""
    if hi:
        parts = "; ".join(f"{esc(friendly(t))} ({esc(', '.join(sorted(e['camps'])))})" for t, e in hi)
        alert = ('<div class="alert-top">🚨 <b>Heads up — Google flagged a possible negative-keyword '
                 'block / conflict.</b> Keywords or search terms may be getting blocked: ' + parts +
                 '. See <b>Google’s Recommendations</b> below to review.</div>')
    return rows, len(kept), note, foot, alert


# ── Preflight: fail loudly on missing/empty/mis-saved data (no silent blank report) ──
# This is what guarantees the routine "never misses a step": a failed query, an empty
# pull, or the known keyword_vocab/clicks_30d overflow-swap all ABORT the build with the
# exact offending filename instead of rendering blank sections. Daily-build only — the
# weekly/monthly/exec paths never call this.
PREFLIGHT_REQUIRED = [
    ("spend_cur.json",         "metrics.cost_micros"),
    ("spend_prior.json",       "metrics.cost_micros"),
    ("demos_cur.json",         "campaign.name"),
    ("demos_prior.json",       "campaign.name"),
    ("ranklost.json",          "metrics.search_budget_lost_impression_share"),
    ("searchterms.json",       "search_term_view.search_term"),
    ("searchterms_prior.json", "metrics.average_cpc"),
    ("monthspend.json",        "segments.date"),
    ("converted90.json",       "search_term_view.search_term"),
]
PREFLIGHT_OPTIONAL = [
    ("clicks_30d.json",        "metrics.clicks"),
    ("demos_30d.json",         "metrics.all_conversions"),
    ("rankis_30d.json",        "metrics.search_rank_lost_impression_share"),
]

def _row_has_key(row, dotted):
    """True if `row` exposes `dotted`, whether stored flat ('a.b') or nested ('a':{'b'})."""
    if not isinstance(row, dict):
        return False
    if dotted in row:                       # flat dotted key (the saved shape)
        return True
    cur = row                               # nested fallback (g() also supports this)
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True

def preflight(anchor):
    """Validate the day's data BEFORE rendering. Aborts (exit 1) with the exact bad
    filenames if any REQUIRED file is missing/empty/mis-saved; OPTIONAL files only warn."""
    errs, warns = [], []
    def check(name, key, rows):
        if not isinstance(rows, list) or not rows:
            return f"{name}: missing or empty"
        if not _row_has_key(rows[0], key):
            return (f"{name}: present but missing key '{key}' — got "
                    f"{sorted(rows[0].keys())[:6]} (wrong query saved? overflow swap?)")
        return None
    cohort_present = bool(load_cohort_demos(anchor))
    for name, key in PREFLIGHT_REQUIRED:
        e = check(name, key, load(anchor, name))
        if e:
            # demos_cur/demos_prior are legitimately empty on a recent day: Bing's
            # Demo_Scheduled goal is an offline/CRM import with a 24-48h lag, and the
            # report uses the Brain Day-0 cohort as the demo basis anyway. When the
            # cohort file is present, don't hard-fail on an empty platform demos file.
            if cohort_present and name in ("demos_cur.json", "demos_prior.json") and "missing or empty" in e:
                warns.append(e + " (tolerated — cohort_demos.json is the demo basis)")
            else:
                errs.append(e)
    # keyword vocabulary may come from the day's pull OR the weekly cache
    vrows = load_vocab(anchor)
    if not vrows:
        errs.append("keyword vocabulary: data/<anchor>/keyword_vocab.json AND "
                    "references/keyword_vocab_cache.json both missing/empty")
    elif not _row_has_key(vrows[0], "ad_group_criterion.keyword.text"):
        errs.append("keyword vocabulary: missing 'ad_group_criterion.keyword.text' (overflow swap?)")
    for name, key in PREFLIGHT_OPTIONAL:
        rows = load(anchor, name)
        if rows:
            e = check(name, key, rows)
            if e:
                warns.append(e)
        else:
            warns.append(f"{name}: not fetched (its section will be omitted/limited)")
    for w in warns:
        print("  ⚠️  preflight:", w)
    if errs:
        print(f"\n❌ PREFLIGHT FAILED for {anchor} — not rendering (would be a blank/wrong report):")
        for e in errs:
            print("   -", e)
        print(f"\nFix the data under data/{anchor}/ and re-run. See routine-prompt.md.")
        sys.exit(1)


# ── HTML rendering ───────────────────────────────────────────────────────────
def render(anchor):
    preflight(anchor)
    run_date = (datetime.strptime(anchor, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    prior = (datetime.strptime(anchor, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    dow = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][datetime.strptime(anchor, "%Y-%m-%d").weekday()]
    pretty = datetime.strptime(anchor, "%Y-%m-%d").strftime("%A %B %-d, %Y")

    spend_cur   = agg_spend(load(anchor, "spend_cur.json"))
    spend_prior = agg_spend(load(anchor, "spend_prior.json"))
    demos_cur   = agg_demos(load(anchor, "demos_cur.json"))
    demos_prior = agg_demos(load(anchor, "demos_prior.json"))
    # Daily Brain Day-0 cohort override (platform fallback when absent — see routine-prompt.md).
    cohort = load_cohort_demos(anchor)
    if cohort:
        demos_cur   = dict(cohort.get("campaign_cur", {}))
        demos_prior = dict(cohort.get("campaign_prior", {}))
    ranklost_rows = load(anchor, "ranklost.json")
    rl_ranked   = rank_lost_ranked(ranklost_rows)
    st_cur      = load(anchor, "searchterms.json")
    st_prior    = load(anchor, "searchterms_prior.json")
    proj        = projected_month(load(anchor, "monthspend.json"), run_date)

    converted90 = set((g(r, "search_term_view.search_term") or "").lower()
                      for r in load(anchor, "converted90.json"))
    # GLOBAL account keyword vocabulary. Bing's keyword corpus is thin (~280 active
    # keywords), so we ENRICH the on-topic token set with two more authoritative sources:
    # search terms that converted in the trailing 90 days, and ad-group names. That keeps
    # legitimate PM queries from being false-flagged while real junk stays off-vocabulary.
    vocab = set()
    for r in load_vocab(anchor):
        for tk in tokens(g(r, "ad_group_criterion.keyword.text") or ""):
            vocab.add(tk)
    for term in converted90:
        for tk in tokens(term):
            vocab.add(tk)
    for r in st_cur:
        for tk in tokens(g(r, "ad_group.name") or ""):
            vocab.add(tk)

    # summary rows
    def summary(excl):
        s, d, cpd = sum_group(spend_cur, demos_cur, excl)
        ps, pd, pcpd = sum_group(spend_prior, demos_prior, excl)
        rows = [
            ("Spend", money(s), money(ps), sdiff(s, ps, "money", neutral=True), "✅"),
            ("Demos Scheduled", conv(d), conv(pd), sdiff(d, pd, "conv"), status_demos(d, pd)),
            ("Cost Per Demo", money(cpd), money(pcpd), sdiff(cpd, pcpd, "money", lower_is_better=True), status_cpd(cpd)),
        ]
        return rows

    all_rows = summary(False)
    # All-campaigns extras
    c3 = top3_cpc(st_cur)
    p3 = " / ".join(money((float(g(r,"metrics.average_cpc") or 0))/1e6) for r in st_prior[:3]) or "—"
    # Rank-lost IS — the single worst (highest) campaign with MEANINGFUL spend (a
    # tiny-spend campaign at a high IS is noise). Falls back to the global highest if
    # none clear the floor, so the row is never blank. No prior-week value.
    qualifying = [o for o in rl_ranked if o["cost"] >= RANK_MIN_SPEND]
    worst_obj = qualifying[0] if qualifying else (rl_ranked[0] if rl_ranked else None)
    worst = worst_obj["rl"] if worst_obj else None
    rl_txt = f"{short_name(worst_obj['name'])} {pct(worst)}" if worst_obj else "—"
    # Search Lost IS (Budget) — report-day only; names every campaign >0; whole row
    # turns red when any campaign is losing volume to budget.
    blost = budget_lost(ranklost_rows)
    if blost:
        bl_txt = ", ".join(f"{short_name(o['name'])} {pct(o['bl'])}" for o in blost)
        bl_row = ("Search Lost IS (Budget)", bl_txt, "—", "—", "🔴", "row-alert")
    else:
        bl_row = ("Search Lost IS (Budget)", "0%", "—", "—", "✅")
    all_rows += [
        ("Top 3 CPCs (search terms)", c3, p3, "—", "🟢"),
        ("Rank-lost IS (Highest)", rl_txt, "—", "—", status_rank(worst)),
        bl_row,
        ("Projected monthly spend", money(proj), "—", "—", ""),
    ]
    excl_rows = summary(True)

    cur_hdr = "Yesterday" if anchor == prior else dow  # label tweak below
    cur_label = "Yesterday" if run_date == (datetime.strptime(anchor,"%Y-%m-%d")+timedelta(days=1)).strftime("%Y-%m-%d") else dow
    prior_label = "Prior " + dow

    # top 20 cpc
    top20 = search_term_cpcs(st_cur)[:20]
    # bad terms (load confirmed bad brands file for deterministic hits)
    known_bad = load_known_bad_brands()
    bad = build_bad_terms(st_cur, converted90, vocab, known_bad)

    def summary_table(tid, rows):
        trs = ""
        for row in rows:
            m, c, p, t, st = row[:5]
            cls = row[5] if len(row) > 5 else ""   # optional row class (e.g. budget-loss alert)
            trs += (f'<tr class="{cls}"><td class="metric-name">{esc(m)}</td>'
                    f'<td class="num">{c}</td><td class="num">{p}</td>'
                    f'<td class="num">{t}</td><td class="status">{st}</td></tr>')
        return (f'<table id="{tid}"><thead><tr><th style="width:30%">Metric</th>'
                f'<th class="num">{cur_label}&dagger;</th><th class="num">{prior_label}&dagger;</th>'
                f'<th class="num">Difference</th><th class="status">Status</th></tr></thead><tbody>{trs}</tbody></table>')

    cpc_rows = ""
    if not top20:
        cpc_rows = '<tr><td colspan="6" class="muted">No search-term data.</td></tr>'
    for o in top20:
        red = o["cpc"] > CPC_RED
        cpc_rows += (f'<tr class="{"flag-red" if red else ""}">'
                     f'<td class="term">{"🔴 " if red else ""}{esc(o["term"])}</td>'
                     f'<td class="num">{money2(o["cpc"])}</td>'
                     f'<td class="num">{intf(o["clicks"])}</td>'
                     f'<td class="num">{money2(o["cost"])}</td>'
                     f'<td>{esc(o["camp"])}</td><td>{esc(o["ag"])}</td></tr>')

    # Bad Search Terms: UNION of the rule-based audit (`bad`, always run) and the day's
    # Notion audit (bad_terms_audit.json, fetched by the routine). `bad_gaps` = terms the
    # Notion audit caught that the rule-based detector missed (drives the learning loop).
    bad_rows, bad_count, bad_banner, bad_foot, bad_source, bad_gaps = \
        render_bad_terms_section(anchor, bad, int(OVERLAP_RESCUE * 100))

    # Clicks & Demos per day (last 30 days, all ad groups). Optional: only if its data
    # file was fetched (the daily routine fetches clicks_30d.json). demos_30d.json is an
    # optional supplement — when present, each chart gains a Clicks/Demos/Both toggle.
    clicks_rows = load(anchor, "clicks_30d.json")
    chart_data = None
    if clicks_rows:
        demos_rows = load(anchor, "demos_30d.json")     # [] if the routine didn't fetch it
        rankis_rows = load(anchor, "rankis_30d.json")   # [] if not fetched → no Rank-Lost IS toggle
        pipeline = load_pipeline(anchor)                # booked-on-day opps/accounts/ARR (Brain → SF)
        chart_data = clicks_daily_data(clicks_rows, anchor,
                                       demos_rows=(None if cohort else (demos_rows or None)),
                                       camp_demos=(cohort.get("daily_series") if cohort else None),
                                       rankis_rows=rankis_rows or None, daily=True,
                                       pipeline=pipeline)
        clicks_json = json.dumps(chart_data, separators=(",", ":"))
        clicks_section = clicks_section_html(f"last {CLICKS_DAYS} days · all ad groups")
        clicks_js = CLICKS_JS
    else:
        clicks_json, clicks_section, clicks_js = "{}", "", ""

    # Daily Alerts — anomaly detection against the same 30-day data the charts use
    # (so the alert numbers always match what the chart shows for that day).
    alerts = compute_alerts(chart_data, anchor) if chart_data else []
    alerts_body, alerts_count = render_alerts_section(alerts, chart_data is not None)


    # Demo-basis pill + footnote — source chain is Brain → Salesforce → platform.
    # cohort_demos.json carries data_source ("brain" | "salesforce"; absent = brain for
    # back-compat); no file at all = platform conversions (last resort). A pill at the top
    # of the page ALWAYS names the demo data source.
    basis_src = (cohort.get("data_source", "brain") if cohort else "platform")
    if basis_src == "salesforce":
        basis_note = ('<span class="pill" style="background:#B45309;color:#fff;border:0">'
                      'Demos: Salesforce Day-0 cohort (Brain unavailable)</span>')
        demo_footnote = (
            '<p>&dagger; <b>Demos = Salesforce Day-0 cohort</b> — the Brain was unreachable this run, so demos '
            'come straight from Salesforce (read-only): leads whose demo-scheduled date equals their lead-created '
            'date (David&rsquo;s CDF1 formula, <code>Original_Demo_Scheduled_At__c</code> basis), attached to each '
            'campaign by <code>Lead.campaign_ID__c</code> = the Microsoft Ads <b>campaign ID</b>. Same Day-0 idea '
            'as the Brain cohort but UTM first-click attribution, so it typically runs <b>~10&ndash;25% above</b> '
            'the Brain number — a documented basis difference, not growth. <b>Prior</b> = the same weekday exactly '
            '7 days earlier. Chart ad-group demo lines are flat &mdash; the cohort has no ad-group split.</p>')
    elif basis_src == "brain":
        basis_note = '<span class="pill" style="background:#3185FC;color:#fff;border:0">Demos: Brain Day-0 cohort</span>'
        demo_footnote = (
            '<p>&dagger; <b>Demos = Brain Day-0 cohort</b> — demos scheduled the <i>same day</i> as the ad click '
            '(DoorLoop Brain &middot; Paid Media Optimizer &middot; Cohort basis &middot; '
            '<code>gtm-paid-media-daily-cohort</code> &middot; <code>SOURCE=bing</code>), attached to each campaign '
            'by Microsoft Ads <b>campaign ID</b>. Apples-to-apples day-over-day, <b>never matures</b>, and recovers '
            'demos the Microsoft Reporting API misses (the Competitors offline-import gap). <b>Prior</b> = the same '
            'weekday exactly 7 days earlier. Chart ad-group demo lines are flat &mdash; the cohort cube has no '
            'ad-group split. Falls back to Salesforce Day-0 cohort, then platform demos, if the Brain is '
            'unreachable.</p>')
    else:
        basis_note = ('<span class="pill" style="background:#64748B;color:#fff;border:0">'
                      'Demos: Microsoft Ads platform conversions (Brain + Salesforce unavailable)</span>')
        demo_footnote = (
            '<p>&dagger; <b>Demos Scheduled</b> = Microsoft Ads conversions for the <i>Demo_Scheduled</i> goal '
            '(Salesforce/CRM import). Competitors counts both <i>Demo_Scheduled</i> and <i>Online Demo Scheduled</i> '
            'to cover history before its goal changed on 2026-06-04. <b>Prior</b> = the same weekday exactly 7 days '
            'earlier. <i>(Platform demos — last-resort basis: both the Brain Day-0 cohort and the Salesforce '
            'fallback were unavailable this run.)</i></p>')

    # The demo-basis footnote (†) now rides as a tooltip on the basis pill (no page-footer prose).
    basis_note = basis_note + tip(demo_footnote)
    html = TEMPLATE.format(
        pretty=pretty, run_date=run_date, anchor=anchor,
        basis_note=basis_note, demo_footnote=demo_footnote,
        all_table=summary_table("tblAll", all_rows),
        excl_table=summary_table("tblExcl", excl_rows),
        alerts_body=alerts_body, alerts_count=alerts_count, alerts_dt=pretty,
        alerts_z=ALERTS_CFG["Z"], alerts_min_spend=int(ALERTS_CFG["MIN_SPEND"]),
        alerts_demo_base=int(ALERTS_CFG["DEMO_MIN_BASE"]),
        alerts_demo_spike=int(ALERTS_CFG["DEMO_SPIKE"]),
        alerts_red_impact=format(int(ALERTS_CFG["RED_IMPACT"]), ",d"),
        alerts_confirm_rel=int(ALERTS_CFG["CONFIRM_REL"] * 100),
        cpc_dt=pretty, cpc_rows=cpc_rows,
        bad_dt=pretty, bad_rows=bad_rows, bad_banner=bad_banner, bad_foot=bad_foot,
        cpc_red=CPC_RED, cpd_target=f"{CPD_TARGET:,}",
        bad_count=bad_count,
        clicks_section=clicks_section, clicks_json=clicks_json, clicks_js=clicks_js,
        line_labels_js=(LINE_LABELS_JS if clicks_section else ""),
        chartjs_src=CHARTJS_SRC, chartjs_sri=CHARTJS_SRI,
    )
    out_dir = os.path.join(ROOT, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{anchor}.html")
    with open(out_path, "w") as f:
        f.write(html)
    print("Wrote", out_path)
    print(f"  All: spend={money(sum_group(spend_cur,demos_cur,False)[0])} demos={conv(sum_group(spend_cur,demos_cur,False)[1])} cpd={money(sum_group(spend_cur,demos_cur,False)[2])}")
    print(f"  Excl-branded: spend={money(sum_group(spend_cur,demos_cur,True)[0])} demos={conv(sum_group(spend_cur,demos_cur,True)[1])} cpd={money(sum_group(spend_cur,demos_cur,True)[2])}")
    print(f"  Projected month: {money(proj)} | Top20 terms: {len(top20)} | Bad terms: {bad_count} ({bad_source})")
    _blp = ", ".join(f"{short_name(o['name'])} {pct(o['bl'])}" for o in blost) or "none"
    print(f"  Rank-lost highest: {rl_txt} | Budget-lost (>0%): {_blp}")
    _ar = sum(1 for a in alerts if a["sev"] == "red")
    print(f"  Daily Alerts: {alerts_count} ({_ar} red / {alerts_count - _ar} yellow)"
          + (" — " + "; ".join(f"{a['entity']}·{a['label']}" for a in alerts[:6]) if alerts else ""))
    return out_path

TEMPLATE = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="googlebot" content="noindex,nofollow">
<title>Microsoft Ads Daily Snapshot — {anchor}</title>
<style>
  :root{{color-scheme:light;--core-blue:#3185FC;--navy:#162050;--neon:#DFFE02;--pink:#FF4998;
    --gray-100:#F5F7FA;--gray-200:#E4E8EE;--gray-300:#C9D0DA;--gray-500:#6B7280;--gray-700:#374151;
    --warn:#B45309;--warn-bg:#FEF3C7;--err:#B91C1C;--err-bg:#FEE2E2;--ok:#047857;--ok-bg:#D1FAE5;
    --shadow:0 1px 3px rgba(22,32,80,.08),0 1px 2px rgba(22,32,80,.05);
    --bg-page:#F5F7FA;--bg-card:#FFFFFF;--text-primary:#162050;--text-secondary:#374151;
    --text-muted:#6B7280;--border:#E4E8EE;--table-hover:#F5F7FA;}}
  [data-theme="dark"]{{color-scheme:dark;--bg-page:#0F1117;--bg-card:#1A1D2E;--text-primary:#E8EAF0;
    --text-secondary:#B0B7C3;--text-muted:#8A93A6;--border:#2D3148;--gray-200:#2D3148;
    --table-hover:#222540;--shadow:0 1px 3px rgba(0,0,0,.4);--warn-bg:#3a2e0d;--err-bg:#3a1414;--ok-bg:#0f2e22;}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg-page);color:var(--text-primary);font-size:14px;line-height:1.45}}
  .wrap{{max-width:1180px;margin:0 auto;padding:22px 22px 60px}}
  header{{display:flex;flex-wrap:wrap;align-items:center;gap:14px}}
  h1{{font-size:20px;margin:0;font-weight:700;letter-spacing:-.01em}}
  .pill{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;background:var(--gray-200);
    color:var(--text-secondary);margin-left:6px;vertical-align:middle}}
  .sub{{color:var(--text-muted);font-size:12.5px;margin:4px 0 20px}}
  #themeBtn{{margin-left:auto;cursor:pointer;border:1px solid var(--border);background:var(--bg-card);
    color:var(--text-primary);border-radius:8px;padding:7px 12px;font-size:13px;font-weight:600}}
  .card{{background:var(--bg-card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);
    padding:16px 18px;margin-bottom:20px}}
  .card h2{{font-size:15px;margin:0 0 12px;font-weight:700}}
  .card h2 .dt{{color:var(--text-muted);font-weight:500;font-size:13px}}
  .kind-tag{{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;
    background:var(--gray-200);color:var(--text-secondary);letter-spacing:.02em;margin-right:2px}}
  .ok-line{{color:var(--ok);font-weight:600;font-size:13px;margin:4px 0 10px}}
  .alert-sub{{color:var(--text-muted);font-size:11.5px;font-weight:400;margin-top:2px}}
  tr.alert-more{{display:none}} tr.alert-more.show{{display:table-row}}
  .alert-toggle{{cursor:pointer;border:1px solid var(--border);background:var(--bg-card);
    color:var(--text-secondary);border-radius:8px;padding:5px 12px;font-size:12px;font-weight:600;margin-top:10px}}
  .card.accordion>h2{{cursor:pointer;user-select:none}}
  .card.accordion>h2::after{{content:"▾";float:right;color:var(--text-muted);font-size:13px}}
  .card.accordion.collapsed>h2{{margin-bottom:0}}
  .card.accordion.collapsed>h2::after{{content:"▸"}}
  .card.accordion.collapsed>*:not(h2){{display:none}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top}}
  th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600}}
  td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
  tr:hover td{{background:var(--table-hover)}}
  .metric-name{{font-weight:600}} .target{{color:var(--text-muted)}} .status{{text-align:center;font-size:15px}}
  .delta{{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}}
  .delta.good{{color:var(--ok,#047857)}} .delta.bad{{color:var(--err,#B91C1C)}} .delta.flat{{color:var(--text-muted);font-weight:500}}
  .flag-red td{{background:rgba(185,28,28,.06)}}
  .verdict-bad{{display:inline-block;background:var(--err-bg);color:var(--err);font-weight:700;font-size:11px;
    padding:2px 7px;border-radius:6px}}
  .warn-banner{{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;
    padding:8px 12px;font-size:12.5px;font-weight:600;margin:0 0 12px}}
  .alert-top{{background:var(--err-bg);color:var(--err);border:2px solid var(--err);border-radius:10px;
    padding:12px 16px;font-size:13.5px;font-weight:600;margin:0 0 18px;box-shadow:var(--shadow)}}
  tr.row-alert td{{background:var(--err-bg)}} tr.row-alert .metric-name{{color:var(--err)}}
  .src-note{{display:inline-block;background:var(--gray-200);color:var(--text-secondary);font-size:11px;
    font-weight:600;padding:2px 8px;border-radius:99px;margin-left:6px;vertical-align:middle}}
  .src-tag{{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;
    margin-left:4px;vertical-align:middle;letter-spacing:.02em}}
  .src-rule{{background:var(--gray-200);color:var(--text-secondary)}}
  .src-both{{background:var(--ok-bg);color:var(--ok)}}
  .src-notion{{background:var(--warn-bg);color:var(--warn)}}
  tr.gap-row td{{background:var(--warn-bg)}}
  .term{{font-weight:600}} .reason{{color:var(--text-secondary);font-size:12.5px}} .muted{{color:var(--text-muted)}}
  .foot{{color:var(--text-muted);font-size:12px;margin-top:6px}} .foot p{{margin:3px 0}}
  /* clicks-per-day — one chart per campaign */
  .chips{{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 10px}}
  .chips.clicks{{max-height:78px;overflow-y:auto;padding:2px}}
  .chip{{cursor:pointer;border:1px solid var(--border);background:var(--bg-card);color:var(--text-secondary);
    border-radius:99px;padding:3px 9px;font-size:11px;font-weight:600;user-select:none;white-space:nowrap}}
  .chip.on{{color:#fff}}
  .clk-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}
  .clk-card{{border:1px solid var(--border);border-radius:12px;padding:12px 12px 8px;background:var(--bg-card);min-width:0}}
  .canvas-wrap{{min-width:0}}
  .clk-title{{font-weight:700;font-size:13px;margin-bottom:8px}}
  .metric-toggle{{display:inline-flex;gap:0;margin:0 0 8px;border:1px solid var(--border);border-radius:8px;overflow:hidden}}
  .mbtn{{cursor:pointer;font-size:11px;font-weight:600;padding:3px 10px;color:var(--text-secondary);
    background:var(--bg-card);user-select:none;border-right:1px solid var(--border)}}
  .mbtn:last-child{{border-right:none}}
  .mbtn.on{{background:var(--core-blue);color:#fff}}
  .mbtn[data-m="demos"].on{{background:#3185FC;color:#fff}}
  .mbtn[data-m="clicks"].on{{background:#162050;color:#fff}}
  .mbtn[data-m="both"].on{{background:linear-gradient(90deg,#162050 0 50%,#3185FC 50% 100%);color:#fff}}
  .mbtn[data-m="imps"].on{{background:#64748B;color:#fff}}
  .mbtn[data-m="spend"].on{{background:#EA580C;color:#fff}}
  .mbtn[data-m="cpc"].on{{background:#0D9488;color:#fff}}
  .mbtn[data-m="cpd"].on{{background:#7C3AED;color:#fff}}
  .mbtn[data-m="rankis"].on{{background:#B45309;color:#fff}}
  .mbtn[data-m="imprshare"].on{{background:#15803D;color:#fff}}
  .mbtn[data-m="ctd"].on{{background:#DB2777;color:#fff}}
  .mbtn[data-m="opps"].on{{background:#0EA5E9;color:#fff}}
  .mbtn[data-m="accts"].on{{background:#9333EA;color:#fff}}
  .mbtn[data-m="arr"].on{{background:#059669;color:#fff}}
  .clk-pipenote{{font-size:11.5px;color:var(--warn);background:var(--warn-bg);border:1px solid #FCD9A8;
    border-radius:8px;padding:7px 11px;margin-bottom:4px}}
  .canvas-wrap{{position:relative;height:210px}}
  @media(max-width:760px){{.clk-grid{{grid-template-columns:1fr}}}}
  /* Metric Grid view (daily-only) */
  .clk-views{{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;margin:0 0 14px}}
  .clk-views .vbtn{{cursor:pointer;font-size:12px;font-weight:600;padding:6px 16px;color:var(--text-secondary);
    background:var(--bg-card);border-right:1px solid var(--border);user-select:none}}
  .clk-views .vbtn:last-child{{border-right:none}}
  .clk-views .vbtn.on{{background:var(--core-blue);color:#fff}}
  .clk-grid.grid-metrics{{grid-template-columns:repeat(3,1fr)}}
  .clk-grid.grid-metrics .clk-tile .canvas-wrap.clk-tilewrap{{height:132px}}
  .clk-sub{{font-size:11px;color:var(--text-secondary);margin:-4px 0 8px}}
  @media(max-width:1100px){{.clk-grid.grid-metrics{{grid-template-columns:repeat(2,1fr)}}}}
  @media(max-width:760px){{.clk-grid.grid-metrics{{grid-template-columns:1fr}}}}
  /* Info-icon tooltips (descriptions moved off the page into hover/focus popovers) */
  .tip{{position:relative;display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
    border-radius:50%;border:1px solid var(--gray-300);color:var(--gray-500);font:700 10px Georgia,serif;
    font-style:italic;cursor:help;margin-left:7px;vertical-align:middle;user-select:none}}
  .tip:hover,.tip:focus{{background:var(--core-blue);border-color:var(--core-blue);color:#fff;outline:none}}
  .tip .tipbox{{display:none;position:absolute;z-index:60;top:150%;left:0;width:380px;max-width:80vw;
    background:var(--bg-card);color:var(--text-secondary);border:1px solid var(--border);border-radius:10px;
    box-shadow:var(--shadow);padding:11px 13px;font:400 12px/1.5 inherit;text-align:left;cursor:auto;white-space:normal}}
  .tip.tip-r .tipbox{{left:auto;right:0}}
  .tip:hover .tipbox,.tip:focus .tipbox{{display:block}}
  .tip .tipbox p{{margin:0 0 7px}} .tip .tipbox p:last-child{{margin:0}}
  .tip .tipbox b{{color:var(--text-primary)}}
</style>
<script src="{chartjs_src}" integrity="{chartjs_sri}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
</head>
<body><div class="wrap">
  <header>
    <h1>Microsoft Ads Daily Snapshot <span class="pill">Microsoft Ads only</span>{basis_note}</h1>
    <button id="themeBtn">◐ Theme</button>
  </header>
  <div class="sub">reporting <b>{pretty}</b> · generated {run_date}</div>


  <div class="card"><h2>All Campaigns</h2>{all_table}</div>

  <div class="card"><h2>Excl. Branded <span class="dt">— active campaigns without “branded” in the name</span></h2>{excl_table}</div>

  <div class="card accordion">
    <h2>Daily Alerts <span class="dt">— {alerts_dt} · {alerts_count} anomalies</span></h2>
    {alerts_body}
  </div>

  {clicks_section}

  <div class="card">
    <h2>Top 20 Search Term CPCs <span class="dt">— {cpc_dt}</span></h2>
    <table><thead><tr><th>Search Term</th><th class="num">Avg CPC</th><th class="num">Clicks</th>
      <th class="num">Cost</th><th>Campaign</th><th>Ad Group</th></tr></thead>
      <tbody>{cpc_rows}</tbody></table>
    <div class="foot"><p>🔴 = Avg CPC over ${cpc_red}.</p></div>
  </div>

  <div class="card">
    <h2>Bad Search Terms <span class="dt">— {bad_dt} · {bad_count} flagged</span>{bad_foot}</h2>
    {bad_banner}
    <table><thead><tr><th class="num">#</th><th>Verdict</th><th>Term</th><th>Campaign / Ad Group</th>
      <th class="num">Spend</th><th class="num">Imps</th><th class="num">Clicks</th><th class="num">Conv</th>
      <th>Reason</th></tr></thead><tbody>{bad_rows}</tbody></table>
  </div>

  </div>
</div>
<script id="clicks-data" type="application/json">{clicks_json}</script>
<script>
  document.getElementById("themeBtn").addEventListener("click",function(){{
    var h=document.documentElement; h.dataset.theme=h.dataset.theme==="dark"?"light":"dark";
  }});
  document.querySelectorAll(".card.accordion>h2").forEach(function(h){{
    h.addEventListener("click",function(){{ h.parentElement.classList.toggle("collapsed"); }});
  }});
</script>
<script>{line_labels_js}</script>
<script>{clicks_js}</script>
</body></html>"""

if __name__ == "__main__":
    args = sys.argv[1:]
    _default = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    if args and args[0] == "--vocab-due":
        # prints FETCH/SKIP so the routine pulls the 523 KB keyword vocab at most weekly
        print(vocab_due(args[1] if len(args) > 1 else _default))
    elif args and args[0] == "--refresh-vocab":
        refresh_vocab(args[1] if len(args) > 1 else _default)
    else:
        render(args[0] if args else _default)
