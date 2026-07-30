"""sf_allsources.py — read-only Salesforce all-channel funnel → series.json for the All-Sources tab.

WHY THIS EXISTS
The Brain's paid-media funnel (gtm-paid-media-daily-funnel) only carries SOURCE ∈ {google-ads, bing,
meta}. The report's All-Sources tab also shows ORGANIC channels — Blog, Direct/Organic, Consumer
Reviews — which live only in Salesforce (bucketed by UTM_Source__c). Without them the All-Sources
funnel scorecard freezes at the seed's last Salesforce date. This module pulls the all-channel funnel
straight from Salesforce, exactly as the original report did (backfill/allsources_backfill.js), ported
to Python — 9 channel slices × 7 metrics by day. First-click UTM basis (intentionally a DIFFERENT
basis from the Brain-cohort per-channel tabs; the report documents that difference).

AUTH — JWT Bearer Flow against the dedicated read-only Connected App (same pattern as the Revops
platform's salesforce/scripts/lib/sf-rest-auth.ts). EVERY query is a SELECT; no writes, ever.
  SF_PROD_CLIENT_ID   Connected App consumer key
  SF_PROD_USERNAME    integration-user email (read-only permission set)
  SF_PROD_LOGIN_URL   login endpoint (default https://login.salesforce.com)
  RSA private key, first of: SF_PROD_JWT_KEY_B64 (base64 PEM — DeployBay), SF_PROD_JWT_KEY (raw PEM),
                             SF_PROD_JWT_KEY_PATH (file — local dev)

OUTPUT — <pipeline_root>/all-channels-artifact/data/<anchor>/series.json
  {"days": [...trailing window, daily...], "slices": {<key>: {label, mqls, demos, demos_day0,
   completed, opps, accounts, arr}}}  — arrays aligned to days.
update_history.py:476 merges this into history/allsources.json; engine.js renders it client-side.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import requests

API_VER = os.environ.get("SF_API_VERSION", "60.0")
WINDOW_DAYS = int(os.environ.get("SF_ALLSOURCES_WINDOW_DAYS", "140"))  # late-stage funnel restates for weeks


# ── auth (mirrors salesforce/scripts/lib/sf-rest-auth.ts) ───────────────────────────────────────
def _private_key() -> str:
    b64 = os.environ.get("SF_PROD_JWT_KEY_B64")
    if b64:
        return base64.b64decode(b64).decode("utf-8")
    pk = os.environ.get("SF_PROD_JWT_KEY")
    if pk:
        return pk
    path = os.environ.get("SF_PROD_JWT_KEY_PATH")
    if path and os.path.exists(path):
        with open(path) as f:
            return f.read()
    raise RuntimeError("no SF private key (set SF_PROD_JWT_KEY_B64 / SF_PROD_JWT_KEY / SF_PROD_JWT_KEY_PATH)")


def _build_jwt(client_id: str, username: str, login_url: str, private_key: str) -> str:
    import jwt as pyjwt  # PyJWT (+ cryptography) — imported lazily so a missing dep is a clear error
    now = int(time.time())
    return pyjwt.encode(
        {"iss": client_id, "sub": username, "aud": login_url, "exp": now + 180},
        private_key, algorithm="RS256",
    )


def _get_token() -> Tuple[str, str]:
    """(access_token, instance_url) via JWT Bearer Flow. Read-only Connected App."""
    for k in ("SF_PROD_CLIENT_ID", "SF_PROD_USERNAME"):
        if not os.environ.get(k):
            raise RuntimeError(f"missing required env var {k}")
    client_id = os.environ["SF_PROD_CLIENT_ID"]
    username = os.environ["SF_PROD_USERNAME"]
    login_url = os.environ.get("SF_PROD_LOGIN_URL", "https://login.salesforce.com").rstrip("/")
    assertion = _build_jwt(client_id, username, login_url, _private_key())
    r = requests.post(
        f"{login_url}/services/oauth2/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"SF JWT token {r.status_code}: {r.text[:300]}")
    d = r.json()
    return d["access_token"], d["instance_url"].rstrip("/")


def _query(token: str, instance_url: str, soql: str) -> List[dict]:
    """SELECT with queryMore pagination. Raises with the SOQL tail on any non-2xx."""
    out: List[dict] = []
    url = f"{instance_url}/services/data/v{API_VER}/query"
    params: Optional[dict] = {"q": soql}
    while True:
        r = requests.get(url, params=params, headers={"authorization": f"Bearer {token}"}, timeout=120)
        if not r.ok:
            raise RuntimeError(f"SF query {r.status_code}: {r.text[:300]}\nSOQL: {soql[:240]}")
        d = r.json()
        out.extend(d.get("records", []))
        nxt = d.get("nextRecordsUrl")
        if d.get("done", True) or not nxt:
            break
        url, params = f"{instance_url}{nxt}", None
    return out


# ── channel + metric config (verbatim from allsources_backfill.js) ──────────────────────────────
def _branded(p: str) -> str:
    return f"({p}UTM_Campaign__c LIKE '%rand%' AND (NOT {p}UTM_Campaign__c LIKE '%non-brand%'))"


# (key, label, where(prefix)) — prefix is '' on the metric's own object, 'Account.' for Opportunity
CHANNELS: List[Tuple[str, str, Callable[[str], str]]] = [
    ("all",       "All Channels",         lambda p: ""),
    ("google",    "Google Ads",           lambda p: f"{p}UTM_Source__c = 'google-ads'"),
    ("google_xb", "Google Ads ex-Brand",  lambda p: f"{p}UTM_Source__c = 'google-ads' AND (NOT {_branded(p)})"),
    ("bing",      "Bing Ads",             lambda p: f"{p}UTM_Source__c = 'bing'"),
    ("bing_xb",   "Bing Ads ex-Brand",    lambda p: f"{p}UTM_Source__c = 'bing' AND (NOT {_branded(p)})"),
    ("meta",      "Meta Ads",             lambda p: f"{p}UTM_Source__c IN ('fb','ig','fb_broken')"),
    ("reviews",   "Consumer Reviews",     lambda p: f"{p}UTM_Source__c IN ('capterra','GetApp','SoftwareAdvice.com')"),
    ("blog",      "Blog",                 lambda p: f"{p}UTM_Source__c = 'Blog'"),
    ("direct",    "Direct/Organic",       lambda p: f"({p}UTM_Source__c = '' OR {p}UTM_Source__c = 'Direct')"),
]

# (metric_key, object, prefix, filter, date_field, sum_arr) — 'completed' is a two-row hybrid that
# accumulates (Demo_Completed_At__c when present, else Original_Demo_Scheduled_At__c ≈9% of the time).
METRICS: List[Tuple[str, str, str, str, str, bool]] = [
    ("mqls",      "Lead",        "",         "Is_MQL__c = true",                                                False),
    ("demos",     "Lead",        "",         "",                                                                False),
    ("completed", "Lead",        "",         "Demo_Status__c = 'Completed' AND Demo_Completed_At__c != null",    False),
    ("completed", "Lead",        "",         "Demo_Status__c = 'Completed' AND Demo_Completed_At__c = null",     False),
    ("opps",      "Opportunity", "Account.", "",                                                                False),
    ("accounts",  "Account",     "",         "Stripe_Subscription_ARR__c != null",                              True),
]
# date field per metric row, in the same order as METRICS
METRIC_DATES: List[str] = [
    "Lead_Created_Date__c", "Original_Demo_Scheduled_At__c", "Demo_Completed_At__c",
    "Original_Demo_Scheduled_At__c", "CreatedDate", "Stripe_Subscription_First_Invoice_At__c",
]


def _ny_date(iso: str) -> Optional[str]:
    """SF UTC datetime string → America/New_York calendar date (YYYY-MM-DD)."""
    if not iso:
        return None
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return str(iso)[:10]


def _classify(src_raw: Optional[str], camp_raw: Optional[str]) -> List[str]:
    """Client-side channel classification for the Day-0 cohort (mirrors classify() in the JS)."""
    src = (src_raw or "").lower()
    camp = (camp_raw or "").lower()
    branded = "rand" in camp and "non-brand" not in camp
    keys = ["all"]
    if src == "google-ads":
        keys.append("google")
        if not branded:
            keys.append("google_xb")
    if src == "bing":
        keys.append("bing")
        if not branded:
            keys.append("bing_xb")
    if src in ("fb", "ig", "fb_broken"):
        keys.append("meta")
    if src in ("capterra", "getapp", "softwareadvice.com"):
        keys.append("reviews")
    if src == "blog":
        keys.append("blog")
    if src in ("", "direct"):
        keys.append("direct")
    return keys


def build_series(anchor: str, pipeline_root: str, window_days: int = WINDOW_DAYS) -> Tuple[str, dict]:
    """Pull the all-channel funnel for the trailing <window_days> and write series.json.

    Returns (path, sanity) where sanity = summed 'all' metrics over the window (for logging)."""
    token, instance_url = _get_token()

    a = date.fromisoformat(anchor)
    start = a - timedelta(days=window_days - 1)
    days: List[str] = []
    d = start
    while d <= a:
        days.append(d.isoformat())
        d += timedelta(days=1)
    idx = {dt: i for i, dt in enumerate(days)}
    n = len(days)
    # WHERE bounds padded ±1 day (UTC offset safety — same as the JS backfill)
    lo = (start - timedelta(days=1)).isoformat() + "T00:00:00Z"
    hi = (a + timedelta(days=2)).isoformat() + "T00:00:00Z"

    slices: Dict[str, dict] = {
        k: {"label": lbl, "mqls": [0] * n, "demos": [0] * n, "demos_day0": [0] * n,
            "completed": [0] * n, "opps": [0] * n, "accounts": [0] * n, "arr": [0.0] * n}
        for k, lbl, _ in CHANNELS
    }

    nq = 0
    # 1) per-day GROUP BY series: 6 metric rows × 9 channels
    for (mkey, obj, prefix, mfilter, sum_arr), mdate in zip(METRICS, METRIC_DATES):
        for ckey, _lbl, cwhere in CHANNELS:
            where = " AND ".join(c for c in (
                f"{prefix}Test_Account__c = false", mfilter, cwhere(prefix),
                f"{mdate} >= {lo}", f"{mdate} < {hi}") if c)
            sel = (f"SELECT DAY_ONLY(convertTimezone({mdate})) d, COUNT(Id) n, SUM(Stripe_Subscription_ARR__c) arr"
                   if sum_arr else f"SELECT DAY_ONLY(convertTimezone({mdate})) d, COUNT(Id) n")
            soql = f"{sel} FROM {obj} WHERE {where} GROUP BY DAY_ONLY(convertTimezone({mdate}))"
            for r in _query(token, instance_url, soql):
                dd = r.get("d")
                i = idx.get(dd if isinstance(dd, str) else (str(dd)[:10] if dd else None))
                if i is None:
                    continue
                slices[ckey][mkey][i] += int(r.get("n") or 0)  # += so completed's two rows accumulate
                if sum_arr:
                    slices[ckey]["arr"][i] += round(float(r.get("arr") or 0), 2)
            nq += 1

    # 2) Day-0 cohort demos (CDF1) — records pull + client-side classify (no GROUP BY: two date fields)
    recs = _query(token, instance_url,
        "SELECT Lead_Created_Date__c, Original_Demo_Scheduled_At__c, Demo_Scheduled_At__c, "
        "UTM_Source__c, UTM_Campaign__c FROM Lead WHERE Test_Account__c = false AND "
        f"((Original_Demo_Scheduled_At__c >= {lo} AND Original_Demo_Scheduled_At__c < {hi}) OR "
        f"(Original_Demo_Scheduled_At__c = null AND Demo_Scheduled_At__c >= {lo} AND Demo_Scheduled_At__c < {hi}))")
    nq += 1
    for r in recs:
        sched = r.get("Original_Demo_Scheduled_At__c") or r.get("Demo_Scheduled_At__c")
        created = r.get("Lead_Created_Date__c")
        if not sched or not created:
            continue
        sd = _ny_date(sched)
        if sd is None or sd != _ny_date(created):  # not a Day-0 cohort demo
            continue
        i = idx.get(sd)
        if i is None:
            continue
        for k in _classify(r.get("UTM_Source__c"), r.get("UTM_Campaign__c")):
            slices[k]["demos_day0"][i] += 1

    out_dir = os.path.join(pipeline_root, "all-channels-artifact", "data", anchor)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "series.json")
    with open(path, "w") as f:
        json.dump({"days": days, "slices": slices,
                   "meta": {"window": [days[0], anchor], "queries": nq, "basis": "salesforce-utm-first-click"}}, f)

    A = slices["all"]
    sanity = {m: (round(sum(A[m])) if m == "arr" else sum(A[m]))
              for m in ("mqls", "demos", "demos_day0", "completed", "opps", "accounts", "arr")}
    return path, sanity


if __name__ == "__main__":  # local test: python sf_allsources.py <ANCHOR> [pipeline_root]
    import sys
    anc = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=1)).isoformat()
    root = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline")
    p, s = build_series(anc, root)
    print(f"wrote {p}")
    print(f"SANITY [All Channels, {WINDOW_DAYS}d]: {s}")
